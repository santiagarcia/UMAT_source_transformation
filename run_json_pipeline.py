from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from umat_oti.cli_json import run_config_transform
from umat_oti.core.config_loader import load_project_config_json
from umat_oti.core.transformation_anchors import merge_completed_anchors_into_config
from umat_oti.validation.abaqus_runner import extract_results, run_both_jobs
from umat_oti.validation.compare_results import compare_validation_results
from umat_oti.validation.job_builder import DEFAULT_ABAQUS_MODULES, DEFAULT_ABAQUS_RUN_PREFIX, build_validation_workspace


# Quick use:
# 1. Put your JSON files in ROOT / "user_jsons" or another folder.
# 2. Set JSON_INPUT_PATH to that file or folder.
# 3. Set OUTPUT_DIRECTORY to where you want the generated results.
# 4. Run: /usr/bin/python3.12 run_json_pipeline.py
#
# JSON_INPUT_PATH can point to a single user-authored JSON file or a directory of
# user-authored JSON files. This script does not create or modify JSON configs.
JSON_INPUT_PATH = ROOT / "examples"
OUTPUT_DIRECTORY = ROOT / "umat_oti_workspace" / "json_pipeline_runs"

# Validation compares the transformed UMAT against the original UMAT using the
# Abaqus pipeline. Leave this off for shareable transformation-only testing, and
# enable it only on a machine with Abaqus configured.
RUN_VALIDATION = False
REUSE_VALIDATION_RESULTS = False
ABAQUS_COMMAND = "abaqus"
ABAQUS_MODULES = DEFAULT_ABAQUS_MODULES
RUN_PREFIX = DEFAULT_ABAQUS_RUN_PREFIX
ABAQUS_JOB_TIMEOUT_SECONDS = 1800
ABAQUS_EXTRACT_TIMEOUT_SECONDS = 600


