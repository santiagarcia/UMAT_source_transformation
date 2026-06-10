from __future__ import annotations

import json
from typing import Any

from umat_oti.app.components import status_row
from umat_oti.fortran.interface_detection import expected_argument_report


def show_file_metadata(st_module, metadata: dict[str, object] | None) -> None:
    if not metadata:
        st_module.info("No source file has been uploaded yet.")
        return
    st_module.table(
        [
            {"field": "file name", "value": metadata.get("file_name", "")},
            {"field": "file path", "value": metadata.get("file_path", "")},
            {"field": "file size", "value": str(metadata.get("file_size", ""))},
            {"field": "SHA256", "value": metadata.get("sha256", "")},
            {"field": "extension", "value": metadata.get("extension", "")},
        ]
    )


def show_source_preview(st_module, source_text: str, analysis: dict[str, Any] | None) -> None:
    if not source_text:
        st_module.info("Upload a UMAT source file to preview it.")
        return
    st_module.code(source_text, language="fortran")
    markers = analysis.get("markers", []) if analysis else []
    st_module.subheader("Detected Markers")
    if markers:
        st_module.dataframe(_display_rows(markers), width="stretch", hide_index=True)
    else:
        st_module.info("No simple markers detected yet.")


def show_analysis(st_module, analysis: dict[str, Any] | None) -> None:
    if not analysis:
        st_module.info("Upload a UMAT source file to run deterministic source analysis.")
        return
    st_module.write(f"Fortran form: `{analysis.get('form', 'unknown')}`")
    st_module.write(f"SUBROUTINE UMAT detected: `{analysis.get('has_subroutine_umat', False)}`")
    tabs = st_module.tabs(
        [
            "Routines",
            "Variables",
            "Assignments",
            "Regions",
            "Uses",
            "Calls and I/O",
            "Warnings",
        ]
    )
    with tabs[0]:
        st_module.subheader("Detected UMAT-like Routines")
        st_module.dataframe(_display_rows(analysis.get("detected_umat_routines", [])), width="stretch", hide_index=True)
        st_module.subheader("Subroutines")
        st_module.dataframe(_display_rows(analysis.get("detected_subroutines", [])), width="stretch", hide_index=True)
        st_module.subheader("Functions")
        st_module.dataframe(_display_rows(analysis.get("detected_functions", [])), width="stretch", hide_index=True)
    with tabs[1]:
        st_module.dataframe(_display_rows(analysis.get("detected_variables", [])), width="stretch", hide_index=True)
    with tabs[2]:
        st_module.subheader("Assignments to STRESS")
        st_module.dataframe(_display_rows(analysis.get("assignments_to_stress", [])), width="stretch", hide_index=True)
        st_module.subheader("Assignments to STATEV")
        st_module.dataframe(_display_rows(analysis.get("assignments_to_statev", [])), width="stretch", hide_index=True)
        st_module.subheader("Assignments to DDSDDE")
        st_module.dataframe(_display_rows(analysis.get("assignments_to_ddsdde", [])), width="stretch", hide_index=True)
    with tabs[3]:
        summary = analysis.get("region_summary", {})
        messages = summary.get("report_messages", [])
        st_module.subheader("Region Replacement Report")
        if messages:
            for message in messages:
                st_module.warning(str(message))
        else:
            st_module.info("No old DDSDDE replacement report is available yet.")
        st_module.subheader("Detected Stress and Tangent Regions")
        st_module.dataframe(_display_rows(analysis.get("detected_regions", [])), width="stretch", hide_index=True)
    with tabs[4]:
        for name, rows in analysis.get("uses", {}).items():
            with st_module.expander(f"Uses of {name} ({len(rows)})", expanded=False):
                st_module.dataframe(_display_rows(rows), width="stretch", hide_index=True)
    with tabs[5]:
        st_module.subheader("CALL Targets")
        st_module.dataframe(_display_rows(analysis.get("calls", [])), width="stretch", hide_index=True)
        st_module.subheader("External or Unsupported Calls")
        st_module.dataframe(_display_rows(analysis.get("possible_external_or_unsupported_calls", [])), width="stretch", hide_index=True)
        st_module.subheader("File I/O")
        st_module.dataframe(_display_rows(analysis.get("file_io", [])), width="stretch", hide_index=True)
    with tabs[6]:
        warnings = analysis.get("warnings", [])
        unsupported = analysis.get("unsupported_features", [])
        st_module.subheader("Warnings")
        st_module.write(warnings if warnings else "None")
        st_module.subheader("Unsupported Features")
        st_module.dataframe(_display_rows(unsupported), width="stretch", hide_index=True)


