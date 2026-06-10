"""UMAT-OTI Streamlit GUI.

Five-tab workflow that drives the same backend the CLI / driver scripts use:

  1. Load Config        - pick a JSON from json_files_completed/ or upload one
  2. Transform          - run / locate the OTI-lifted UMAT for the loaded config
  3. Validate           - build the workspace, run both Abaqus jobs, extract ODB
  4. Constitutive Jac.  - per-contract Original vs OTIS tables + line plots
  5. Report             - status panel, downloadable artifacts, raw JSON browse

The previous monolithic GUI is preserved as ``streamlit_app_legacy.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import streamlit as st

from umat_oti.core.config_loader import load_project_config_json
from umat_oti.transform.source_transform import transform_umat_to_oti_from_config
from umat_oti.validation.abaqus_runner import (
    extract_results,
    run_both_jobs,
)
from umat_oti.validation.compare_results import compare_validation_results
from umat_oti.validation.job_builder import (
    DEFAULT_ABAQUS_MODULES,
    DEFAULT_ABAQUS_RUN_PREFIX,
    build_validation_workspace,
)


# ----------------------------------------------------------------------------
# Path discovery
# ----------------------------------------------------------------------------

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


COMPLETED_JSON_DIR = _repo_root() / "json_files_completed"
WORKSPACE_ROOT = _repo_root() / "umat_oti_workspace"


# ----------------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "config": None,
    "config_path": "",
    "config_label": "",
    "transform_dir": "",
    "transformed_umat": "",
    "transform_report": None,
    "validation_dir": "",
    "build_result": None,
    "run_result": None,
    "extract_result": None,
    "compare_result": None,
    "material_test_mode": "single element plastic tension",
    "abaqus_command": "abaqus",
    "abaqus_modules": DEFAULT_ABAQUS_MODULES,
    "abaqus_run_prefix": DEFAULT_ABAQUS_RUN_PREFIX,
    "compare_outputs": ["STRESS", "STATEV", "DDSDDE", "CONSTITUTIVE_JACOBIANS", "CONVERGENCE"],
    "presentation_label": "",
}


def _init_state() -> None:
    for key, value in _DEFAULTS.items():
        st.session_state.setdefault(key, value)


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

@dataclass
class _ConfigSummary:
    name: str
    selected_umat_file: str
    selected_umat: str
    ntens: int
    order: int
    contracts: list[dict[str, Any]]
    helper_surfaces: list[dict[str, Any]]


def _summarize_config(cfg: dict[str, Any]) -> _ConfigSummary:
    project = cfg.get("project") or {}
    source = cfg.get("source") or {}
    ts = cfg.get("transformation_settings") or {}
    contracts = cfg.get("extra_jacobian_contracts") or []
    return _ConfigSummary(
        name=str(project.get("name") or "unnamed"),
        selected_umat_file=str(source.get("selected_umat_file") or ""),
        selected_umat=str(source.get("selected_umat_name") or source.get("detected_umat_name") or "UMAT"),
        ntens=int(ts.get("ntens") or 0),
        order=int(ts.get("order") or 1),
        contracts=list(contracts),
        helper_surfaces=list(cfg.get("helper_output_surfaces") or []),
    )


def _find_existing_transformed(umat_name: str) -> Path | None:
    """Return the most recent ``<name>_oti.for`` already present in the workspace."""
    if not umat_name:
        return None
    pattern = f"*/oti_transform/{umat_name}/{umat_name}_oti.for"
    matches = sorted(
        WORKSPACE_ROOT.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def _list_completed_configs() -> list[Path]:
    if not COMPLETED_JSON_DIR.is_dir():
        return []
    return sorted(
        p for p in COMPLETED_JSON_DIR.glob("*.json") if p.name not in {"completion_report.json"}
    )


def _read_json(path: Path | str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _tail(text: str, n: int = 30) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _status_badge(status: str | None) -> str:
    if not status:
        return ":grey[unknown]"
    s = status.lower()
    if s in {"completed", "passed", "ok"}:
        return f":green[{status}]"
    if s in {"failed", "timeout", "error"}:
        return f":red[{status}]"
    if s in {"not_run", "configured"}:
        return f":grey[{status}]"
    return f":orange[{status}]"


# ----------------------------------------------------------------------------
# Tab 1 - Load Config
# ----------------------------------------------------------------------------

def _tab_load_config() -> None:
    st.subheader("1. Load Project Configuration")
    st.caption(
        "Pick one of the completed JSON files under `json_files_completed/` "
        "or upload your own. The selected config drives every downstream step."
    )

    completed = _list_completed_configs()
    options = ["(choose...)"] + [p.name for p in completed]
    left, right = st.columns([2, 1])
    with left:
        choice = st.selectbox(
            "Completed configs",
            options,
            index=0,
            help=f"Directory: {COMPLETED_JSON_DIR}",
        )
        if choice != "(choose...)" and st.button("Load selected", type="primary"):
            picked = COMPLETED_JSON_DIR / choice
            cfg = load_project_config_json(picked.read_bytes(), origin_path=picked)
            st.session_state.config = cfg
            st.session_state.config_path = str(picked)
            st.session_state.config_label = picked.stem
            st.session_state.transformed_umat = ""
            st.session_state.transform_dir = ""
            st.session_state.transform_report = None
            st.session_state.validation_dir = ""
            st.success(f"Loaded {picked.name}")

    with right:
        upload = st.file_uploader("Upload JSON", type=["json"], accept_multiple_files=False)
        if upload is not None and st.button("Use uploaded"):
            payload = upload.read()
            cfg = load_project_config_json(payload, origin_path=upload.name)
            st.session_state.config = cfg
            st.session_state.config_path = upload.name
            st.session_state.config_label = Path(upload.name).stem
            st.session_state.transformed_umat = ""
            st.session_state.transform_dir = ""
            st.session_state.transform_report = None
            st.session_state.validation_dir = ""
            st.success(f"Loaded {upload.name}")

    cfg = st.session_state.config
    if not cfg:
        st.info("No config loaded yet.")
        return

    summary = _summarize_config(cfg)
    st.markdown("---")
    st.markdown(f"### {summary.name}")
    st.markdown(
        f"- **UMAT file:** `{summary.selected_umat_file}`"
        f"\n- **Selected routine:** `{summary.selected_umat}`"
        f"\n- **NTENS:** `{summary.ntens}`  |  **Order:** `{summary.order}`"
        f"\n- **Extra Jacobian contracts:** `{len(summary.contracts)}`"
        f"\n- **Helper output surfaces:** `{len(summary.helper_surfaces)}`"
    )

    src_path = Path(summary.selected_umat_file)
    if src_path.is_absolute() and not src_path.is_file():
        st.warning(
            f"UMAT source file `{src_path}` was not found on disk. "
            "Transform and Validate steps will fail until the path is fixed."
        )

    if summary.contracts:
        st.markdown("**Contracts**")
        rows = []
        for c in summary.contracts:
            seed = c.get("seed") or {}
            out = c.get("output") or {}
            internal = c.get("internal_use") or {}
            rows.append(
                {
                    "id": c.get("id"),
                    "seed_variable": seed.get("variable"),
                    "seed_shape": seed.get("shape"),
                    "output_variable": out.get("variable"),
                    "output_shape": out.get("shape"),
                    "replaces": internal.get("replace_variable"),
                    "additional_extractions": len(c.get("additional_extractions") or []),
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("Raw JSON"):
        st.json(cfg, expanded=False)


# ----------------------------------------------------------------------------
# Tab 2 - Transform
# ----------------------------------------------------------------------------

def _tab_transform() -> None:
    st.subheader("2. Transform UMAT to OTI")
    cfg = st.session_state.config
    if not cfg:
        st.info("Load a config in tab 1 first.")
        return

    summary = _summarize_config(cfg)
    src_path = Path(summary.selected_umat_file)

    existing = _find_existing_transformed(summary.name)
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Generate new transform**")
        default_out = WORKSPACE_ROOT / f"gui_{summary.name}" / "oti_transform" / summary.name
        out_dir_str = st.text_input(
            "Output directory",
            value=str(default_out),
            help="OTI-lifted UMAT and module files will be written here.",
        )
        if st.button("Run transformation", type="primary", disabled=not src_path.is_file()):
            if not src_path.is_file():
                st.error(f"UMAT source not found: {src_path}")
            else:
                with st.spinner("Transforming..."):
                    out_dir = Path(out_dir_str).resolve()
                    out_dir.mkdir(parents=True, exist_ok=True)
                    result = transform_umat_to_oti_from_config(
                        source_text=src_path.read_text(encoding="utf-8", errors="replace"),
                        config=cfg,
                        output_dir=out_dir,
                        ntens=summary.ntens,
                    )
                st.session_state.transform_dir = str(out_dir)
                st.session_state.transform_report = result.report
                # locate transformed file (named like <Name>_oti.for or umat_<name>_oti.for)
                produced = sorted(out_dir.glob("*_oti.for"))
                if produced:
                    st.session_state.transformed_umat = str(produced[0])
                if result.success:
                    st.success("Transformation succeeded.")
                else:
                    st.error("Transformation reported blockers (see below).")

    with col_b:
        st.markdown("**Reuse existing transform**")
        if existing is not None:
            st.markdown(f"Most recent on disk: `{existing}`")
            if st.button("Use existing transform"):
                st.session_state.transformed_umat = str(existing)
                st.session_state.transform_dir = str(existing.parent)
                report_path = existing.parent / "transform_report.json"
                st.session_state.transform_report = _read_json(report_path)
                st.success(f"Using {existing.name}")
        else:
            st.caption("No previously transformed UMAT found in `umat_oti_workspace/`.")

    if st.session_state.transformed_umat:
        st.markdown("---")
        st.markdown(f"**Active transformed UMAT:** `{st.session_state.transformed_umat}`")
        st.markdown(f"**Generated dir:** `{st.session_state.transform_dir}`")

    report = st.session_state.transform_report
    if report:
        st.markdown("---")
        st.markdown("### Transform report")
        c1, c2, c3 = st.columns(3)
        c1.metric("Success", "yes" if report.get("success") else "no")
        c2.metric("Blockers", len(report.get("blockers", [])))
        c3.metric("Warnings", len(report.get("warnings", [])))
        if report.get("blockers"):
            st.error("Blockers:")
            for b in report["blockers"]:
                st.markdown(f"- {b}")
        if report.get("warnings"):
            with st.expander(f"Warnings ({len(report['warnings'])})"):
                for w in report["warnings"]:
                    st.markdown(f"- {w}")
        if report.get("generated_files"):
            with st.expander("Generated files"):
                for f in report["generated_files"]:
                    st.markdown(f"- `{f}`")
        with st.expander("Raw transform report"):
            st.json(report, expanded=False)


# ----------------------------------------------------------------------------
# Tab 3 - Validate
# ----------------------------------------------------------------------------

def _tab_validate() -> None:
    st.subheader("3. Validate (build, run, extract, compare)")
    cfg = st.session_state.config
    if not cfg:
        st.info("Load a config in tab 1 first.")
        return
    if not st.session_state.transformed_umat:
        st.info("Generate or pick a transformed UMAT in tab 2 first.")
        return

    summary = _summarize_config(cfg)
    src_path = Path(summary.selected_umat_file)
    transformed = Path(st.session_state.transformed_umat)
    transform_dir = Path(st.session_state.transform_dir or transformed.parent)

    st.markdown("**Workspace**")
    label_default = st.session_state.presentation_label or f"gui_{summary.name}"
    label = st.text_input("Workspace label", value=label_default, key="presentation_label")
    default_val = WORKSPACE_ROOT / label / "validation" / summary.name
    val_dir_str = st.text_input("Validation directory", value=str(default_val))

    st.markdown("**Run options**")
    cc1, cc2 = st.columns(2)
    with cc1:
        material_modes = [
            "single element plastic tension",
            "single element tension",
            "single element plastic shear",
            "single element finite tension",
            "single element plastic finite tension",
        ]
        try:
            mode_idx = material_modes.index(st.session_state.material_test_mode)
        except ValueError:
            mode_idx = 0
        st.session_state.material_test_mode = st.selectbox(
            "Material test mode", material_modes, index=mode_idx
        )
        st.session_state.compare_outputs = st.multiselect(
            "Compare outputs",
            ["STRESS", "STATEV", "DDSDDE", "CONSTITUTIVE_JACOBIANS", "CONVERGENCE"],
            default=st.session_state.compare_outputs,
        )
    with cc2:
        st.session_state.abaqus_command = st.text_input(
            "Abaqus command", value=st.session_state.abaqus_command
        )
        st.session_state.abaqus_modules = st.text_input(
            "Abaqus modules", value=st.session_state.abaqus_modules
        )
        st.session_state.abaqus_run_prefix = st.text_input(
            "Run prefix (srun/...)", value=st.session_state.abaqus_run_prefix
        )

    st.markdown("---")
    st.markdown("**Pipeline**")
    b1, b2, b3, b4 = st.columns(4)

    if b1.button("Build workspace", type="primary"):
        with st.spinner("Building workspace..."):
            val_dir = Path(val_dir_str).resolve()
            res = build_validation_workspace(
                validation_dir=val_dir,
                original_umat=src_path,
                transformed_umat=transformed,
                generated_dir=transform_dir,
                project_config=cfg,
                ntens=summary.ntens,
                abaqus_command=st.session_state.abaqus_command,
                abaqus_modules=st.session_state.abaqus_modules,
                run_prefix=st.session_state.abaqus_run_prefix,
                material_test_mode=st.session_state.material_test_mode,
                compare_outputs=list(st.session_state.compare_outputs),
                run_compile_smoke=False,
            )
        st.session_state.validation_dir = str(val_dir)
        st.session_state.build_result = {
            "warnings": res.warnings,
            "report": res.report,
            "files": res.files,
        }
        st.success(f"Workspace built at `{val_dir}`")

    val_dir = Path(st.session_state.validation_dir) if st.session_state.validation_dir else None

    if b2.button("Run Abaqus jobs", disabled=val_dir is None):
        with st.spinner("Running both UMAT jobs (this may take a few minutes)..."):
            res = run_both_jobs(
                val_dir.resolve(),
                abaqus_command=st.session_state.abaqus_command,
                abaqus_modules=st.session_state.abaqus_modules,
                run_prefix=st.session_state.abaqus_run_prefix,
            )
        st.session_state.run_result = res
        ok_o = res.get("original", {}).get("status") == "completed"
        ok_t = res.get("transformed", {}).get("status") == "completed"
        if ok_o and ok_t:
            st.success("Both jobs completed.")
        else:
            st.error("At least one job did not complete - see logs below.")

    if b3.button("Extract ODB", disabled=val_dir is None):
        with st.spinner("Running abaqus python extraction..."):
            res = extract_results(
                val_dir.resolve(),
                abaqus_command=st.session_state.abaqus_command,
                abaqus_modules=st.session_state.abaqus_modules,
                run_prefix=st.session_state.abaqus_run_prefix,
            )
        st.session_state.extract_result = res.to_json()
        if res.status == "completed":
            st.success("Extraction completed.")
        else:
            st.error(f"Extraction status: {res.status}")

    if b4.button("Compare results", disabled=val_dir is None):
        res = compare_validation_results(val_dir.resolve())
        st.session_state.compare_result = res.to_json()
        if res.passed:
            st.success(f"PASS  (max abs = {res.max_abs_difference})")
        else:
            st.warning(f"Status: {res.status}  (max abs = {res.max_abs_difference})")

    # ---- Status panel ----------------------------------------------------
    st.markdown("---")
    st.markdown("### Status")
    grid = st.columns(4)
    build = st.session_state.build_result
    runres = st.session_state.run_result
    ext = st.session_state.extract_result
    comp = st.session_state.compare_result
    grid[0].markdown(f"**Build:** {_status_badge('configured' if build else None)}")
    if runres:
        orig_st = runres.get("original", {}).get("status")
        oti_st = runres.get("transformed", {}).get("status")
        grid[1].markdown(f"**Original:** {_status_badge(orig_st)}<br>**OTIS:** {_status_badge(oti_st)}", unsafe_allow_html=True)
    else:
        grid[1].markdown(f"**Jobs:** {_status_badge(None)}")
    grid[2].markdown(f"**Extract:** {_status_badge((ext or {}).get('status'))}")
    if comp is not None:
        comp_status = "passed" if comp.get("pass") else (comp.get("status") or "unknown")
        grid[3].markdown(f"**Compare:** {_status_badge(comp_status)}")
    else:
        grid[3].markdown(f"**Compare:** {_status_badge(None)}")

    if runres:
        with st.expander("Run job logs"):
            for which in ("original", "transformed"):
                entry = runres.get(which, {})
                st.markdown(f"**{which}** - status: `{entry.get('status')}` - rc: `{entry.get('returncode')}`")
                st.code(_tail(entry.get("stdout_excerpt") or "", 20) or "(no stdout excerpt)", language="text")
                if entry.get("stderr_excerpt"):
                    st.caption("stderr tail")
                    st.code(_tail(entry.get("stderr_excerpt"), 15), language="text")
    if ext and ext.get("stderr_excerpt"):
        with st.expander("Extraction stderr tail"):
            st.code(_tail(ext["stderr_excerpt"], 25), language="text")

    if comp:
        st.markdown("### Comparison summary")
        st.json(comp, expanded=False)
        # show DDSDDE & stress diff highlights if comparison_report.json exists
        rep = _read_json(comp.get("report_json_path"))
        if rep:
            with st.expander("Top-level comparison report"):
                st.json(rep, expanded=False)


# ----------------------------------------------------------------------------
# Tab 4 - Constitutive Jacobians
# ----------------------------------------------------------------------------

def _tab_constitutive() -> None:
    st.subheader("4. Constitutive Jacobians (Original vs OTIS)")
    val_dir = Path(st.session_state.validation_dir) if st.session_state.validation_dir else None
    if val_dir is None or not val_dir.is_dir():
        st.info("Build and run a validation workspace in tab 3 first.")
        return

    orig = _read_json(val_dir / "original_results.json")
    otis = _read_json(val_dir / "otis_results.json")
    if not orig or not otis:
        st.info("Run the Abaqus jobs and extract ODB results in tab 3 first.")
        return

    incs_o = orig.get("increments") or []
    incs_t = otis.get("increments") or []
    if not incs_o or not incs_t:
        st.warning("No increments found in extracted results.")
        return

    art_ids = sorted({k for inc in incs_o for k in (inc.get("constitutive_outputs") or {})})
    if not art_ids:
        st.warning(
            "No constitutive_outputs artifacts in the extracted results. "
            "Re-build the workspace with `CONSTITUTIVE_JACOBIANS` in compare_outputs and a config that defines `extra_jacobian_contracts`."
        )
        return

    # Always show DDSDDE evolution and EQPLAS-like state slot as context
    st.markdown("### DDSDDE evolution (context)")
    _ddsdde_overview(incs_o, incs_t)

    st.markdown("---")
    selected = st.multiselect(
        "Artifacts to display",
        art_ids,
        default=art_ids,
    )
    for art_id in selected:
        _render_artifact(art_id, incs_o, incs_t)


def _ddsdde_overview(incs_o: list[dict], incs_t: list[dict]) -> None:
    # show DDSDDE(1,1) and DDSDDE(4,4) per increment
    try:
        time = [float(inc.get("frame_value") or inc.get("increment_number")) for inc in incs_o]
        d11_o = [float(inc["ddsdde"][0][0]) for inc in incs_o]
        d11_t = [float(inc["ddsdde"][0][0]) for inc in incs_t]
        d44_o = [float(inc["ddsdde"][3][3]) for inc in incs_o] if len(incs_o[0]["ddsdde"]) > 3 else None
        d44_t = [float(inc["ddsdde"][3][3]) for inc in incs_t] if len(incs_t[0]["ddsdde"]) > 3 else None
    except Exception:  # noqa: BLE001
        st.warning("DDSDDE not available in extracted results.")
        return

    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("**DDSDDE(1,1)**")
        st.line_chart({"step": time, "original": d11_o, "otis": d11_t}, x="step")
    if d44_o is not None:
        with cc2:
            st.markdown("**DDSDDE(4,4)**")
            st.line_chart({"step": time, "original": d44_o, "otis": d44_t}, x="step")


def _render_artifact(art_id: str, incs_o: list[dict], incs_t: list[dict]) -> None:
    sample = None
    for inc in incs_o:
        if art_id in (inc.get("constitutive_outputs") or {}):
            sample = inc["constitutive_outputs"][art_id]
            break
    if not sample:
        return

    labels = sample.get("component_labels") or [f"c{i}" for i in range(int(sample.get("component_count") or 0))]
    target = sample.get("target_variable") or art_id
    source = sample.get("source_variable") or "?"
    kind = sample.get("source_kind") or "?"

    st.markdown(f"#### `{art_id}`   -   `{source} -> {target}`   ({kind})")

    # build per-component arrays
    time = [float(inc.get("frame_value") or inc.get("increment_number")) for inc in incs_o]
    arr_o = np.array(
        [list((inc.get("constitutive_outputs") or {}).get(art_id, {}).get("values") or [np.nan] * len(labels)) for inc in incs_o],
        dtype=float,
    )
    arr_t = np.array(
        [list((inc.get("constitutive_outputs") or {}).get(art_id, {}).get("values") or [np.nan] * len(labels)) for inc in incs_t],
        dtype=float,
    )

    # alignment guard
    if arr_o.shape != arr_t.shape:
        st.warning(f"Component shape mismatch: original {arr_o.shape} vs otis {arr_t.shape}.")
        return

    # summary table
    abs_err = np.abs(arr_o - arr_t)
    denom = np.maximum(np.abs(arr_o), 1e-30)
    rel_err = abs_err / denom
    rows: list[dict[str, Any]] = []
    for j, lbl in enumerate(labels):
        rows.append(
            {
                "component": lbl,
                "max |original|": float(np.max(np.abs(arr_o[:, j]))),
                "max |otis|": float(np.max(np.abs(arr_t[:, j]))),
                "max abs err": float(np.max(abs_err[:, j])),
                "max rel err": float(np.max(rel_err[:, j])),
                "RMSE": float(np.sqrt(np.mean((arr_o[:, j] - arr_t[:, j]) ** 2))),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    # per-component plots in a grid
    n = len(labels)
    cols_per_row = 2 if n > 1 else 1
    for row_start in range(0, n, cols_per_row):
        cols = st.columns(cols_per_row)
        for k in range(cols_per_row):
            j = row_start + k
            if j >= n:
                break
            with cols[k]:
                st.markdown(f"**{labels[j]}**")
                st.line_chart(
                    {
                        "step": time,
                        "original": arr_o[:, j].tolist(),
                        "otis": arr_t[:, j].tolist(),
                    },
                    x="step",
                )

    # per-increment data table
    with st.expander("Per-increment values"):
        per_rows: list[dict[str, Any]] = []
        for i, t in enumerate(time):
            for j, lbl in enumerate(labels):
                per_rows.append(
                    {
                        "step": t,
                        "component": lbl,
                        "original": float(arr_o[i, j]),
                        "otis": float(arr_t[i, j]),
                        "abs_err": float(abs_err[i, j]),
                    }
                )
        st.dataframe(per_rows, use_container_width=True, hide_index=True)


# ----------------------------------------------------------------------------
# Tab 5 - Report / artifacts
# ----------------------------------------------------------------------------

_ARTIFACT_PRIORITY = [
    "validation_report.json",
    "validation_report.md",
    "comparison_report.json",
    "comparison_report.md",
    "original_results.json",
    "otis_results.json",
    "original_umat_validation.inp",
    "otis_umat_validation.inp",
    "original_abaqus_stdout.log",
    "original_abaqus_stderr.log",
    "otis_abaqus_stdout.log",
    "otis_abaqus_stderr.log",
    "extract_results_stdout.log",
    "extract_results_stderr.log",
]


def _tab_report() -> None:
    st.subheader("5. Report and Artifacts")
    val_dir = Path(st.session_state.validation_dir) if st.session_state.validation_dir else None
    if val_dir is None or not val_dir.is_dir():
        st.info("Build a validation workspace in tab 3 first.")
        return

    st.markdown(f"**Validation dir:** `{val_dir}`")

    md = val_dir / "comparison_report.md"
    if md.is_file():
        with st.expander("Comparison report (markdown)", expanded=True):
            st.markdown(md.read_text(encoding="utf-8"))

    vmd = val_dir / "validation_report.md"
    if vmd.is_file():
        with st.expander("Validation report (markdown)"):
            st.markdown(vmd.read_text(encoding="utf-8"))

    st.markdown("### Downloads")
    present = []
    for name in _ARTIFACT_PRIORITY:
        p = val_dir / name
        if p.is_file():
            present.append(p)
    # add anything else not in priority list
    for p in sorted(val_dir.iterdir()):
        if p.is_file() and p not in present:
            present.append(p)

    cols = st.columns(2)
    for idx, p in enumerate(present):
        col = cols[idx % 2]
        with col:
            try:
                data = p.read_bytes()
            except Exception:  # noqa: BLE001
                continue
            col.download_button(
                p.name,
                data=data,
                file_name=p.name,
                key=f"dl_{p.name}",
            )


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="UMAT-OTI", layout="wide")
    _init_state()

    cfg = st.session_state.config
    summary = _summarize_config(cfg) if cfg else None

    with st.sidebar:
        st.title("UMAT-OTI")
        st.caption("OTI source transformation, validation, and constitutive Jacobians")
        st.markdown("---")
        if summary:
            st.markdown(f"**Config:** `{st.session_state.config_label or summary.name}`")
            st.markdown(f"NTENS = `{summary.ntens}`  -  contracts = `{len(summary.contracts)}`")
        else:
            st.markdown(":grey[No config loaded]")
        if st.session_state.transformed_umat:
            st.markdown(f"**OTI UMAT:** `{Path(st.session_state.transformed_umat).name}`")
        if st.session_state.validation_dir:
            st.markdown(f"**Validation dir:** `{Path(st.session_state.validation_dir).name}`")
        st.markdown("---")
        st.caption(f"Repo root: {_repo_root()}")
        st.caption(f"Completed JSON dir: {COMPLETED_JSON_DIR}")

    tabs = st.tabs(
        ["1. Load Config", "2. Transform", "3. Validate", "4. Constitutive Jacobians", "5. Report"]
    )
    with tabs[0]:
        _tab_load_config()
    with tabs[1]:
        _tab_transform()
    with tabs[2]:
        _tab_validate()
    with tabs[3]:
        _tab_constitutive()
    with tabs[4]:
        _tab_report()


if __name__ == "__main__":
    main()