def _resolve_path(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (ROOT / expanded).resolve()


def _resolve_config_paths(path: Path) -> list[Path]:
    resolved = _resolve_path(path)
    if resolved.is_file():
        if resolved.suffix.lower() != ".json":
            raise ValueError(f"Expected a .json file, got: {resolved}")
        return [resolved]
    if resolved.is_dir():
        config_paths = [
            candidate
            for candidate in sorted(resolved.glob("*.json"))
            if candidate.name != "completion_report.json" and not candidate.name.endswith("_report.json")
        ]
        if not config_paths:
            raise FileNotFoundError(f"No JSON files found in directory: {resolved}")
        return config_paths
    raise FileNotFoundError(f"JSON input path not found: {resolved}")


def _status_label(exit_code: int) -> str:
    if exit_code == 0:
        return "transformation_succeeded"
    if exit_code == 2:
        return "needs_json_completion"
    return "transformation_failed"


def _load_resolved_config(config_path: Path) -> tuple[dict[str, Any], Path, int]:
    config = load_project_config_json(config_path.read_bytes(), origin_path=config_path)
    source = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
    source_path = Path(str(source.get("selected_umat_file", ""))).expanduser()
    source_text = source_path.read_text(encoding="utf-8", errors="replace") if source_path.is_file() else ""
    if source_text:
        config = merge_completed_anchors_into_config(config, source_text)
    settings = config.get("transformation_settings", {}) if isinstance(config.get("transformation_settings"), dict) else {}
    ntens = int(settings.get("ntens") or 0)
    return config, source_path, ntens


def _material_test_mode(config: dict[str, Any]) -> str:
    validation_settings = config.get("validation_settings", {}) if isinstance(config.get("validation_settings"), dict) else {}
    if validation_settings.get("material_test_mode"):
        return str(validation_settings["material_test_mode"])
    analysis = config.get("analysis", {}) if isinstance(config.get("analysis"), dict) else {}
    finite = analysis.get("finite_strain", {}) if isinstance(analysis.get("finite_strain"), dict) else {}
    plastic = analysis.get("plasticity_indicators", {}) if isinstance(analysis.get("plasticity_indicators"), dict) else {}
    if plastic.get("is_plasticity_candidate") and finite.get("executable_dfgrd_use"):
        return "single element plastic finite strain tension"
    if plastic.get("is_plasticity_candidate"):
        return "single element plastic tension"
    if finite.get("executable_dfgrd_use"):
        return "single element plastic finite strain tension"
    return "single element tension"


def _validation_compare_outputs(config: dict[str, Any]) -> list[str] | None:
    validation_settings = config.get("validation_settings", {}) if isinstance(config.get("validation_settings"), dict) else {}
    compare_outputs = validation_settings.get("compare_outputs")
    if not isinstance(compare_outputs, list):
        return None
    values = [str(value).upper() for value in compare_outputs if str(value).strip()]
    return values or None


def _validation_float(config: dict[str, Any], key: str) -> float | None:
    validation_settings = config.get("validation_settings", {}) if isinstance(config.get("validation_settings"), dict) else {}
    value = validation_settings.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _comparison_report_payload(comparison: Any, validation_dir: Path) -> dict[str, Any]:
    report_path = validation_dir / "comparison_report.json"
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return comparison.to_json() if hasattr(comparison, "to_json") else {}


def _validation_status_from_report(report: dict[str, Any]) -> str:
    if report.get("pass"):
        return "validation_passed"
    status = str(report.get("status", "failed"))
    if status == "missing_results":
        return "validation_missing_results"
    if status == "failed_execution":
        return "validation_failed_execution"
    return "validation_failed"


def _run_validation_pipeline(config_path: Path, transform_summary: dict[str, Any]) -> tuple[dict[str, Any], int]:
    config, source_path, ntens = _load_resolved_config(config_path)
    transform_dir = Path(str(transform_summary.get("out_dir", ""))).resolve()
    transformed_source = Path(str(transform_summary.get("transformed_source", ""))).resolve()
    validation_dir = transform_dir / "validation"
    comparison_report_path = validation_dir / "comparison_report.json"

    try:
        if REUSE_VALIDATION_RESULTS and comparison_report_path.is_file():
            comparison_report = json.loads(comparison_report_path.read_text(encoding="utf-8"))
        else:
            build_validation_workspace(
                validation_dir=validation_dir,
                original_umat=source_path,
                transformed_umat=transformed_source,
                generated_dir=transform_dir,
                project_config=config,
                ntens=ntens,
                abaqus_command=ABAQUS_COMMAND,
                abaqus_modules=ABAQUS_MODULES,
                run_prefix=RUN_PREFIX,
                material_test_mode=_material_test_mode(config),
                run_compile_smoke=False,
                compare_outputs=_validation_compare_outputs(config),
                comparison_abs_tolerance=_validation_float(config, "absolute_tolerance"),
                comparison_rel_tolerance=_validation_float(config, "relative_tolerance"),
                comparison_ddsdde_abs_tolerance=_validation_float(config, "ddsdde_absolute_tolerance"),
                comparison_ddsdde_rel_tolerance=_validation_float(config, "ddsdde_relative_tolerance"),
            )
            run_both_jobs(
                validation_dir,
                ABAQUS_COMMAND,
                ABAQUS_MODULES,
                RUN_PREFIX,
                timeout_seconds=ABAQUS_JOB_TIMEOUT_SECONDS,
            )
            extract_results(
                validation_dir,
                ABAQUS_COMMAND,
                ABAQUS_MODULES,
                RUN_PREFIX,
                timeout_seconds=ABAQUS_EXTRACT_TIMEOUT_SECONDS,
            )
            comparison = compare_validation_results(validation_dir)
            comparison_report = _comparison_report_payload(comparison, validation_dir)
    except Exception as exc:
        return {
            "validation_requested": True,
            "validation_status": "validation_failed_execution",
            "validation_dir": str(validation_dir),
            "validation_error": f"{type(exc).__name__}: {exc}",
            "comparison_report": str(comparison_report_path),
        }, 1

    passed = bool(comparison_report.get("pass"))
    return {
        "validation_requested": True,
        "validation_status": _validation_status_from_report(comparison_report),
        "validation_dir": str(validation_dir),
        "comparison_report": str(comparison_report_path),
        "comparison_pass": passed,
        "comparison_status": comparison_report.get("status"),
        "stress_comparison": comparison_report.get("stress_comparison", {}),
        "state_variable_comparison": comparison_report.get("state_variable_comparison", {}),
        "ddsdde_comparison": comparison_report.get("ddsdde_comparison", {}),
        "constitutive_comparison": comparison_report.get("constitutive_comparison", {}),
        "convergence_comparison": comparison_report.get("convergence_comparison", {}),
        "activation_check": comparison_report.get("activation_check", {}),
        "validation_errors": comparison_report.get("errors", []),
        "validation_warnings": comparison_report.get("warnings", []),
    }, 0 if passed else 1


def _final_status(transform_exit_code: int, summary: dict[str, Any]) -> str:
    if transform_exit_code == 2:
        return "needs_json_completion"
    if transform_exit_code != 0:
        return "transformation_failed"
    if not RUN_VALIDATION:
        return "transformation_succeeded"
    return str(summary.get("validation_status") or "validation_failed")


def main() -> int:
    config_paths = _resolve_config_paths(JSON_INPUT_PATH)
    output_root = _resolve_path(OUTPUT_DIRECTORY)
    output_root.mkdir(parents=True, exist_ok=True)

    print(f"JSON input path: { _resolve_path(JSON_INPUT_PATH) }")
    print(f"Output directory: {output_root}")
    print(f"Configs to process: {len(config_paths)}")
    print(f"Run validation: {RUN_VALIDATION}")

    results = []
    for config_path in config_paths:
        case_output_dir = output_root if len(config_paths) == 1 else output_root / config_path.stem
        summary, exit_code = run_config_transform(config_path, case_output_dir)
        if RUN_VALIDATION and exit_code == 0:
            validation_summary, validation_exit_code = _run_validation_pipeline(config_path, summary)
            summary.update(validation_summary)
            exit_code = validation_exit_code
        else:
            summary["validation_requested"] = False
        summary["status"] = _final_status(0 if summary.get("transform_success") else exit_code, summary) if RUN_VALIDATION else _status_label(exit_code)
        summary["exit_code"] = exit_code
        results.append(summary)
        print(f"[{summary['status']}] {config_path.name} -> {summary.get('out_dir', case_output_dir)}")
        if summary.get("validation_requested"):
            print(f"  comparison report: {summary.get('comparison_report', '')}")
            print(f"  comparison pass: {summary.get('comparison_pass')}")

    payload = {
        "json_input_path": str(_resolve_path(JSON_INPUT_PATH)),
        "output_directory": str(output_root),
        "run_validation": RUN_VALIDATION,
        "results": results,
    }
    status_path = output_root / "pipeline_status.json"
    status_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote pipeline summary to {status_path}")

    return 0 if all(result.get("exit_code") == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())