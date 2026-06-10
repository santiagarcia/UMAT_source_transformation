from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from umat_oti.validation.job_builder import update_validation_report


DEFAULT_STRESS_ABS_TOLERANCE = 1.0e-5
DEFAULT_STRESS_REL_TOLERANCE = 1.0e-7
DEFAULT_DDSDDE_ABS_TOLERANCE = 5.0e-2
DEFAULT_DDSDDE_REL_TOLERANCE = 5.0e-3


@dataclass
class ComparisonResult:
    status: str
    passed: bool
    report_json_path: Path
    report_md_path: Path
    max_abs_difference: float | None = None
    max_rel_difference: float | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pass": self.passed,
            "max_abs_difference": self.max_abs_difference,
            "max_rel_difference": self.max_rel_difference,
            "report_json_path": str(self.report_json_path),
            "report_md_path": str(self.report_md_path),
            "warnings": self.warnings,
            "errors": self.errors,
        }


def compare_validation_results(
    validation_dir: Path,
    abs_tolerance: float | None = None,
    rel_tolerance: float | None = None,
    ddsdde_abs_tolerance: float | None = None,
    ddsdde_rel_tolerance: float | None = None,
) -> ComparisonResult:
    validation_dir = Path(validation_dir)
    json_path = validation_dir / "comparison_report.json"
    md_path = validation_dir / "comparison_report.md"
    original_path = validation_dir / "original_results.json"
    otis_path = validation_dir / "otis_results.json"
    validation_report = _load_validation_report(validation_dir)
    comparison_settings = _comparison_settings(validation_report)
    abs_tolerance = comparison_settings["absolute_tolerance"] if abs_tolerance is None else abs_tolerance
    rel_tolerance = comparison_settings["relative_tolerance"] if rel_tolerance is None else rel_tolerance
    ddsdde_abs_tolerance = comparison_settings["ddsdde_absolute_tolerance"] if ddsdde_abs_tolerance is None else ddsdde_abs_tolerance
    ddsdde_rel_tolerance = comparison_settings["ddsdde_relative_tolerance"] if ddsdde_rel_tolerance is None else ddsdde_rel_tolerance
    compare_outputs = comparison_settings["compare_outputs"]
    execution_errors = _execution_status_errors(validation_report)
    errors: list[str] = []
    if not original_path.is_file():
        errors.append(f"Missing original results file: {original_path}")
    if not otis_path.is_file():
        errors.append(f"Missing OTIS results file: {otis_path}")
    if errors:
        all_errors = [*execution_errors, *errors]
        report = _comparison_report(
            "failed_execution" if execution_errors else "missing_results",
            False,
            abs_tolerance,
            rel_tolerance,
            ddsdde_abs_tolerance,
            ddsdde_rel_tolerance,
            {},
            {},
            {},
            {},
            {},
            {},
            all_errors,
            [],
        )
        _write_reports(json_path, md_path, report)
        update_validation_report(validation_dir, {"comparison_status": report, "errors": all_errors, "final_pass": False, "status": "failed"})
        return ComparisonResult(report["status"], False, json_path, md_path, errors=all_errors)
    original = json.loads(original_path.read_text(encoding="utf-8"))
    otis = json.loads(otis_path.read_text(encoding="utf-8"))
    original_stress = _stress_vector(original)
    otis_stress = _stress_vector(otis)
    if not original_stress or not otis_stress:
        errors.append("Both results must contain final_stress or stress vectors.")
    if len(original_stress) != len(otis_stress):
        errors.append(f"Stress vector lengths differ: original={len(original_stress)}, otis={len(otis_stress)}")
    if errors:
        stress_comparison = _vector_comparison(original_stress, otis_stress, abs_tolerance, rel_tolerance)
        all_errors = [*execution_errors, *errors]
        report = _comparison_report(
            "failed_execution" if execution_errors else "invalid_results",
            False,
            abs_tolerance,
            rel_tolerance,
            ddsdde_abs_tolerance,
            ddsdde_rel_tolerance,
            stress_comparison,
            {},
            {},
            {},
            {},
            {},
            all_errors,
            [],
        )
        _write_reports(json_path, md_path, report)
        update_validation_report(validation_dir, {"comparison_status": report, "errors": all_errors, "final_pass": False, "status": "failed"})
        return ComparisonResult(report["status"], False, json_path, md_path, errors=all_errors)
    warnings: list[str] = []
    stress_comparison = _vector_comparison(original_stress, otis_stress, abs_tolerance, rel_tolerance) if "STRESS" in compare_outputs else _skipped_comparison("Stress comparison not requested.")
    state_comparison = _state_comparison(original, otis, abs_tolerance, rel_tolerance, warnings) if "STATEV" in compare_outputs else _skipped_comparison("State variable comparison not requested.")
    convergence_comparison = _convergence_comparison(validation_dir) if "CONVERGENCE" in compare_outputs else _skipped_comparison("Convergence comparison not requested.")
    activation_check = _activation_check(validation_dir, original, otis)
    ddsdde_comparison = _ddsdde_comparison(validation_dir, original, otis, ddsdde_abs_tolerance, ddsdde_rel_tolerance, warnings) if "DDSDDE" in compare_outputs else _skipped_comparison("DDSDDE comparison not requested.")
    constitutive_comparison = _constitutive_comparison(validation_dir, original, otis, ddsdde_abs_tolerance, ddsdde_rel_tolerance, warnings) if "CONSTITUTIVE_JACOBIANS" in compare_outputs else _skipped_comparison("Constitutive Jacobian comparison not requested.")
    passed = (
        not execution_errors
        and
        bool(stress_comparison.get("pass"))
        and bool(state_comparison.get("pass", True))
        and bool(convergence_comparison.get("pass", True))
        and bool(activation_check.get("pass", True))
        and bool(ddsdde_comparison.get("pass", True))
        and bool(constitutive_comparison.get("pass", True))
    )
    status = "passed" if passed else ("failed_execution" if execution_errors else "failed")
    report = _comparison_report(
        status,
        passed,
        abs_tolerance,
        rel_tolerance,
        ddsdde_abs_tolerance,
        ddsdde_rel_tolerance,
        stress_comparison,
        state_comparison,
        convergence_comparison,
        activation_check,
        ddsdde_comparison,
        constitutive_comparison,
        execution_errors,
        warnings,
    )
    _write_reports(json_path, md_path, report)
    update_validation_report(
        validation_dir,
        {
            "comparison_status": report,
            "stress_comparison": report["stress_comparison"],
            "state_variable_comparison": report["state_variable_comparison"],
            "convergence_comparison": report["convergence_comparison"],
            "activation_check": report["activation_check"],
            "ddsdde_comparison": report["ddsdde_comparison"],
            "constitutive_comparison": report["constitutive_comparison"],
            "final_pass": passed,
            "errors": execution_errors,
            "status": "passed" if passed else "failed",
        },
    )
    return ComparisonResult(
        report["status"],
        passed,
        json_path,
        md_path,
        stress_comparison.get("max_abs_difference"),
        stress_comparison.get("max_rel_difference"),
        errors=execution_errors,
        warnings=warnings,
    )


