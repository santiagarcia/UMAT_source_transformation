from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from umat_oti.app.displays import (
    show_advanced_diagnostics,
    show_file_metadata,
    show_pipeline_status,
    show_transformation_review,
)
from umat_oti.app.forms import (
    default_mappings,
    render_config_upload,
    render_findings_log,
    render_metadata,
    render_project_setup,
    render_region_classification,
    render_routine_classification,
    render_select_main_umat,
    render_transformation_anchor_completion,
    render_upload,
    render_variable_mapping,
    render_variable_roles,
)
from umat_oti.app.state import initialize_session_state
from umat_oti.core.config import build_project_config, merge_project_config
from umat_oti.core.output_layout import default_transform_output_dir, default_validation_work_dir, is_legacy_generated_validation_dir, migrate_validation_work_dir
from umat_oti.core.pipeline_status import evaluate_pipeline_status
from umat_oti.core.transformation_review import build_transformation_review
from umat_oti.reports.config_writer import write_config_files
from umat_oti.reports.otis_run_log import write_otis_run_log
from umat_oti.reports.summary import write_setup_summary
from umat_oti.transform.source_transform import TransformResult, _directions_required, transform_umat_to_oti_from_config
from umat_oti.validation.abaqus_runner import extract_results, run_both_jobs, run_original_job, run_transformed_job
from umat_oti.validation.compare_results import DEFAULT_DDSDDE_ABS_TOLERANCE, DEFAULT_DDSDDE_REL_TOLERANCE, DEFAULT_STRESS_ABS_TOLERANCE, DEFAULT_STRESS_REL_TOLERANCE, compare_validation_results
from umat_oti.validation.job_builder import DEFAULT_ABAQUS_MODULES, DEFAULT_ABAQUS_RUN_PREFIX, _constitutive_validation_artifacts, build_validation_workspace, infer_validation_dimensions_from_source, load_validation_report


_PIPELINE_SOURCE_FILES: tuple[Path, ...] = tuple(
    Path(__file__).resolve().parent.parent / rel
    for rel in (
        "transform/source_transform.py",
        "transform/helper_lifting.py",
        "validation/job_builder.py",
        "validation/abaqus_runner.py",
        "validation/compare_results.py",
    )
)


def _pipeline_source_mtime() -> float:
    return max((p.stat().st_mtime for p in _PIPELINE_SOURCE_FILES if p.is_file()), default=0.0)


def _validation_artifact_mtime(validation_dir: Path) -> float:
    candidates = [
        validation_dir / "combined_oti_user.f90",
        validation_dir / "validation_report.json",
    ]
    return min((p.stat().st_mtime for p in candidates if p.is_file()), default=0.0)


_PIPELINE_MODULE_NAMES: tuple[str, ...] = (
    "umat_oti.transform.helper_lifting",
    "umat_oti.transform.source_transform",
    "umat_oti.validation.abaqus_runner",
    "umat_oti.validation.compare_results",
    "umat_oti.validation.job_builder",
)


def _refresh_pipeline_modules_if_stale() -> None:
    """Reload pipeline submodules when their source is newer than the cached version.

    Streamlit hot-reloads only the entrypoint module on rerun; transitively imported
    modules sit in ``sys.modules``. Without this, edits to the transformer/validation
    pipeline are invisible to a long-lived ``streamlit run`` session.
    """
    import importlib
    import sys
    global TransformResult, _directions_required, transform_umat_to_oti_from_config
    global extract_results, run_both_jobs, run_original_job, run_transformed_job
    global compare_validation_results
    global DEFAULT_DDSDDE_ABS_TOLERANCE, DEFAULT_DDSDDE_REL_TOLERANCE
    global DEFAULT_STRESS_ABS_TOLERANCE, DEFAULT_STRESS_REL_TOLERANCE
    global _constitutive_validation_artifacts, build_validation_workspace, infer_validation_dimensions_from_source, load_validation_report
    global DEFAULT_ABAQUS_MODULES, DEFAULT_ABAQUS_RUN_PREFIX

    current_mtime = _pipeline_source_mtime()
    cached_mtime = float(st.session_state.get("_pipeline_modules_mtime", 0.0))
    if current_mtime <= cached_mtime:
        return
    for name in _PIPELINE_MODULE_NAMES:
        mod = sys.modules.get(name)
        if mod is not None:
            importlib.reload(mod)
    transform_mod = sys.modules["umat_oti.transform.source_transform"]
    TransformResult = transform_mod.TransformResult
    _directions_required = transform_mod._directions_required
    transform_umat_to_oti_from_config = transform_mod.transform_umat_to_oti_from_config
    runner_mod = sys.modules["umat_oti.validation.abaqus_runner"]
    extract_results = runner_mod.extract_results
    run_both_jobs = runner_mod.run_both_jobs
    run_original_job = runner_mod.run_original_job
    run_transformed_job = runner_mod.run_transformed_job
    compare_mod = sys.modules["umat_oti.validation.compare_results"]
    compare_validation_results = compare_mod.compare_validation_results
    DEFAULT_DDSDDE_ABS_TOLERANCE = compare_mod.DEFAULT_DDSDDE_ABS_TOLERANCE
    DEFAULT_DDSDDE_REL_TOLERANCE = compare_mod.DEFAULT_DDSDDE_REL_TOLERANCE
    DEFAULT_STRESS_ABS_TOLERANCE = compare_mod.DEFAULT_STRESS_ABS_TOLERANCE
    DEFAULT_STRESS_REL_TOLERANCE = compare_mod.DEFAULT_STRESS_REL_TOLERANCE
    job_mod = sys.modules["umat_oti.validation.job_builder"]
    _constitutive_validation_artifacts = job_mod._constitutive_validation_artifacts
    build_validation_workspace = job_mod.build_validation_workspace
    infer_validation_dimensions_from_source = job_mod.infer_validation_dimensions_from_source
    load_validation_report = job_mod.load_validation_report
    DEFAULT_ABAQUS_MODULES = job_mod.DEFAULT_ABAQUS_MODULES
    DEFAULT_ABAQUS_RUN_PREFIX = job_mod.DEFAULT_ABAQUS_RUN_PREFIX
    st.session_state["_pipeline_modules_mtime"] = current_mtime


def main() -> None:
    st.set_page_config(page_title="UMAT OTIS Runner", layout="wide")
    initialize_session_state(st)
    _refresh_pipeline_modules_if_stale()
    st.title("UMAT OTIS Runner")
    st.caption("Load a completed JSON contract, run the OTIS transformation, and optionally run Abaqus validation. Advanced review tools stay hidden until they are needed.")

    advanced_mode = st.checkbox(
        "Show advanced contract editing and diagnostics",
        value=bool(st.session_state.get("ui_show_advanced", False)),
        key="ui_show_advanced",
        help="Use this when the contract needs manual review or when you are debugging a difficult UMAT.",
    )

    with st.expander("1. Load JSON contract", expanded=not bool(st.session_state.get("analysis"))):
        render_config_upload(st)
        if advanced_mode:
            st.divider()
            render_project_setup(st)
            st.divider()
            render_upload(st)
            show_file_metadata(st, st.session_state.get("file_metadata"))

    selected_arguments: list[str] = []
    mappings = dict(st.session_state.get("mappings", {}))
    routine_roles = list(st.session_state.get("routine_roles", []))
    variable_roles = list(st.session_state.get("variable_roles", []))
    region_classifications = list(st.session_state.get("region_classifications", []))
    findings_log = list(st.session_state.get("findings_log", []))
    metadata = dict(st.session_state.get("metadata", {}))
    transformation_anchors = dict(st.session_state.get("transformation_anchors") or {})

    if st.session_state.get("analysis"):
        selected_arguments = _selected_arguments_for_umat(st.session_state.get("analysis") or {}, st.session_state.get("selected_umat", ""))
        if not selected_arguments:
            selected_arguments = render_select_main_umat(st)
        if not mappings and selected_arguments:
            mappings = default_mappings(selected_arguments)
            st.session_state["mappings"] = mappings

        st.session_state["transformation_review"] = build_transformation_review(
            st.session_state.get("analysis"),
            selected_umat=st.session_state.get("selected_umat", ""),
            mappings=mappings,
            routine_roles=routine_roles,
            region_classifications=region_classifications,
            variable_roles=variable_roles,
        )
        review = st.session_state.get("transformation_review") or {}
        pipeline = _current_pipeline(mappings, routine_roles, variable_roles)

        _render_workspace_summary(st, review, pipeline)
        _show_loaded_contract_json(st)

        if advanced_mode:
            with st.expander(
                "Advanced contract review",
                expanded=bool(review.get("completion_issues"))
                or _has_unknown_routines(routine_roles)
                or _has_ambiguous_regions(region_classifications)
                or _has_unknown_variables(variable_roles),
            ):
                selected_arguments = render_select_main_umat(st)
                mappings = dict(st.session_state.get("mappings", {}))
                with st.expander("Standard UMAT mapping", expanded=False):
                    mappings = render_variable_mapping(st, selected_arguments)

                show_transformation_review(
                    st,
                    review,
                    st.session_state.get("analysis"),
                    st.session_state.get("selected_umat", ""),
                    st.session_state.get("file_metadata"),
                )

                with st.expander("Complete transformation JSON", expanded=bool(review.get("completion_issues"))):
                    transformation_anchors = render_transformation_anchor_completion(st)

                if _has_relevant_helper_routines(routine_roles, st.session_state.get("selected_umat", "")):
                    with st.expander("Helper routine review", expanded=_has_unknown_routines(routine_roles)):
                        routine_roles = render_routine_classification(st)
                if _has_ambiguous_regions(region_classifications):
                    with st.expander("Region classification", expanded=True):
                        region_classifications = render_region_classification(st)
                if _has_unknown_variables(variable_roles):
                    with st.expander("Variable ambiguities", expanded=True):
                        variable_roles = render_variable_roles(st)

                with st.expander("Status and metadata", expanded=False):
                    metadata = render_metadata(st)
                    st.divider()
                    st.subheader("Pipeline status")
                    st.session_state["accept_unknown_routine_warnings"] = st.checkbox(
                        "Accept Unknown helper routine classifications as warnings for this milestone",
                        value=st.session_state.get("accept_unknown_routine_warnings", False),
                    )
                    pipeline = _current_pipeline(mappings, routine_roles, variable_roles)
                    show_pipeline_status(st, pipeline)

                show_advanced_diagnostics(
                    st,
                    st.session_state.get("source_text", ""),
                    st.session_state.get("analysis"),
                    findings_log,
                    variable_roles,
                    routine_roles,
                    region_classifications,
                )

                with st.expander("Editable findings log", expanded=False):
                    findings_log = render_findings_log(st)

        st.divider()
        transformation_settings = _render_transformation_panel(
            st,
            selected_arguments,
            mappings,
            routine_roles,
            region_classifications,
            variable_roles,
            findings_log,
            metadata,
            pipeline,
            transformation_anchors,
            advanced_mode,
        )
        _render_abaqus_validation_panel(st, transformation_settings, advanced_mode)

        if advanced_mode:
            st.subheader("Save & Next")
            if st.button("Save OTIS project configuration"):
                _save_configuration(st, selected_arguments, mappings, routine_roles, region_classifications, variable_roles, findings_log, metadata, pipeline, transformation_settings, st.session_state.get("transformation_anchors") or {})
            if st.session_state.get("config_paths"):
                st.success("Configuration files saved.")
                st.json(st.session_state["config_paths"])
            if st.session_state.get("summary_path"):
                st.write(f"Meeting summary: `{st.session_state['summary_path']}`")
    else:
        st.info("Load a JSON configuration to begin.")


