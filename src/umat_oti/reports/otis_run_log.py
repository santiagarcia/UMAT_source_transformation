from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_otis_run_log(
    *,
    output_dir: Path,
    config: dict[str, Any],
    result: Any | None = None,
    error: BaseException | None = None,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "otis_gui_run_log.json"
    payload = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": "Run OTIS Transformation button activated",
        "source": config.get("source", {}),
        "selected_umat": _selected_umat(config),
        "mapping": config.get("mapping", {}),
        "transformation_settings": config.get("transformation_settings", {}),
        "pipeline": config.get("pipeline", {}),
        "review_summary": _review_summary(config),
        "result": _result_summary(result),
        "error": _error_summary(error),
    }
    log_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return log_path


def _selected_umat(config: dict[str, Any]) -> str:
    source = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
    return str(source.get("selected_umat_name") or source.get("detected_umat_name") or "")


def _review_summary(config: dict[str, Any]) -> dict[str, Any]:
    review = config.get("transformation_review", {}) if isinstance(config.get("transformation_review"), dict) else {}
    return {
        "ready_for_transformation": bool(review.get("ready_for_transformation", False)),
        "action_needed_count": len(review.get("action_needed", []) or []),
        "ambiguous_item_count": len(review.get("ambiguous_items", []) or []),
        "stress_region_count": len(review.get("stress_update_regions_to_transform", []) or []),
        "old_tangent_region_count": len(review.get("old_tangent_regions_to_replace", []) or []),
        "promoted_variables": list(review.get("promoted_variables", []) or []),
        "seed_variables": list(review.get("seed_variables", []) or []),
    }


def _result_summary(result: Any | None) -> dict[str, Any]:
    if result is None:
        return {"status": "not_completed"}
    generated_files = [str(path) for path in getattr(result, "generated_files", []) or []]
    return {
        "success": bool(getattr(result, "success", False)),
        "output_dir": str(getattr(result, "output_dir", "")),
        "transformed_source_path": str(getattr(result, "transformed_source_path", "") or ""),
        "report_path": str(getattr(result, "report_path", "") or ""),
        "generated_file_count": len(generated_files),
        "generated_files": generated_files,
        "blockers": list(getattr(result, "blockers", []) or []),
        "warnings": list(getattr(result, "warnings", []) or []),
    }


def _error_summary(error: BaseException | None) -> dict[str, str]:
    if error is None:
        return {}
    return {"type": type(error).__name__, "message": str(error)}