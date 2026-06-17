"""Batch local Abaqus validation of all completed benchmark contracts.

For each JSON in json_files_completed/, transform it, build a LOCAL Abaqus
validation workspace (no module/srun), run original + transformed jobs, extract
ODB results, and compare. Collects per-case stress / STATEV / DDSDDE verdicts.

Usage:
  python3 validate_all_local.py [strip]   # 'strip' drops constant/real first
"""
from __future__ import annotations

import json, sys, copy, time, traceback
from pathlib import Path

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
        "stress_pass": sc.get("pass"), "stress_max_abs": sc.get("max_abs_difference"), "stress_max_rel": sc.get("max_rel_difference"),
        "ddsdde_status": dd.get("status"), "ddsdde_pass": dd.get("pass"),
        "ddsdde_expected": dd.get("expected"), "ddsdde_compared_incs": dd.get("compared_increment_count"),
        "ddsdde_max_abs": dd.get("max_abs_difference"), "ddsdde_max_rel": dd.get("max_rel_difference"),
        "statev_status": sv.get("status"), "statev_pass": sv.get("pass"),
        "errors": rep.get("errors", []),
    })
    return rec


def main():
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
    n = len(results)
    dd_pass = sum(1 for r in results if r.get("ddsdde_status") == "passed")
    overall = sum(1 for r in results if r.get("overall_pass"))
    print("=" * 70)
    print(f"overall pass: {overall}/{n} | ddsdde compared&passed: {dd_pass}/{n}")
    print(f"summary: {OUT/'summary.json'}")


if __name__ == "__main__":
    main()
