from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from umat_oti.core.diagnostics import unsupported_report
from umat_oti.fortran.interface_detection import umat_like_routines
from umat_oti.fortran.normalize import detect_source_form
from umat_oti.fortran.parser import logical_lines_from_text, parse_subroutines, split_top_level
from umat_oti.fortran.regions import _is_executable_line, detect_candidate_regions
from umat_oti.fortran.variables import ASSIGNMENT_RE, TOKEN_RE, collect_variables
from umat_oti.core.model import CallSite, FortranLogicalLine, ParsedFortranSource, UnsupportedFeature
from umat_oti.core.diagnostics import scan_unsupported_features


ABAQUS_UTILITY_CALLS = {"SPRINC", "SINV", "ROTSIG", "GETVRM"}
IO_PATTERNS = ("READ", "WRITE", "OPEN")
PLASTICITY_PATTERNS = {
    "yield": r"\b(YIELD|SYIEL\w*|SIGY\w*|MISES|FBAR|ZY)\b",
    "plastic_strain": r"\b(PLAST\w*|EPLAS\w*|EQPLAS\w*|DPSTRAN\w*|DPSTRN\w*)\b",
    "hardening": r"\b(HARD\w*|EHARD\w*|HMOD|HRDRATE|SIGSAT|KUHARD\w*)\b",
    "return_mapping": r"\b(RETURN\s+MAP\w*|CONSIST\w*|CORRECTOR|TRIAL|RADIAL)\b",
    "gamma": r"\b(GAMMA\w*|GAM\w*|GAM_PAR)\b",
    "lambda": r"\b(LAMBDA\w*|DLAM\w*)\b",
    "flow": r"\b(FLOW\w*|XNV\w*|XNDIR\w*)\b",
    "residual": r"\b(RES\w*|FGAM|FJAC)\b",
    "newton_iteration": r"\b(NEWTON|KNEWT\w*|ITER\w*|MAXITER)\b",
}
CONVERGENCE_PATTERN = re.compile(r"\b(ABS|DABS|NORM|TOLER|TOL|RES\w*|FGAM|FJAC|ITER\w*|MAXITER|CONVERG\w*)\b", flags=re.IGNORECASE)


def analyze_fortran_source(path: Path) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
        warnings.append("Source was decoded with replacement characters because UTF-8 decoding failed.")
    try:
        form = detect_source_form(path, text)
        logical_lines = logical_lines_from_text(text, form)
        subroutines = parse_subroutines(logical_lines)
        parsed = ParsedFortranSource(path, form, text, logical_lines, subroutines)
    except Exception as exc:
        warnings.append(f"Fortran parser warning: {exc}")
        form = detect_source_form(path, text)
        logical_lines = tuple(
            FortranLogicalLine(line.strip(), (number,))
            for number, line in enumerate(text.splitlines(), start=1)
            if line.strip()
        )
        parsed = ParsedFortranSource(path, form, text, logical_lines, ())
    call_sites = _collect_calls(parsed)
    unsupported = scan_unsupported_features(parsed.logical_lines, call_sites)
    external_calls = _external_calls(parsed, call_sites)
    detected_regions = detect_candidate_regions(parsed)
    if not parsed.subroutines:
        warnings.append("No subroutines were detected. The source may be malformed or use unsupported syntax.")
    if not any(routine.upper_name == "UMAT" for routine in parsed.subroutines):
        warnings.append("No SUBROUTINE UMAT entry point was detected.")
    return {
        "assignments_to_ddsdde": _assignments_to("DDSDDE", parsed.logical_lines),
        "assignments_to_statev": _assignments_to("STATEV", parsed.logical_lines),
        "branch_conditions": _branch_conditions(parsed.logical_lines),
        "assignments_to_stress": _assignments_to("STRESS", parsed.logical_lines),
        "call_targets": sorted({call.callee.upper() for call in call_sites}),
        "calls": [_call_to_json(call) for call in call_sites],
        "detected_functions": _collect_functions(parsed.logical_lines),
        "detected_subroutines": _subroutines_to_json(parsed),
        "detected_umat_routines": umat_like_routines(parsed),
        "detected_variables": [record.to_json() for record in collect_variables(parsed).values()],
        "file_io": _scan_file_io(parsed.logical_lines),
        "finite_strain": _finite_strain_analysis(parsed.logical_lines, detected_regions["regions"]),
        "form": parsed.form,
        "has_subroutine_umat": any(routine.upper_name == "UMAT" for routine in parsed.subroutines),
        "markers": source_markers(parsed),
        "possible_external_or_unsupported_calls": external_calls,
        "plasticity_indicators": _plasticity_indicators(parsed.logical_lines),
        "routine_effects": _routine_effects(parsed),
        "statev_accesses": _statev_accesses(parsed.logical_lines),
        "stress_path_helpers": _stress_path_helpers(call_sites, detected_regions["regions"]),
        "detected_regions": detected_regions["regions"],
        "region_summary": detected_regions["summary"],
        "unsupported_features": unsupported_report(unsupported)["features"],
        "uses": {
            "DFGRD0": _uses_of("DFGRD0", parsed.logical_lines),
            "DFGRD1": _uses_of("DFGRD1", parsed.logical_lines),
            "DPRED": _uses_of("DPRED", parsed.logical_lines),
            "DSTRAN": _uses_of("DSTRAN", parsed.logical_lines),
            "DTEMP": _uses_of("DTEMP", parsed.logical_lines),
            "DTIME": _uses_of("DTIME", parsed.logical_lines),
            "PREDEF": _uses_of("PREDEF", parsed.logical_lines),
            "PROPS": _uses_of("PROPS", parsed.logical_lines),
            "STRAN": _uses_of("STRAN", parsed.logical_lines),
            "TEMP": _uses_of("TEMP", parsed.logical_lines),
            "TIME": _uses_of("TIME", parsed.logical_lines),
        },
        "warnings": warnings,
    }


