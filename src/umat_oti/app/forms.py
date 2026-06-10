from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from umat_oti.app.components import merge_rows_by_key, normalize_editor_rows
from umat_oti.core.findings_log import FINDING_SEVERITIES, FINDING_STATUSES, build_findings_log, finding_log_summary
from umat_oti.core.config_loader import load_project_config_json, session_state_from_config
from umat_oti.core.project import create_project_workspace, save_uploaded_bytes
from umat_oti.core.roles import OTIS_ROLES, ROUTINE_ROLES, apply_bulk_action
from umat_oti.core.transformation_anchors import FILE_IO_ACTIONS, TANGENT_REGION_ROLES, build_transformation_anchors
from umat_oti.core.transformation_settings import DEFAULT_GENERATED_NTENS, build_transformation_settings
from umat_oti.core.transformation_review import build_transformation_review
from umat_oti.fortran.interface_detection import OPTIONAL_GUI_MAPPINGS, REQUIRED_GUI_MAPPINGS
from umat_oti.fortran.regions import SHARED_REGION_CLASSIFICATIONS, STRESS_REGION_CLASSIFICATIONS, TANGENT_REGION_CLASSIFICATIONS
from umat_oti.fortran.scanner import analyze_fortran_source


def render_config_upload(st_module) -> None:
    st_module.subheader("Load Existing JSON Configuration")
    server_configs = _server_config_files()
    batch_status = _latest_batch_status_by_config()
    if server_configs:
        config_options = {_relative_label(path): path for path in server_configs}
        st_module.caption(f"Server JSON contracts: {len(server_configs)} available.")
        st_module.dataframe(_json_contract_rows(server_configs, batch_status), width="stretch", hide_index=True)
        selected_config = st_module.selectbox(
            "Select a JSON configuration already on this server",
            options=list(config_options),
            key="server_json_config_select",
        )
        selected_path = config_options[selected_config]
        with st_module.expander("Preview selected server JSON", expanded=False):
            _show_json_payload(st_module, _raw_json_payload(selected_path.read_bytes()))
        if st_module.button("Load selected server JSON configuration"):
            path = selected_path
            raw_config = _raw_json_payload(path.read_bytes())
            try:
                config = load_project_config_json(path.read_bytes(), origin_path=path)
                _load_config_into_session(st_module, config, selected_config, raw_config=raw_config)
                _load_json_umat_source_into_session(st_module, config)
            except ValueError as exc:
                st_module.error(str(exc))
                return
            st_module.success(f"Configuration loaded from `{selected_config}`.")
    else:
        st_module.info("No server-side JSON configs were found under `json_files/`.")
    st_module.divider()
    uploaded = st_module.file_uploader(
        "Upload the required OTIS compact JSON contract",
        type=["json"],
        accept_multiple_files=False,
        key="json_config_uploader",
    )
    if uploaded is None:
        st_module.caption("Server-side JSON files can be loaded above. Browser-uploaded JSON should use an absolute source path on this machine.")
    else:
        st_module.success(f"Ready to load `{uploaded.name}`.")
        with st_module.expander("Preview uploaded JSON", expanded=False):
            _show_json_payload(st_module, _raw_json_payload(uploaded.getvalue()))
    if uploaded is not None and st_module.button("Load JSON configuration", type="primary"):
        raw_config = _raw_json_payload(uploaded.getvalue())
        try:
            config = load_project_config_json(uploaded.getvalue())
        except ValueError as exc:
            st_module.error(str(exc))
            return
        _load_config_into_session(st_module, config, uploaded.name, raw_config=raw_config)
        _load_json_umat_source_into_session(st_module, config)
        st_module.success("Configuration loaded into the GUI session.")

    loaded_label = st_module.session_state.get("loaded_input_json_label")
    loaded_payload = st_module.session_state.get("loaded_input_json")
    if loaded_label and loaded_payload is not None:
        with st_module.expander(f"Current loaded JSON: {loaded_label}", expanded=False):
            _show_json_payload(st_module, loaded_payload)

def _load_json_umat_source_into_session(st_module, config: dict[str, Any]) -> None:
    source = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
    source_path_text = str(source.get("selected_umat_file") or source.get("file") or source.get("uploaded_file") or "").strip()
    if not source_path_text:
        return
    source_path = Path(source_path_text).expanduser()
    if not source_path.is_absolute():
        source_path = (_project_root() / source_path).resolve()
    if not source_path.is_file():
        st_module.warning(f"JSON source file was not found: `{source_path_text}`")
        return
    try:
        source_text = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        st_module.warning(f"JSON source file could not be read: `{source_path_text}` ({exc})")
        return
    st_module.session_state["source_text"] = source_text
    st_module.session_state["source_filename"] = source_path.name
    st_module.session_state["selected_umat_file"] = str(source_path)

