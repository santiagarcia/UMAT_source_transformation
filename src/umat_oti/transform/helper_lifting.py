from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from umat_oti.core.model import ParsedFortranSource, ParsedSubroutine
from umat_oti.fortran.parser import split_top_level


class HelperLiftingError(ValueError):
    """Raised when a helper closure cannot be safely lifted to OTI."""


@dataclass(frozen=True)
class LiftedHelperSet:
    helper_names: tuple[str, ...]
    source: str


_HEADER_RE = re.compile(r"^\s*SUBROUTINE\s+([A-Z_][A-Z0-9_]*)\s*\((.*)\)\s*$", re.IGNORECASE)
_CALL_RE = re.compile(r"\bCALL\s+([A-Z_][A-Z0-9_]*)\s*\(", re.IGNORECASE)
_PARAMETER_RE = re.compile(r"^\s*PARAMETER\s*\((.*)\)\s*$", re.IGNORECASE)
_DIMENSION_RE = re.compile(r"^\s*DIMENSION\s*(?:::)?\s*(.*)$", re.IGNORECASE)
_INTEGER_RE = re.compile(r"^\s*INTEGER(?:\s*\*\s*\d+|\s*\([^)]*\))?\s*(?:::)?\s*(.*)$", re.IGNORECASE)
_REAL_RE = re.compile(r"^\s*(?:REAL(?:\s*\*\s*\d+|\s*\([^)]*\))?|DOUBLE\s+PRECISION)\s*(?:::)?\s*(.*)$", re.IGNORECASE)
_CHARACTER_RE = re.compile(r"^\s*CHARACTER(?:\s*\*\s*\d+|\s*\([^)]*\))?\s*(?:::)?\s*(.*)$", re.IGNORECASE)
_LOGICAL_RE = re.compile(r"^\s*LOGICAL(?:\s*\*\s*\d+|\s*\([^)]*\))?\s*(?:::)?\s*(.*)$", re.IGNORECASE)
_DATA_RE = re.compile(r"^\s*DATA\s+(.*)$", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\b([A-Z_][A-Z0-9_]*)\b", re.IGNORECASE)
_LHS_ASSIGN_RE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*(?:\([^=]*\))?\s*=", re.IGNORECASE)
_IF_RE = re.compile(r"^(\s*(?:\d+\s+)?(?:ELSE\s+)?IF\s*)\(", re.IGNORECASE)
_TYPED_INTRINSIC_MAP = {
    "DABS": "ABS",
    "DACOS": "ACOS",
    "DASIN": "ASIN",
    "DATAN": "ATAN",
    "DATAN2": "ATAN2",
    "DCOS": "COS",
    "DCOSH": "COSH",
    "DEXP": "EXP",
    "DLOG": "LOG",
    "DLOG10": "LOG10",
    "DMAX1": "MAX",
    "DMIN1": "MIN",
    "DMOD": "MOD",
    "DSIGN": "SIGN",
    "DSIN": "SIN",
    "DSINH": "SINH",
    "DSQRT": "SQRT",
    "DTAN": "TAN",
    "DTANH": "TANH",
}
_TYPED_INTRINSIC_RE = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in sorted(_TYPED_INTRINSIC_MAP, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_KEYWORDS = {
    "AND",
    "CALL",
    "CONTINUE",
    "DO",
    "ELSE",
    "END",
    "EQ",
    "ENDIF",
    "GE",
    "GO",
    "GOTO",
    "GT",
    "IF",
    "LE",
    "LT",
    "NE",
    "NOT",
    "OR",
    "RETURN",
    "THEN",
}
_INTRINSIC_NAMES = {
    "ABS",
    "ACOS",
    "ASIN",
    "ATAN",
    "ATAN2",
    "COS",
    "COSH",
    "EXP",
    "LOG",
    "LOG10",
    "MAX",
    "MIN",
    "MOD",
    "REAL",
    "SIGN",
    "SIN",
    "SINH",
    "SQRT",
    "TAN",
    "TANH",
}
_IMPLICIT_INTEGER_FIRST_LETTERS = frozenset("IJKLMN")


def helper_lift_closure(
    parsed: ParsedFortranSource,
    helper_roots: Iterable[str],
    *,
    selected_umat: str,
) -> tuple[str, ...]:
    routines = {routine.upper_name: routine for routine in parsed.subroutines if routine.upper_name != selected_umat.upper()}
    source_lines = parsed.text.splitlines()
    pending = [str(name).upper() for name in helper_roots if str(name).strip()]
    if not pending:
        return ()
    missing = sorted({name for name in pending if name not in routines})
    if missing:
        raise HelperLiftingError(
            f"Helper lifting requires source definitions for {missing}. The completed JSON rewrites those calls, so pass-through is unsafe."
        )
    ordered: list[str] = []
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        ordered.append(current)
        routine = routines[current]
        for callee in _routine_callees(routine, parsed.form, source_lines):
            if callee == selected_umat.upper():
                continue
            if callee in _LIFTED_BODY_INLINED:
                # Trivial utility (e.g. KCLEAR) inlined directly in the lifted
                # body, so it needs no definition and is not lifted. Lets UMATs
                # that omit its definition (it resolves from a shared library at
                # Abaqus link time) still lift their helper closures.
                continue
            if callee not in routines:
                raise HelperLiftingError(
                    f"Helper lifting for {current} reached external or undefined callee {callee}. Add lifting support for that dependency before rewriting the call through OTI."
                )
            if callee not in seen:
                pending.append(callee)
    return tuple(ordered)


def lift_helper_set_source(
    parsed: ParsedFortranSource,
    helper_names: Iterable[str],
    *,
    module_name: str,
    type_name: str,
    helper_output_copies: dict[str, list[dict[str, Any]]] | None = None,
    helper_output_surfaces: dict[str, list[dict[str, Any]]] | None = None,
) -> LiftedHelperSet:
    routines = {routine.upper_name: routine for routine in parsed.subroutines}
    source_lines = parsed.text.splitlines()
    ordered = tuple(dict.fromkeys(str(name).upper() for name in helper_names if str(name).strip()))
    missing = [name for name in ordered if name not in routines]
    if missing:
        raise HelperLiftingError(f"Helper lifting could not find parsed routines for {missing}.")
    lifted_set = set(ordered)
    body = "\n\n".join(
        _lift_helper_routine(
            routines[name],
            parsed.form,
            source_lines,
            lifted_set,
            module_name,
            type_name,
            helper_output_copies=(helper_output_copies or {}).get(name, []),
            helper_output_surfaces=(helper_output_surfaces or {}).get(name, []),
        )
        for name in ordered
    )
    return LiftedHelperSet(helper_names=ordered, source=body + ("\n" if body else ""))


def _routine_callees(routine: ParsedSubroutine, form: str, source_lines: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in _continuation_stitch(_routine_source_lines(source_lines, routine), form):
        statement = _statement_text(raw, form)
        for match in _CALL_RE.finditer(statement):
            callee = match.group(1).upper()
            if callee not in seen:
                seen.add(callee)
                ordered.append(callee)
    return tuple(ordered)


# Trivial utility helpers inlined directly inside lifted helper bodies (so they
# need no source definition and are never lifted). KCLEAR(X,NR,NC) zeroes X.
_LIFTED_BODY_INLINED = frozenset({"KCLEAR"})

_KCLEAR_CALL_RE = re.compile(
    r"^\s*CALL\s+KCLEAR\s*\(\s*([A-Za-z_]\w*)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)\s*$",
    re.IGNORECASE,
)


def _kclear_inline_lines(statement: str) -> list[str] | None:
    """Inline a CALL KCLEAR(target, nr, nc) as an explicit zeroing loop.

    Returns None if the statement is not a KCLEAR call. Loop indices use M-names
    (integer under the lifted body's `implicit integer (i-n)`).
    """
    match = _KCLEAR_CALL_RE.match(statement)
    if not match:
        return None
    target, nr, nc = match.group(1), match.group(2).strip(), match.group(3).strip()
    if nc == "1":
        return [f"    do mclr1 = 1, {nr}", f"      {target}(mclr1) = 0.0d0", "    end do"]
    return [
        f"    do mclr1 = 1, {nr}",
        f"      do mclr2 = 1, {nc}",
        f"        {target}(mclr1,mclr2) = 0.0d0",
        "      end do",
        "    end do",
    ]


def _lift_helper_routine(
    routine: ParsedSubroutine,
    form: str,
    source_lines: list[str],
    lifted_names: set[str],
    module_name: str,
    type_name: str,
    helper_output_copies: list[dict[str, Any]],
    helper_output_surfaces: list[dict[str, Any]],
) -> str:
    raw_lines = _routine_source_lines(source_lines, routine)
    if not raw_lines:
        raise HelperLiftingError(f"Routine {routine.name} is empty.")
    stitched_lines = _continuation_stitch(raw_lines, form)
    if not stitched_lines:
        raise HelperLiftingError(f"Routine {routine.name} did not produce any stitched source lines.")
    header_match = _HEADER_RE.match(_statement_text(stitched_lines[0], form))
    if not header_match:
        raise HelperLiftingError(f"Cannot parse helper header for {routine.name}: {stitched_lines[0]!r}")
    original_name = header_match.group(1).upper()
    args = [arg.strip() for arg in split_top_level(header_match.group(2)) if arg.strip()]
    existing_arg_names = {arg.upper() for arg in args}
    args.extend(
        spec["caller_variable"]
        for spec in helper_output_surfaces
        if spec.get("caller_variable") and spec["caller_variable"] not in existing_arg_names
    )
    integer_names: set[str] = set()
    character_names: set[str] = set()
    logical_names: set[str] = set()
    parameter_names: set[str] = set()
    declaration_oti_names: set[str] = set()
    prelude: list[str] = []
    body: list[str] = []
    data_assignments: list[str] = []

    for raw in stitched_lines[1:-1]:
        stripped = _statement_text(raw, form)
        if not stripped:
            continue
        if stripped.upper().startswith("INCLUDE "):
            continue
        if re.match(r"^\s*IMPLICIT\s+", stripped, re.IGNORECASE):
            continue
        parameter_match = _PARAMETER_RE.match(stripped)
        if parameter_match:
            parameter_lines, names = _rewrite_parameter_line(parameter_match.group(1))
            prelude.extend(parameter_lines)
            parameter_names.update(names)
            continue
        dimension_match = _DIMENSION_RE.match(stripped)
        if dimension_match:
            lines, oti_names, ints = _rewrite_dimension_line(dimension_match.group(1), type_name)
            prelude.extend(lines)
            declaration_oti_names.update(oti_names)
            integer_names.update(ints)
            continue
        integer_match = _INTEGER_RE.match(stripped)
        if integer_match:
            payload = integer_match.group(1).strip()
            prelude.append(f"    integer :: {payload}")
            integer_names.update(_declared_names(payload))
            continue
        real_match = _REAL_RE.match(stripped)
        if real_match:
            payload = real_match.group(1).strip()
            prelude.append(f"    type({type_name}) :: {payload}")
            declaration_oti_names.update(_declared_names(payload))
            continue
        character_match = _CHARACTER_RE.match(stripped)
        if character_match:
            prelude.append(f"    {stripped}")
            character_names.update(_declared_names(character_match.group(1)))
            continue
        logical_match = _LOGICAL_RE.match(stripped)
        if logical_match:
            prelude.append(f"    {stripped}")
            logical_names.update(_declared_names(logical_match.group(1)))
            continue
        data_match = _DATA_RE.match(stripped)
        if data_match:
            data_assignments.extend(f"    {assignment}" for assignment in _data_to_assignments(data_match.group(1)))
            continue
        body.append(raw)

    for spec in helper_output_surfaces:
        caller_variable = str(spec.get("caller_variable") or "").upper()
        declared_shape = str(spec.get("declared_shape") or "").strip()
        if not caller_variable or caller_variable in declaration_oti_names:
            continue
        suffix = f"({declared_shape})" if declared_shape else ""
        prelude.append(f"    type({type_name}) :: {caller_variable}{suffix}")
        declaration_oti_names.add(caller_variable)

    declared_non_oti = integer_names | character_names | logical_names | parameter_names
    oti_names = set(declaration_oti_names)
    for arg in args:
        upper = arg.upper()
        if upper not in declared_non_oti and not _is_implicit_integer_name(upper):
            oti_names.add(upper)
    oti_names.update(
        _implicit_oti_names(
            [_split_label_and_statement(raw, form)[1] for raw in body],
            lifted_names,
            declared_non_oti,
            parameter_names,
        )
    )

    lines = [
        f"subroutine {original_name.lower()}_oti({', '.join(arg.lower() for arg in args)})",
        f"    use {module_name}, OTI_HELPER_DP => DP",
        f"    implicit type({type_name}) (a-h,o-z)",
        "    implicit integer (i-n)",
    ]
    lines.extend(prelude)
    lines.extend(data_assignments)
    for raw in body:
        label_prefix, statement = _split_label_and_statement(raw, form)
        kclear_lines = _kclear_inline_lines(statement)
        if kclear_lines is not None:
            lines.extend(kclear_lines)
            continue
        rewritten = _rewrite_helper_executable_line(statement, lifted_names, oti_names)
        if re.match(r"^\s*RETURN\b", rewritten, re.IGNORECASE) and helper_output_surfaces:
            lines.extend(_helper_output_surface_lines(helper_output_surfaces))
        if re.match(r"^\s*RETURN\b", rewritten, re.IGNORECASE) and helper_output_copies:
            lines.extend(_helper_output_copy_lines(helper_output_copies))
        lines.append(f"    {label_prefix}{rewritten}")
    lines.append(f"end subroutine {original_name.lower()}_oti")
    return "\n".join(lines)


def _helper_output_surface_lines(helper_output_surfaces: list[dict[str, Any]]) -> list[str]:
    lines = ["    ! OTIS helper-output surface"]
    for spec in helper_output_surfaces:
        target = str(spec.get("caller_variable") or "").upper()
        source = str(spec.get("source_local") or "").upper()
        for component in spec.get("components") or []:
            target_ref = _helper_indexed_name(target, list(component.get("target_indices") or []))
            source_ref = _helper_indexed_name(source, list(component.get("output_indices") or []))
            if target_ref and source_ref:
                lines.append(f"    {target_ref} = {source_ref}")
    return lines


def _helper_output_copy_lines(helper_output_copies: list[dict[str, Any]]) -> list[str]:
    lines = ["    ! OTIS helper-output copy"]
    for spec in helper_output_copies:
        target = str(spec.get("target_argument") or "").upper()
        source = str(spec.get("source_local") or "").upper()
        for component in spec.get("components") or []:
            target_ref = _helper_indexed_name(target, list(component.get("target_indices") or []))
            source_ref = _helper_indexed_name(source, list(component.get("output_indices") or []))
            if target_ref and source_ref:
                lines.append(f"    {target_ref} = {source_ref}")
    return lines


def _helper_indexed_name(name: str, indices: list[int]) -> str:
    if not name:
        return ""
    if not indices:
        return name
    return f"{name}({', '.join(str(index) for index in indices)})"


def _rewrite_parameter_line(payload: str) -> tuple[list[str], set[str]]:
    lines: list[str] = []
    names: set[str] = set()
    for assignment in split_top_level(payload):
        if "=" not in assignment:
            raise HelperLiftingError(f"PARAMETER entry missing '=': {assignment!r}")
        name, value = assignment.split("=", 1)
        name = name.strip()
        value = value.strip()
        names.add(name.upper())
        if re.fullmatch(r"[+-]?\d+", value):
            lines.append(f"    integer, parameter :: {name} = {value}")
        else:
            lines.append(f"    real(8), parameter :: {name} = {_normalize_real_literal(value)}")
    return lines, names


def _rewrite_dimension_line(payload: str, type_name: str) -> tuple[list[str], set[str], set[str]]:
    oti_entries: list[str] = []
    int_entries: list[str] = []
    oti_names: set[str] = set()
    integer_names: set[str] = set()
    for entry in split_top_level(payload):
        clean = entry.strip()
        name = clean.split("(", 1)[0].strip().upper()
        if not name:
            continue
        if _is_implicit_integer_name(name):
            int_entries.append(clean)
            integer_names.add(name)
        else:
            oti_entries.append(clean)
            oti_names.add(name)
    lines: list[str] = []
    if oti_entries:
        lines.append(f"    type({type_name}) :: {', '.join(oti_entries)}")
    if int_entries:
        lines.append(f"    integer :: {', '.join(int_entries)}")
    return lines, oti_names, integer_names


def _declared_names(payload: str) -> set[str]:
    names: set[str] = set()
    for entry in split_top_level(payload):
        clean = entry.strip()
        if not clean:
            continue
        names.add(clean.split("(", 1)[0].split("=", 1)[0].strip().upper())
    return names


def _implicit_oti_names(
    body: list[str],
    lifted_names: set[str],
    declared_non_oti: set[str],
    parameter_names: set[str],
) -> set[str]:
    result: set[str] = set()
    for line in body:
        lhs_match = _LHS_ASSIGN_RE.match(line)
        if lhs_match:
            name = lhs_match.group(1).upper()
            if name not in declared_non_oti and name not in parameter_names and not _is_implicit_integer_name(name):
                result.add(name)
        for match in _TOKEN_RE.finditer(line):
            name = match.group(1).upper()
            if name in _KEYWORDS or name in _INTRINSIC_NAMES or name in _TYPED_INTRINSIC_MAP:
                continue
            if name in lifted_names or name in declared_non_oti or name in parameter_names:
                continue
            if _is_implicit_integer_name(name):
                continue
            result.add(name)
    return result


def _rewrite_helper_executable_line(line: str, lifted_names: set[str], oti_names: set[str]) -> str:
    rewritten = _rewrite_lifted_call(line, lifted_names)
    rewritten = _wrap_condition_with_real_tokens(rewritten, oti_names)
    rewritten = _normalize_typed_intrinsics(rewritten, oti_names)
    return _normalize_numeric_literals(rewritten, oti_names)


def _rewrite_lifted_call(line: str, lifted_names: set[str]) -> str:
    match = re.match(r"^(\s*(?:\d+\s+)?CALL\s+)([A-Z_][A-Z0-9_]*)(\s*\(.*)$", line, re.IGNORECASE)
    if not match:
        return line
    callee = match.group(2).upper()
    if callee not in lifted_names:
        return line
    return f"{match.group(1)}{callee}_OTI{match.group(3)}"


def _wrap_condition_with_real_tokens(line: str, oti_names: set[str]) -> str:
    if not oti_names:
        return line
    match = _IF_RE.match(line)
    if not match:
        return line
    condition_start = match.end()
    depth = 1
    condition_end = -1
    for index in range(condition_start, len(line)):
        char = line[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                condition_end = index
                break
    if condition_end < 0:
        return line
    condition = line[condition_start:condition_end]
    wrapped = _real_wrapped_tokens(condition, oti_names)
    return f"{match.group(1)}({wrapped}){line[condition_end + 1:]}"


def _real_wrapped_tokens(condition: str, oti_names: set[str]) -> str:
    if not oti_names:
        return condition
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(name) for name in sorted(oti_names, key=len, reverse=True)) + r")\b(?:\([^()]*\))?",
        re.IGNORECASE,
    )

    def replacement(match: re.Match[str]) -> str:
        token = match.group(0)
        before = condition[: match.start()].upper()
        if before.endswith("REAL("):
            return token
        return f"REAL({token})"

    return pattern.sub(replacement, condition)


def _normalize_typed_intrinsics(line: str, oti_names: set[str]) -> str:
    if not _contains_oti_name(line, oti_names):
        return line
    return _TYPED_INTRINSIC_RE.sub(lambda match: _TYPED_INTRINSIC_MAP[match.group(1).upper()], line)


def _normalize_numeric_literals(line: str, oti_names: set[str]) -> str:
    if not _contains_oti_name(line, oti_names):
        return line
    if re.match(r"^\s*STOP\b", line, re.IGNORECASE):
        return line
    normalized = re.sub(
        r"(?<!\w)(\d+\.\d*|\.\d+|\d+)[eE]([+-]?\d+)",
        lambda match: f"{match.group(1)}D{match.group(2)}",
        line,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9_])((?:\d+\.\d*)|(?:\d+\.))(?![A-Za-z0-9_.dDeE])",
        lambda match: match.group(1).rstrip(".") + (".0" if match.group(1).endswith(".") else "") + "D0",
        normalized,
    )
    normalized = re.sub(r"(?<![A-Za-z0-9_.)])(\d+)(?![A-Za-z0-9_.])(?=\s*[*\/])", r"\1.0D0", normalized)
    normalized = re.sub(r"([*\/])\s*(\d+)(?![A-Za-z0-9_.])", r"\1\2.0D0", normalized)
    return _promote_bare_integers_for_oti(normalized)