def source_markers(parsed: ParsedFortranSource) -> list[dict[str, object]]:
    markers: list[dict[str, object]] = []
    for routine in parsed.subroutines:
        if routine.upper_name == "UMAT":
            markers.append(
                {
                    "kind": "UMAT subroutine region",
                    "line_numbers": [routine.lines[0].line_numbers[0], routine.lines[-1].line_numbers[-1]],
                    "text": routine.name,
                }
            )
    for line in parsed.logical_lines:
        text = line.text
        for variable, kind in (
            ("STRESS", "assignment to STRESS"),
            ("DDSDDE", "assignment to DDSDDE"),
        ):
            if _is_assignment_to(variable, text):
                markers.append({"kind": kind, "line_numbers": list(line.line_numbers), "text": text})
        if _is_executable_line(text) and re.search(r"\bDSTRAN\b", text, flags=re.IGNORECASE):
            markers.append({"kind": "use of DSTRAN", "line_numbers": list(line.line_numbers), "text": text})
        if re.search(r"\bCALL\s+\w+\s*\(", text, flags=re.IGNORECASE):
            markers.append({"kind": "CALL statement", "line_numbers": list(line.line_numbers), "text": text})
    return markers


def _collect_calls(parsed: ParsedFortranSource) -> tuple[CallSite, ...]:
    calls: list[CallSite] = []
    routine_by_line: dict[int, str] = {}
    for routine in parsed.subroutines:
        for line in routine.lines:
            for number in line.line_numbers:
                routine_by_line[number] = routine.name
    for line in parsed.logical_lines:
        for match in re.finditer(r"\bcall\s+(\w+)\s*\((.*)\)", line.text, flags=re.IGNORECASE):
            caller = routine_by_line.get(line.line_numbers[0], "<file>")
            arguments = tuple(argument.strip() for argument in split_top_level(match.group(2)) if argument.strip())
            calls.append(CallSite(caller, match.group(1).upper(), line.line_numbers, arguments))
    return tuple(sorted(calls, key=lambda call: (call.caller.upper(), call.callee.upper(), call.line_numbers)))


def _collect_functions(logical_lines: tuple[FortranLogicalLine, ...]) -> list[dict[str, object]]:
    functions: list[dict[str, object]] = []
    for line in logical_lines:
        match = re.match(r"^\s*(?:\w+(?:\([^)]*\))?\s+)?function\s+(\w+)\s*\(", line.text, flags=re.IGNORECASE)
        if match:
            functions.append({"line_numbers": list(line.line_numbers), "name": match.group(1).upper()})
    return functions


def _subroutines_to_json(parsed: ParsedFortranSource) -> list[dict[str, object]]:
    return [
        {
            "arguments": [arg.upper() for arg in routine.args],
            "line_numbers": [routine.lines[0].line_numbers[0], routine.lines[-1].line_numbers[-1]],
            "name": routine.name.upper(),
        }
        for routine in parsed.subroutines
        if routine.lines
    ]


