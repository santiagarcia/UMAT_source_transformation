"""Batch local Abaqus validation of all completed benchmark contracts.

For each JSON in json_files_completed/, transform it, build a LOCAL Abaqus
validation workspace (no module/srun), run original + transformed jobs, extract
ODB results, and compare. Collects per-case stress / STATEV / DDSDDE verdicts.

Usage:
  python3 validate_all_local.py [strip]   # 'strip' drops constant/real first
    python3 validate_all_local.py table     # build summary_table.md from cached results
"""
from __future__ import annotations

import json, sys, copy, time, traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from umat_oti.cli_json import run_config_transform
from umat_oti.core.config_loader import load_project_config_json
from umat_oti.core.transformation_anchors import merge_completed_anchors_into_config
from umat_oti.validation.abaqus_runner import extract_results, run_both_jobs
from umat_oti.validation.compare_results import compare_validation_results
from umat_oti.validation.job_builder import build_validation_workspace

ABAQUS_COMMAND, ABAQUS_MODULES, RUN_PREFIX = "abaqus", "", ""
SRC = ROOT / "json_files_completed"
TABLE_ONLY = len(sys.argv) > 1 and sys.argv[1] == "table"
STRIP = len(sys.argv) > 1 and sys.argv[1] == "strip"
OUT = ROOT / "umat_oti_workspace" / ("validate_all_strip" if STRIP else "validate_all")
OUT.mkdir(parents=True, exist_ok=True)


def material_test_mode(config):
    a = config.get("analysis", {}) if isinstance(config.get("analysis"), dict) else {}
    fin = a.get("finite_strain", {}) if isinstance(a.get("finite_strain"), dict) else {}
    pl = a.get("plasticity_indicators", {}) if isinstance(a.get("plasticity_indicators"), dict) else {}
    if pl.get("is_plasticity_candidate") and fin.get("executable_dfgrd_use"):
        return "single element plastic finite strain tension"
    if pl.get("is_plasticity_candidate"):
        return "single element plastic tension"
    if fin.get("executable_dfgrd_use"):
        return "single element plastic finite strain tension"
    return "single element tension"


def physics_model(config: dict[str, Any], case_name: str = "") -> str:
    analysis = config.get("analysis", {}) if isinstance(config.get("analysis"), dict) else {}
    finite = analysis.get("finite_strain", {}) if isinstance(analysis.get("finite_strain"), dict) else {}
    plastic = analysis.get("plasticity_indicators", {}) if isinstance(analysis.get("plasticity_indicators"), dict) else {}
    visco_tokens = ("VISCO", "CREEP", "RATE", "VISC")
    name = str(config.get("name") or config.get("case_name") or case_name).upper()
    promoted = " ".join(str(value).upper() for value in config.get("promote", []) if str(value).strip())
    is_visco = any(token in name or token in promoted for token in visco_tokens)
    if is_visco and plastic.get("is_plasticity_candidate"):
        return "small-strain viscoplasticity"
    if is_visco:
        return "viscoelastic/viscoplastic response"
    if plastic.get("is_plasticity_candidate") and finite.get("executable_dfgrd_use"):
        return "finite-strain plasticity"
    if plastic.get("is_plasticity_candidate"):
        return "small-strain plasticity"
    if finite.get("executable_dfgrd_use") or finite.get("dfgrd_driven_stress_update"):
        return "finite-strain elasticity"
    return "linear/small-strain elasticity"


def difference_reason(report: dict[str, Any]) -> str:
    errors = [str(error) for error in report.get("errors", []) or [] if str(error)]
    if errors:
        return "; ".join(errors)
    convergence = report.get("convergence_comparison", {}) if isinstance(report.get("convergence_comparison"), dict) else {}
    blocking = convergence.get("blocking_differences") if isinstance(convergence.get("blocking_differences"), dict) else {}
    if blocking:
        return "Abaqus convergence metadata differs: " + ", ".join(sorted(blocking))
    ddsdde = report.get("ddsdde_comparison", {}) if isinstance(report.get("ddsdde_comparison"), dict) else {}
    stress = report.get("stress_comparison", {}) if isinstance(report.get("stress_comparison"), dict) else {}
    if ddsdde.get("pass") is False:
        return "DDSDDE tangent differs beyond tolerance."
    if stress.get("pass") is False:
        return "Final stress differs beyond tolerance."
    max_abs = _optional_float(ddsdde.get("max_abs_difference"))
    max_rel = _optional_float(ddsdde.get("max_rel_difference"))
    if max_abs and max_abs > 0:
        return "DDSDDE differs slightly but remains within configured tolerance."
    stress_abs = _optional_float(stress.get("max_abs_difference"))
    if stress_abs and stress_abs > 0:
        return "Stress differs slightly but remains within configured tolerance."
    return "No numerical difference detected."