def _contains_oti_name(line: str, oti_names: set[str]) -> bool:
    return any(re.search(rf"\b{re.escape(name)}\b", line, re.IGNORECASE) for name in oti_names)


def _normalize_real_literal(value: str) -> str:
    promoted = re.sub(r"(?<!\w)(\d+\.\d*|\.\d+|\d+)[eE]([+-]?\d+)", lambda match: f"{match.group(1)}D{match.group(2)}", value)
    return re.sub(r"(?<![A-Za-z0-9_])(\d+\.\d*|\.\d+)(?![A-Za-z0-9_.dDeE])", lambda match: match.group(1) + "D0", promoted)


def _data_to_assignments(payload: str) -> list[str]:
    groups: list[tuple[str, str]] = []
    current: list[str] = []
    names = ""
    in_values = False
    for char in payload:
        if char == "/":
            if not in_values:
                names = "".join(current).strip().rstrip(",")
                current = []
                in_values = True
            else:
                groups.append((names, "".join(current).strip()))
                current = []
                names = ""
                in_values = False
            continue
        current.append(char)
    assignments: list[str] = []
    for names_text, values_text in groups:
        name_entries = [entry.strip() for entry in split_top_level(names_text) if entry.strip()]
        value_entries: list[str] = []
        for entry in split_top_level(values_text):
            clean = entry.strip()
            repeat = re.match(r"^(\d+)\s*\*\s*(.+)$", clean)
            if repeat:
                value_entries.extend([repeat.group(2).strip()] * int(repeat.group(1)))
            else:
                value_entries.append(clean)
        if len(value_entries) == 1 and len(name_entries) > 1:
            value_entries = value_entries * len(name_entries)
        if len(value_entries) != len(name_entries):
            raise HelperLiftingError(f"Unsupported DATA statement shape: {payload!r}")
        for name, value in zip(name_entries, value_entries):
            assignments.append(f"{name} = {_normalize_real_literal(value)}")
    return assignments