def _routine_effects(parsed: ParsedFortranSource) -> list[dict[str, object]]:
    effects: list[dict[str, object]] = []
    for routine in parsed.subroutines:
        writes_stress = any(_is_assignment_to("STRESS", line.text) for line in routine.lines)
        writes_statev = any(_is_assignment_to("STATEV", line.text) for line in routine.lines)
        writes_ddsdde = any(_is_assignment_to("DDSDDE", line.text) for line in routine.lines)
        has_branch_control = any(re.search(r"^\s*(?:IF\s*\(|ELSE\s+IF\s*\(|GOTO\b|GO\s+TO\b)", line.text, flags=re.IGNORECASE) for line in routine.lines)
        has_file_io = any(re.search(r"^\s*(?:READ|WRITE|OPEN)\b", line.text, flags=re.IGNORECASE) for line in routine.lines)
        effects.append(
            {
                "arguments": [arg.upper() for arg in routine.args],
                "has_branch_control": has_branch_control,
                "has_file_io": has_file_io,
                "name": routine.name.upper(),
                "writes_ddsdde": writes_ddsdde,
                "writes_statev": writes_statev,
                "writes_stress": writes_stress,
            }
        )
    return effects


def _assignments_to(variable: str, logical_lines: tuple[FortranLogicalLine, ...]) -> list[dict[str, object]]:
    return [
        {"line_numbers": list(line.line_numbers), "text": line.text}
        for line in logical_lines
        if _is_assignment_to(variable, line.text)
    ]


def _uses_of(variable: str, logical_lines: tuple[FortranLogicalLine, ...]) -> list[dict[str, object]]:
    pattern = re.compile(rf"\b{re.escape(variable)}\b", flags=re.IGNORECASE)
    return [
        {"line_numbers": list(line.line_numbers), "text": line.text}
        for line in logical_lines
        if _is_executable_line(line.text) and pattern.search(line.text)
    ]