def convergence_iterations(report: dict[str, Any], side: str) -> Any:
    convergence = report.get("convergence_comparison", {}) if isinstance(report.get("convergence_comparison"), dict) else {}
    metrics = convergence.get(side, {}) if isinstance(convergence.get(side), dict) else {}
    return metrics.get("iterations")


def comparison_difference(report: dict[str, Any]) -> tuple[Any, Any]:
    for key in ("ddsdde_comparison", "stress_comparison", "state_variable_comparison"):
        comparison = report.get(key, {}) if isinstance(report.get(key), dict) else {}
        max_abs = comparison.get("max_abs_difference")
        max_rel = comparison.get("max_rel_difference")
        if max_abs is not None or max_rel is not None:
            return max_abs, max_rel
    return None, None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def _escape_md(value: Any) -> str:
    return _fmt(value).replace("|", "\\|")


def load_resolved(config_path):
    config = load_project_config_json(config_path.read_bytes(), origin_path=config_path)
    source = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
    sp = Path(str(source.get("selected_umat_file", ""))).expanduser()
    txt = sp.read_text(encoding="utf-8", errors="replace") if sp.is_file() else ""
    if txt:
        config = merge_completed_anchors_into_config(config, txt)
    s = config.get("transformation_settings", {}) if isinstance(config.get("transformation_settings"), dict) else {}
    return config, sp, int(s.get("ntens") or 0)


def validate_one(orig_json: Path):
    name = orig_json.stem
    raw = json.loads(orig_json.read_text())
    src = raw.get("source")
    if isinstance(src, str) and not Path(src).is_absolute():
        raw["source"] = str((orig_json.parent / src).resolve())
    if STRIP:
        raw["constant"] = []; raw["real"] = []
    work = OUT / name
    work.mkdir(parents=True, exist_ok=True)
    cfg_path = work / "config.json"
    cfg_path.write_text(json.dumps(raw))

    summary, ec = run_config_transform(cfg_path, work / "out")
    rec = {"case": name, "transform_exit": ec, "transform_ok": bool(summary.get("transform_success"))}
    if ec != 0:
        rec["error"] = "transform_failed"
        return rec
    config, source_path, ntens = load_resolved(cfg_path)
    tdir = Path(str(summary.get("out_dir"))).resolve()
    tsrc = Path(str(summary.get("transformed_source"))).resolve()
    vdir = tdir / "validation"
    vs = config.get("validation_settings", {}) if isinstance(config.get("validation_settings"), dict) else {}

    def vfloat(key):
        try:
            return float(vs.get(key))
        except (TypeError, ValueError):
            return None
    # Force DDSDDE into the compared outputs (this verification is about DDSDDE),
    # but honour the per-config tolerances so stress noise does not false-fail.
    build_validation_workspace(
        validation_dir=vdir, original_umat=source_path, transformed_umat=tsrc,
        generated_dir=tdir, project_config=config, ntens=ntens,
        abaqus_command=ABAQUS_COMMAND, abaqus_modules=ABAQUS_MODULES, run_prefix=RUN_PREFIX,
        material_test_mode=material_test_mode(config), run_compile_smoke=False,
        compare_outputs=["STRESS", "STATEV", "DDSDDE", "CONVERGENCE"],
        comparison_abs_tolerance=vfloat("absolute_tolerance"),
        comparison_rel_tolerance=vfloat("relative_tolerance"),
        comparison_ddsdde_abs_tolerance=vfloat("ddsdde_absolute_tolerance"),
        comparison_ddsdde_rel_tolerance=vfloat("ddsdde_relative_tolerance"),
    )
    run_both_jobs(vdir, ABAQUS_COMMAND, ABAQUS_MODULES, RUN_PREFIX, timeout_seconds=1800)
    extract_results(vdir, ABAQUS_COMMAND, ABAQUS_MODULES, RUN_PREFIX, timeout_seconds=600)
    compare_validation_results(vdir)
    rep = json.loads((vdir / "comparison_report.json").read_text())
    sc, dd, sv = rep.get("stress_comparison", {}), rep.get("ddsdde_comparison", {}), rep.get("state_variable_comparison", {})
    rec.update({
        "overall_pass": rep.get("pass"), "overall_status": rep.get("status"),
        "physics_model": physics_model(config, name),
        "difference_reason": difference_reason(rep),
        "original_iterations": convergence_iterations(rep, "original"),
        "transformed_iterations": convergence_iterations(rep, "otis"),
        "stress_pass": sc.get("pass"), "stress_max_abs": sc.get("max_abs_difference"), "stress_max_rel": sc.get("max_rel_difference"),
        "ddsdde_status": dd.get("status"), "ddsdde_pass": dd.get("pass"),
        "ddsdde_expected": dd.get("expected"), "ddsdde_compared_incs": dd.get("compared_increment_count"),
        "ddsdde_max_abs": dd.get("max_abs_difference"), "ddsdde_max_rel": dd.get("max_rel_difference"),
        "statev_status": sv.get("status"), "statev_pass": sv.get("pass"),
        "errors": rep.get("errors", []),
    })
    return rec