def show_transformation_review(
    st_module,
    review: dict[str, Any] | None,
    analysis: dict[str, Any] | None,
    selected_umat: str,
    source_metadata: dict[str, object] | None = None,
) -> None:
    if not analysis:
        st_module.info("Select or upload a UMAT source to build the transformation review.")
        return
    review = review or {}
    source_metadata = source_metadata or {}

    st_module.subheader("UMAT Summary")
    summary_rows = [
        {"field": "main UMAT", "value": selected_umat or ""},
        {"field": "source", "value": source_metadata.get("file_path", "")},
        {"field": "Fortran form", "value": analysis.get("form", "unknown")},
        {"field": "ready", "value": str(review.get("ready_for_transformation", False))},
    ]
    st_module.dataframe(_display_rows(summary_rows), width="stretch", hide_index=True)
    action_needed = review.get("action_needed", [])
    if action_needed:
        st_module.warning("Action needed before transformation review is complete.")
        st_module.dataframe(_display_rows(action_needed), width="stretch", hide_index=True)
    else:
        st_module.success("No user decisions are currently blocking the next transformation step.")

    st_module.subheader("Propagation Path")
    path_rows = [
        {"role": "seed", "variables": review.get("seed_variables", [])},
        {"role": "promote", "variables": review.get("promoted_variables", [])},
        {"role": "constant", "variables": review.get("constant_variables", [])},
    ]
    st_module.dataframe(_display_rows(path_rows), width="stretch", hide_index=True)
    _show_region_table(st_module, review.get("stress_update_regions_to_transform", []), "Stress update regions to transform")
    _show_region_table(st_module, review.get("shared_setup_regions_to_keep", []), "Shared setup regions to keep")

    st_module.subheader("Old Tangent Replacement")
    _show_region_table(st_module, review.get("old_tangent_regions_to_replace", []), "Old tangent regions to replace")
    keep_real = review.get("keep_real_variables", [])
    if keep_real:
        st_module.caption("Keep-real/tangent-only variables")
        st_module.write(", ".join(str(name) for name in keep_real))

    st_module.subheader("Ambiguities Requiring User Review")
    ambiguous = review.get("ambiguous_items", [])
    if ambiguous:
        st_module.dataframe(_display_rows(ambiguous), width="stretch", hide_index=True)
    else:
        st_module.info("None detected.")


def show_advanced_diagnostics(
    st_module,
    source_text: str,
    analysis: dict[str, Any] | None,
    findings_log: list[dict[str, object]] | None,
    variable_roles: list[dict[str, object]] | None,
    routine_roles: list[dict[str, object]] | None,
    region_classifications: list[dict[str, object]] | None,
) -> None:
    with st_module.expander("Advanced Diagnostics", expanded=False):
        tabs = st_module.tabs(["Source", "Analysis", "Roles", "Findings", "Compact JSON"])
        with tabs[0]:
            show_source_preview(st_module, source_text, analysis)
        with tabs[1]:
            show_analysis(st_module, analysis)
        with tabs[2]:
            st_module.subheader("Variable Roles")
            st_module.dataframe(_display_rows(variable_roles or []), width="stretch", hide_index=True)
            st_module.subheader("Routine Roles")
            st_module.dataframe(_display_rows(routine_roles or []), width="stretch", hide_index=True)
            st_module.subheader("Region Classifications")
            st_module.dataframe(_display_rows(region_classifications or []), width="stretch", hide_index=True)
        with tabs[3]:
            st_module.dataframe(_display_rows(findings_log or []), width="stretch", hide_index=True)
        with tabs[4]:
            if analysis:
                st_module.json(
                    {
                        "region_summary": analysis.get("region_summary", {}),
                        "detected_regions": analysis.get("detected_regions", []),
                    }
                )