def _scan_file_io(logical_lines: tuple[FortranLogicalLine, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in logical_lines:
        for word in IO_PATTERNS:
            if re.search(rf"^\s*{word}\b", line.text, flags=re.IGNORECASE):
                rows.append({"kind": word, "line_numbers": list(line.line_numbers), "text": line.text})
    return rows


def _external_calls(parsed: ParsedFortranSource, calls: tuple[CallSite, ...]) -> list[dict[str, object]]:
    defined = {routine.upper_name for routine in parsed.subroutines} | {function["name"] for function in _collect_functions(parsed.logical_lines)}
    rows: list[dict[str, object]] = []
    for call in calls:
        if call.callee in ABAQUS_UTILITY_CALLS:
            rows.append(
                {
                    "call": call.callee,
                    "classification": "common Abaqus utility",
                    "line_numbers": list(call.line_numbers),
                }
            )
        elif call.callee not in defined:
            rows.append(
                {
                    "call": call.callee,
                    "classification": "external or unsupported",
                    "line_numbers": list(call.line_numbers),
                }
            )
    return rows


def _call_to_json(call: CallSite) -> dict[str, object]:
    return {"arguments": list(call.arguments), "callee": call.callee, "caller": call.caller, "line_numbers": list(call.line_numbers)}


def _plasticity_indicators(logical_lines: tuple[FortranLogicalLine, ...]) -> dict[str, object]:
    by_category: dict[str, list[dict[str, object]]] = {key: [] for key in PLASTICITY_PATTERNS}
    variables: set[str] = set()
    for line in logical_lines:
        for category, pattern in PLASTICITY_PATTERNS.items():
            if not re.search(pattern, line.text, flags=re.IGNORECASE):
                continue
            tokens = _tokens(line.text)
            variables.update(tokens)
            by_category[category].append(
                {
                    "executable": _is_executable_line(line.text),
                    "line_numbers": list(line.line_numbers),
                    "text": line.text,
                    "tokens": sorted(tokens),
                }
            )
    categories_present = sorted(category for category, rows in by_category.items() if rows)
    decisive_categories = sorted(
        category
        for category in categories_present
        if category in {"yield", "plastic_strain", "hardening", "return_mapping", "gamma", "lambda"}
    )
    return {
        "by_category": by_category,
        "categories_present": categories_present,
        "decisive_categories_present": decisive_categories,
        "is_plasticity_candidate": bool(decisive_categories),
        "variables": sorted(variables),
    }


def _finite_strain_analysis(logical_lines: tuple[FortranLogicalLine, ...], regions: list[dict[str, object]]) -> dict[str, object]:
    dfgrd0_uses = _uses_of("DFGRD0", logical_lines)
    dfgrd1_uses = _uses_of("DFGRD1", logical_lines)
    executable_uses = dfgrd0_uses + dfgrd1_uses
    stress_regions = [_normalized_region(row) for row in regions if str(row.get("region type", "")) == "stress"]
    dfgrd_driven = bool(executable_uses and stress_regions)
    return {
        "dfgrd0_executable_uses": dfgrd0_uses,
        "dfgrd1_executable_uses": dfgrd1_uses,
        "dfgrd_driven_stress_update": dfgrd_driven,
        "executable_dfgrd_use": bool(executable_uses),
    }


def _statev_accesses(logical_lines: tuple[FortranLogicalLine, ...]) -> dict[str, object]:
    reads: list[dict[str, object]] = []
    writes: list[dict[str, object]] = []
    for line in logical_lines:
        if not _is_executable_line(line.text) or not re.search(r"\bSTATEV\b", line.text, flags=re.IGNORECASE):
            continue
        row = {"line_numbers": list(line.line_numbers), "text": line.text}
        assignment = ASSIGNMENT_RE.match(line.text)
        if assignment and assignment.group("lhs").upper() == "STATEV":
            writes.append(row)
            if re.search(r"\bSTATEV\b", assignment.group("rhs"), flags=re.IGNORECASE):
                reads.append(row)
        else:
            reads.append(row)
    return {"reads": reads, "writes": writes, "read_count": len(reads), "write_count": len(writes)}


def _branch_conditions(logical_lines: tuple[FortranLogicalLine, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in logical_lines:
        match = re.match(r"^\s*(?:ELSE\s*)?IF\s*\((.*)\)\s*(?:THEN)?\b", line.text, flags=re.IGNORECASE)
        if not match:
            continue
        condition = match.group(1)
        tokens = _tokens(condition)
        rows.append(
            {
                "condition": condition,
                "is_convergence_check": bool(CONVERGENCE_PATTERN.search(condition)),
                "line_numbers": list(line.line_numbers),
                "text": line.text,
                "tokens": sorted(tokens),
            }
        )
    return rows


def _stress_path_helpers(call_sites: tuple[CallSite, ...], regions: list[dict[str, object]]) -> list[dict[str, object]]:
    stress_regions = [_normalized_region(row) for row in regions if str(row.get("region type", "")) == "stress"]
    if not stress_regions:
        return []
    first_stress = min(region["start_line"] for region in stress_regions)
    last_stress = max(region["end_line"] for region in stress_regions)
    rows: list[dict[str, object]] = []
    for call in call_sites:
        in_interval = any(first_stress <= number <= last_stress for number in call.line_numbers)
        in_region = _line_numbers_intersect(call.line_numbers, stress_regions)
        if not in_interval and not in_region:
            continue
        rows.append(
            {
                "arguments": list(call.arguments),
                "callee": call.callee,
                "caller": call.caller,
                "line_numbers": list(call.line_numbers),
                "reason": "call inside detected stress-update interval" if in_interval else "call inside detected stress region",
            }
        )
    return rows


def _normalized_region(row: dict[str, object]) -> dict[str, int]:
    return {
        "start_line": _as_int(row.get("start_line") or row.get("start line")),
        "end_line": _as_int(row.get("end_line") or row.get("end line")),
    }


def _line_numbers_intersect(line_numbers: object, regions: list[dict[str, int]]) -> bool:
    try:
        numbers = [int(value) for value in line_numbers]  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return any(region["start_line"] <= number <= region["end_line"] for number in numbers for region in regions)


def _tokens(text: str) -> set[str]:
    ignored = {"IF", "THEN", "ELSE", "AND", "OR", "NOT", "ABS", "DABS", "SQRT", "DSQRT", "LT", "LE", "GT", "GE", "EQ", "NE"}
    return {token.upper() for token in TOKEN_RE.findall(text) if token.upper() not in ignored}


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _is_assignment_to(variable: str, text: str) -> bool:
    assignment = ASSIGNMENT_RE.match(text)
    return bool(assignment and assignment.group("lhs").upper() == variable.upper())