def _is_implicit_integer_name(name: str) -> bool:
    return bool(name) and name[0].upper() in _IMPLICIT_INTEGER_FIRST_LETTERS


def _promote_bare_integers_for_oti(line: str) -> str:
    if not line.strip() or not any(char.isdigit() for char in line):
        return line
    if re.match(r"^\s*DO\b", line, re.IGNORECASE):
        return line
    out: list[str] = []
    paren_stack: list[bool] = []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "(":
            cursor = len(out) - 1
            while cursor >= 0 and out[cursor] == " ":
                cursor -= 1
            paren_stack.append(cursor >= 0 and bool(re.match(r"[A-Za-z0-9_]", out[cursor])))
            out.append(char)
            index += 1
            continue
        if char == ")":
            if paren_stack:
                paren_stack.pop()
            out.append(char)
            index += 1
            continue
        if char.isdigit():
            previous = line[index - 1] if index > 0 else ""
            if previous.isalnum() or previous in {"_", "."}:
                out.append(char)
                index += 1
                continue
            end = index
            while end < len(line) and line[end].isdigit():
                end += 1
            if end < len(line) and line[end] in ".eEdD":
                out.append(line[index:end])
                index = end
                continue
            literal = line[index:end]
            left = index - 1
            while left >= 0 and line[left] == " ":
                left -= 1
            right = end
            while right < len(line) and line[right] == " ":
                right += 1
            prev_char = line[left] if left >= 0 else ""
            next_char = line[right] if right < len(line) else ""
            if not any(paren_stack) and (prev_char in "+-*/" or next_char in "+-*/"):
                out.append(f"{literal}.0D0")
            else:
                out.append(literal)
            index = end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _routine_source_lines(source_lines: list[str], routine: ParsedSubroutine) -> list[str]:
    if not routine.lines:
        return []
    start = routine.lines[0].line_numbers[0]
    end = routine.lines[-1].line_numbers[-1]
    return source_lines[max(start - 1, 0) : min(end, len(source_lines))]


