from __future__ import annotations

from typing import Any


FINDING_STATUSES = ("Open", "Reviewed", "Action needed", "Resolved", "Ignore")
FINDING_SEVERITIES = ("Info", "Warning", "Error")


def build_findings_log(
    analysis: dict[str, Any],
    source_metadata: dict[str, object] | None = None,
    routine_roles: list[dict[str, object]] | None = None,
    region_classifications: list[dict[str, object]] | None = None,
    variable_roles: list[dict[str, object]] | None = None,
    existing_rows: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    source_metadata = source_metadata or {}
    return _build_compact_findings_log(
        analysis,
        routine_roles or [],
        region_classifications or [],
        variable_roles or [],
        existing_rows or [],
    )
    _add(
        rows,
        severity="Info",
        category="source",
        source="upload",
        finding="Source file processed",
        details=f"{source_metadata.get('file_name', '')} ({source_metadata.get('file_size', '')} bytes)",
        action="Use this file as the current UMAT analysis source.",
    )
    _add(
        rows,
        severity="Info",
        category="source",
        source="scanner",
        finding="Fortran source form detected",
        details=str(analysis.get("form", "unknown")),
        action="Verify source form if parsing looks incorrect.",
    )
    _add(
        rows,
        severity="Info" if analysis.get("has_subroutine_umat") else "Warning",
        category="interface",
        source="scanner",
        finding="SUBROUTINE UMAT detection",
        details=str(analysis.get("has_subroutine_umat", False)),
        action="Select or map the main UMAT routine before transformation.",
    )
    for routine in analysis.get("detected_umat_routines", []):
        _add(
            rows,
            severity="Info",
            category="interface",
            line_numbers=routine.get("line_numbers", []),
            source="scanner",
            finding="UMAT-like routine candidate",
            details=f"{routine.get('name', '')}({', '.join(str(arg) for arg in routine.get('arguments', []))})",
            action="Confirm this is the main Abaqus UMAT entry point.",
        )
    for routine in analysis.get("detected_subroutines", []):
        _add(
            rows,
            severity="Info",
            category="routine",
            line_numbers=routine.get("line_numbers", []),
            source="scanner",
            finding="Subroutine detected",
            details=f"{routine.get('name', '')} with {len(routine.get('arguments', []))} arguments",
            action="Classify helper role if it participates in the UMAT path.",
        )
    for function in analysis.get("detected_functions", []):
        _add(
            rows,
            severity="Info",
            category="routine",
            line_numbers=function.get("line_numbers", []),
            source="scanner",
            finding="Function detected",
            details=str(function.get("name", "")),
            action="Review whether the function must be promoted, kept real, or treated as external.",
        )
    for variable in analysis.get("detected_variables", []):
        usage = ", ".join(str(item) for item in variable.get("detected_usage", []))
        _add(
            rows,
            severity="Info",
            category="variable",
            source="scanner",
            finding="Variable detected",
            details=(
                f"{variable.get('variable_name', '')}: type={variable.get('detected_type', 'unknown')}, "
                f"shape={variable.get('detected_shape', '')}, access={variable.get('read_write', 'unknown')}, usage={usage}"
            ),
            action="Confirm OTIS role in the variable role table.",
        )
    for name, assignments in (
        ("STRESS", analysis.get("assignments_to_stress", [])),
        ("STATEV", analysis.get("assignments_to_statev", [])),
        ("DDSDDE", analysis.get("assignments_to_ddsdde", [])),
    ):
        for assignment in assignments:
            _add(
                rows,
                severity="Info",
                category="assignment",
                line_numbers=assignment.get("line_numbers", []),
                source="scanner",
                finding=f"Assignment to {name}",
                details=str(assignment.get("text", "")),
                action=_assignment_action(name),
            )
    for region in analysis.get("detected_regions", []):
        _add(
            rows,
            severity="Warning" if region.get("region type") == "tangent" else "Info",
            category="region",
            line_numbers=[region.get("start line", ""), region.get("end line", "")],
            source="region detector",
            finding=f"{str(region.get('region type', '')).title()} code region detected",
            details=f"{region.get('region id', '')}: {region.get('detected reason', '')}",
            action=str(region.get("suggested classification", "Unknown")),
        )
    for message in analysis.get("region_summary", {}).get("report_messages", []):
        _add(
            rows,
            severity="Warning",
            category="replacement plan",
            source="region detector",
            finding="Old tangent replacement finding",
            details=str(message),
            action="Use tangent-region classifications to skip old DDSDDE code and insert OTIS extraction later.",
        )
    for call in analysis.get("calls", []):
        _add(
            rows,
            severity="Info",
            category="call",
            line_numbers=call.get("line_numbers", []),
            source="scanner",
            finding="CALL statement detected",
            details=f"{call.get('caller', '')} calls {call.get('callee', '')}",
            action="Confirm callee is available, transformable, or intentionally external.",
        )
    for call in analysis.get("possible_external_or_unsupported_calls", []):
        _add(
            rows,
            severity="Warning",
            category="call",
            line_numbers=call.get("line_numbers", []),
            source="scanner",
            finding="External or unsupported call candidate",
            details=f"{call.get('call', '')}: {call.get('classification', '')}",
            action="Review whether this helper must be provided, wrapped, or excluded.",
        )
    for io_row in analysis.get("file_io", []):
        _add(
            rows,
            severity="Warning",
            category="I/O",
            line_numbers=io_row.get("line_numbers", []),
            source="scanner",
            finding="File I/O detected",
            details=f"{io_row.get('kind', '')}: {io_row.get('text', '')}",
            action="Decide whether this diagnostic or data I/O must be preserved outside transformed code.",
        )
    for warning in analysis.get("warnings", []):
        _add(
            rows,
            severity="Warning",
            category="warning",
            source="scanner",
            finding="Scanner warning",
            details=str(warning),
            action="Review before trusting downstream classifications.",
        )
    for feature in analysis.get("unsupported_features", []):
        severity = str(feature.get("severity", "Warning")).title()
        if severity not in FINDING_SEVERITIES:
            severity = "Warning"
        _add(
            rows,
            severity=severity,
            category="unsupported feature",
            line_numbers=feature.get("line_numbers", []),
            source="validator",
            finding=str(feature.get("code", "Unsupported feature")),
            details=str(feature.get("message", "")),
            action="Resolve or explicitly accept before transformation.",
        )
    for routine in routine_roles or []:
        _add(
            rows,
            severity="Warning" if routine.get("selected_role") == "Unknown" else "Info",
            category="classification",
            source="routine roles",
            finding="Routine role suggestion",
            details=f"{routine.get('routine_name', '')}: {routine.get('suggested_role', 'Unknown')}",
            action="Confirm or edit routine classification.",
        )
    for region in region_classifications or []:
        _add(
            rows,
            severity="Warning" if region.get("user-selected classification") == "Unknown" else "Info",
            category="classification",
            line_numbers=[region.get("start line", ""), region.get("end line", "")],
            source="region roles",
            finding="Region classification suggestion",
            details=f"{region.get('region id', '')}: {region.get('suggested classification', 'Unknown')}",
            action="Confirm or edit region classification.",
        )
    for variable in variable_roles or []:
        _add(
            rows,
            severity="Warning" if variable.get("user-selected OTIS role") == "Unknown" else "Info",
            category="classification",
            source="variable roles",
            finding="Variable OTIS role suggestion",
            details=f"{variable.get('variable name', '')}: {variable.get('suggested OTIS role', 'Unknown')}",
            action="Confirm or edit OTIS variable role.",
        )
    return merge_findings_log(rows, existing_rows or [])


def _build_compact_findings_log(
    analysis: dict[str, Any],
    routine_roles: list[dict[str, object]],
    region_classifications: list[dict[str, object]],
    variable_roles: list[dict[str, object]],
    existing_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not analysis.get("has_subroutine_umat"):
        _add(
            rows,
            severity="Warning",
            category="interface",
            source="scanner",
            finding="Main UMAT not detected",
            details="No SUBROUTINE UMAT entry point was detected.",
            action="Select the intended entry routine or inspect the parser diagnostics.",
        )
    for feature in analysis.get("unsupported_features", []):
        severity = str(feature.get("severity", "Warning")).title()
        if severity not in FINDING_SEVERITIES:
            severity = "Warning"
        _add(
            rows,
            severity=severity,
            category="unsupported feature",
            line_numbers=feature.get("line_numbers", []),
            source="validator",
            finding=str(feature.get("code", "Unsupported feature")),
            details=str(feature.get("message", "")),
            action="Resolve or explicitly accept before transformation.",
        )
    for call in analysis.get("possible_external_or_unsupported_calls", []):
        _add(
            rows,
            severity="Warning",
            category="call",
            line_numbers=call.get("line_numbers", []),
            source="scanner",
            finding="External or unsupported call candidate",
            details=f"{call.get('call', '')}: {call.get('classification', '')}",
            action="Review whether this helper must be provided, wrapped, or excluded.",
        )
    for io_row in analysis.get("file_io", []):
        _add(
            rows,
            severity="Warning",
            category="I/O",
            line_numbers=io_row.get("line_numbers", []),
            source="scanner",
            finding="File I/O detected",
            details=f"{io_row.get('kind', '')}: {io_row.get('text', '')}",
            action="Decide whether this diagnostic or data I/O must be preserved outside transformed code.",
        )
    for warning in analysis.get("warnings", []):
        _add(
            rows,
            severity="Warning",
            category="warning",
            source="scanner",
            finding="Scanner warning",
            details=str(warning),
            action="Review before trusting downstream classifications.",
        )
    for routine in routine_roles:
        if routine.get("selected_role") == "Unknown":
            _add(
                rows,
                severity="Warning",
                category="classification",
                source="routine roles",
                finding="Routine role requires review",
                details=f"{routine.get('routine_name', '')}: {routine.get('suggested_role', 'Unknown')}",
                action="Confirm whether this helper participates in the DSTRAN to STRESS path.",
            )
    for region in region_classifications:
        if region.get("user-selected classification") == "Unknown":
            _add(
                rows,
                severity="Warning",
                category="classification",
                line_numbers=[region.get("start line", ""), region.get("end line", "")],
                source="region roles",
                finding="Region classification requires review",
                details=f"{region.get('region id', '')}: {region.get('detected reason', '')}",
                action="Confirm whether this region is stress update, old tangent, shared setup, or ignored.",
            )
    for variable in variable_roles:
        if variable.get("user-selected OTIS role") == "Unknown":
            _add(
                rows,
                severity="Warning",
                category="classification",
                source="variable roles",
                finding="Variable OTIS role requires review",
                details=f"{variable.get('variable name', '')}: {variable.get('notes', '')}",
                action="Decide whether the variable is promoted, constant, kept real, or the DSTRAN seed.",
            )
    return merge_findings_log(rows, existing_rows)


def merge_findings_log(
    generated_rows: list[dict[str, object]], existing_rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    existing_by_id = {str(row.get("log id", "")): row for row in existing_rows}
    merged: list[dict[str, object]] = []
    for row in generated_rows:
        log_id = str(row.get("log id", ""))
        existing = existing_by_id.get(log_id)
        if existing:
            updated = dict(row)
            updated["status"] = existing.get("status", row.get("status", "Open"))
            updated["notes"] = existing.get("notes", row.get("notes", ""))
            merged.append(updated)
        else:
            merged.append(dict(row))
    manual_rows = [row for row in existing_rows if str(row.get("log id", "")).startswith("MANUAL-")]
    return merged + [dict(row) for row in manual_rows]


def finding_log_summary(rows: list[dict[str, object]]) -> dict[str, int]:
    summary = {"total": len(rows), "open": 0, "warnings": 0, "errors": 0, "action_needed": 0}
    for row in rows:
        if row.get("status") == "Open":
            summary["open"] += 1
        if row.get("status") == "Action needed":
            summary["action_needed"] += 1
        if row.get("severity") == "Warning":
            summary["warnings"] += 1
        if row.get("severity") == "Error":
            summary["errors"] += 1
    return summary


def _add(
    rows: list[dict[str, object]],
    *,
    severity: str,
    category: str,
    source: str,
    finding: str,
    details: str,
    action: str,
    line_numbers: Any = None,
) -> None:
    line_text = _line_text(line_numbers)
    log_id = f"FIND-{len(rows) + 1:04d}"
    rows.append(
        {
            "log id": log_id,
            "severity": severity,
            "category": category,
            "line(s)": line_text,
            "source": source,
            "finding": finding,
            "details": details,
            "suggested action": action,
            "status": "Open",
            "notes": "",
        }
    )


def _line_text(line_numbers: Any) -> str:
    if not line_numbers:
        return ""
    if isinstance(line_numbers, (str, int)):
        return str(line_numbers)
    values = [str(item) for item in line_numbers if str(item) != ""]
    if not values:
        return ""
    if len(values) == 2 and values[0] != values[1]:
        return f"{values[0]}-{values[1]}"
    return ", ".join(values)


def _assignment_action(name: str) -> str:
    if name == "STRESS":
        return "Review as part of the OTIS-transformed stress update path."
    if name == "DDSDDE":
        return "Mark old tangent code for replacement by OTIS derivative extraction."
    if name == "STATEV":
        return "Review state update dependency and derivative propagation needs."
    return "Review assignment."