def _stress_vector(result: dict[str, Any]) -> list[float]:
    values = result.get("final_stress", result.get("stress", []))
    return [float(value) for value in values]


def _comparison_settings(validation_report: dict[str, Any]) -> dict[str, Any]:
    settings = validation_report.get("comparison_settings") if isinstance(validation_report.get("comparison_settings"), dict) else {}
    compare_outputs_raw = settings.get("compare_outputs") if isinstance(settings.get("compare_outputs"), list) else []
    compare_outputs = [str(value).upper() for value in compare_outputs_raw if str(value).strip()]
    if not compare_outputs:
        compare_outputs = ["STRESS", "STATEV", "DDSDDE", "CONVERGENCE"]
    return {
        "compare_outputs": compare_outputs,
        "absolute_tolerance": _float_or_default(settings.get("absolute_tolerance"), DEFAULT_STRESS_ABS_TOLERANCE),
        "relative_tolerance": _float_or_default(settings.get("relative_tolerance"), DEFAULT_STRESS_REL_TOLERANCE),
        "ddsdde_absolute_tolerance": _float_or_default(settings.get("ddsdde_absolute_tolerance"), DEFAULT_DDSDDE_ABS_TOLERANCE),
        "ddsdde_relative_tolerance": _float_or_default(settings.get("ddsdde_relative_tolerance"), DEFAULT_DDSDDE_REL_TOLERANCE),
    }


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _skipped_comparison(message: str) -> dict[str, Any]:
    return {"status": "not_requested", "message": message, "pass": True}