def render_project_setup(st_module) -> None:
    st_module.subheader("Section 1: Project Setup")
    st_module.text_input("Project name", key="project_name_input")
    st_module.text_input("Workspace directory", key="workdir_input")
    st_module.text_area("Optional description", key="project_description_input")
    st_module.caption(
        "The workspace is created and reused from the loaded JSON contract."
    )
    if st_module.session_state.get("project"):
        st_module.success(f"Workspace ready: {st_module.session_state['project']['workdir']}")
        st_module.json(st_module.session_state["project"])


def render_upload(st_module) -> None:
    st_module.subheader("Section 2: Source-Only Upload Disabled")
    st_module.info("This workflow no longer generates or guesses the OTIS contract from a raw UMAT source.")
    st_module.caption("Provide the compact JSON contract explicitly, and point source at the UMAT source you want to transform.")


def render_select_main_umat(st_module) -> list[str]:
    st_module.subheader("Section 5: Select Main UMAT")
    analysis = st_module.session_state.get("analysis")
    if not analysis:
        st_module.info("Upload a UMAT source file before selecting the main routine.")
        return []
    candidates = analysis.get("detected_umat_routines", []) or analysis.get("detected_subroutines", [])
    names = [str(row.get("name", "")).upper() for row in candidates if row.get("name")]
    if not names:
        st_module.error("No UMAT-like routine was detected.")
        return []
    current = st_module.session_state.get("selected_umat") or names[0]
    if current not in names:
        current = names[0]
    selected = st_module.selectbox("Main UMAT routine", options=names, index=names.index(current))
    st_module.session_state["selected_umat"] = selected
    arguments = _selected_arguments(analysis, selected)
    if not st_module.session_state.get("mappings"):
        st_module.session_state["mappings"] = default_mappings(arguments)
    return arguments


def render_variable_mapping(st_module, arguments: list[str]) -> dict[str, str]:
    st_module.subheader("Section 6: Variable Mapping")
    if not arguments:
        st_module.info("Select a UMAT routine to map its arguments.")
        return {}
    mappings = dict(st_module.session_state.get("mappings", {}))
    columns = st_module.columns(3)
    for index, required in enumerate(REQUIRED_GUI_MAPPINGS):
        with columns[index % 3]:
            key = required.lower()
            mappings[key] = _mapping_select(st_module, required, key, arguments, mappings.get(key, ""))
    st_module.subheader("Optional mappings")
    optional_columns = st_module.columns(3)
    for index, optional in enumerate(OPTIONAL_GUI_MAPPINGS):
        with optional_columns[index % 3]:
            key = optional.lower()
            mappings[key] = _mapping_select(st_module, optional, key, arguments, mappings.get(key, ""), required=False)
    st_module.session_state["mappings"] = mappings
    return mappings


def render_routine_classification(st_module) -> list[dict[str, object]]:
    st_module.subheader("Section 7: Helper Routine Classification")
    rows = st_module.session_state.get("routine_roles", [])
    if not rows:
        st_module.info("No routines or calls are available for classification yet.")
        return []
    st_module.caption("Edit each helper routine classification below. The selected role and notes are saved into the project configuration.")
    edited_rows: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        routine_name = str(row.get("routine_name", f"routine_{index}"))
        suggested = str(row.get("suggested_role", "Unknown"))
        current = str(row.get("selected_role", suggested))
        if current not in ROUTINE_ROLES:
            current = "Unknown"
        with st_module.container(border=True):
            columns = st_module.columns([2, 2, 3])
            with columns[0]:
                st_module.markdown(f"**{routine_name}**")
                st_module.caption(f"Suggested: {suggested}")
            with columns[1]:
                selected = st_module.selectbox(
                    "Selected role",
                    options=list(ROUTINE_ROLES),
                    index=list(ROUTINE_ROLES).index(current),
                    key=f"routine_role_{_safe_key(routine_name)}_{index}",
                )
            with columns[2]:
                notes = st_module.text_input(
                    "Notes",
                    value=str(row.get("notes", "")),
                    key=f"routine_notes_{_safe_key(routine_name)}_{index}",
                )
        edited_rows.append(
            {
                "notes": notes,
                "routine_name": routine_name,
                "selected_role": selected,
                "suggested_role": suggested,
            }
        )
    st_module.session_state["routine_roles"] = edited_rows
    return st_module.session_state["routine_roles"]


