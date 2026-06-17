from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from umat_oti.core.config_loader import load_project_config_json
from umat_oti.core.transformation_anchors import anchor_completion_status
from umat_oti.core.transformation_anchors import merge_completed_anchors_into_config
from umat_oti.transform.source_transform import transform_umat_to_oti_from_config
from umat_oti.validation.abaqus_runner import extract_results, run_both_jobs
from umat_oti.validation.compare_results import compare_validation_results
from umat_oti.validation.job_builder import DEFAULT_ABAQUS_MODULES, DEFAULT_ABAQUS_RUN_PREFIX, build_validation_workspace


def main() -> int:
    parser = argparse.ArgumentParser(description="Run transformation/optional validation from completed UMAT JSON contracts.")
    parser.add_argument("--config-dir", type=Path, default=Path("json_files_completed"))
    parser.add_argument("--batch-dir", type=Path, default=Path("umat_oti_workspace/completed_json_batch"))
    parser.add_argument("--validate", action="store_true", help="Run Abaqus validation for semantically clean transforms.")
    parser.add_argument("--abaqus-command", default="abaqus")
    parser.add_argument("--abaqus-modules", default=DEFAULT_ABAQUS_MODULES)
    parser.add_argument("--run-prefix", default=DEFAULT_ABAQUS_RUN_PREFIX)
    parser.add_argument("--reuse-validation-results", action="store_true", help="Reuse existing comparison reports instead of rerunning Abaqus jobs.")
    args = parser.parse_args()

    config_dir = args.config_dir.resolve()
    batch_dir = args.batch_dir.resolve()
    transform_root = batch_dir / "oti_transform"
    validation_root = batch_dir / "validation"
    transform_root.mkdir(parents=True, exist_ok=True)
    validation_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for config_path in sorted(config_dir.glob("*.json")):
        if config_path.name.endswith("_report.json") or config_path.name == "completion_report.json":
            continue
        config = load_project_config_json(config_path.read_bytes(), origin_path=config_path)
        name = config_path.stem
        source_path = Path(str(config.get("source", {}).get("selected_umat_file", "")))
        source_text = source_path.read_text(encoding="utf-8", errors="replace") if source_path.is_file() else ""
        if source_text:
            config = merge_completed_anchors_into_config(config, source_text)
        ntens = _as_int((config.get("transformation_settings", {}) or {}).get("ntens"))
        completion = anchor_completion_status(config)
        row: dict[str, Any] = {
            "config": config_path.name,
            "umat": name,
            "source": str(source_path),
            "anchor_status": completion.get("status"),
            "completion_issues": completion.get("completion_issues", []),
            "transform_success": False,
            "validation_status": "not_run",
        }
        if completion.get("status") == "needs_json_completion":
            row["category"] = "needs_json_completion"
            row["status"] = "; ".join(str(issue.get("message", issue)) for issue in completion.get("completion_issues", []) if isinstance(issue, dict))
            results.append(row)
            continue
        transform_dir = transform_root / name
        transform = transform_umat_to_oti_from_config(source_text, config, transform_dir, ntens)
        row.update(
            {
                "transform_success": transform.success,
                "transform_report": str(transform.report_path or ""),
                "transformed_source": str(transform.transformed_source_path or ""),
                "blockers": transform.blockers,
                "warnings": transform.warnings,
                "semantic_checks": transform.report.get("semantic_checks", {}),
            }
        )
        if transform.blockers:
            row["category"] = "blocked_by_user_marked_unsafe" if any("marked unsafe" in blocker for blocker in transform.blockers) else "needs_json_completion" if any("JSON completion" in blocker for blocker in transform.blockers) else "transformation_generated_invalid_code"
            row["status"] = "; ".join(transform.blockers)
            results.append(row)
            continue
        if not transform.success:
            row["category"] = "transformation_generated_invalid_code"
            row["status"] = "; ".join(transform.warnings)
            results.append(row)
            continue
        row["category"] = "ready_with_json_contract"
        row["status"] = "Transformation succeeded; validation not requested."
        if args.validate:
            validation_dir = validation_root / name
            try:
                comparison_report_path = validation_dir / "comparison_report.json"
                if args.reuse_validation_results and comparison_report_path.is_file():
                    comparison_report = json.loads(comparison_report_path.read_text(encoding="utf-8"))
                else:
                    build_validation_workspace(
                        validation_dir=validation_dir,
                        original_umat=source_path,
                        transformed_umat=Path(transform.transformed_source_path or ""),
                        generated_dir=transform_dir,
                        project_config=config,
                        ntens=ntens,
                        abaqus_command=args.abaqus_command,
                        abaqus_modules=args.abaqus_modules,
                        run_prefix=args.run_prefix,
                        material_test_mode=_material_test_mode(config),
                        run_compile_smoke=False,
                        compare_outputs=_validation_compare_outputs(config),
                        comparison_abs_tolerance=_validation_float(config, "absolute_tolerance"),
                        comparison_rel_tolerance=_validation_float(config, "relative_tolerance"),
                        comparison_ddsdde_abs_tolerance=_validation_float(config, "ddsdde_absolute_tolerance"),
                        comparison_ddsdde_rel_tolerance=_validation_float(config, "ddsdde_relative_tolerance"),
                    )
                    run_both_jobs(validation_dir, args.abaqus_command, args.abaqus_modules, args.run_prefix)
                    extract_results(validation_dir, args.abaqus_command, args.abaqus_modules, args.run_prefix)
                    comparison = compare_validation_results(validation_dir)
                    comparison_report = _comparison_result_report(comparison)
                comparison_passed = bool(comparison_report.get("pass"))
                row["validation_status"] = "passed" if comparison_passed else "failed"
                row["comparison_report"] = str(comparison_report_path)
                row["category"] = _validation_category(validation_dir, comparison_report)
                row["status"] = _validation_status_message(validation_dir, comparison_report)
            except Exception as exc:
                row["validation_status"] = "failed"
                row["category"] = "transformed_but_failed_compile"
                row["status"] = f"Validation raised {type(exc).__name__}: {exc}"
        results.append(row)

    _write_reports(batch_dir, results)
    print(f"wrote batch reports in {batch_dir}")
    for row in results:
        print(f"{row['config']}\t{row['category']}\t{row.get('status','')}")
    return 0