def _has_relevant_helper_routines(routine_roles: list[dict[str, object]], selected_umat: str) -> bool:
    selected = selected_umat.upper()
    return any(str(row.get("routine_name", "")).upper() not in {"", selected} for row in routine_roles)


def _has_unknown_routines(routine_roles: list[dict[str, object]]) -> bool:
    return any(str(row.get("selected_role", row.get("suggested_role", "Unknown"))) == "Unknown" for row in routine_roles)


def _has_ambiguous_regions(region_classifications: list[dict[str, object]]) -> bool:
    return any(
        str(row.get("user-selected classification", row.get("suggested classification", "Unknown"))) == "Unknown"
        for row in region_classifications
    )


def _has_unknown_variables(variable_roles: list[dict[str, object]]) -> bool:
    return any(
        str(row.get("user-selected OTIS role", row.get("suggested OTIS role", "Unknown"))) == "Unknown"
        for row in variable_roles
    )


def _render_transformation_panel(
    st_module,
    selected_arguments: list[str],
    mappings: dict[str, str],
    routine_roles: list[dict[str, object]],
    region_classifications: list[dict[str, object]],
    variable_roles: list[dict[str, object]],
    findings_log: list[dict[str, object]],
    metadata: dict[str, str],
    pipeline: dict[str, object],
    transformation_anchors: dict[str, object] | None = None,
    advanced_mode: bool = False,
) -> dict[str, object]:
    st_module.subheader("2. Run transformation")
    st_module.caption("This step only needs the loaded contract. The current NTENS and output folder can be overridden if required.")
    project = st_module.session_state.get("project") or {}
    default_output = default_transform_output_dir(project.get("workdir", st_module.session_state.get("workdir", "umat_oti_workspace")))
    if not st_module.session_state.get("transformation_output_dir"):
        st_module.session_state["transformation_output_dir"] = str(default_output)
    loaded_settings = st_module.session_state.get("transformation_settings") or {}
    if loaded_settings and not st_module.session_state.get("transformation_ntens_input") and loaded_settings.get("ntens") is not None:
        st_module.session_state["transformation_ntens_input"] = str(loaded_settings.get("ntens"))
    if loaded_settings and loaded_settings.get("order") and not st_module.session_state.get("transformation_order_input"):
        st_module.session_state["transformation_order_input"] = int(loaded_settings.get("order", 1))
    ntens_text, order, output_dir_text = _render_transformation_inputs(st_module, advanced_mode)
    ntens = _parse_ntens(ntens_text)
    transformation_settings = {
        "ntens": ntens or None,
        "ntens_source": loaded_settings.get("ntens_source", "loaded/generated config" if ntens else "missing"),
        "ntens_confidence": loaded_settings.get("ntens_confidence", "configured" if ntens else "missing"),
        "ntens_warning": loaded_settings.get("ntens_warning", ""),
        "order": int(order),
        "output_dir": output_dir_text,
    }
    st_module.session_state["transformation_settings"] = transformation_settings
    _clear_stale_transform_result_for_output(st_module, str(output_dir_text))
    if transformation_settings.get("ntens_warning"):
        st_module.caption(str(transformation_settings["ntens_warning"]))

    if st_module.button("Run OTIS transformation", type="primary"):
        _refresh_source_text_from_config_source(st_module)
        config = _build_gui_project_config(
            st_module,
            selected_arguments=selected_arguments,
            mappings=mappings,
            routine_roles=routine_roles,
            region_classifications=region_classifications,
            variable_roles=variable_roles,
            findings_log=findings_log,
            metadata=metadata,
            pipeline=pipeline,
            transformation_settings=transformation_settings,
            transformation_anchors=transformation_anchors,
        )
        output_dir = Path(output_dir_text)
        try:
            result = transform_umat_to_oti_from_config(
                st_module.session_state.get("source_text", ""),
                config,
                output_dir,
                ntens,
            )
            log_path = write_otis_run_log(output_dir=output_dir, config=config, result=result)
            session_result = _result_to_session(result)
            session_result["gui_run_log_path"] = str(log_path)
            session_result["generated_files"] = _append_unique_path(session_result.get("generated_files", []), log_path)
            st_module.session_state["transformation_run_log_path"] = str(log_path)
            st_module.session_state["transformation_result"] = session_result
            if result.success:
                _sync_validation_output_paths(
                    st_module,
                    _validation_defaults(st_module, transformation_settings),
                    transformation_settings,
                    force=True,
                )
        except Exception as exc:
            log_path = write_otis_run_log(output_dir=output_dir, config=config, error=exc)
            st_module.session_state["transformation_run_log_path"] = str(log_path)
            st_module.session_state["transformation_result"] = {
                "success": False,
                "output_dir": str(output_dir),
                "transformed_source": "",
                "transformed_source_path": "",
                "report_path": "",
                "generated_files": [str(log_path)],
                "gui_run_log_path": str(log_path),
                "blockers": [f"OTIS transformation raised {type(exc).__name__}: {exc}"],
                "warnings": [],
                "report": {},
            }

    _show_transformation_result(st_module, st_module.session_state.get("transformation_result"), advanced_mode=advanced_mode)
    return transformation_settings