def render_region_classification(st_module) -> list[dict[str, object]]:
    st_module.subheader("Section 8: Stress and Tangent Region Classification")
    rows = st_module.session_state.get("region_classifications", [])
    analysis = st_module.session_state.get("analysis") or {}
    if not rows:
        st_module.info("No stress or tangent candidate regions were detected yet.")
        return []
    summary = analysis.get("region_summary", {})
    messages = summary.get("report_messages", [])
    if messages:
        for message in messages:
            st_module.warning(str(message))
    st_module.caption(
        "Classify each detected code region. Tangent-only regions will later be skipped or removed and replaced by OTIS derivative extraction."
    )
    edited_rows: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        region_id = str(row.get("region id", f"region_{index}"))
        region_type = str(row.get("region type", "unknown"))
        options = _region_classification_options(region_type)
        suggested = str(row.get("suggested classification", "Unknown"))
        current = str(row.get("user-selected classification", suggested))
        if current not in options:
            current = "Unknown"
        with st_module.container(border=True):
            columns = st_module.columns([1.2, 1.2, 2.4, 2.2])
            with columns[0]:
                st_module.markdown(f"**{region_id}**")
                st_module.caption(region_type)
                st_module.write(f"Lines {row.get('start line', '')}-{row.get('end line', '')}")
            with columns[1]:
                st_module.caption("Suggested")
                st_module.write(suggested)
                st_module.caption("Reason")
                st_module.write(row.get("detected reason", ""))
            with columns[2]:
                st_module.code(str(row.get("short code preview", "")), language="fortran")
            with columns[3]:
                selected = st_module.selectbox(
                    "Selected classification",
                    options=list(options),
                    index=list(options).index(current),
                    key=f"region_classification_{_safe_key(region_id)}_{index}",
                )
                notes = st_module.text_input(
                    "Notes",
                    value=str(row.get("notes", "")),
                    key=f"region_notes_{_safe_key(region_id)}_{index}",
                )
        updated = dict(row)
        updated["user-selected classification"] = selected
        updated["notes"] = notes
        edited_rows.append(updated)
    st_module.session_state["region_classifications"] = edited_rows
    return st_module.session_state["region_classifications"]


def render_transformation_anchor_completion(st_module) -> dict[str, Any]:
    st_module.subheader("Complete Transformation JSON")
    analysis = st_module.session_state.get("analysis") or {}
    review = st_module.session_state.get("transformation_review") or {}
    mappings = st_module.session_state.get("mappings") or {}
    source_text = st_module.session_state.get("source_text", "")
    if not analysis:
        st_module.info("Analyze a UMAT source before completing transformation anchors.")
        return {}
    config_stub = {
        "analysis": analysis,
        "mapping": mappings,
        "source": {"selected_umat_name": st_module.session_state.get("selected_umat", "")},
        "transformation_review": review,
    }
    anchors = dict(st_module.session_state.get("transformation_anchors") or build_transformation_anchors(config_stub, source_text))
    status = str(anchors.get("status", "needs_json_completion"))
    issues = anchors.get("completion_issues", []) or []
    if status == "ready_with_json_contract":
        st_module.success("Transformation JSON anchor contract is complete enough for deterministic transformation.")
    if issues:
        st_module.dataframe(_display_rows(issues), width="stretch", hide_index=True)

    seed = dict(anchors.get("seed_insertion") or {})
    seed["line_before"] = int(st_module.number_input("Seed before source line", min_value=0, value=int(seed.get("line_before") or 0), step=1, key="anchor_seed_line_before"))
    seed["reason"] = st_module.text_input("Seed insertion reason", value=str(seed.get("reason", "")), key="anchor_seed_reason")
    anchors["seed_insertion"] = seed

    st_module.caption("Stress update regions to transform")
    st_module.dataframe(_display_rows((anchors.get("stress_update") or {}).get("regions", [])), width="stretch", hide_index=True)

    old = dict(anchors.get("old_tangent") or {})
    helper_regions = list(old.get("helper_regions", []) or [])
    if helper_regions:
        edited_helpers = st_module.data_editor(
            helper_regions,
            column_config={"role": st_module.column_config.SelectboxColumn("role", options=list(TANGENT_REGION_ROLES))},
            disabled=["region_id", "start_line", "end_line", "classification", "variables", "preview"],
            hide_index=True,
            key="anchor_tangent_helper_editor",
            width="stretch",
        )
        old["helper_regions"] = normalize_editor_rows(edited_helpers)
    output_region = dict(old.get("output_region") or {})
    if output_region:
        role_options = list(TANGENT_REGION_ROLES)
        current_role = str(output_region.get("role", "ddsdde_output_replace"))
        if current_role not in role_options:
            current_role = "user_review_required"
        output_region["role"] = st_module.selectbox(
            "DDSDDE output region classification",
            options=role_options,
            index=role_options.index(current_role),
            key="anchor_ddsdde_output_role",
        )
        output_region["reason"] = st_module.text_input("DDSDDE output reason", value=str(output_region.get("reason", "")), key="anchor_ddsdde_output_reason")
        old["output_region"] = output_region
    anchors["old_tangent"] = old

    file_io_regions = list(anchors.get("file_io_regions", []) or [])
    if file_io_regions:
        st_module.caption("File I/O regions")
        edited_io = st_module.data_editor(
            file_io_regions,
            column_config={"action": st_module.column_config.SelectboxColumn("action", options=list(FILE_IO_ACTIONS))},
            disabled=["start_line", "end_line", "line_numbers", "kind", "text", "preview"],
            hide_index=True,
            key="anchor_file_io_editor",
            width="stretch",
        )
        anchors["file_io_regions"] = normalize_editor_rows(edited_io)

    real_output = dict(anchors.get("real_output_extraction") or {})
    real_output["insert_after_line"] = int(st_module.number_input("Copy real outputs after source line", min_value=0, value=int(real_output.get("insert_after_line") or 0), step=1, key="anchor_real_output_after"))
    anchors["real_output_extraction"] = real_output
    ddsdde = dict(anchors.get("ddsdde_extraction") or {})
    ddsdde["insert_after_line"] = int(st_module.number_input("Extract DDSDDE after source line", min_value=0, value=int(ddsdde.get("insert_after_line") or 0), step=1, key="anchor_ddsdde_after"))
    anchors["ddsdde_extraction"] = ddsdde

    anchors["completion_issues"] = _anchor_completion_issues(anchors)
    anchors["status"] = "ready_with_json_contract" if not anchors["completion_issues"] else "needs_json_completion"
    st_module.session_state["transformation_anchors"] = anchors
    return anchors