def _state_vector(result: dict[str, Any]) -> list[float]:
    values = result.get("final_state_variables", result.get("state_variables", []))
    return [float(value) for value in values]


def _execution_status_errors(validation_report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field, label in (
        ("original_run_status", "Original Abaqus validation job"),
        ("transformed_run_status", "Transformed Abaqus validation job"),
        ("extraction_status", "Result extraction"),
    ):
        payload = validation_report.get(field)
        if not isinstance(payload, dict):
            continue
        raw_status = str(payload.get("status") or "").strip()
        status = raw_status.lower()
        returncode = payload.get("returncode")
        if isinstance(returncode, int) and returncode != 0:
            errors.append(f"{label} did not complete successfully (status={raw_status or 'unknown'}, returncode={returncode}).")
            continue
        if raw_status and status not in {"completed", "configured", "not_run"}:
            errors.append(f"{label} did not complete successfully (status={raw_status}).")
    return errors


def _vector_comparison(left: list[float], right: list[float], abs_tolerance: float, rel_tolerance: float) -> dict[str, Any]:
    differences = [abs(left_value - right_value) for left_value, right_value in zip(left, right)]
    rel_differences = [diff / max(abs(left_value), abs(right_value), 1.0) for diff, left_value, right_value in zip(differences, left, right)]
    max_abs = max(differences, default=0.0)
    max_rel = max(rel_differences, default=0.0)
    return {
        "original_values": left,
        "otis_values": right,
        "max_abs_difference": max_abs,
        "max_rel_difference": max_rel,
        "pass": len(left) == len(right) and (max_abs <= abs_tolerance or max_rel <= rel_tolerance),
    }


def _state_comparison(
    original: dict[str, Any],
    otis: dict[str, Any],
    abs_tolerance: float,
    rel_tolerance: float,
    warnings: list[str],
) -> dict[str, Any]:
    original_state = _state_vector(original)
    otis_state = _state_vector(otis)
    if not original_state and not otis_state:
        return {"status": "not_available", "message": "No final state variables were reported by either job.", "pass": True}
    if not original_state or not otis_state:
        warnings.append("State variables were available for only one validation job; state comparison was skipped.")
        return {
            "status": "partial",
            "original_values": original_state,
            "otis_values": otis_state,
            "message": "State variables were available for only one validation job.",
            "pass": True,
        }
    comparison = _vector_comparison(original_state, otis_state, abs_tolerance, rel_tolerance)
    comparison["status"] = "passed" if comparison["pass"] else "failed"
    return comparison


def _convergence_comparison(validation_dir: Path) -> dict[str, Any]:
    original = _convergence_metrics(validation_dir, "original_umat_validation")
    otis = _convergence_metrics(validation_dir, "otis_umat_validation")
    if original.get("status") == "not_available" and otis.get("status") == "not_available":
        return {"status": "not_available", "message": "Abaqus convergence files were not available.", "pass": True}
    keys = ["completed", "increments", "cutbacks", "iterations", "input_warnings", "analysis_warnings", "error_messages"]
    differences = {key: {"original": original.get(key), "otis": otis.get(key)} for key in keys if original.get(key) != otis.get(key)}
    blocking_differences = {key: value for key, value in differences.items() if key != "iterations"}
    return {
        "status": "passed" if not blocking_differences else "failed",
        "pass": not blocking_differences,
        "original": original,
        "otis": otis,
        "differences": differences,
        "blocking_differences": blocking_differences,
    }


def _convergence_metrics(validation_dir: Path, stem: str) -> dict[str, Any]:
    sta_path = validation_dir / f"{stem}.sta"
    msg_path = validation_dir / f"{stem}.msg"
    dat_path = validation_dir / f"{stem}.dat"
    if not sta_path.exists() and not msg_path.exists() and not dat_path.exists():
        return {"status": "not_available"}
    sta_text = sta_path.read_text(encoding="utf-8", errors="ignore") if sta_path.exists() else ""
    msg_text = msg_path.read_text(encoding="utf-8", errors="ignore") if msg_path.exists() else ""
    dat_text = dat_path.read_text(encoding="utf-8", errors="ignore") if dat_path.exists() else ""
    combined = "\n".join([sta_text, msg_text, dat_text])
    return {
        "status": "available",
        "completed": "THE ANALYSIS HAS COMPLETED" in combined.upper(),
        "increments": _first_int(r"TOTAL OF\s+(\d+)\s+INCREMENTS", msg_text) or _sta_increment_count(sta_text),
        "cutbacks": _first_int(r"(\d+)\s+CUTBACKS IN AUTOMATIC INCREMENTATION", msg_text),
        "iterations": _first_int(r"(\d+)\s+ITERATIONS INCLUDING CONTACT ITERATIONS", msg_text),
        "input_warnings": _first_int(r"(\d+)\s+WARNING MESSAGES DURING USER INPUT PROCESSING", msg_text),
        "analysis_warnings": _first_int(r"(\d+)\s+WARNING MESSAGES DURING ANALYSIS", msg_text),
        "error_messages": _first_int(r"(\d+)\s+ERROR MESSAGES", msg_text),
        "sta_increment_rows": _sta_increment_rows(sta_text),
    }


def _sta_increment_count(text: str) -> int | None:
    rows = _sta_increment_rows(text)
    return len(rows) if rows else None


def _sta_increment_rows(text: str) -> list[dict[str, int]]:
    rows: list[dict[str, int]] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+", line)
        if not match:
            continue
        rows.append(
            {
                "step": int(match.group(1)),
                "increment": int(match.group(2)),
                "attempt": int(match.group(3)),
                "severe_discontinuity_iterations": int(match.group(4)),
                "equilibrium_iterations": int(match.group(5)),
                "total_iterations": int(match.group(6)),
            }
        )
    return rows


def _first_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _activation_check(validation_dir: Path, original: dict[str, Any], otis: dict[str, Any]) -> dict[str, Any]:
    validation_report = _load_validation_report(validation_dir)
    load_case = validation_report.get("load_case", {}) if isinstance(validation_report.get("load_case"), dict) else {}
    expected_plasticity = bool(load_case.get("expected_plasticity"))
    expected_finite_geometry = bool(load_case.get("expected_finite_geometry")) if "expected_finite_geometry" in load_case else str(load_case.get("nlgeom", "NO")).upper() == "YES"
    original_state = _state_vector(original)
    otis_state = _state_vector(otis)
    result = {
        "expected_plasticity": expected_plasticity,
        "expected_finite_geometry": expected_finite_geometry,
        "nlgeom": load_case.get("nlgeom", "NO"),
        "pass": True,
        "status": "not_required",
    }
    if not expected_plasticity:
        return result
    result["original_state_variables"] = original_state
    result["otis_state_variables"] = otis_state
    result["plastic_state_threshold"] = 1.0e-12
    if not original_state or not otis_state:
        result.update(
            {
                "pass": False,
                "status": "not_available",
                "message": "Plasticity was requested, but state variables were not available in one or both ODB extractions.",
            }
        )
        return result
    original_active = any(abs(value) > 1.0e-12 for value in original_state)
    otis_active = any(abs(value) > 1.0e-12 for value in otis_state)
    result.update(
        {
            "original_plasticity_active": original_active,
            "otis_plasticity_active": otis_active,
            "pass": original_active and otis_active,
            "status": "passed" if original_active and otis_active else "failed",
        }
    )
    if not result["pass"]:
        result["message"] = "Plasticity was requested, but one or both jobs ended with zero plastic state variables."
    return result


def _ddsdde_comparison(
    validation_dir: Path,
    original: dict[str, Any],
    otis: dict[str, Any],
    abs_tolerance: float,
    rel_tolerance: float,
    warnings: list[str],
) -> dict[str, Any]:
    validation_report = _load_validation_report(validation_dir)
    validation_config = validation_report.get("ddsdde_validation", {}) if isinstance(validation_report.get("ddsdde_validation"), dict) else {}
    enabled = bool(validation_config.get("enabled")) or validation_config.get("status") == "configured"
    original_series = _ddsdde_series(original)
    otis_series = _ddsdde_series(otis)
    result: dict[str, Any] = {
        "absolute_tolerance": abs_tolerance,
        "relative_tolerance": rel_tolerance,
        "expected": enabled,
        "original_increment_count": len(original_series),
        "otis_increment_count": len(otis_series),
        "compared_increment_count": 0,
        "max_abs_difference": None,
        "max_rel_difference": None,
        "pass": True,
        "status": "not_required" if not enabled else "not_available",
    }
    if not original_series and not otis_series:
        if enabled:
            result.update(
                {
                    "pass": False,
                    "message": "DDSDDE validation was configured, but no DDSDDE SDV data was extracted from either ODB.",
                }
            )
        return result
    if not original_series or not otis_series:
        result.update(
            {
                "pass": not enabled,
                "status": "partial",
                "message": "DDSDDE SDV data was available for only one validation job.",
            }
        )
        if not enabled:
            warnings.append("DDSDDE data was available for only one validation job; comparison was skipped because validation was not configured.")
        return result
    paired_count = min(len(original_series), len(otis_series))
    increment_results: list[dict[str, Any]] = []
    max_abs = 0.0
    max_rel = 0.0
    worst: dict[str, Any] = {}
    for pair_index in range(paired_count):
        original_record = original_series[pair_index]
        otis_record = otis_series[pair_index]
        comparison = _vector_comparison(original_record["values"], otis_record["values"], abs_tolerance, rel_tolerance)
        increment_result = {
            "pair_index": pair_index,
            "original_frame_index": original_record.get("frame_index"),
            "otis_frame_index": otis_record.get("frame_index"),
            "original_increment_number": original_record.get("increment_number"),
            "otis_increment_number": otis_record.get("increment_number"),
            "component_count": len(original_record["values"]),
            "max_abs_difference": comparison["max_abs_difference"],
            "max_rel_difference": comparison["max_rel_difference"],
            "pass": comparison["pass"],
        }
        increment_results.append(increment_result)
        if comparison["max_abs_difference"] >= max_abs:
            max_abs = comparison["max_abs_difference"]
            worst = increment_result
        max_rel = max(max_rel, comparison["max_rel_difference"])
    count_match = len(original_series) == len(otis_series)
    component_lengths_match = all(len(original_series[index]["values"]) == len(otis_series[index]["values"]) for index in range(paired_count))
    all_increments_pass = all(row["pass"] for row in increment_results)
    passed = count_match and component_lengths_match and all_increments_pass
    result.update(
        {
            "status": "passed" if passed else "failed",
            "pass": passed,
            "compared_increment_count": paired_count,
            "max_abs_difference": max_abs,
            "max_rel_difference": max_rel,
            "worst_increment": worst,
            "increments": increment_results,
        }
    )
    if not count_match:
        result["message"] = "Original and OTIS jobs produced different DDSDDE increment counts."
    elif not component_lengths_match:
        result["message"] = "Original and OTIS DDSDDE vectors have different component counts."
    return result


def _ddsdde_series(result: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    increments = result.get("increments", [])
    if isinstance(increments, list):
        for index, increment in enumerate(increments):
            if not isinstance(increment, dict):
                continue
            values = _flat_ddsdde_values(increment)
            if values:
                records.append(
                    {
                        "frame_index": increment.get("frame_index", index),
                        "increment_number": increment.get("increment_number"),
                        "frame_value": increment.get("frame_value"),
                        "values": values,
                    }
                )
    if records:
        return records
    values = _flat_ddsdde_values(result)
    if values:
        return [{"frame_index": None, "increment_number": None, "frame_value": None, "values": values}]
    return []


def _flat_ddsdde_values(record: dict[str, Any]) -> list[float]:
    values = record.get("ddsdde_flat", record.get("final_ddsdde_flat", []))
    if not values and isinstance(record.get("ddsdde"), list):
        values = [component for row in record.get("ddsdde", []) for component in (row if isinstance(row, list) else [row])]
    if not values and isinstance(record.get("final_ddsdde"), list):
        values = [component for row in record.get("final_ddsdde", []) for component in (row if isinstance(row, list) else [row])]
    return [float(value) for value in values]


def _constitutive_comparison(
    validation_dir: Path,
    original: dict[str, Any],
    otis: dict[str, Any],
    abs_tolerance: float,
    rel_tolerance: float,
    warnings: list[str],
) -> dict[str, Any]:
    validation_report = _load_validation_report(validation_dir)
    validation_config = validation_report.get("constitutive_validation", {}) if isinstance(validation_report.get("constitutive_validation"), dict) else {}
    artifacts = validation_config.get("artifacts") if isinstance(validation_config.get("artifacts"), list) else []
    comparable_artifacts = [artifact for artifact in artifacts if isinstance(artifact, dict) and artifact.get("comparison_status") == "comparable"]
    preview_only = [artifact for artifact in artifacts if isinstance(artifact, dict) and artifact.get("comparison_status") != "comparable"]
    enabled = bool(validation_config.get("enabled")) and bool(comparable_artifacts)
    result: dict[str, Any] = {
        "absolute_tolerance": abs_tolerance,
        "relative_tolerance": rel_tolerance,
        "expected": enabled,
        "available_artifact_count": len(comparable_artifacts),
        "preview_only_artifact_count": len(preview_only),
        "compared_artifact_count": 0,
        "max_abs_difference": None,
        "max_rel_difference": None,
        "artifacts": [],
        "preview_only_artifacts": preview_only,
        "pass": True,
        "status": "not_required" if not comparable_artifacts else "not_available",
    }
    if not comparable_artifacts:
        if preview_only:
            result["message"] = "Constitutive Jacobian preview artifacts are available, but none are directly comparable in the original UMAT scope."
        return result
    artifact_results: list[dict[str, Any]] = []
    global_max_abs = 0.0
    global_max_rel = 0.0
    for artifact in comparable_artifacts:
        artifact_id = str(artifact.get("id") or artifact.get("target_variable") or "")
        artifact_result = _constitutive_artifact_comparison(artifact_id, artifact, original, otis, abs_tolerance, rel_tolerance, enabled, warnings)
        artifact_results.append(artifact_result)
        max_abs = _optional_float(artifact_result.get("max_abs_difference")) or 0.0
        max_rel = _optional_float(artifact_result.get("max_rel_difference")) or 0.0
        global_max_abs = max(global_max_abs, max_abs)
        global_max_rel = max(global_max_rel, max_rel)
    passed = all(bool(artifact.get("pass", True)) for artifact in artifact_results)
    result.update(
        {
            "status": "passed" if passed else "failed",
            "pass": passed,
            "compared_artifact_count": len(artifact_results),
            "max_abs_difference": global_max_abs if artifact_results else None,
            "max_rel_difference": global_max_rel if artifact_results else None,
            "artifacts": artifact_results,
        }
    )
    return result


def _constitutive_artifact_comparison(
    artifact_id: str,
    artifact_config: dict[str, Any],
    original: dict[str, Any],
    otis: dict[str, Any],
    abs_tolerance: float,
    rel_tolerance: float,
    enabled: bool,
    warnings: list[str],
) -> dict[str, Any]:
    original_series = _constitutive_series(original, artifact_id)
    otis_series = _constitutive_series(otis, artifact_id)
    result: dict[str, Any] = {
        "id": artifact_id,
        "target_variable": str(artifact_config.get("target_variable") or ""),
        "artifact_class": str(artifact_config.get("artifact_class") or "constitutive_artifact"),
        "component_count": int(artifact_config.get("component_count") or 0),
        "component_labels": list(artifact_config.get("component_labels") or []),
        "absolute_tolerance": abs_tolerance,
        "relative_tolerance": rel_tolerance,
        "original_increment_count": len(original_series),
        "otis_increment_count": len(otis_series),
        "compared_increment_count": 0,
        "max_abs_difference": None,
        "max_rel_difference": None,
        "pass": True,
        "status": "not_required" if not enabled else "not_available",
    }
    if not original_series and not otis_series:
        if enabled:
            result.update(
                {
                    "pass": False,
                    "message": "Configured constitutive Jacobian artifact was not extracted from either ODB.",
                }
            )
        return result
    if not original_series or not otis_series:
        result.update(
            {
                "pass": not enabled,
                "status": "partial",
                "message": "Constitutive Jacobian data was available for only one validation job.",
            }
        )
        if not enabled:
            warnings.append("Constitutive Jacobian data was available for only one validation job; comparison was skipped because it was not requested.")
        return result
    paired_count = min(len(original_series), len(otis_series))
    increment_results: list[dict[str, Any]] = []
    max_abs = 0.0
    max_rel = 0.0
    worst: dict[str, Any] = {}
    for pair_index in range(paired_count):
        original_record = original_series[pair_index]
        otis_record = otis_series[pair_index]
        comparison = _vector_comparison(original_record["values"], otis_record["values"], abs_tolerance, rel_tolerance)
        increment_result = {
            "pair_index": pair_index,
            "original_frame_index": original_record.get("frame_index"),
            "otis_frame_index": otis_record.get("frame_index"),
            "original_increment_number": original_record.get("increment_number"),
            "otis_increment_number": otis_record.get("increment_number"),
            "component_count": len(original_record["values"]),
            "max_abs_difference": comparison["max_abs_difference"],
            "max_rel_difference": comparison["max_rel_difference"],
            "pass": comparison["pass"],
        }
        increment_results.append(increment_result)
        if comparison["max_abs_difference"] >= max_abs:
            max_abs = comparison["max_abs_difference"]
            worst = increment_result
        max_rel = max(max_rel, comparison["max_rel_difference"])
    count_match = len(original_series) == len(otis_series)
    component_lengths_match = all(len(original_series[index]["values"]) == len(otis_series[index]["values"]) for index in range(paired_count))
    all_increments_pass = all(row["pass"] for row in increment_results)
    passed = count_match and component_lengths_match and all_increments_pass
    result.update(
        {
            "status": "passed" if passed else "failed",
            "pass": passed,
            "compared_increment_count": paired_count,
            "max_abs_difference": max_abs,
            "max_rel_difference": max_rel,
            "worst_increment": worst,
            "increments": increment_results,
        }
    )
    if not count_match:
        result["message"] = "Original and OTIS jobs produced different constitutive Jacobian increment counts."
    elif not component_lengths_match:
        result["message"] = "Original and OTIS constitutive Jacobian vectors have different component counts."
    return result


def _constitutive_series(result: dict[str, Any], artifact_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    increments = result.get("increments", [])
    if isinstance(increments, list):
        for index, increment in enumerate(increments):
            if not isinstance(increment, dict):
                continue
            constitutive_outputs = increment.get("constitutive_outputs") if isinstance(increment.get("constitutive_outputs"), dict) else {}
            artifact = constitutive_outputs.get(artifact_id)
            values = _constitutive_values(artifact)
            if values:
                records.append(
                    {
                        "frame_index": increment.get("frame_index", index),
                        "increment_number": increment.get("increment_number"),
                        "frame_value": increment.get("frame_value"),
                        "values": values,
                    }
                )
    if records:
        return records
    final_outputs = result.get("final_constitutive_outputs") if isinstance(result.get("final_constitutive_outputs"), dict) else {}
    values = _constitutive_values(final_outputs.get(artifact_id))
    if values:
        return [{"frame_index": None, "increment_number": None, "frame_value": None, "values": values}]
    return []


def _constitutive_values(payload: object) -> list[float]:
    if not isinstance(payload, dict):
        return []
    values = payload.get("values")
    if not isinstance(values, list):
        return []
    return [float(value) for value in values]


def _load_validation_report(validation_dir: Path) -> dict[str, Any]:
    path = Path(validation_dir) / "validation_report.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _comparison_report(
    status: str,
    passed: bool,
    abs_tolerance: float,
    rel_tolerance: float,
    ddsdde_abs_tolerance: float,
    ddsdde_rel_tolerance: float,
    stress_comparison: dict[str, Any],
    state_comparison: dict[str, Any],
    convergence_comparison: dict[str, Any],
    activation_check: dict[str, Any],
    ddsdde_comparison: dict[str, Any],
    constitutive_comparison: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    if not stress_comparison:
        stress_comparison = {"original_values": [], "otis_values": [], "max_abs_difference": None, "max_rel_difference": None, "pass": False}
    return {
        "status": status,
        "pass": passed,
        "absolute_tolerance": abs_tolerance,
        "relative_tolerance": rel_tolerance,
        "ddsdde_absolute_tolerance": ddsdde_abs_tolerance,
        "ddsdde_relative_tolerance": ddsdde_rel_tolerance,
        "stress_comparison": {
            "original_final_stress": stress_comparison.get("original_values", []),
            "otis_final_stress": stress_comparison.get("otis_values", []),
            "max_abs_difference": stress_comparison.get("max_abs_difference"),
            "max_rel_difference": stress_comparison.get("max_rel_difference"),
            "pass": stress_comparison.get("pass", False),
        },
        "state_variable_comparison": state_comparison or {"status": "not_available", "pass": True},
        "convergence_comparison": convergence_comparison or {"status": "not_available", "pass": True},
        "activation_check": activation_check or {"status": "not_required", "pass": True, "expected_plasticity": False},
        "ddsdde_comparison": ddsdde_comparison or {"status": "not_available", "pass": True},
        "constitutive_comparison": constitutive_comparison or {"status": "not_available", "pass": True},
        "warnings": warnings,
        "errors": errors,
    }


def _write_reports(json_path: Path, md_path: Path, report: dict[str, Any]) -> None:
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Abaqus Stress Comparison",
        "",
        f"Status: `{report['status']}`",
        f"Pass: `{report['pass']}`",
        f"Absolute tolerance: `{report['absolute_tolerance']}`",
        f"Relative tolerance: `{report['relative_tolerance']}`",
        "",
        "## Stress",
        f"Original: `{report['stress_comparison']['original_final_stress']}`",
        f"OTIS: `{report['stress_comparison']['otis_final_stress']}`",
        f"Max absolute difference: `{report['stress_comparison']['max_abs_difference']}`",
        f"Max relative difference: `{report['stress_comparison']['max_rel_difference']}`",
        "",
        "## State Variables",
        f"Status: `{report['state_variable_comparison'].get('status', 'available')}`",
        f"Pass: `{report['state_variable_comparison'].get('pass')}`",
        "",
        "## DDSDDE",
        f"Status: `{report['ddsdde_comparison'].get('status')}`",
        f"Pass: `{report['ddsdde_comparison'].get('pass')}`",
        f"Compared increments: `{report['ddsdde_comparison'].get('compared_increment_count')}`",
        f"Max absolute difference: `{report['ddsdde_comparison'].get('max_abs_difference')}`",
        f"Max relative difference: `{report['ddsdde_comparison'].get('max_rel_difference')}`",
        "",
        "## Constitutive Jacobians",
        f"Status: `{report['constitutive_comparison'].get('status')}`",
        f"Pass: `{report['constitutive_comparison'].get('pass')}`",
        f"Compared artifacts: `{report['constitutive_comparison'].get('compared_artifact_count')}`",
        f"Preview-only artifacts: `{report['constitutive_comparison'].get('preview_only_artifact_count')}`",
        f"Max absolute difference: `{report['constitutive_comparison'].get('max_abs_difference')}`",
        f"Max relative difference: `{report['constitutive_comparison'].get('max_rel_difference')}`",
        "",
        "## Convergence",
        f"Status: `{report['convergence_comparison'].get('status')}`",
        f"Pass: `{report['convergence_comparison'].get('pass')}`",
        "",
        "## Activation",
        f"Status: `{report['activation_check'].get('status')}`",
        f"Expected plasticity: `{report['activation_check'].get('expected_plasticity')}`",
        f"Expected finite geometry: `{report['activation_check'].get('expected_finite_geometry')}`",
        f"Pass: `{report['activation_check'].get('pass')}`",
    ]
    if report["errors"]:
        lines.extend(["", "## Errors", *[f"- {error}" for error in report["errors"]]])
    if report["warnings"]:
        lines.extend(["", "## Warnings", *[f"- {warning}" for warning in report["warnings"]]])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")