def main():
    if TABLE_ONLY:
        results = cached_summary_rows()
        write_summary_table(results, OUT / "summary_table.md")
        print(f"table: {OUT/'summary_table.md'}")
        return
    cfgs = sorted(p for p in SRC.glob("*.json")
                  if p.name != "completion_report.json" and not p.name.endswith("_report.json"))
    print(f"mode: {'STRIP constant/real' if STRIP else 'as-shipped'} | cases: {len(cfgs)} | out: {OUT}")
    results = []
    for cp in cfgs:
        t0 = time.time()
        try:
            rec = validate_one(cp)
        except Exception as exc:
            rec = {"case": cp.stem, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-500:]}
        rec["seconds"] = round(time.time() - t0, 1)
        results.append(rec)
        print(f"  [{rec.get('overall_status', rec.get('error','?')):<22}] {rec['case']:<16} "
              f"ddsdde={rec.get('ddsdde_status','-'):<12} "
              f"abs={rec.get('ddsdde_max_abs')} rel={rec.get('ddsdde_max_rel')} ({rec['seconds']}s)")
    (OUT / "summary.json").write_text(json.dumps(results, indent=2, sort_keys=True))
    write_summary_table(results, OUT / "summary_table.md")
    n = len(results)
    dd_pass = sum(1 for r in results if r.get("ddsdde_status") == "passed")
    overall = sum(1 for r in results if r.get("overall_pass"))
    print("=" * 70)
    print(f"overall pass: {overall}/{n} | ddsdde compared&passed: {dd_pass}/{n}")
    print(f"summary: {OUT/'summary.json'}")
    print(f"table: {OUT/'summary_table.md'}")


def write_summary_table(results: list[dict[str, Any]], path: Path) -> None:
    lines = [
        "# Validation Difference Summary",
        "",
        "| Example UMAT | Difference (absolute / relative) | Why there is a difference | Physics model | Original iterations | Transformed UMAT iterations |",
        "|---|---|---|---|---:|---:|",
    ]
    for row in results:
        difference = f"{_fmt(row.get('ddsdde_max_abs'))} / {_fmt(row.get('ddsdde_max_rel'))}"
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_md(row.get("case", "")),
                    _escape_md(difference),
                    _escape_md(row.get("difference_reason") or row.get("error") or "n/a"),
                    _escape_md(row.get("physics_model", "n/a")),
                    _escape_md(row.get("original_iterations")),
                    _escape_md(row.get("transformed_iterations")),
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cached_summary_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cfgs = sorted(p for p in SRC.glob("*.json")
                  if p.name != "completion_report.json" and not p.name.endswith("_report.json"))
    for config_path in cfgs:
        case = config_path.stem
        report_path = OUT / case / "out" / "validation" / "comparison_report.json"
        if not report_path.is_file():
            rows.append({"case": case, "error": f"Missing cached report: {report_path}"})
            continue
        try:
            config, _source_path, _ntens = load_resolved(config_path)
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            rows.append({"case": case, "error": f"{type(exc).__name__}: {exc}"})
            continue
        max_abs, max_rel = comparison_difference(report)
        rows.append(
            {
                "case": case,
                "physics_model": physics_model(config, case),
                "difference_reason": difference_reason(report),
                "ddsdde_max_abs": max_abs,
                "ddsdde_max_rel": max_rel,
                "original_iterations": convergence_iterations(report, "original"),
                "transformed_iterations": convergence_iterations(report, "otis"),
                "overall_pass": report.get("pass"),
                "overall_status": report.get("status"),
            }
        )
    return rows


if __name__ == "__main__":
    main()