def render_findings_log(st_module) -> list[dict[str, object]]:
    st_module.subheader("Section 9: Findings Log")
    rows = st_module.session_state.get("findings_log", [])
    analysis = st_module.session_state.get("analysis")
    if not analysis:
        st_module.info("Process a UMAT source file to populate the findings log.")
        return []
    if st_module.button("Refresh findings from current analysis"):
        rows = build_findings_log(
            analysis,
            st_module.session_state.get("file_metadata"),
            st_module.session_state.get("routine_roles", []),
            st_module.session_state.get("region_classifications", []),
            st_module.session_state.get("variable_roles", []),
            rows,
        )
        st_module.session_state["findings_log"] = rows
    if not rows:
        rows = build_findings_log(
            analysis,
            st_module.session_state.get("file_metadata"),
            st_module.session_state.get("routine_roles", []),
            st_module.session_state.get("region_classifications", []),
            st_module.session_state.get("variable_roles", []),
        )
        st_module.session_state["findings_log"] = rows
    summary = finding_log_summary(rows)
    metric_columns = st_module.columns(5)
    metric_columns[0].metric("Total", summary["total"])
    metric_columns[1].metric("Open", summary["open"])
    metric_columns[2].metric("Action needed", summary["action_needed"])
    metric_columns[3].metric("Warnings", summary["warnings"])
    metric_columns[4].metric("Errors", summary["errors"])

    filter_columns = st_module.columns(4)
    with filter_columns[0]:
        severity_filter = st_module.selectbox("Severity", options=["All"] + list(FINDING_SEVERITIES))
    with filter_columns[1]:
        category_filter = st_module.selectbox("Category", options=["All"] + _unique_values(rows, "category"))
    with filter_columns[2]:
        status_filter = st_module.selectbox("Status", options=["All"] + list(FINDING_STATUSES))
    with filter_columns[3]:
        search = st_module.text_input("Search findings")

    filtered = _filter_findings(rows, severity_filter, category_filter, status_filter, search)
    edited = st_module.data_editor(
        filtered,
        column_config={
            "severity": st_module.column_config.SelectboxColumn("severity", options=list(FINDING_SEVERITIES)),
            "status": st_module.column_config.SelectboxColumn("status", options=list(FINDING_STATUSES)),
        },
        disabled=[
            "log id",
            "category",
            "line(s)",
            "source",
            "finding",
            "details",
            "suggested action",
        ],
        hide_index=True,
        key="findings_log_editor",
        width="stretch",
    )
    st_module.session_state["findings_log"] = merge_rows_by_key(rows, normalize_editor_rows(edited), "log id")
    _render_manual_finding_form(st_module)
    return st_module.session_state["findings_log"]