def _parse_ntens(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _show_transformation_result(st_module, result: dict[str, object] | None, *, advanced_mode: bool = False) -> None:
    if not result:
        return
    if result.get("success"):
        st_module.success("Transformation completed.")
    else:
        st_module.error("Transformation blocked.")
    summary_rows = [
        {"item": "Output folder", "value": str(result.get("output_dir", ""))},
        {"item": "Transformed source", "value": str(result.get("transformed_source_path", "")) or "Not generated"},
        {"item": "Warnings", "value": str(len(result.get("warnings", [])))},
        {"item": "Blockers", "value": str(len(result.get("blockers", [])))},
    ]
    jacobian_summary = _jacobian_summary(result.get("report", {}))
    direction_summary = result.get("report", {}).get("directions_required", {}) if isinstance(result.get("report", {}), dict) else {}
    if jacobian_summary["extra_contracts"] or jacobian_summary["artifacts"]:
        summary_rows.extend(
            [
                {"item": "Extra Jacobian contracts", "value": str(jacobian_summary["extra_contracts"])},
                {"item": "Jacobian artifacts", "value": str(jacobian_summary["artifacts"])},
            ]
        )
    if isinstance(direction_summary, dict) and direction_summary:
        summary_rows.extend(
            [
                {"item": "DDSDDE directions", "value": str(direction_summary.get("ddsdde_directions", ""))},
                {"item": "Extra OTI directions", "value": str(direction_summary.get("extra_directions", ""))},
                {"item": "Total OTI directions", "value": str(direction_summary.get("total_directions", ""))},
            ]
        )
    st_module.table(summary_rows)
    log_path = Path(str(result.get("gui_run_log_path") or ""))
    if str(log_path) and log_path.is_file():
        st_module.caption(f"Run log: `{log_path}`")
        st_module.download_button(
            "Download otis_gui_run_log.json",
            data=log_path.read_bytes(),
            file_name=log_path.name,
            mime="application/json",
        )
    blockers = result.get("blockers", [])
    if blockers:
        for blocker in blockers[:3]:
            st_module.warning(str(blocker))
        if len(blockers) > 3:
            st_module.info(f"{len(blockers) - 3} more blocker(s) are hidden in advanced details.")
    warnings = result.get("warnings", [])
    if warnings and not blockers:
        st_module.info(str(warnings[0]))
        if len(warnings) > 1:
            st_module.caption(f"{len(warnings) - 1} more warning(s) are hidden in advanced details.")
    transformed_source = result.get("transformed_source", "")
    report = result.get("report", {})
    action_columns = st_module.columns(2)
    with action_columns[0]:
        if transformed_source:
            st_module.download_button(
                "Download transformed UMAT source",
                data=str(transformed_source).encode("utf-8"),
                file_name=Path(str(result.get("transformed_source_path", "umat_oti.f"))).name,
                mime="text/plain",
            )
    with action_columns[1]:
        if report:
            st_module.download_button(
                "Download transform_report.json",
                data=json.dumps(report, indent=2, sort_keys=True).encode("utf-8"),
                file_name="transform_report.json",
                mime="application/json",
            )

    if advanced_mode:
        _show_transformation_result_details(st_module, result)
    elif blockers or warnings or report or transformed_source:
        with st_module.expander("Advanced transformation details", expanded=False):
            _show_transformation_result_details(st_module, result)


def _result_to_session(result: TransformResult) -> dict[str, object]:
    return {
        "success": result.success,
        "output_dir": str(result.output_dir),
        "transformed_source": result.transformed_source,
        "transformed_source_path": str(result.transformed_source_path or ""),
        "report_path": str(result.report_path or ""),
        "generated_files": [str(path) for path in result.generated_files],
        "blockers": result.blockers,
        "warnings": result.warnings,
        "report": result.report,
    }


def _append_unique_path(values: object, path: Path) -> list[str]:
    result = [str(value) for value in values or []]
    path_text = str(path)
    if path_text not in result:
        result.append(path_text)
    return result


def _normalized_path_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(Path(text).expanduser().resolve())


def _clear_stale_transform_result_for_output(st_module, expected_output_dir: str) -> None:
    result = st_module.session_state.get("transformation_result")
    if not isinstance(result, dict) or not result:
        return
    current_output_dir = _normalized_path_text(result.get("output_dir"))
    expected_output_dir = _normalized_path_text(expected_output_dir)
    if not current_output_dir or not expected_output_dir or current_output_dir == expected_output_dir:
        return
    for key in (
        "transformation_result",
        "transformation_run_log_path",
        "validation_result",
        "validation_generated_otilib_dir",
        "validation_transformed_umat_path",
        "validation_work_dir",
        "_validation_synced_generated_otilib_dir",
        "_validation_synced_transformed_umat_path",
        "_validation_synced_work_dir",
    ):
        if key in st_module.session_state:
            st_module.session_state[key] = {} if key == "validation_result" else ""


def _render_abaqus_validation_panel(st_module, transformation_settings: dict[str, object], advanced_mode: bool = False) -> None:
    st_module.subheader("3. Run Abaqus verification")
    st_module.caption("The default action builds the verification workspace, runs both Abaqus jobs, extracts results, and compares the requested outputs.")
    defaults = _validation_defaults(st_module, transformation_settings)
    for key, value in defaults.items():
        if not st_module.session_state.get(key):
            st_module.session_state[key] = value
    _sync_validation_output_paths(st_module, defaults, transformation_settings)
    _migrate_validation_modules(st_module)
    _migrate_validation_run_prefix(st_module)
    abaqus_command, abaqus_modules, run_prefix, material_test_mode, abs_tol, rel_tol, ddsdde_abs_tol, ddsdde_rel_tol = _validation_inputs(st_module, advanced_mode)
    st_module.session_state["validation_settings"] = {
        **dict(st_module.session_state.get("validation_settings") or {}),
        "material_test_mode": material_test_mode,
        "absolute_tolerance": float(abs_tol),
        "relative_tolerance": float(rel_tol),
        "ddsdde_absolute_tolerance": float(ddsdde_abs_tol),
        "ddsdde_relative_tolerance": float(ddsdde_rel_tol),
        "compare_outputs": _validation_compare_outputs(st_module),
    }
    compare_outputs = _validation_compare_outputs(st_module)
    summary_rows = [
        {"item": "Verification mode", "value": material_test_mode},
        {"item": "Compared outputs", "value": ", ".join(compare_outputs)},
        {"item": "Original UMAT", "value": str(st_module.session_state.get("validation_original_umat_path", ""))},
        {"item": "Transformed UMAT", "value": str(st_module.session_state.get("validation_transformed_umat_path", ""))},
        {"item": "Verification folder", "value": str(st_module.session_state.get("validation_work_dir", ""))},
    ]
    st_module.table(summary_rows)
    transformed_ready = _validation_target_ready(st_module)
    if not transformed_ready:
        st_module.info("Run the transformation first, or point the verification panel at an existing transformed UMAT file.")
    if st_module.button("Run full Abaqus verification", type="primary", disabled=not transformed_ready):
        _run_full_validation(
            st_module,
            transformation_settings,
            material_test_mode,
            abaqus_command,
            abaqus_modules,
            run_prefix,
            abs_tol,
            rel_tol,
            ddsdde_abs_tol,
            ddsdde_rel_tol,
        )
    if advanced_mode:
        buttons = st_module.columns(5)
        with buttons[0]:
            if st_module.button("Build verification job"):
                _build_validation_job_from_gui(st_module, transformation_settings, material_test_mode, abaqus_command, abaqus_modules, run_prefix)
        with buttons[1]:
            if st_module.button("Run original Abaqus job"):
                _run_validation_action(st_module, "original", transformation_settings, material_test_mode, abaqus_command, abaqus_modules, run_prefix)
        with buttons[2]:
            if st_module.button("Run transformed Abaqus job"):
                _run_validation_action(st_module, "transformed", transformation_settings, material_test_mode, abaqus_command, abaqus_modules, run_prefix)
        with buttons[3]:
            if st_module.button("Run both"):
                _run_validation_action(st_module, "both", transformation_settings, material_test_mode, abaqus_command, abaqus_modules, run_prefix)
        with buttons[4]:
            if st_module.button("Extract results"):
                _run_validation_action(st_module, "extract", transformation_settings, material_test_mode, abaqus_command, abaqus_modules, run_prefix)
        if st_module.button("Compare results"):
            validation_dir = Path(str(st_module.session_state.get("validation_work_dir", "")))
            result = compare_validation_results(validation_dir, float(abs_tol), float(rel_tol), float(ddsdde_abs_tol), float(ddsdde_rel_tol))
            st_module.session_state["validation_result"] = {"comparison": result.to_json(), "report": load_validation_report(validation_dir)}
    else:
        with st_module.expander("Override verification settings", expanded=False):
            _validation_inputs(st_module, advanced_mode=True)
    _show_validation_status(st_module, advanced_mode=advanced_mode)


def _validation_defaults(st_module, transformation_settings: dict[str, object]) -> dict[str, object]:
    project = st_module.session_state.get("project") or {}
    project_workdir = Path(str(project.get("workdir", st_module.session_state.get("workdir", "umat_oti_workspace"))))
    transform_result = st_module.session_state.get("transformation_result") or {}
    transform_output_dir = transform_result.get("output_dir") or transformation_settings.get("output_dir") or default_transform_output_dir(project_workdir)
    validation_settings = dict(st_module.session_state.get("validation_settings") or {})
    return {
        "validation_abaqus_command": "abaqus",
        "validation_abaqus_modules": DEFAULT_ABAQUS_MODULES,
        "validation_run_prefix": DEFAULT_ABAQUS_RUN_PREFIX,
        "validation_work_dir": str(default_validation_work_dir(project_workdir, transform_output_dir)),
        "validation_original_umat_path": _current_original_umat_path(st_module),
        "validation_transformed_umat_path": str(transform_result.get("transformed_source_path", "")),
        "validation_generated_otilib_dir": str(transform_output_dir),
        "validation_material_test_mode": str(validation_settings.get("material_test_mode", "single element tension")),
        "validation_abs_tolerance": _float_or_default(validation_settings.get("absolute_tolerance"), DEFAULT_STRESS_ABS_TOLERANCE),
        "validation_rel_tolerance": _float_or_default(validation_settings.get("relative_tolerance"), DEFAULT_STRESS_REL_TOLERANCE),
        "validation_ddsdde_abs_tolerance": _float_or_default(validation_settings.get("ddsdde_absolute_tolerance"), DEFAULT_DDSDDE_ABS_TOLERANCE),
        "validation_ddsdde_rel_tolerance": _float_or_default(validation_settings.get("ddsdde_relative_tolerance"), DEFAULT_DDSDDE_REL_TOLERANCE),
    }


def _current_original_umat_path(st_module) -> str:
    source = st_module.session_state.get("source", {})
    source_metadata = st_module.session_state.get("file_metadata") or {}
    if isinstance(source, dict):
        source_path_text = str(
            source.get("selected_umat_file") or source.get("file") or source.get("uploaded_file") or ""
        ).strip()
        if source_path_text:
            source_path = Path(source_path_text).expanduser()
            if not source_path.is_absolute():
                source_path = (Path(__file__).resolve().parents[3] / source_path).resolve()
            return str(source_path)
    return str(
        st_module.session_state.get("selected_umat_file")
        or st_module.session_state.get("source_path")
        or source_metadata.get("file_path")
        or ""
    )


def _set_synced_path(st_module, key: str, marker_key: str, value: str, *, force: bool = False) -> None:
    if not value:
        return
    current = st_module.session_state.get(key)
    previous_synced = st_module.session_state.get(marker_key)
    if force or current in {"", None} or current == previous_synced or previous_synced != value:
        st_module.session_state[key] = value
        st_module.session_state[marker_key] = value


def _sync_validation_output_paths(st_module, defaults: dict[str, object], transformation_settings: dict[str, object], *, force: bool = False) -> None:
    project = st_module.session_state.get("project") or {}
    project_workdir = Path(str(project.get("workdir", st_module.session_state.get("workdir", "umat_oti_workspace"))))
    transform_result = st_module.session_state.get("transformation_result") or {}
    transform_output_dir = str(transform_result.get("output_dir") or transformation_settings.get("output_dir") or defaults.get("validation_generated_otilib_dir", ""))
    transformed_source_path = str(transform_result.get("transformed_source_path") or defaults.get("validation_transformed_umat_path") or "")
    original_umat_path = _current_original_umat_path(st_module) or str(defaults.get("validation_original_umat_path") or "")
    transformed_changed = bool(
        transformed_source_path
        and st_module.session_state.get("_validation_synced_transformed_umat_path") != transformed_source_path
    )
    _set_synced_path(
        st_module,
        "validation_original_umat_path",
        "_validation_synced_original_umat_path",
        original_umat_path,
        force=force,
    )
    _set_synced_path(
        st_module,
        "validation_transformed_umat_path",
        "_validation_synced_transformed_umat_path",
        transformed_source_path,
        force=force or transformed_changed,
    )
    _set_synced_path(
        st_module,
        "validation_generated_otilib_dir",
        "_validation_synced_generated_otilib_dir",
        transform_output_dir,
        force=force or transformed_changed,
    )
    current_validation_dir = st_module.session_state.get("validation_work_dir")
    next_validation_dir = str(migrate_validation_work_dir(project_workdir, current_validation_dir, transform_output_dir))
    previous_validation_dir = st_module.session_state.get("_validation_synced_work_dir")
    if (
        force
        or transformed_changed
        or current_validation_dir in {"", None}
        or current_validation_dir == previous_validation_dir
        or is_legacy_generated_validation_dir(project_workdir, current_validation_dir)
    ):
        st_module.session_state["validation_work_dir"] = next_validation_dir
        st_module.session_state["_validation_synced_work_dir"] = next_validation_dir


def _migrate_validation_run_prefix(st_module) -> None:
    legacy_prefix = "srun --partition=compute1 --ntasks=1 --cpus-per-task=2"
    if st_module.session_state.get("validation_run_prefix") == legacy_prefix:
        st_module.session_state["validation_run_prefix"] = DEFAULT_ABAQUS_RUN_PREFIX


def _migrate_validation_modules(st_module) -> None:
    if st_module.session_state.get("validation_abaqus_modules") == "abaqus/2024":
        st_module.session_state["validation_abaqus_modules"] = DEFAULT_ABAQUS_MODULES


def _build_validation_job_from_gui(st_module, transformation_settings: dict[str, object], material_test_mode: str, abaqus_command: str, abaqus_modules: str, run_prefix: str):
    ntens = _parse_ntens(transformation_settings.get("ntens") or st_module.session_state.get("transformation_ntens_input"))
    validation_dir = Path(str(st_module.session_state.get("validation_work_dir", "")))
    original_path = Path(str(st_module.session_state.get("validation_original_umat_path", "")))
    transformed_path = Path(str(st_module.session_state.get("validation_transformed_umat_path", "")))
    generated_dir = Path(str(st_module.session_state.get("validation_generated_otilib_dir", "")))
    mappings = dict(st_module.session_state.get("mappings", {}))
    validation_settings = dict(st_module.session_state.get("validation_settings") or {})
    statev_name = str(mappings.get("statev") or "STATEV")
    ddsdde_name = str(mappings.get("ddsdde") or "DDSDDE")
    nstatv, nprops = _validation_dimensions_from_gui(st_module, original_path)
    if ntens <= 0:
        st_module.error("NTENS is required before building verification files.")
        return None
    if not original_path.is_file():
        st_module.error(f"Original UMAT source was not found: `{original_path}`")
        return None
    if not transformed_path.is_file():
        st_module.error(f"Transformed UMAT source was not found: `{transformed_path}`")
        return None
    result = build_validation_workspace(
        validation_dir=validation_dir,
        original_umat=original_path,
        transformed_umat=transformed_path,
        generated_dir=generated_dir,
        project_config=_current_validation_project_config(st_module),
        ntens=ntens,
        abaqus_command=abaqus_command,
        abaqus_modules=abaqus_modules,
        run_prefix=run_prefix,
        material_test_mode=material_test_mode,
        nstatv=nstatv,
        nprops=nprops,
        statev_name=statev_name,
        ddsdde_name=ddsdde_name,
        compare_outputs=_validation_compare_outputs(st_module),
        comparison_abs_tolerance=float(st_module.session_state.get("validation_abs_tolerance", DEFAULT_STRESS_ABS_TOLERANCE)),
        comparison_rel_tolerance=float(st_module.session_state.get("validation_rel_tolerance", DEFAULT_STRESS_REL_TOLERANCE)),
        comparison_ddsdde_abs_tolerance=float(st_module.session_state.get("validation_ddsdde_abs_tolerance", DEFAULT_DDSDDE_ABS_TOLERANCE)),
        comparison_ddsdde_rel_tolerance=float(st_module.session_state.get("validation_ddsdde_rel_tolerance", DEFAULT_DDSDDE_REL_TOLERANCE)),
    )
    st_module.session_state["validation_result"] = {"build": {"files": result.files, "warnings": result.warnings}, "report": result.report}
    st_module.session_state["validation_settings"] = {**validation_settings, "compare_outputs": _validation_compare_outputs(st_module)}
    return result


def _run_validation_action(st_module, action: str, transformation_settings: dict[str, object], material_test_mode: str, abaqus_command: str, abaqus_modules: str, run_prefix: str) -> None:
    validation_dir = Path(str(st_module.session_state.get("validation_work_dir", "")))
    if not _ensure_validation_workspace(st_module, action, transformation_settings, material_test_mode, abaqus_command, abaqus_modules, run_prefix):
        return
    if action == "original":
        result = run_original_job(validation_dir, abaqus_command, abaqus_modules, run_prefix)
        payload = {"original_run": result.to_json()}
    elif action == "transformed":
        result = run_transformed_job(validation_dir, abaqus_command, abaqus_modules, run_prefix)
        payload = {"transformed_run": result.to_json()}
    elif action == "both":
        payload = {"both_runs": run_both_jobs(validation_dir, abaqus_command, abaqus_modules, run_prefix)}
    elif action == "extract":
        result = extract_results(validation_dir, abaqus_command, abaqus_modules, run_prefix)
        payload = {"extraction": result.to_json()}
    else:
        payload = {}
    payload["report"] = load_validation_report(validation_dir)
    st_module.session_state["validation_result"] = payload


def _ensure_validation_workspace(st_module, action: str, transformation_settings: dict[str, object], material_test_mode: str, abaqus_command: str, abaqus_modules: str, run_prefix: str) -> bool:
    validation_dir = Path(str(st_module.session_state.get("validation_work_dir", "")))
    original_path = Path(str(st_module.session_state.get("validation_original_umat_path", "")))
    transformed_path = Path(str(st_module.session_state.get("validation_transformed_umat_path", "")))
    generated_dir = Path(str(st_module.session_state.get("validation_generated_otilib_dir", "")))
    ntens = _parse_ntens(transformation_settings.get("ntens") or st_module.session_state.get("transformation_ntens_input"))
    nstatv, nprops = _validation_dimensions_from_gui(st_module, original_path)
    mappings = dict(st_module.session_state.get("mappings", {}))
    statev_name = str(mappings.get("statev") or "STATEV")
    ddsdde_name = str(mappings.get("ddsdde") or "DDSDDE")
    compare_outputs = _validation_compare_outputs(st_module)
    constitutive_preview = _current_constitutive_validation_preview(st_module, transformation_settings)
    required_scripts = {
        "original": ["run_original_abaqus.sh"],
        "transformed": ["run_otis_abaqus.sh"],
        "both": ["run_original_abaqus.sh", "run_otis_abaqus.sh"],
        "extract": ["extract_results.py"],
    }.get(action, [])
    if _validation_workspace_is_current(
        validation_dir,
        required_scripts,
        action,
        ntens=ntens,
        nstatv=nstatv,
        nprops=nprops,
        material_test_mode=material_test_mode,
        original_path=original_path,
        transformed_path=transformed_path,
        generated_dir=generated_dir,
        statev_name=statev_name,
        ddsdde_name=ddsdde_name,
        compare_outputs=compare_outputs,
        constitutive_preview=constitutive_preview,
    ):
        return True
    pipeline_mtime = _pipeline_source_mtime()
    if (
        pipeline_mtime > 0.0
        and transformed_path.is_file()
        and transformed_path.stat().st_mtime < pipeline_mtime
    ):
        st_module.error(
            "Transformed UMAT is older than the OTIS pipeline source. "
            "Click **Run OTIS transformation** to regenerate it before validating."
        )
        return False
    _build_validation_job_from_gui(st_module, transformation_settings, material_test_mode, abaqus_command, abaqus_modules, run_prefix)
    validation_dir = Path(str(st_module.session_state.get("validation_work_dir", "")))
    if _validation_workspace_is_current(
        validation_dir,
        required_scripts,
        action,
        ntens=ntens,
        nstatv=nstatv,
        nprops=nprops,
        material_test_mode=material_test_mode,
        original_path=original_path,
        transformed_path=transformed_path,
        generated_dir=generated_dir,
        statev_name=statev_name,
        ddsdde_name=ddsdde_name,
        compare_outputs=compare_outputs,
        constitutive_preview=constitutive_preview,
    ):
        return True
    st_module.error("Verification files could not be built for the requested Abaqus action.")
    return False


def _validation_workspace_is_current(
    validation_dir: Path,
    required_scripts: list[str],
    action: str,
    *,
    ntens: int,
    nstatv: int,
    nprops: int,
    material_test_mode: str,
    original_path: Path,
    transformed_path: Path,
    generated_dir: Path,
    statev_name: str,
    ddsdde_name: str,
    compare_outputs: list[str],
    constitutive_preview: dict[str, Any],
) -> bool:
    if not validation_dir.is_dir() or not all((validation_dir / name).is_file() for name in required_scripts):
        return False
    report = load_validation_report(validation_dir)
    if not report:
        return False
    pipeline_mtime = _pipeline_source_mtime()
    artifact_mtime = _validation_artifact_mtime(validation_dir)
    if pipeline_mtime > 0.0 and artifact_mtime > 0.0 and pipeline_mtime > artifact_mtime:
        return False
    if report.get("ntens") != ntens or report.get("nstatv") != nstatv or report.get("nprops") != nprops:
        return False
    if report.get("material_test_mode") != material_test_mode:
        return False
    report_settings = report.get("comparison_settings", {}) if isinstance(report.get("comparison_settings"), dict) else {}
    report_compare_outputs = [str(value).upper() for value in report_settings.get("compare_outputs", []) if str(value).strip()]
    if report_compare_outputs != [str(value).upper() for value in compare_outputs if str(value).strip()]:
        return False
    if _normalized_validation_path(report.get("original_umat_path")) != _normalized_validation_path(original_path):
        return False
    if _normalized_validation_path(report.get("transformed_umat_path")) != _normalized_validation_path(transformed_path):
        return False
    if _normalized_validation_path(report.get("generated_otilib_dir")) != _normalized_validation_path(generated_dir):
        return False
    ddsdde_validation = report.get("ddsdde_validation", {}) if isinstance(report.get("ddsdde_validation"), dict) else {}
    if ddsdde_validation.get("status") != "configured":
        return False
    if ddsdde_validation.get("state_variable_name") != statev_name or ddsdde_validation.get("ddsdde_name") != ddsdde_name:
        return False
    if ddsdde_validation.get("user_state_variable_count") != nstatv:
        return False
    constitutive_validation = report.get("constitutive_validation", {}) if isinstance(report.get("constitutive_validation"), dict) else {}
    if int(constitutive_validation.get("component_count") or 0) != int(constitutive_preview.get("component_count") or 0):
        return False
    if int(constitutive_validation.get("comparable_artifact_count") or 0) != int(constitutive_preview.get("comparable_artifact_count") or 0):
        return False
    if int(constitutive_validation.get("preview_only_artifact_count") or 0) != int(constitutive_preview.get("preview_only_artifact_count") or 0):
        return False
    if action in {"transformed", "both"}:
        run_otis_script = validation_dir / "run_otis_abaqus.sh"
        if not (validation_dir / "combined_oti_user.f90").is_file():
            return False
        if run_otis_script.is_file() and "combined_oti_user.f90" not in run_otis_script.read_text(encoding="utf-8", errors="replace"):
            return False
    return True


def _validation_dimensions_from_gui(st_module, original_path: Path) -> tuple[int, int]:
    mappings = dict(st_module.session_state.get("mappings", {}))
    source_text = original_path.read_text(encoding="utf-8", errors="replace") if original_path.is_file() else ""
    return infer_validation_dimensions_from_source(
        source_text,
        statev_name=str(mappings.get("statev") or "STATEV"),
        props_name=str(mappings.get("props") or "PROPS"),
    )


def _normalized_validation_path(value: object) -> str:
    text = str(value or "")
    return str(Path(text).resolve()) if text else ""


def _show_validation_status(st_module, *, advanced_mode: bool = False) -> None:
    result = st_module.session_state.get("validation_result") or {}
    if not (isinstance(result, dict) and result.get("report")):
        return
    validation_dir_text = str(st_module.session_state.get("validation_work_dir", ""))
    report = result.get("report")
    comparison_report = _load_json_path(Path(validation_dir_text) / "comparison_report.json") if validation_dir_text else {}
    if report or comparison_report:
        st_module.subheader("Verification status")
        _show_validation_summary(st_module, report if isinstance(report, dict) else {}, comparison_report)
        if advanced_mode:
            if report:
                st_module.json(report)
            if comparison_report:
                with st_module.expander("Comparison report", expanded=False):
                    st_module.json(comparison_report)
            _show_validation_run_messages(st_module, report if isinstance(report, dict) else {})
        else:
            with st_module.expander("Advanced verification details", expanded=False):
                if report:
                    st_module.json(report)
                if comparison_report:
                    st_module.json(comparison_report)
                _show_validation_run_messages(st_module, report if isinstance(report, dict) else {})
    generated_files = report.get("generated_files", {}) if isinstance(report, dict) else {}
    if validation_dir_text:
        validation_dir = Path(validation_dir_text)
        generated_files = {
            **generated_files,
            "original_results_json": str(validation_dir / "original_results.json"),
            "otis_results_json": str(validation_dir / "otis_results.json"),
            "comparison_report_json": str(validation_dir / "comparison_report.json"),
            "comparison_report_md": str(validation_dir / "comparison_report.md"),
            "validation_report_json": str(validation_dir / "validation_report.json"),
            "validation_report_md": str(validation_dir / "validation_report.md"),
        }
    if generated_files and advanced_mode:
        with st_module.expander("Verification Files", expanded=False):
            for label, value in generated_files.items():
                path = Path(str(value))
                if path.is_file():
                    st_module.write(f"{label}: `{path}`")
                else:
                    st_module.write(f"{label}: missing `{path}`")
    if isinstance(result, dict):
        for value in result.values():
            if isinstance(value, dict) and value.get("message"):
                st_module.warning(str(value["message"]))


def _show_validation_run_messages(st_module, report: dict[str, object]) -> None:
    for key in ("original_run_status", "transformed_run_status", "extraction_status"):
        status = report.get(key, {})
        if not isinstance(status, dict):
            continue
        message = status.get("message")
        stderr_excerpt = status.get("stderr_excerpt")
        if message:
            st_module.warning(str(message))
        if stderr_excerpt:
            with st_module.expander(f"{key} stderr", expanded=False):
                st_module.code(str(stderr_excerpt))


def _render_workspace_summary(st_module, review: dict[str, Any], pipeline: dict[str, object]) -> None:
    file_metadata = dict(st_module.session_state.get("file_metadata") or {})
    loaded_project_config = dict(st_module.session_state.get("loaded_project_config") or {})
    transformation_settings = dict(st_module.session_state.get("transformation_settings") or {})
    validation_settings = dict(st_module.session_state.get("validation_settings") or {})
    comparison_report = _load_json_path(Path(str(st_module.session_state.get("validation_work_dir", ""))) / "comparison_report.json")
    transform_result = st_module.session_state.get("transformation_result") or {}
    transform_label = "Done" if transform_result.get("success") else "Blocked" if transform_result else "Not run"
    validation_label = _validation_status_label(comparison_report)
    contract_summary = _jacobian_summary(loaded_project_config)
    direction_summary = _current_direction_summary(st_module, transformation_settings)
    constitutive_preview = _current_constitutive_validation_preview(st_module, transformation_settings)

    with st_module.container(border=True):
        st_module.subheader("Current UMAT")
        metrics = st_module.columns(4)
        metrics[0].metric("Contract", "Loaded")
        metrics[1].metric("Main UMAT", str(st_module.session_state.get("selected_umat") or "Not set"))
        metrics[2].metric("Transform", transform_label)
        metrics[3].metric("Verification", validation_label)
        st_module.table(
            _workspace_summary_rows(
                file_metadata=file_metadata,
                transformation_settings=transformation_settings,
                validation_settings=validation_settings,
                compare_outputs=_validation_compare_outputs(st_module),
                contract_summary=contract_summary,
                direction_summary=direction_summary,
                constitutive_preview=constitutive_preview,
            )
        )
        missing = list(pipeline.get("missing_information", []) or [])
        action_needed = list(review.get("action_needed", []) or [])
        if pipeline.get("ready_for_transformation") and not action_needed:
            st_module.success("Ready to run the transformation.")


def _show_loaded_contract_json(st_module) -> None:
    label = st_module.session_state.get("loaded_input_json_label")
    payload = st_module.session_state.get("loaded_input_json")
    if not label or payload is None:
        return
    with st_module.expander(f"Input JSON: {label}", expanded=False):
        if isinstance(payload, (dict, list)):
            st_module.json(payload)
        else:
            st_module.code(str(payload), language="json")


def _render_transformation_inputs(st_module, advanced_mode: bool) -> tuple[str, int, str]:
    if advanced_mode:
        columns = st_module.columns([1, 1, 3])
        with columns[0]:
            ntens_text = st_module.text_input(
                "NTENS",
                key="transformation_ntens_input",
                placeholder="Enter NTENS, the number of strain/stress components for this UMAT.",
            )
        with columns[1]:
            order = st_module.number_input("OTI order", min_value=1, max_value=1, step=1, key="transformation_order_input")
        with columns[2]:
            output_dir_text = st_module.text_input("Output directory", key="transformation_output_dir")
        _show_jacobian_change_preview(st_module, {"ntens": ntens_text})
        return ntens_text, int(order), output_dir_text

    ntens_text = str(st_module.session_state.get("transformation_ntens_input", ""))
    order = int(st_module.session_state.get("transformation_order_input", 1) or 1)
    output_dir_text = str(st_module.session_state.get("transformation_output_dir", ""))
    direction_summary = _current_direction_summary(st_module, {"ntens": ntens_text})
    input_rows = [
        {"item": "NTENS", "value": ntens_text or "Missing"},
        {"item": "OTI order", "value": str(order)},
        {"item": "Output directory", "value": output_dir_text},
    ]
    if direction_summary:
        input_rows.extend(
            [
                {"item": "DDSDDE directions", "value": str(direction_summary.get("ddsdde_directions", ""))},
                {"item": "Extra OTI directions", "value": str(direction_summary.get("extra_directions", ""))},
                {"item": "Total OTI directions", "value": str(direction_summary.get("total_directions", ""))},
            ]
        )
    st_module.table(
        input_rows
    )
    with st_module.expander("Override transformation settings", expanded=not bool(ntens_text)):
        columns = st_module.columns([1, 1, 3])
        with columns[0]:
            ntens_text = st_module.text_input(
                "NTENS",
                key="transformation_ntens_input",
                placeholder="Enter NTENS, the number of strain/stress components for this UMAT.",
            )
        with columns[1]:
            order = st_module.number_input("OTI order", min_value=1, max_value=1, step=1, key="transformation_order_input")
        with columns[2]:
            output_dir_text = st_module.text_input("Output directory", key="transformation_output_dir")
    _show_jacobian_change_preview(st_module, {"ntens": ntens_text})
    return ntens_text, int(order), output_dir_text


def _show_transformation_result_details(st_module, result: dict[str, object]) -> None:
    warnings = result.get("warnings", [])
    if warnings:
        st_module.subheader("Warnings")
        for warning in warnings:
            st_module.write(str(warning))
    report = result.get("report", {})
    jacobian_contract_rows = _jacobian_contract_rows(report)
    jacobian_artifact_rows = _jacobian_artifact_rows(report)
    direction_rows = _direction_slot_rows(report.get("directions_required", {}) if isinstance(report, dict) else {})
    constitutive_preview_rows = _constitutive_preview_rows(_current_constitutive_validation_preview(st_module))
    if jacobian_contract_rows or jacobian_artifact_rows:
        st_module.subheader("Jacobian Outputs")
        if jacobian_contract_rows:
            st_module.caption("Extra Jacobian contracts")
            st_module.dataframe(jacobian_contract_rows, width="stretch", hide_index=True)
        if jacobian_artifact_rows:
            st_module.caption("Jacobian artifacts")
            st_module.dataframe(jacobian_artifact_rows, width="stretch", hide_index=True)
    if direction_rows or constitutive_preview_rows:
        st_module.subheader("Jacobian Change Preview")
        if direction_rows:
            st_module.caption("Extra direction slot assignments")
            st_module.dataframe(direction_rows, width="stretch", hide_index=True)
        if constitutive_preview_rows:
            st_module.caption("Abaqus constitutive comparison preview")
            st_module.dataframe(constitutive_preview_rows, width="stretch", hide_index=True)
    if report:
        st_module.subheader("Transform report")
        st_module.json(report)
    transformed_source = result.get("transformed_source", "")
    if transformed_source:
        st_module.subheader("Transformed source preview")
        st_module.code(str(transformed_source), language="fortran")
    generated_files = [Path(str(path)) for path in result.get("generated_files", [])]
    if generated_files:
        st_module.subheader("Generated files")
        for path in generated_files:
            if not path.is_file():
                st_module.write(f"Missing: `{path}`")
                continue
            st_module.write(f"`{path}`")
            st_module.download_button(
                f"Download {path.name}",
                data=path.read_bytes(),
                file_name=path.name,
                mime="text/plain",
                key=f"download_{path.name}_{abs(hash(str(path)))}",
            )


def _validation_inputs(st_module, advanced_mode: bool) -> tuple[str, str, str, str, float, float, float, float]:
    if advanced_mode:
        command_col, module_col, prefix_col = st_module.columns([2, 2, 3])
        with command_col:
            abaqus_command = st_module.text_input("Abaqus command", key="validation_abaqus_command")
        with module_col:
            abaqus_modules = st_module.text_input("Environment modules", key="validation_abaqus_modules")
        with prefix_col:
            run_prefix = st_module.text_input("Run prefix", key="validation_run_prefix")
        mode_col, abs_col, rel_col = st_module.columns([2, 2, 2])
        with mode_col:
            material_test_mode = st_module.selectbox(
                "Material test mode",
                [
                    "single element tension",
                    "single element shear",
                    "single element mixed strain",
                    "single element plastic tension",
                    "single element plastic finite strain tension",
                ],
                key="validation_material_test_mode",
            )
        with abs_col:
            abs_tol = st_module.number_input("Stress abs tolerance", min_value=0.0, value=float(st_module.session_state.get("validation_abs_tolerance", DEFAULT_STRESS_ABS_TOLERANCE)), format="%.1e", key="validation_abs_tolerance")
        with rel_col:
            rel_tol = st_module.number_input("Stress rel tolerance", min_value=0.0, value=float(st_module.session_state.get("validation_rel_tolerance", DEFAULT_STRESS_REL_TOLERANCE)), format="%.1e", key="validation_rel_tolerance")
        ddsdde_abs_col, ddsdde_rel_col = st_module.columns(2)
        with ddsdde_abs_col:
            ddsdde_abs_tol = st_module.number_input("DDSDDE abs tolerance", min_value=0.0, value=float(st_module.session_state.get("validation_ddsdde_abs_tolerance", DEFAULT_DDSDDE_ABS_TOLERANCE)), format="%.1e", key="validation_ddsdde_abs_tolerance")
        with ddsdde_rel_col:
            ddsdde_rel_tol = st_module.number_input("DDSDDE rel tolerance", min_value=0.0, value=float(st_module.session_state.get("validation_ddsdde_rel_tolerance", DEFAULT_DDSDDE_REL_TOLERANCE)), format="%.1e", key="validation_ddsdde_rel_tolerance")
        st_module.text_input("Verification work directory", key="validation_work_dir")
        st_module.text_input("Original UMAT source path", key="validation_original_umat_path")
        st_module.text_input("Transformed UMAT source path", key="validation_transformed_umat_path")
        st_module.text_input("Generated OTILIB files directory", key="validation_generated_otilib_dir")
        return (
            str(abaqus_command),
            str(abaqus_modules),
            str(run_prefix),
            str(material_test_mode),
            float(abs_tol),
            float(rel_tol),
            float(ddsdde_abs_tol),
            float(ddsdde_rel_tol),
        )

    return (
        str(st_module.session_state.get("validation_abaqus_command", "abaqus")),
        str(st_module.session_state.get("validation_abaqus_modules", DEFAULT_ABAQUS_MODULES)),
        str(st_module.session_state.get("validation_run_prefix", DEFAULT_ABAQUS_RUN_PREFIX)),
        str(st_module.session_state.get("validation_material_test_mode", "single element tension")),
        float(st_module.session_state.get("validation_abs_tolerance", DEFAULT_STRESS_ABS_TOLERANCE)),
        float(st_module.session_state.get("validation_rel_tolerance", DEFAULT_STRESS_REL_TOLERANCE)),
        float(st_module.session_state.get("validation_ddsdde_abs_tolerance", DEFAULT_DDSDDE_ABS_TOLERANCE)),
        float(st_module.session_state.get("validation_ddsdde_rel_tolerance", DEFAULT_DDSDDE_REL_TOLERANCE)),
    )


def _run_full_validation(
    st_module,
    transformation_settings: dict[str, object],
    material_test_mode: str,
    abaqus_command: str,
    abaqus_modules: str,
    run_prefix: str,
    abs_tol: float,
    rel_tol: float,
    ddsdde_abs_tol: float,
    ddsdde_rel_tol: float,
) -> None:
    _refresh_source_text_from_config_source(st_module)
    build_result = _build_validation_job_from_gui(st_module, transformation_settings, material_test_mode, abaqus_command, abaqus_modules, run_prefix)
    if build_result is None:
        return
    validation_dir = Path(str(st_module.session_state.get("validation_work_dir", "")))
    run_both_jobs(validation_dir, abaqus_command, abaqus_modules, run_prefix)
    extract_results(validation_dir, abaqus_command, abaqus_modules, run_prefix)
    comparison = compare_validation_results(validation_dir, float(abs_tol), float(rel_tol), float(ddsdde_abs_tol), float(ddsdde_rel_tol))
    st_module.session_state["validation_result"] = {"comparison": comparison.to_json(), "report": load_validation_report(validation_dir)}


def _refresh_source_text_from_config_source(st_module) -> None:
    source_path_text = _current_original_umat_path(st_module)
    if not source_path_text:
        return
    source_path = Path(source_path_text).expanduser()
    if not source_path.is_absolute():
        source_path = (Path(__file__).resolve().parents[3] / source_path).resolve()
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


def _validation_compare_outputs(st_module) -> list[str]:
    validation_settings = dict(st_module.session_state.get("validation_settings") or {})
    values = validation_settings.get("compare_outputs")
    default_outputs = ["STRESS", "STATEV", "DDSDDE", "CONVERGENCE"]
    if not isinstance(values, list):
        normalized = list(default_outputs)
    else:
        normalized = [str(value).upper() for value in values if str(value).strip()]
        if not normalized:
            normalized = list(default_outputs)
    constitutive_preview = _current_constitutive_validation_preview(st_module)
    if constitutive_preview.get("available") and "CONSTITUTIVE_JACOBIANS" not in normalized:
        if "CONVERGENCE" in normalized:
            normalized.insert(normalized.index("CONVERGENCE"), "CONSTITUTIVE_JACOBIANS")
        else:
            normalized.append("CONSTITUTIVE_JACOBIANS")
    return normalized


def _current_validation_project_config(st_module) -> dict[str, Any] | None:
    payload = st_module.session_state.get("loaded_project_config")
    return dict(payload) if isinstance(payload, dict) else None


def _current_direction_summary(st_module, transformation_settings: dict[str, object] | None = None) -> dict[str, Any]:
    transform_result = st_module.session_state.get("transformation_result") or {}
    report = transform_result.get("report") if isinstance(transform_result.get("report"), dict) else {}
    directions = report.get("directions_required") if isinstance(report.get("directions_required"), dict) else {}
    if directions:
        return directions
    project_config = _current_validation_project_config(st_module)
    ntens = _parse_ntens((transformation_settings or {}).get("ntens") or st_module.session_state.get("transformation_ntens_input"))
    if not isinstance(project_config, dict) or ntens <= 0:
        return {}
    return _directions_required(ntens, project_config)


def _current_constitutive_validation_preview(st_module, transformation_settings: dict[str, object] | None = None) -> dict[str, Any]:
    project_config = _current_validation_project_config(st_module)
    source_text = str(st_module.session_state.get("source_text") or "")
    ntens = _parse_ntens((transformation_settings or {}).get("ntens") or st_module.session_state.get("transformation_ntens_input"))
    if not isinstance(project_config, dict) or not source_text or ntens <= 0:
        return {}
    original_path = Path(_current_original_umat_path(st_module) or st_module.session_state.get("validation_original_umat_path") or "")
    nstatv, _ = _validation_dimensions_from_gui(st_module, original_path)
    return _constitutive_validation_artifacts(project_config, source_text, nstatv, ntens * ntens)


def _validation_target_ready(st_module) -> bool:
    transformed_path = Path(str(st_module.session_state.get("validation_transformed_umat_path", "")))
    return transformed_path.is_file()


def _show_validation_summary(st_module, report: dict[str, Any], comparison_report: dict[str, Any]) -> None:
    status = _validation_status_label(comparison_report)
    if comparison_report.get("pass"):
        st_module.success("Verification passed.")
    elif comparison_report:
        st_module.error("Verification failed.")
    elif report:
        st_module.info("Verification workspace is configured, but no comparison report is available yet.")
    metrics = st_module.columns(5)
    metrics[0].metric("Overall", status)
    metrics[1].metric("Stress", _component_status_label(comparison_report.get("stress_comparison")))
    metrics[2].metric("STATEV", _component_status_label(comparison_report.get("state_variable_comparison")))
    metrics[3].metric("DDSDDE", _component_status_label(comparison_report.get("ddsdde_comparison")))
    metrics[4].metric("Constitutive", _component_status_label(comparison_report.get("constitutive_comparison")))

    overview_frame = _validation_overview_frame(comparison_report)
    if not overview_frame.empty:
        st_module.caption("Difference overview")
        diff_columns = st_module.columns(2)
        with diff_columns[0]:
            st_module.caption("Maximum absolute difference")
            st_module.bar_chart(overview_frame.set_index("check")[["max_abs_difference", "absolute_tolerance"]])
        with diff_columns[1]:
            st_module.caption("Maximum relative difference")
            st_module.bar_chart(overview_frame.set_index("check")[["max_rel_difference", "relative_tolerance"]])

    tab_renderers: list[tuple[str, Any]] = []
    if _comparison_vector_frame(comparison_report.get("stress_comparison"), ("original_values", "original_final_stress"), ("otis_values", "otis_final_stress")).shape[0] > 0:
        tab_renderers.append(("Stress", lambda tab: _show_vector_comparison_plot(tab, "Stress components", comparison_report.get("stress_comparison"), ("original_values", "original_final_stress"), ("otis_values", "otis_final_stress"))))
    if _comparison_vector_frame(comparison_report.get("state_variable_comparison"), ("original_values",), ("otis_values",)).shape[0] > 0:
        tab_renderers.append(("STATEV", lambda tab: _show_vector_comparison_plot(tab, "State variable components", comparison_report.get("state_variable_comparison"), ("original_values",), ("otis_values",))))
    if _ddsdde_increment_frame(comparison_report.get("ddsdde_comparison")).shape[0] > 0:
        tab_renderers.append(("DDSDDE", lambda tab: _show_ddsdde_plot(tab, comparison_report.get("ddsdde_comparison"))))
    if _constitutive_artifact_frame(comparison_report.get("constitutive_comparison")).shape[0] > 0:
        tab_renderers.append(("Constitutive", lambda tab: _show_constitutive_plot(tab, comparison_report.get("constitutive_comparison"))))
    if _convergence_frame(comparison_report.get("convergence_comparison")).shape[0] > 0:
        tab_renderers.append(("Convergence", lambda tab: _show_convergence_plot(tab, comparison_report.get("convergence_comparison"))))
    if tab_renderers:
        tabs = st_module.tabs([label for label, _ in tab_renderers])
        for tab, (_, renderer) in zip(tabs, tab_renderers):
            renderer(tab)

    messages = [str(error) for error in comparison_report.get("errors", []) or [] if str(error)]
    if not messages:
        activation = comparison_report.get("activation_check")
        if isinstance(activation, dict) and activation.get("message"):
            messages.append(str(activation.get("message")))
    if messages:
        st_module.warning(messages[0])
        if len(messages) > 1:
            st_module.caption(f"{len(messages) - 1} more validation message(s) are hidden in advanced details.")


def _component_status_label(payload: object) -> str:
    if not isinstance(payload, dict):
        return "Not run"
    if payload.get("status") == "not_requested":
        return "Skipped"
    if payload.get("status") == "preview_only":
        return "Preview only"
    if payload.get("pass") is True:
        return "Passed"
    if payload.get("pass") is False:
        return "Failed"
    return str(payload.get("status", "Unknown")).replace("_", " ").title()


def _validation_status_label(comparison_report: dict[str, Any]) -> str:
    if not comparison_report:
        return "Not run"
    if comparison_report.get("pass") is True:
        return "Passed"
    if comparison_report.get("pass") is False:
        return "Failed"
    return str(comparison_report.get("status", "Unknown")).replace("_", " ").title()


def _selected_arguments_for_umat(analysis: dict[str, Any], selected_umat: str) -> list[str]:
    selected = str(selected_umat).upper()
    if not selected:
        return []
    routines = analysis.get("detected_umat_routines", []) or analysis.get("detected_subroutines", [])
    for row in routines:
        if str(row.get("name", "")).upper() == selected:
            return list(row.get("arguments", []) or [])
    return []


def _load_json_path(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _float_or_default(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _format_optional_float(value: object) -> str:
    try:
        return f"{float(value):.3e}"
    except (TypeError, ValueError):
        return ""


def _validation_overview_frame(comparison_report: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for label, payload, abs_key, rel_key in (
        ("Stress", comparison_report.get("stress_comparison"), "absolute_tolerance", "relative_tolerance"),
        ("STATEV", comparison_report.get("state_variable_comparison"), "absolute_tolerance", "relative_tolerance"),
        ("DDSDDE", comparison_report.get("ddsdde_comparison"), "ddsdde_absolute_tolerance", "ddsdde_relative_tolerance"),
        ("Constitutive", comparison_report.get("constitutive_comparison"), "ddsdde_absolute_tolerance", "ddsdde_relative_tolerance"),
    ):
        if not isinstance(payload, dict):
            continue
        max_abs = _optional_float(payload.get("max_abs_difference"))
        max_rel = _optional_float(payload.get("max_rel_difference"))
        if max_abs is None and max_rel is None:
            continue
        rows.append(
            {
                "check": label,
                "status": _component_status_label(payload),
                "max_abs_difference": max_abs or 0.0,
                "max_rel_difference": max_rel or 0.0,
                "absolute_tolerance": _optional_float(comparison_report.get(abs_key)) or 0.0,
                "relative_tolerance": _optional_float(comparison_report.get(rel_key)) or 0.0,
            }
        )
    return pd.DataFrame(rows)


def _show_vector_comparison_plot(st_module, title: str, payload: object, original_keys: tuple[str, ...], otis_keys: tuple[str, ...]) -> None:
    frame = _comparison_vector_frame(payload, original_keys, otis_keys)
    if frame.empty:
        st_module.info(f"{title} data is not available.")
        return
    st_module.caption(title)
    chart_columns = st_module.columns(2)
    indexed = frame.set_index("component")
    with chart_columns[0]:
        st_module.line_chart(indexed[["Original", "OTIS"]])
    with chart_columns[1]:
        st_module.bar_chart(indexed[["Absolute difference"]])


def _comparison_vector_frame(payload: object, original_keys: tuple[str, ...], otis_keys: tuple[str, ...]) -> pd.DataFrame:
    if not isinstance(payload, dict):
        return pd.DataFrame()
    original = _first_numeric_list(payload, original_keys)
    otis = _first_numeric_list(payload, otis_keys)
    if not original and not otis:
        return pd.DataFrame()
    length = min(len(original), len(otis)) if original and otis else max(len(original), len(otis))
    rows: list[dict[str, object]] = []
    for index in range(length):
        original_value = original[index] if index < len(original) else None
        otis_value = otis[index] if index < len(otis) else None
        rows.append(
            {
                "component": index + 1,
                "Original": original_value,
                "OTIS": otis_value,
                "Absolute difference": abs((original_value or 0.0) - (otis_value or 0.0)),
            }
        )
    return pd.DataFrame(rows)


def _show_ddsdde_plot(st_module, payload: object) -> None:
    frame = _ddsdde_increment_frame(payload)
    if frame.empty:
        st_module.info("DDSDDE increment comparison data is not available.")
        return
    st_module.caption("DDSDDE difference by increment")
    chart_columns = st_module.columns(2)
    indexed = frame.set_index("increment")
    with chart_columns[0]:
        st_module.line_chart(indexed[["max_abs_difference"]])
    with chart_columns[1]:
        st_module.line_chart(indexed[["max_rel_difference"]])
    worst_increment = payload.get("worst_increment") if isinstance(payload, dict) else None
    if isinstance(worst_increment, dict):
        st_module.caption(
            "Worst increment: "
            f"{int(worst_increment.get('original_increment_number', 0) or 0)} "
            f"with abs diff {_format_optional_float(worst_increment.get('max_abs_difference'))}"
        )


def _show_constitutive_plot(st_module, payload: object) -> None:
    frame = _constitutive_artifact_frame(payload)
    if frame.empty:
        st_module.info("Constitutive Jacobian comparison data is not available.")
        return
    st_module.caption("Constitutive Jacobian artifact differences")
    chart_columns = st_module.columns(2)
    indexed = frame.set_index("artifact")
    with chart_columns[0]:
        st_module.bar_chart(indexed[["max_abs_difference"]])
    with chart_columns[1]:
        st_module.bar_chart(indexed[["max_rel_difference"]])
    st_module.dataframe(frame, width="stretch", hide_index=True)
    preview_only = payload.get("preview_only_artifacts") if isinstance(payload, dict) else []
    if isinstance(preview_only, list) and preview_only:
        preview_rows = [
            {
                "artifact": str(row.get("target_variable") or row.get("id") or "artifact"),
                "status": str(row.get("comparison_status") or "preview_only"),
                "message": str(row.get("message") or ""),
            }
            for row in preview_only
            if isinstance(row, dict)
        ]
        if preview_rows:
            st_module.caption("Preview-only constitutive artifacts")
            st_module.dataframe(preview_rows, width="stretch", hide_index=True)


def _ddsdde_increment_frame(payload: object) -> pd.DataFrame:
    if not isinstance(payload, dict):
        return pd.DataFrame()
    increments = payload.get("increments")
    if not isinstance(increments, list):
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for increment in increments:
        if not isinstance(increment, dict):
            continue
        rows.append(
            {
                "increment": int(increment.get("original_increment_number") or increment.get("pair_index", 0)) + (0 if increment.get("original_increment_number") else 1),
                "max_abs_difference": _optional_float(increment.get("max_abs_difference")) or 0.0,
                "max_rel_difference": _optional_float(increment.get("max_rel_difference")) or 0.0,
            }
        )
    return pd.DataFrame(rows)


def _constitutive_artifact_frame(payload: object) -> pd.DataFrame:
    if not isinstance(payload, dict):
        return pd.DataFrame()
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        rows.append(
            {
                "artifact": str(artifact.get("target_variable") or artifact.get("id") or "artifact"),
                "status": _component_status_label(artifact),
                "component_count": int(artifact.get("component_count") or 0),
                "max_abs_difference": _optional_float(artifact.get("max_abs_difference")) or 0.0,
                "max_rel_difference": _optional_float(artifact.get("max_rel_difference")) or 0.0,
            }
        )
    return pd.DataFrame(rows)


def _show_convergence_plot(st_module, payload: object) -> None:
    frame = _convergence_frame(payload)
    if frame.empty:
        st_module.info("Convergence increment data is not available.")
        return
    st_module.caption("Total iterations by increment")
    st_module.line_chart(frame.set_index("increment")[["Original", "OTIS"]])


def _convergence_frame(payload: object) -> pd.DataFrame:
    if not isinstance(payload, dict):
        return pd.DataFrame()
    original = _increment_rows(payload.get("original"))
    otis = _increment_rows(payload.get("otis"))
    if not original and not otis:
        return pd.DataFrame()
    increments = sorted(set(original) | set(otis))
    rows = [
        {
            "increment": increment,
            "Original": original.get(increment),
            "OTIS": otis.get(increment),
        }
        for increment in increments
    ]
    return pd.DataFrame(rows)


def _increment_rows(payload: object) -> dict[int, int]:
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("sta_increment_rows")
    if not isinstance(rows, list):
        return {}
    result: dict[int, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        increment = int(row.get("increment") or 0)
        total_iterations = int(row.get("total_iterations") or 0)
        if increment > 0:
            result[increment] = total_iterations
    return result


def _first_numeric_list(payload: dict[str, Any], keys: tuple[str, ...]) -> list[float]:
    for key in keys:
        values = payload.get(key)
        if isinstance(values, list):
            return [_optional_float(value) or 0.0 for value in values]
    return []


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _current_pipeline(
    mappings: dict[str, str],
    routine_roles: list[dict[str, object]],
    variable_roles: list[dict[str, object]],
) -> dict[str, object]:
    pipeline = evaluate_pipeline_status(
        has_upload=bool(st.session_state.get("source_path")),
        selected_umat=st.session_state.get("selected_umat"),
        mappings=mappings,
        variable_roles=variable_roles,
        routine_roles=routine_roles,
        region_classifications=st.session_state.get("region_classifications", []),
        accept_unknown_routine_warnings=st.session_state.get("accept_unknown_routine_warnings", False),
    )
    pipeline["configuration_saved"] = bool(st.session_state.get("config_paths"))
    return pipeline


def _save_configuration(
    st_module,
    selected_arguments: list[str],
    mappings: dict[str, str],
    routine_roles: list[dict[str, object]],
    region_classifications: list[dict[str, object]],
    variable_roles: list[dict[str, object]],
    findings_log: list[dict[str, object]],
    metadata: dict[str, str],
    pipeline: dict[str, object],
    transformation_settings: dict[str, object] | None = None,
    transformation_anchors: dict[str, object] | None = None,
) -> None:
    project = st_module.session_state.get("project")
    analysis = st_module.session_state.get("analysis")
    source_metadata = st_module.session_state.get("file_metadata")
    if not project or not analysis or not source_metadata:
        st_module.error("Upload and process a UMAT before saving configuration.")
        return
    config = _build_gui_project_config(
        st_module,
        selected_arguments=selected_arguments,
        mappings=mappings,
        routine_roles=routine_roles,
        region_classifications=region_classifications,
        variable_roles=variable_roles,
        findings_log=findings_log,
        metadata=metadata,
        pipeline=pipeline,
        transformation_settings=transformation_settings,
        transformation_anchors=transformation_anchors,
    )
    workdir = Path(project["workdir"])
    st_module.session_state["config_paths"] = write_config_files(workdir, config)
    st_module.session_state["summary_path"] = str(write_setup_summary(workdir, config))


def _build_gui_project_config(
    st_module,
    *,
    selected_arguments: list[str],
    mappings: dict[str, str],
    routine_roles: list[dict[str, object]],
    region_classifications: list[dict[str, object]],
    variable_roles: list[dict[str, object]],
    findings_log: list[dict[str, object]],
    metadata: dict[str, str],
    pipeline: dict[str, object],
    transformation_settings: dict[str, object] | None = None,
    transformation_anchors: dict[str, object] | None = None,
) -> dict[str, Any]:
    updated_config = build_project_config(
        project=st_module.session_state.get("project") or {},
        source_metadata=st_module.session_state.get("file_metadata") or {},
        analysis=st_module.session_state.get("analysis") or {},
        selected_umat=st_module.session_state.get("selected_umat", ""),
        selected_umat_arguments=selected_arguments,
        mappings=mappings,
        routine_roles=routine_roles,
        region_classifications=region_classifications,
        variable_roles=variable_roles,
        findings_log=findings_log,
        metadata=metadata,
        pipeline=pipeline,
        transformation_settings=transformation_settings,
        validation_settings=st_module.session_state.get("validation_settings") or {},
        transformation_anchors=transformation_anchors,
    )
    return merge_project_config(st_module.session_state.get("loaded_project_config"), updated_config)


def _jacobian_summary(config_or_report: object) -> dict[str, int]:
    payload = config_or_report if isinstance(config_or_report, dict) else {}
    return {
        "extra_contracts": len(payload.get("extra_jacobian_contracts", [])) if isinstance(payload.get("extra_jacobian_contracts"), list) else 0,
        "helper_surfaces": len(payload.get("helper_output_surfaces", [])) if isinstance(payload.get("helper_output_surfaces"), list) else 0,
        "artifacts": len(payload.get("jacobian_artifacts", [])) if isinstance(payload.get("jacobian_artifacts"), list) else 0,
    }


def _workspace_summary_rows(
    *,
    file_metadata: dict[str, Any],
    transformation_settings: dict[str, Any],
    validation_settings: dict[str, Any],
    compare_outputs: list[str],
    contract_summary: dict[str, int],
    direction_summary: dict[str, Any],
    constitutive_preview: dict[str, Any],
) -> list[dict[str, str]]:
    rows = [
        {"item": "Source file", "value": str(file_metadata.get("file_path", ""))},
        {"item": "NTENS", "value": str(transformation_settings.get("ntens") or "Missing")},
        {"item": "Validation mode", "value": str(validation_settings.get("material_test_mode", "single element tension"))},
        {"item": "Compared outputs", "value": ", ".join(compare_outputs)},
    ]
    if direction_summary:
        rows.extend(
            [
                {"item": "DDSDDE directions", "value": str(direction_summary.get("ddsdde_directions", ""))},
                {"item": "Extra OTI directions", "value": str(direction_summary.get("extra_directions", ""))},
                {"item": "Total OTI directions", "value": str(direction_summary.get("total_directions", ""))},
            ]
        )
    if contract_summary["extra_contracts"] or contract_summary["helper_surfaces"]:
        rows.extend(
            [
                {"item": "Extra Jacobian contracts", "value": str(contract_summary["extra_contracts"])},
                {"item": "Helper output surfaces", "value": str(contract_summary["helper_surfaces"])},
            ]
        )
    if constitutive_preview:
        rows.append(
            {
                "item": "Constitutive validation",
                "value": (
                    f"{int(constitutive_preview.get('comparable_artifact_count') or 0)} comparable, "
                    f"{int(constitutive_preview.get('preview_only_artifact_count') or 0)} preview-only"
                ),
            }
        )
    return rows


def _direction_slot_rows(direction_summary: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for assignment in direction_summary.get("slot_assignments", []) if isinstance(direction_summary.get("slot_assignments"), list) else []:
        if not isinstance(assignment, dict):
            continue
        rows.append(
            {
                "contract": str(assignment.get("id") or ""),
                "seed": str(assignment.get("seed_variable") or ""),
                "directions": str(assignment.get("directions") or ""),
                "slot range": f"{assignment.get('slot_start')} - {assignment.get('slot_end')}",
            }
        )
    return rows


def _constitutive_preview_rows(preview: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for artifact in preview.get("artifacts", []) if isinstance(preview.get("artifacts"), list) else []:
        if not isinstance(artifact, dict):
            continue
        slot_start = artifact.get("statev_start_index")
        slot_end = artifact.get("statev_end_index")
        slot_range = ""
        if slot_start and slot_end:
            slot_range = f"{slot_start} - {slot_end}"
        rows.append(
            {
                "target": str(artifact.get("target_variable") or artifact.get("id") or "artifact"),
                "status": str(artifact.get("comparison_status") or "preview_only").replace("_", " "),
                "components": str(artifact.get("component_count") or ""),
                "validation SDV slots": slot_range,
                "message": str(artifact.get("message") or ""),
            }
        )
    return rows


def _show_jacobian_change_preview(st_module, transformation_settings: dict[str, object] | None = None) -> None:
    direction_summary = _current_direction_summary(st_module, transformation_settings)
    constitutive_preview = _current_constitutive_validation_preview(st_module, transformation_settings)
    if not direction_summary and not constitutive_preview:
        return
    summary_rows: list[dict[str, str]] = []
    if direction_summary:
        summary_rows.extend(
            [
                {"item": "DDSDDE directions", "value": str(direction_summary.get("ddsdde_directions", ""))},
                {"item": "Extra OTI directions", "value": str(direction_summary.get("extra_directions", ""))},
                {"item": "Total OTI directions", "value": str(direction_summary.get("total_directions", ""))},
            ]
        )
    if constitutive_preview:
        summary_rows.append(
            {
                "item": "Constitutive comparison preview",
                "value": (
                    f"{int(constitutive_preview.get('comparable_artifact_count') or 0)} direct, "
                    f"{int(constitutive_preview.get('preview_only_artifact_count') or 0)} preview-only"
                ),
            }
        )
    with st_module.container(border=True):
        st_module.caption("Jacobian change preview")
        if summary_rows:
            st_module.table(summary_rows)
        direction_rows = _direction_slot_rows(direction_summary)
        if direction_rows:
            st_module.caption("Extra direction slot assignments")
            st_module.dataframe(direction_rows, width="stretch", hide_index=True)
        preview_rows = _constitutive_preview_rows(constitutive_preview)
        if preview_rows:
            st_module.caption("Abaqus constitutive comparison preview")
            st_module.dataframe(preview_rows, width="stretch", hide_index=True)


def _jacobian_contract_rows(report: object) -> list[dict[str, str]]:
    payload = report if isinstance(report, dict) else {}
    rows: list[dict[str, str]] = []
    for contract in payload.get("extra_jacobian_contracts", []):
        if not isinstance(contract, dict):
            continue
        output = contract.get("output") if isinstance(contract.get("output"), dict) else {}
        seed = contract.get("seed") if isinstance(contract.get("seed"), dict) else {}
        extraction = contract.get("extraction") if isinstance(contract.get("extraction"), dict) else {}
        rows.append(
            {
                "id": str(contract.get("id", "")),
                "output": str(output.get("variable", "")),
                "seed": str(seed.get("variable", "")),
                "extraction": str(extraction.get("extract_kind", "")),
                "description": str(contract.get("description", "")),
            }
        )
    return rows


def _jacobian_artifact_rows(report: object) -> list[dict[str, str]]:
    payload = report if isinstance(report, dict) else {}
    rows: list[dict[str, str]] = []
    for artifact in payload.get("jacobian_artifacts", []):
        if not isinstance(artifact, dict):
            continue
        rows.append(
            {
                "kind": str(artifact.get("artifact_class", artifact.get("kind", ""))),
                "helper": str(artifact.get("helper_name", "")),
                "target": str(artifact.get("target_variable", artifact.get("caller_variable", ""))),
                "source": str(artifact.get("source_variable", artifact.get("source_local", artifact.get("from_output_variable", "")))),
                "description": str(artifact.get("description", artifact.get("id", ""))),
            }
        )
    return rows


if __name__ == "__main__":
    main()