def _show_region_table(st_module, rows: Any, label: str) -> None:
    st_module.caption(label)
    if rows:
        display_rows = [
            {
                "region": row.get("region_id", ""),
                "lines": _line_span(row.get("start_line", ""), row.get("end_line", "")),
                "classification": row.get("classification", ""),
                "variables": row.get("variables", []),
                "reason": row.get("reason", ""),
            }
            for row in rows
            if isinstance(row, dict)
        ]
        st_module.dataframe(_display_rows(display_rows), width="stretch", hide_index=True)
    else:
        st_module.info("None detected.")


def _line_span(start: object, end: object) -> str:
    if start == end:
        return str(start)
    return f"{start}-{end}"


def show_selected_umat(
    st_module,
    analysis: dict[str, Any] | None,
    selected_name: str,
    source_metadata: dict[str, object] | None = None,
) -> list[str]:
    if not analysis or not selected_name:
        st_module.info("Select a main UMAT after uploading and analyzing a source file.")
        return []
    routines = analysis.get("detected_subroutines", [])
    selected = next((row for row in routines if row.get("name") == selected_name.upper()), None)
    arguments = list(selected.get("arguments", [])) if selected else []
    report = expected_argument_report(arguments)
    st_module.write(f"Selected UMAT name: `{selected_name}`")
    if source_metadata:
        st_module.write(f"Selected source file: `{source_metadata.get('file_path', '')}`")
    st_module.write(f"Detected argument count: `{len(arguments)}`")
    st_module.subheader("Detected Argument List")
    st_module.write(arguments)
    columns = st_module.columns(3)
    with columns[0]:
        st_module.subheader("Expected Found")
        st_module.write(report["found"])
    with columns[1]:
        st_module.subheader("Expected Missing")
        st_module.write(report["missing"])
    with columns[2]:
        st_module.subheader("Extra Arguments")
        st_module.write(report["extra_arguments"])
    warnings = analysis.get("warnings", [])
    if warnings:
        st_module.warning("Warnings: " + "; ".join(warnings))
    return arguments


def show_pipeline_status(st_module, pipeline: dict[str, Any]) -> None:
    rows = [
        status_row("Upload UMAT", pipeline.get("upload_status", "Not started")),
        status_row("Analyze source", pipeline.get("analysis_status", "Not started")),
        status_row("Select main UMAT", pipeline.get("selected_umat_status", "Needs input")),
        status_row("Map standard variables", pipeline.get("mapping_status", "Needs input")),
        status_row("Classify helper routines", pipeline.get("routine_classification_status", "Needs input")),
        status_row("Classify code regions", pipeline.get("region_classification_status", "Needs input")),
        status_row("Assign OTIS variable roles", pipeline.get("variable_role_status", "Needs input")),
        status_row("Save configuration", "Complete" if pipeline.get("configuration_saved") else "Needs input"),
        status_row("Ready for transformation", pipeline.get("ready_status", "Needs input")),
    ]
    st_module.dataframe(rows, width="stretch", hide_index=True)
    missing = pipeline.get("missing_information", [])
    if pipeline.get("ready_for_transformation"):
        st_module.success("Ready for transformation configuration stage.")
    elif missing:
        st_module.warning("Missing information: " + "; ".join(missing))
    else:
        st_module.info("Complete the setup sections to prepare for transformation.")


def _display_rows(rows: Any) -> list[dict[str, str]]:
    if not rows:
        return []
    result: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            result.append({str(key): _display_value(value) for key, value in row.items()})
        else:
            result.append({"value": _display_value(row)})
    return result


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)