def render_variable_roles(st_module) -> list[dict[str, object]]:
    st_module.subheader("Sections 10 and 11: OTIS Variable Role Table and Controls")
    rows = st_module.session_state.get("variable_roles", [])
    if not rows:
        st_module.info("No detected variables are available yet.")
        return []
    control_columns = st_module.columns(4)
    with control_columns[0]:
        if st_module.button("Bulk promote local DOUBLE PRECISION/REAL"):
            rows = apply_bulk_action(rows, "promote_local_real")
    with control_columns[1]:
        if st_module.button("Bulk constant PROPS-derived"):
            rows = apply_bulk_action(rows, "constant_props_derived")
    with control_columns[2]:
        if st_module.button("Bulk keep INTEGER real"):
            rows = apply_bulk_action(rows, "keep_integer")
    with control_columns[3]:
        if st_module.button("Reset to suggested roles"):
            rows = apply_bulk_action(rows, "reset")
    st_module.session_state["variable_roles"] = rows

    filter_columns = st_module.columns(4)
    with filter_columns[0]:
        role_filter = st_module.selectbox("Filter by role", options=["All"] + list(OTIS_ROLES))
    with filter_columns[1]:
        only_unknown = st_module.checkbox("Show only Unknown variables")
    with filter_columns[2]:
        search = st_module.text_input("Search variable name")
    with filter_columns[3]:
        only_stress_path = st_module.checkbox("Only stress-update variables")

    filtered = _filter_variable_rows(rows, role_filter, only_unknown, search, only_stress_path)
    edited = st_module.data_editor(
        filtered,
        column_config={
            "user-selected OTIS role": st_module.column_config.SelectboxColumn(
                "user-selected OTIS role", options=list(OTIS_ROLES)
            ),
        },
        disabled=[
            "variable name",
            "detected type",
            "detected shape/dimension",
            "appears in UMAT arguments yes/no",
            "read/write/unknown",
            "detected usage",
            "suggested OTIS role",
        ],
        hide_index=True,
        key="variable_role_editor",
        width="stretch",
    )
    st_module.session_state["variable_roles"] = merge_rows_by_key(rows, normalize_editor_rows(edited), "variable name")
    return st_module.session_state["variable_roles"]


def render_metadata(st_module) -> dict[str, str]:
    st_module.subheader("Section 12: Optional Model Metadata")
    current = dict(st_module.session_state.get("metadata", {}))
    columns = st_module.columns(2)
    with columns[0]:
        current["model_family"] = st_module.selectbox(
            "Model family",
            ["elasticity", "plasticity", "viscoelasticity", "damage", "crystal plasticity", "other", "unknown"],
            index=_index_or_last(["elasticity", "plasticity", "viscoelasticity", "damage", "crystal plasticity", "other", "unknown"], current.get("model_family", "unknown")),
        )
        current["stress_update_style"] = st_module.selectbox(
            "Stress update style",
            ["explicit", "implicit", "return mapping", "local Newton", "unknown"],
            index=_index_or_last(["explicit", "implicit", "return mapping", "local Newton", "unknown"], current.get("stress_update_style", "unknown")),
        )
    with columns[1]:
        current["strain_regime"] = st_module.selectbox(
            "Strain regime",
            ["small strain", "finite strain", "unknown"],
            index=_index_or_last(["small strain", "finite strain", "unknown"], current.get("strain_regime", "unknown")),
        )
        current["primary_kinematic_driver"] = st_module.selectbox(
            "Primary kinematic driver",
            ["DSTRAN", "STRAN + DSTRAN", "DFGRD0/DFGRD1", "unknown"],
            index=_index_or_last(["DSTRAN", "STRAN + DSTRAN", "DFGRD0/DFGRD1", "unknown"], current.get("primary_kinematic_driver", "unknown")),
        )
    st_module.session_state["metadata"] = current
    return current


def _mapping_select(
    st_module, label: str, key: str, arguments: list[str], current: str, required: bool = True
) -> str:
    upper_args = [arg.upper() for arg in arguments]
    options = [""] + upper_args + ["<manual>"]
    default = current.upper() if current else (label if label in upper_args else "")
    index = options.index(default) if default in options else options.index("<manual>")
    selected = st_module.selectbox(label + (" *" if required else ""), options=options, index=index, key=f"mapping_select_{key}")
    if selected == "<manual>":
        return st_module.text_input(f"Manual {label} mapping", value=current if current not in options else "", key=f"mapping_manual_{key}").upper()
    return selected


def _selected_arguments(analysis: dict[str, Any], selected: str) -> list[str]:
    for routine in analysis.get("detected_subroutines", []):
        if str(routine.get("name", "")).upper() == selected.upper():
            return [str(arg).upper() for arg in routine.get("arguments", [])]
    return []