def _statement_text(raw: str, form: str) -> str:
    if form != "fixed":
        return raw.split("!", 1)[0].strip()
    return _split_label_and_statement(raw, form)[1]


def _split_label_and_statement(raw: str, form: str) -> tuple[str, str]:
    if form != "fixed":
        return "", raw.split("!", 1)[0].strip()
    clean = _strip_fixed_form_comment(raw)
    if not clean:
        return "", ""
    expanded = _expand_fixed_form_tabs(clean)
    label_field = expanded[:5].strip() if len(expanded) >= 5 else ""
    statement = expanded[6:] if len(expanded) > 6 else ""
    label_prefix = f"{label_field} " if label_field else ""
    return label_prefix, statement.strip()


def _strip_fixed_form_comment(line: str) -> str:
    if not line:
        return ""
    if line[0] in {"C", "c", "*", "!"}:
        return ""
    if "!" in line:
        return line.split("!", 1)[0]
    return line


def _expand_fixed_form_tabs(raw: str) -> str:
    if not raw or raw[0] != "\t":
        return raw
    if len(raw) >= 2 and raw[1].isdigit() and raw[1] != "0":
        return "     " + raw[1:]
    return "      " + raw[1:]


def _continuation_stitch(lines: list[str], form: str) -> list[str]:
    if form != "fixed":
        return [line for line in lines if _statement_text(line, form)]
    merged: list[str] = []
    for raw in lines:
        raw = _expand_fixed_form_tabs(raw)
        clean = _strip_fixed_form_comment(raw)
        if not clean.strip():
            continue
        if merged and len(raw) >= 6 and raw[5] not in {" ", "0"} and raw[0] not in {"C", "c", "*", "!"}:
            merged[-1] = merged[-1].rstrip() + " " + clean[6:].strip()
            continue
        merged.append(clean)
    return merged