def _write_reports(batch_dir: Path, results: list[dict[str, Any]]) -> None:
    payload = {"results": results, "category_counts": _category_counts(results)}
    (batch_dir / "completed_json_batch_report.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Completed JSON Batch Report",
        "",
        f"Total configs: {len(results)}",
        "",
        "## Category Counts",
        *[f"- {key}: {value}" for key, value in sorted(_category_counts(results).items())],
        "",
        "| UMAT | Anchor status | Category | Status |",
        "|---|---|---|---|",
    ]
    for row in results:
        status = str(row.get("status", "")).replace("|", "\\|")
        lines.append(f"| {row.get('umat','')} | {row.get('anchor_status','')} | {row.get('category','')} | {status} |")
    lines.append("")
    (batch_dir / "completed_json_batch_report.md").write_text("\n".join(lines), encoding="utf-8")


def _category_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in results:
        category = str(row.get("category", "unknown"))
        counts[category] = counts.get(category, 0) + 1
    return counts


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


def _comparison_result_report(comparison: Any) -> dict[str, Any]:
    try:
        return json.loads(Path(comparison.report_json_path).read_text(encoding="utf-8"))
    except Exception:
        return comparison.to_json() if hasattr(comparison, "to_json") else {}


def _validation_category(validation_dir: Path, comparison_report: dict[str, Any]) -> str:
    if comparison_report.get("pass"):
        return "transformed_and_validated"
    if str(comparison_report.get("status", "")) == "missing_results":
        return "transformed_but_failed_compile"
    validation_report = _load_validation_report(validation_dir)
    transformed_status = _dict(validation_report.get("transformed_run_status")).get("status")
    if transformed_status == "failed" and not (validation_dir / "otis_results.json").is_file():
        return "transformed_but_failed_compile"
    return "transformed_but_failed_validation"


def _validation_status_message(validation_dir: Path, comparison_report: dict[str, Any]) -> str:
    status = str(comparison_report.get("status", "unknown"))
    if comparison_report.get("pass"):
        stress = _dict(comparison_report.get("stress_comparison"))
        ddsdde = _dict(comparison_report.get("ddsdde_comparison"))
        return _join_status_parts(
            [
                "Validation passed",
                _difference_summary("stress", stress),
                _difference_summary("DDSDDE", ddsdde),
            ]
        )
    errors = [str(error) for error in comparison_report.get("errors", []) or [] if str(error)]
    validation_report = _load_validation_report(validation_dir)
    transformed = _dict(validation_report.get("transformed_run_status"))
    transformed_message = str(transformed.get("message", ""))
    compiler_signature = _compiler_error_signature(validation_dir)
    if errors or transformed_message or compiler_signature:
        return _join_status_parts([f"Validation {status}", *errors, transformed_message, compiler_signature])
    stress = _dict(comparison_report.get("stress_comparison"))
    ddsdde = _dict(comparison_report.get("ddsdde_comparison"))
    state = _dict(comparison_report.get("state_variable_comparison"))
    convergence = _dict(comparison_report.get("convergence_comparison"))
    activation = _dict(comparison_report.get("activation_check"))
    return _join_status_parts(
        [
            f"Validation {status}",
            _difference_summary("stress", stress),
            _difference_summary("DDSDDE", ddsdde),
            _component_status("STATEV", state),
            _component_status("convergence", convergence),
            _component_status("activation", activation),
        ]
    )


def _difference_summary(label: str, row: dict[str, Any]) -> str:
    if not row:
        return ""
    max_abs = row.get("max_abs_difference")
    max_rel = row.get("max_rel_difference")
    if max_abs is None and max_rel is None:
        return _component_status(label, row)
    return f"{label} max_abs={max_abs} max_rel={max_rel}"


def _component_status(label: str, row: dict[str, Any]) -> str:
    if not row:
        return ""
    status = row.get("status")
    if status is None:
        status = "passed" if row.get("pass") else "failed"
    return f"{label} {status}"


def _join_status_parts(parts: list[str]) -> str:
    return "; ".join(part for part in parts if part)


def _load_validation_report(validation_dir: Path) -> dict[str, Any]:
    path = validation_dir / "validation_report.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _compiler_error_signature(validation_dir: Path) -> str:
    patterns = (
        "error #",
        "error:",
        "severe",
        "undefined reference",
        "unresolved",
        "type mismatch",
        "invalid for this data type",
        "arithmetic or logical type is required",
        "incompatible with this intrinsic",
    )
    snippets: list[str] = []
    for name in ("otis_abaqus_stderr.log", "otis_abaqus_stdout.log"):
        path = validation_dir / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            text = line.strip()
            if not text:
                continue
            lower = text.lower()
            if any(pattern in lower for pattern in patterns):
                snippets.append(text[:180])
            if len(snippets) >= 3:
                return "Compiler signature: " + "; ".join(snippets)
    return "Compiler signature: " + "; ".join(snippets) if snippets else ""


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())