def _process_umat_bytes(st_module, filename: str, content: bytes) -> None:
    project = create_project_workspace(
        st_module.session_state.get("project_name_input", "umat_oti_project"),
        Path(st_module.session_state.get("workdir_input", "umat_oti_workspace")),
        st_module.session_state.get("project_description_input", ""),
    )
    path, metadata = save_uploaded_bytes(project, filename, content)
    analysis = analyze_fortran_source(path)
    st_module.session_state["project"] = project.to_json()
    st_module.session_state["project_name"] = project.name
    st_module.session_state["workdir"] = str(project.workdir)
    st_module.session_state["project_description"] = project.description
    st_module.session_state["source_path"] = str(path)
    st_module.session_state["file_metadata"] = metadata
    st_module.session_state["source_text"] = path.read_text(encoding="utf-8", errors="replace")
    st_module.session_state["analysis"] = analysis
    umat_candidates = analysis.get("detected_umat_routines", [])
    st_module.session_state["selected_umat"] = str(umat_candidates[0]["name"]).upper() if umat_candidates else ""
    from umat_oti.core.roles import suggest_routine_roles, suggest_variable_roles

    st_module.session_state["routine_roles"] = suggest_routine_roles(analysis)
    st_module.session_state["variable_roles"] = suggest_variable_roles(analysis)
    st_module.session_state["region_classifications"] = analysis.get("detected_regions", [])
    st_module.session_state["findings_log"] = build_findings_log(
        analysis,
        metadata,
        st_module.session_state["routine_roles"],
        st_module.session_state["region_classifications"],
        st_module.session_state["variable_roles"],
    )
    st_module.session_state["mappings"] = default_mappings(_selected_arguments(analysis, st_module.session_state["selected_umat"]))
    st_module.session_state["transformation_review"] = build_transformation_review(
        analysis,
        selected_umat=st_module.session_state["selected_umat"],
        mappings=st_module.session_state["mappings"],
        routine_roles=st_module.session_state["routine_roles"],
        region_classifications=st_module.session_state["region_classifications"],
        variable_roles=st_module.session_state["variable_roles"],
    )
    st_module.session_state["transformation_anchors"] = build_transformation_anchors(
        {
            "analysis": analysis,
            "mapping": st_module.session_state["mappings"],
            "source": {"selected_umat_name": st_module.session_state["selected_umat"]},
            "transformation_review": st_module.session_state["transformation_review"],
        },
        st_module.session_state["source_text"],
    )
    transformation_settings = build_transformation_settings(
        analysis=analysis,
        project_workdir=project.workdir,
        source_text=st_module.session_state["source_text"],
        fallback_ntens=DEFAULT_GENERATED_NTENS,
    )
    st_module.session_state["transformation_settings"] = transformation_settings
    st_module.session_state["transformation_ntens_input"] = "" if transformation_settings.get("ntens") is None else str(transformation_settings.get("ntens"))
    st_module.session_state["transformation_order_input"] = int(transformation_settings.get("order", 1))
    st_module.session_state["transformation_output_dir"] = str(transformation_settings.get("output_dir", ""))


def _load_config_into_session(st_module, config: dict[str, Any], source_label: str, raw_config: Any | None = None) -> None:
    state, warnings = session_state_from_config(config)
    state["config_paths"] = {"imported_json": source_label}
    state["loaded_input_json"] = raw_config if raw_config is not None else config
    state["loaded_input_json_label"] = source_label
    state["loaded_project_config"] = config
    _clear_loaded_contract_outputs(st_module)
    for key, value in state.items():
        st_module.session_state[key] = value
    for warning in warnings:
        st_module.warning(warning)


def _clear_loaded_contract_outputs(st_module) -> None:
    for key in (
        "transformation_result",
        "transformation_run_log_path",
        "validation_result",
        "validation_generated_otilib_dir",
        "validation_original_umat_path",
        "validation_transformed_umat_path",
        "validation_work_dir",
        "_validation_synced_generated_otilib_dir",
        "_validation_synced_original_umat_path",
        "_validation_synced_transformed_umat_path",
        "_validation_synced_work_dir",
    ):
        if key in st_module.session_state:
            st_module.session_state[key] = {} if key == "validation_result" else ""


def _server_files(relative_dir: str, extensions: set[str]) -> list[Path]:
    root = _project_root() / relative_dir
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in extensions)


def _server_config_files() -> list[Path]:
    for relative_dir in ("json_files_completed", "json_files"):
        candidates = [
            path
            for path in _server_files(relative_dir, {".json"})
            if path.name != "completion_report.json" and not path.name.endswith("_report.json")
        ]
        if candidates:
            return candidates
    return []


