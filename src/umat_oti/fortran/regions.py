from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from umat_oti.core.model import FortranLogicalLine, ParsedFortranSource
from umat_oti.fortran.parser import split_top_level
from umat_oti.fortran.variables import ASSIGNMENT_RE, FORTRAN_KEYWORDS, TOKEN_RE, collect_variables


STRESS_REGION_CLASSIFICATIONS = (
    "Main stress update, transform with OTIS",
    "Stress initialization, transform if needed",
    "Stress post-processing",
    "Ignore",
    "Unknown",
)
TANGENT_REGION_CLASSIFICATIONS = (
    "Tangent-only, replace with OTIS extraction",
    "Tangent helper only, skip if not used by stress",
    "Needed by stress update, keep/promote",
    "Validation only, preserve outside transformed path",
    "Unknown",
)
SHARED_REGION_CLASSIFICATIONS = (
    "Shared setup, keep real before transformed path",
    "Material constants/setup, keep real",
    "Needed by both stress update and old tangent",
    "Ignore",
    "Unknown",
)
TANGENT_HELPER_NAMES = {"DDS", "TANGENT", "JAC", "JACOBIAN", "AMAT", "C", "CELAS", "DDSDDE"}
INTRINSIC_TOKEN_NAMES = {
    "ABS",
    "ACOS",
    "ASIN",
    "ATAN",
    "ATAN2",
    "COS",
    "COSH",
    "DABS",
    "DACOS",
    "DASIN",
    "DATAN",
    "DATAN2",
    "DCOS",
    "DCOSH",
    "DEXP",
    "DLOG",
    "DLOG10",
    "DMAX1",
    "DMIN1",
    "DMOD",
    "DSIGN",
    "DSIN",
    "DSINH",
    "DSQRT",
    "DTAN",
    "DTANH",
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
TRANSFORMABLE_HELPER_EFFECTS = {
    "DOTPROD": {"output": 2, "inputs": (0, 1)},
    "DYADICPROD": {"output": 2, "inputs": (0, 1)},
    "KDEVIA": {"output": 2, "inputs": (0, 1)},
    "KEFFP": {"output": 1, "inputs": (0,)},
    "KMAVEC": {"output": 4, "inputs": (0, 3)},
    "KMMULT": {"output": 6, "inputs": (0, 3)},
    "KMLT1": {"output": 2, "inputs": (0, 1)},
    "KMTRAN": {"output": 3, "inputs": (0,)},
    "KSMULT": {"output": 0, "inputs": (0, 3)},
    "KSPECTRAL": {"output": (0, 1, 2, 3, 4), "inputs": (5, 9)},
    "KINVER": {"output": 1, "inputs": (0,)},
    "KMLT": {"output": 2, "inputs": (0, 1)},
    "KTRACE": {"output": 1, "inputs": (0,)},
    "KTRANS": {"output": 1, "inputs": (0,)},
    "KUPDVEC": {"output": 0, "inputs": (0, 2)},
}
STANDARD_CONSTANT_INPUTS = {
    "PROPS",
    "STRAN",
    "TIME",
    "DTIME",
    "TEMP",
    "DTEMP",
    "PREDEF",
    "DPRED",
    "COORDS",
    "DROT",
}
STANDARD_BOOKKEEPING_NAMES = {
    "CMNAME",
    "NDI",
    "NSHR",
    "NTENS",
    "NSTATV",
    "NPROPS",
    "NOEL",
    "NPT",
    "LAYER",
    "KSPT",
    "KSTEP",
    "KINC",
    "SSE",
    "SPD",
    "SCD",
    "RPL",
    "DDSDDT",
    "DRPLDE",
    "DRPLDT",
    "CELENT",
    "PNEWDT",
    "DFGRD0",
    "DFGRD1",
}


@dataclass
class AssignmentInfo:
    lhs: str
    rhs_tokens: set[str]
    line_index: int
    line_numbers: tuple[int, ...]
    text: str


@dataclass
class RegionSignal:
    region_type: str
    start_line: int
    end_line: int
    reason: str
    suggested_classification: str
    variables: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class DependencySummary:
    stress_path_variables: set[str]
    tangent_only_variables: set[str]
    shared_setup_variables: set[str]
    constant_variables: set[str]
    ignored_or_unused_variables: set[str]
    upstream_to_stress: set[str]
    upstream_to_ddsdde: set[str]
    downstream_from_dstran: set[str]
    ddsdde_stress_input_lines: set[int]
    parameter_variables: set[str]
    statev_path_variables: set[str]


def detect_candidate_regions(parsed: ParsedFortranSource) -> dict[str, Any]:
    source_lines = parsed.text.splitlines()
    assignments = _assignments(parsed.logical_lines) + _call_effect_assignments(parsed.logical_lines)
    block_ranges = _block_ranges(parsed.logical_lines)
    dependency_summary = _dependency_summary(parsed, assignments)
    signals = _assignment_signals(assignments, dependency_summary, block_ranges)
    regions = _signals_to_regions(signals, source_lines)
    summary = _region_summary(regions, parsed, dependency_summary)
    return {"regions": regions, "summary": summary}


def _assignments(logical_lines: tuple[FortranLogicalLine, ...]) -> list[AssignmentInfo]:
    result: list[AssignmentInfo] = []
    for line_index, line in enumerate(logical_lines):
        if not _is_executable_line(line.text):
            continue
        match = ASSIGNMENT_RE.match(line.text)
        if not match:
            continue
        result.append(
            AssignmentInfo(
                lhs=match.group("lhs").upper(),
                rhs_tokens=_tokens(match.group("rhs")),
                line_index=line_index,
                line_numbers=line.line_numbers,
                text=line.text,
            )
        )
    return result


def _call_effect_assignments(logical_lines: tuple[FortranLogicalLine, ...]) -> list[AssignmentInfo]:
    result: list[AssignmentInfo] = []
    for line_index, line in enumerate(logical_lines):
        if not _is_executable_line(line.text):
            continue
        match = re.match(r"^\s*CALL\s+(\w+)\s*\((.*)\)\s*$", line.text, flags=re.IGNORECASE)
        if not match:
            continue
        callee = match.group(1).upper()
        effect = TRANSFORMABLE_HELPER_EFFECTS.get(callee)
        if not effect:
            continue
        arguments = [argument.strip() for argument in split_top_level(match.group(2))]
        rhs_tokens = {_base_name(arguments[index]) for index in effect["inputs"] if int(index) < len(arguments)}
        rhs_tokens.discard("")
        output_indexes = effect["output"]
        if isinstance(output_indexes, int):
            output_indexes = (output_indexes,)
        for output_index in output_indexes:
            output_index = int(output_index)
            if output_index >= len(arguments):
                continue
            lhs = _base_name(arguments[output_index])
            if not lhs:
                continue
            result.append(
                AssignmentInfo(
                    lhs=lhs,
                    rhs_tokens=rhs_tokens,
                    line_index=line_index,
                    line_numbers=line.line_numbers,
                    text=line.text,
                )
            )
    return result


def _dependency_summary(parsed: ParsedFortranSource, assignments: list[AssignmentInfo]) -> DependencySummary:
    upstream_to_stress = _upstream_dependencies_for("STRESS", assignments, stop_lhs={"DDSDDE"})
    upstream_to_ddsdde = _upstream_dependencies_for("DDSDDE", assignments)
    downstream_from_dstran = _downstream_dependencies_from("DSTRAN", assignments)
    statev_path_variables = _statev_path_variables(assignments)
    parameter_variables = _parameter_variables(parsed.logical_lines)
    assigned = {assignment.lhs for assignment in assignments}
    written = _written_variables(assignments)
    variables = collect_variables(parsed)
    constant_variables = _constant_variables(
        assignments,
        parameter_variables,
        written,
        {name for name, record in variables.items() if "read" not in record.access and "write" not in record.access},
    )
    finite_strain_path_variables = _finite_strain_path_variables(upstream_to_stress, constant_variables)
    stress_path_variables = (downstream_from_dstran & (upstream_to_stress | {"STRESS"})) | finite_strain_path_variables | {"DSTRAN", "STRESS"}
    stress_path_variables |= {
        name
        for name in statev_path_variables
        if name not in constant_variables and name not in STANDARD_BOOKKEEPING_NAMES and not _is_loop_counter(name)
    }
    stress_path_variables |= {
        name
        for name in _branch_condition_variables(parsed.logical_lines)
        if name not in constant_variables and name not in STANDARD_BOOKKEEPING_NAMES and not _is_loop_counter(name)
    }
    ddsdde_stress_input_lines = {
        assignment.line_numbers[0]
        for assignment in assignments
        if assignment.lhs in stress_path_variables and "DDSDDE" in assignment.rhs_tokens
    }
    upstream_to_stress_without_target = upstream_to_stress - {"STRESS"}
    upstream_to_ddsdde_without_target = upstream_to_ddsdde - {"DDSDDE"}
    shared_setup_variables = (
        (upstream_to_stress_without_target & upstream_to_ddsdde_without_target)
        | (constant_variables & (upstream_to_stress_without_target | upstream_to_ddsdde_without_target))
    ) - stress_path_variables
    tangent_only_variables = {
        name
        for name in upstream_to_ddsdde_without_target - upstream_to_stress_without_target
        if name not in stress_path_variables
        and name not in constant_variables
        and name not in statev_path_variables
        and not _is_loop_counter(name)
        and name in assigned | TANGENT_HELPER_NAMES
    }
    shared_setup_variables -= tangent_only_variables
    ignored_or_unused_variables = _ignored_or_unused_variables(variables, written, stress_path_variables, tangent_only_variables, constant_variables)
    return DependencySummary(
        stress_path_variables=stress_path_variables,
        tangent_only_variables=tangent_only_variables,
        shared_setup_variables=shared_setup_variables,
        constant_variables=constant_variables,
        ignored_or_unused_variables=ignored_or_unused_variables,
        upstream_to_stress=upstream_to_stress,
        upstream_to_ddsdde=upstream_to_ddsdde,
        downstream_from_dstran=downstream_from_dstran,
        ddsdde_stress_input_lines=ddsdde_stress_input_lines,
        parameter_variables=parameter_variables,
        statev_path_variables=statev_path_variables,
    )


def _finite_strain_path_variables(upstream_to_stress: set[str], constant_variables: set[str]) -> set[str]:
    if not ({"DFGRD0", "DFGRD1"} & upstream_to_stress):
        return set()
    keep_real_bookkeeping = STANDARD_BOOKKEEPING_NAMES - {"DFGRD0", "DFGRD1"}
    return {
        name
        for name in upstream_to_stress
        if name not in constant_variables
        and name not in keep_real_bookkeeping
        and not _is_loop_counter(name)
    }


def _upstream_dependencies_for(target: str, assignments: list[AssignmentInfo], stop_lhs: set[str] | None = None) -> set[str]:
    stop_lhs = stop_lhs or set()
    dependencies = {target.upper()}
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            if assignment.lhs in stop_lhs:
                continue
            if assignment.lhs not in dependencies:
                continue
            for token in assignment.rhs_tokens:
                if token not in dependencies and token != assignment.lhs:
                    dependencies.add(token)
                    changed = True
    return dependencies


def _downstream_dependencies_from(seed: str, assignments: list[AssignmentInfo]) -> set[str]:
    downstream = {seed.upper()}
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            if assignment.lhs in downstream:
                continue
            if assignment.rhs_tokens & downstream:
                downstream.add(assignment.lhs)
                changed = True
    return downstream


def _constant_variables(
    assignments: list[AssignmentInfo],
    parameter_variables: set[str],
    written_variables: set[str],
    declaration_only_variables: set[str],
) -> set[str]:
    constants = set(parameter_variables) | set(STANDARD_CONSTANT_INPUTS)
    constants.update(declaration_only_variables & STANDARD_CONSTANT_INPUTS)
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            if assignment.lhs in {"STRESS", "STATEV", "DDSDDE"}:
                continue
            if assignment.lhs in constants:
                continue
            if assignment.lhs in assignment.rhs_tokens and assignment.lhs not in constants:
                continue
            rhs_tokens = assignment.rhs_tokens - {assignment.lhs}
            if rhs_tokens <= constants:
                constants.add(assignment.lhs)
                changed = True
    constants.update(name for name in STANDARD_CONSTANT_INPUTS if name not in written_variables)
    return constants


def _assignment_signals(
    assignments: list[AssignmentInfo],
    dependency_summary: DependencySummary,
    block_ranges: list[tuple[int, int]],
) -> list[RegionSignal]:
    signals: list[RegionSignal] = []
    for assignment in assignments:
        region_type, classification, reason = _assignment_region(assignment, dependency_summary)
        if not region_type:
            continue
        start_line, end_line = _expanded_range(assignment.line_numbers, block_ranges)
        signals.append(
            RegionSignal(
                region_type,
                start_line,
                end_line,
                reason,
                classification,
                {assignment.lhs} | assignment.rhs_tokens,
            )
        )
    return signals


def _assignment_region(assignment: AssignmentInfo, dependency_summary: DependencySummary) -> tuple[str, str, str]:
    lhs = assignment.lhs
    if lhs == "DDSDDE":
        first_stress_use = min(dependency_summary.ddsdde_stress_input_lines, default=0)
        if first_stress_use and assignment.line_numbers[-1] < first_stress_use:
            return (
                "shared_setup",
                "Needed by both stress update and old tangent",
                "DDSDDE setup is consumed by a stress-path helper before final tangent replacement",
            )
        return (
            "tangent",
            "Tangent-only, replace with OTIS extraction",
            "assignment to DDSDDE old tangent/Jacobian output",
        )
    if lhs in dependency_summary.stress_path_variables and lhs != "DSTRAN":
        return (
            "stress",
            "Main stress update, transform with OTIS",
            f"{lhs} is on the DSTRAN to STRESS propagation path",
        )
    if lhs in dependency_summary.tangent_only_variables:
        return (
            "tangent",
            "Tangent helper only, skip if not used by stress",
            f"{lhs} feeds only the old DDSDDE/tangent block",
        )
    if lhs in dependency_summary.shared_setup_variables:
        return (
            "shared_setup",
            "Shared setup, keep real before transformed path",
            f"{lhs} provides setup used before stress/tangent handling",
        )
    if lhs in dependency_summary.constant_variables and (
        lhs in dependency_summary.upstream_to_stress or lhs in dependency_summary.upstream_to_ddsdde
    ):
        return (
            "shared_setup",
            "Material constants/setup, keep real",
            f"{lhs} is deterministic material/setup data",
        )
    return "", "", ""


def _signals_to_regions(signals: list[RegionSignal], source_lines: list[str]) -> list[dict[str, object]]:
    merged: list[RegionSignal] = []
    for signal in sorted(signals, key=lambda item: (item.start_line, item.end_line, item.region_type)):
        if merged and _can_merge(merged[-1], signal, source_lines):
            previous = merged[-1]
            previous.end_line = max(previous.end_line, signal.end_line)
            previous.reason = _join_unique(previous.reason, signal.reason)
            if previous.suggested_classification == "Unknown" and signal.suggested_classification != "Unknown":
                previous.suggested_classification = signal.suggested_classification
            previous.variables.update(signal.variables)
        else:
            merged.append(
                RegionSignal(
                    signal.region_type,
                    signal.start_line,
                    signal.end_line,
                    signal.reason,
                    signal.suggested_classification,
                    set(signal.variables),
                )
            )
    regions: list[dict[str, object]] = []
    for index, signal in enumerate(sorted(merged, key=lambda item: (item.start_line, item.end_line, item.region_type)), start=1):
        region_type_label = signal.region_type.upper().replace("_", "-")
        regions.append(
            {
                "region id": f"{region_type_label}-{index:03d}",
                "region type": signal.region_type,
                "start line": signal.start_line,
                "end line": signal.end_line,
                "short code preview": _preview(source_lines, signal.start_line, signal.end_line),
                "detected reason": signal.reason,
                "suggested classification": signal.suggested_classification,
                "user-selected classification": signal.suggested_classification,
                "notes": "",
                "detected variables": sorted(signal.variables),
            }
        )
    return regions


def _region_summary(
    regions: list[dict[str, object]],
    parsed: ParsedFortranSource,
    dependency_summary: DependencySummary,
) -> dict[str, object]:
    old_ddsdde_block_detected = any(
        row.get("region type") == "tangent" and "DDSDDE" in str(row.get("detected reason", "")).upper()
        for row in regions
    ) or any(_is_assignment_to_ddsdde(line.text) for line in parsed.logical_lines if _is_executable_line(line.text))
    messages = []
    if old_ddsdde_block_detected:
        messages.append("old DDSDDE block detected")
        messages.append("old DDSDDE block will be replaced by OTIS derivative extraction in a later transformation stage")
    if dependency_summary.tangent_only_variables:
        messages.append(
            "variables used only by old tangent block can be kept real or removed from the transformed path: "
            + ", ".join(sorted(dependency_summary.tangent_only_variables))
        )
    return {
        "old_ddsdde_block_detected": old_ddsdde_block_detected,
        "old_ddsdde_block_will_be_replaced": old_ddsdde_block_detected,
        "tangent_only_variables": sorted(dependency_summary.tangent_only_variables),
        "stress_path_variables": sorted(dependency_summary.stress_path_variables),
        "shared_setup_variables": sorted(dependency_summary.shared_setup_variables),
        "constant_variables": sorted(dependency_summary.constant_variables),
        "ignored_or_unused_variables": sorted(dependency_summary.ignored_or_unused_variables),
        "upstream_to_stress": sorted(dependency_summary.upstream_to_stress),
        "upstream_to_ddsdde": sorted(dependency_summary.upstream_to_ddsdde),
        "downstream_from_dstran": sorted(dependency_summary.downstream_from_dstran),
        "parameter_variables": sorted(dependency_summary.parameter_variables),
        "statev_path_variables": sorted(dependency_summary.statev_path_variables),
        "report_messages": messages,
    }


def _block_ranges(logical_lines: tuple[FortranLogicalLine, ...]) -> list[tuple[int, int]]:
    stack: list[tuple[str, int]] = []
    ranges: list[tuple[int, int]] = []
    for line in logical_lines:
        text = line.text.strip()
        lower_text = text.lower()
        opener = _block_opener(lower_text)
        if opener:
            stack.append((opener, line.line_numbers[0]))
        closer = _block_closer(lower_text)
        if closer:
            for stack_index in range(len(stack) - 1, -1, -1):
                block_kind, start_line = stack[stack_index]
                if block_kind == closer:
                    ranges.append((start_line, line.line_numbers[-1]))
                    del stack[stack_index:]
                    break
    return ranges


def _block_opener(lower_text: str) -> str | None:
    if re.match(r"^do\b", lower_text):
        return "do"
    return None


def _block_closer(lower_text: str) -> str | None:
    if re.match(r"^end\s*do\b", lower_text):
        return "do"
    return None


def _expanded_range(line_numbers: tuple[int, ...], block_ranges: list[tuple[int, int]]) -> tuple[int, int]:
    start_line = line_numbers[0]
    end_line = line_numbers[-1]
    containing = [block_range for block_range in block_ranges if block_range[0] <= start_line and end_line <= block_range[1]]
    if not containing:
        return start_line, end_line
    expanded_start, expanded_end = min(containing, key=lambda item: item[1] - item[0])
    return expanded_start, expanded_end


def _statev_path_variables(assignments: list[AssignmentInfo]) -> set[str]:
    dependencies = {"STATEV"}
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            if assignment.lhs == "DDSDDE" or assignment.lhs not in dependencies:
                continue
            for token in assignment.rhs_tokens:
                if token not in dependencies and token != assignment.lhs and not _is_loop_counter(token):
                    dependencies.add(token)
                    changed = True
    return dependencies


def _branch_condition_variables(logical_lines: tuple[FortranLogicalLine, ...]) -> set[str]:
    variables: set[str] = set()
    for line in logical_lines:
        match = re.match(r"^\s*(?:ELSE\s*)?IF\s*\((.*)\)", line.text, flags=re.IGNORECASE)
        if match:
            variables.update(_tokens(match.group(1)))
    return variables


def _base_name(argument: str) -> str:
    match = re.match(r"\s*([A-Za-z_]\w*)", argument)
    return match.group(1).upper() if match else ""


def _can_merge(previous: RegionSignal, current: RegionSignal, source_lines: list[str]) -> bool:
    if previous.region_type != current.region_type:
        return False
    if current.start_line <= previous.end_line + 2:
        return True
    if previous.region_type == "shared_setup" and _gap_is_comments_or_blank(previous.end_line, current.start_line, source_lines):
        return True
    return False


def _gap_is_comments_or_blank(previous_end: int, current_start: int, source_lines: list[str]) -> bool:
    if current_start <= previous_end + 1:
        return True
    gap = source_lines[previous_end : current_start - 1]
    if len(gap) > 6:
        return False
    return all(_is_comment_or_blank(line) for line in gap)


def _join_unique(left: str, right: str) -> str:
    parts: list[str] = []
    for value in left.split("; ") + right.split("; "):
        if value and value not in parts:
            parts.append(value)
    return "; ".join(parts)


def _preview(source_lines: list[str], start_line: int, end_line: int) -> str:
    if not source_lines:
        return ""
    start_index = max(start_line - 1, 0)
    end_index = min(end_line, len(source_lines))
    selected = source_lines[start_index:end_index]
    if len(selected) > 8:
        selected = selected[:4] + ["..."] + selected[-3:]
    return "\n".join(line.rstrip() for line in selected)


def _tokens(text: str) -> set[str]:
    result: set[str] = set()
    for token in TOKEN_RE.findall(text):
        upper_token = token.upper()
        if upper_token in FORTRAN_KEYWORDS:
            continue
        if upper_token in INTRINSIC_TOKEN_NAMES:
            continue
        if upper_token in {"LT", "LE", "GT", "GE", "EQ", "NE"}:
            continue
        if re.match(r"^[DE]\d+$", upper_token):
            continue
        result.add(upper_token)
    return result


def _parameter_variables(logical_lines: tuple[FortranLogicalLine, ...]) -> set[str]:
    variables: set[str] = set()
    for line in logical_lines:
        if not re.match(r"^\s*parameter\b", line.text, flags=re.IGNORECASE):
            continue
        for match in re.finditer(r"\b([A-Za-z_]\w*)\s*=", line.text):
            variables.add(match.group(1).upper())
    return variables


def _written_variables(assignments: list[AssignmentInfo]) -> set[str]:
    return {assignment.lhs for assignment in assignments}


def _ignored_or_unused_variables(
    variables: dict[str, Any],
    written_variables: set[str],
    stress_path_variables: set[str],
    tangent_only_variables: set[str],
    constant_variables: set[str],
) -> set[str]:
    ignored: set[str] = set()
    for name, record in variables.items():
        if name in stress_path_variables or name in tangent_only_variables or name in constant_variables:
            continue
        if name in STANDARD_BOOKKEEPING_NAMES or _is_loop_counter(name):
            ignored.add(name)
            continue
        if record.is_argument and name not in written_variables and "read" not in record.access:
            ignored.add(name)
    return ignored


def _is_assignment_to_ddsdde(text: str) -> bool:
    assignment = ASSIGNMENT_RE.match(text)
    return bool(assignment and assignment.group("lhs").upper() == "DDSDDE")


def _is_loop_counter(name: str) -> bool:
    return name in {"I", "J", "K", "K1", "K2", "K3", "L", "M", "N", "II", "JJ", "KK", "ITER", "IT", "COUNT"} or bool(re.match(r"^[IJKLMN]\d+$", name))


def _is_executable_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    lower_text = stripped.lower()
    if re.match(r"^(subroutine|function|program|module|end\b|return\b|continue\b)", lower_text):
        return False
    if re.match(r"^(include|implicit|dimension|parameter|common|save|data|equivalence|external|intrinsic)\b", lower_text):
        return False
    if re.match(r"^(character|integer|logical|real|double\s+precision|double|complex)\b", lower_text):
        return False
    return True


def _is_comment_or_blank(raw_line: str) -> bool:
    stripped = raw_line.strip()
    if not stripped:
        return True
    if raw_line and raw_line[0] in {"c", "C", "*"}:
        return True
    return stripped.startswith("!")