def _server_umat_sources() -> list[Path]:
    sources: list[Path] = []
    for config_path in _server_files("json_files", {".json"}):
        try:
            config = load_project_config_json(config_path.read_bytes(), origin_path=config_path)
        except ValueError:
            continue
        source = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
        source_path = Path(str(source.get("selected_umat_file", "")))
        if source_path.is_file():
            sources.append(source_path.resolve())
    if sources:
        return sorted(dict.fromkeys(sources))
    return _server_files("UMATs", {".f", ".for", ".f90"})


def _relative_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_project_root()))
    except ValueError:
        return str(path)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _json_contract_rows(paths: list[Path], batch_status: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        raw = _raw_json_payload(path.read_bytes())
        status = batch_status.get(path.name, {})
        if isinstance(raw, dict):
            project = raw.get("project") if isinstance(raw.get("project"), dict) else {}
            source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
            otis = raw.get("otis") if isinstance(raw.get("otis"), dict) else {}
            transform = raw.get("transformation_settings") if isinstance(raw.get("transformation_settings"), dict) else {}
            validation = raw.get("validation_settings") if isinstance(raw.get("validation_settings"), dict) else {}
            rows.append(
                {
                    "file": _relative_label(path),
                    "case": str(raw.get("case_name") or project.get("name") or path.stem),
                    "umat": str(source.get("umat") or source.get("selected_umat_name") or ""),
                    "ntens": str(otis.get("ntens") or transform.get("ntens") or ""),
                    "transform": _transform_status_label(status),
                    "verification": _validation_status_label(status),
                    "compare": ", ".join(str(value) for value in (validation.get("compare_outputs") or [])),
                }
            )
            continue
        rows.append(
            {
                "file": _relative_label(path),
                "case": path.stem,
                "umat": "",
                "ntens": "",
                "transform": _transform_status_label(status),
                "verification": _validation_status_label(status),
                "compare": "",
            }
        )
    return rows


def _latest_batch_status_by_config() -> dict[str, dict[str, Any]]:
    root = _project_root() / "umat_oti_workspace"
    if not root.is_dir():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for report_path in root.rglob("completed_json_batch_report.json"):
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mtime = report_path.stat().st_mtime
        for row in payload.get("results", []):
            if not isinstance(row, dict):
                continue
            config_name = str(row.get("config") or "").strip()
            if not config_name:
                continue
            previous = latest.get(config_name)
            if previous is not None and mtime < float(previous.get("_mtime", 0.0)):
                continue
            latest[config_name] = {**row, "_mtime": mtime, "_report_path": str(report_path)}
    return latest


def _is_transform_runnable(config_name: str, batch_status: dict[str, dict[str, Any]]) -> bool:
    row = batch_status.get(config_name)
    if not isinstance(row, dict):
        return True
    transform_success = row.get("transform_success")
    if transform_success is True:
        return True
    if transform_success is False:
        return False
    category = str(row.get("category", ""))
    return category not in {"transformation_generated_invalid_code", "needs_json_completion", "blocked_by_user_marked_unsafe"}


def _transform_status_label(status: dict[str, Any]) -> str:
    if not isinstance(status, dict) or not status:
        return "Unknown"
    if status.get("transform_success") is True:
        return "Ready"
    if status.get("transform_success") is False:
        return "Blocked"
    return "Unknown"


def _validation_status_label(status: dict[str, Any]) -> str:
    if not isinstance(status, dict) or not status:
        return "Unknown"
    value = str(status.get("validation_status") or "").strip()
    if not value:
        return "Unknown"
    return value.replace("_", " ").title()


def _short_batch_status(status: dict[str, Any]) -> str:
    message = str(status.get("status") or "This contract is currently known to block during transformation.").strip()
    if len(message) <= 220:
        return message
    return message[:217].rstrip() + "..."


def _raw_json_payload(payload: bytes) -> Any:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return payload.decode("utf-8", errors="replace")


def _show_json_payload(st_module, payload: Any) -> None:
    if isinstance(payload, (dict, list)):
        st_module.json(payload)
        return
    st_module.code(str(payload), language="json")


def default_mappings(arguments: list[str]) -> dict[str, str]:
    arg_set = {arg.upper() for arg in arguments}
    keys = list(REQUIRED_GUI_MAPPINGS) + list(OPTIONAL_GUI_MAPPINGS)
    return {key.lower(): key if key in arg_set else "" for key in keys}


def _filter_variable_rows(
    rows: list[dict[str, object]], role_filter: str, only_unknown: bool, search: str, only_stress_path: bool
) -> list[dict[str, object]]:
    result = []
    needle = search.strip().upper()
    for row in rows:
        role = str(row.get("user-selected OTIS role", "Unknown"))
        name = str(row.get("variable name", "")).upper()
        usage = str(row.get("detected usage", ""))
        if role_filter != "All" and role != role_filter:
            continue
        if only_unknown and role != "Unknown":
            continue
        if needle and needle not in name:
            continue
        if only_stress_path and "stress-update-path" not in usage:
            continue
        result.append(row)
    return result


def _filter_findings(
    rows: list[dict[str, object]], severity_filter: str, category_filter: str, status_filter: str, search: str
) -> list[dict[str, object]]:
    result = []
    needle = search.strip().lower()
    for row in rows:
        if severity_filter != "All" and row.get("severity") != severity_filter:
            continue
        if category_filter != "All" and row.get("category") != category_filter:
            continue
        if status_filter != "All" and row.get("status") != status_filter:
            continue
        text = " ".join(str(row.get(key, "")) for key in ("finding", "details", "suggested action", "notes")).lower()
        if needle and needle not in text:
            continue
        result.append(row)
    return result


def _anchor_completion_issues(anchors: dict[str, Any]) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    if not (anchors.get("stress_update") or {}).get("regions"):
        issues.append({"kind": "missing_stress_update_region", "message": "Select source lines for the stress update."})
    if not (anchors.get("seed_insertion") or {}).get("line_before"):
        issues.append({"kind": "missing_seed_insertion", "message": "Choose the source line before which OTIS seeding is inserted."})
    if not (anchors.get("ddsdde_extraction") or {}).get("insert_after_line"):
        issues.append({"kind": "missing_ddsdde_extraction", "message": "Choose where DDSDDE extraction is inserted."})
    for row in anchors.get("file_io_regions", []) or []:
        if isinstance(row, dict) and row.get("action") == "user_review_required":
            issues.append({"kind": "unclassified_file_io", "message": f"Classify file I/O at line {row.get('start_line', '')}."})
    old = anchors.get("old_tangent") or {}
    for row in old.get("helper_regions", []) or []:
        if isinstance(row, dict) and row.get("role") == "user_review_required":
            issues.append({"kind": "unclassified_tangent_region", "message": f"Classify tangent region {row.get('region_id', '')}."})
    output = old.get("output_region") or {}
    if isinstance(output, dict) and output.get("role") == "user_review_required":
        issues.append({"kind": "unclassified_ddsdde_output", "message": f"Classify DDSDDE output region {output.get('region_id', '')}."})
    return issues


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
        import json

        return json.dumps(value, sort_keys=True)
    return str(value)


def _render_manual_finding_form(st_module) -> None:
    with st_module.expander("Add manual log entry", expanded=False):
        columns = st_module.columns(2)
        with columns[0]:
            severity = st_module.selectbox("Manual severity", options=list(FINDING_SEVERITIES), key="manual_log_severity")
            category = st_module.text_input("Manual category", value="note", key="manual_log_category")
            status = st_module.selectbox("Manual status", options=list(FINDING_STATUSES), key="manual_log_status")
        with columns[1]:
            finding = st_module.text_input("Manual finding", key="manual_log_finding")
            action = st_module.text_input("Manual suggested action", key="manual_log_action")
        details = st_module.text_area("Manual details", key="manual_log_details")
        notes = st_module.text_input("Manual notes", key="manual_log_notes")
        if st_module.button("Add manual finding"):
            rows = list(st_module.session_state.get("findings_log", []))
            manual_id = _next_manual_log_id(rows)
            rows.append(
                {
                    "log id": manual_id,
                    "severity": severity,
                    "category": category or "note",
                    "line(s)": "",
                    "source": "user",
                    "finding": finding or "Manual finding",
                    "details": details,
                    "suggested action": action,
                    "status": status,
                    "notes": notes,
                }
            )
            st_module.session_state["findings_log"] = rows
            st_module.success(f"Added {manual_id} to the findings log.")


def _next_manual_log_id(rows: list[dict[str, object]]) -> str:
    highest = 0
    for row in rows:
        value = str(row.get("log id", ""))
        if value.startswith("MANUAL-"):
            try:
                highest = max(highest, int(value.rsplit("-", 1)[1]))
            except ValueError:
                continue
    return f"MANUAL-{highest + 1:04d}"


def _unique_values(rows: list[dict[str, object]], key: str) -> list[str]:
    return sorted({str(row.get(key, "")) for row in rows if row.get(key)})


def _index_or_last(options: list[str], value: str) -> int:
    return options.index(value) if value in options else len(options) - 1


def _region_classification_options(region_type: str) -> tuple[str, ...]:
    if region_type == "stress":
        return STRESS_REGION_CLASSIFICATIONS
    if region_type == "tangent":
        return TANGENT_REGION_CLASSIFICATIONS
    if region_type == "shared_setup":
        return SHARED_REGION_CLASSIFICATIONS
    return ("Unknown", "Ignore")


def _safe_key(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in value)
