from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from umat_oti.core.config_loader import load_project_config_json
from umat_oti.core.transformation_anchors import anchor_completion_status, merge_completed_anchors_into_config
from umat_oti.transform.source_transform import transform_umat_to_oti_from_config


def run_config_transform(config_path: Path, out_dir: Path) -> tuple[dict[str, Any], int]:
    config_path = config_path.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()

    try:
        config = load_project_config_json(config_path.read_bytes(), origin_path=config_path)
    except Exception as exc:
        return {"config": str(config_path), "error": f"{type(exc).__name__}: {exc}"}, 1

    source = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
    source_path = Path(str(source.get("selected_umat_file", ""))).expanduser()
    if not source_path.is_file():
        return {"config": str(config_path), "error": f"Source file not found: {source_path}", "status_category": "source_not_found"}, 1

    source_text = source_path.read_text(encoding="utf-8", errors="replace")
    config = merge_completed_anchors_into_config(config, source_text)
    completion = anchor_completion_status(config)
    settings = config.get("transformation_settings", {}) if isinstance(config.get("transformation_settings"), dict) else {}
    ntens = int(settings.get("ntens") or 0)
    summary: dict[str, Any] = {
        "config": str(config_path),
        "out_dir": str(out_dir),
        "source": str(source_path),
        "anchor_status": completion.get("status"),
        "completion_issues": completion.get("completion_issues", []),
        "ntens": ntens,
        "order": settings.get("order"),
    }
    if completion.get("status") == "needs_json_completion":
        summary["status_category"] = "needs_json_completion"
        return summary, 2

    out_dir.mkdir(parents=True, exist_ok=True)
    result = transform_umat_to_oti_from_config(source_text, config, out_dir, ntens)
    combined = _write_combined_source(out_dir, result.transformed_source_path) if result.success else None
    summary.update(
        {
            "transform_success": result.success,
            "blockers": result.blockers,
            "warnings": result.warnings,
            "report_path": str(result.report_path or ""),
            "transformed_source": str(result.transformed_source_path or ""),
            "combined_source": str(combined or ""),
            "semantic_checks": result.report.get("semantic_checks", {}),
            "status_category": _classify_outcome(result),
        }
    )
    return summary, 0 if result.success else 1


def _write_combined_source(out_dir: Path, transformed_source: Any) -> Path | None:
    """Write one Abaqus-submittable file: the OTI support modules followed by the
    transformed UMAT, all as free-form Fortran (the fixed-form UMAT is converted).

    Submit it directly with `abaqus job=... user=<name>_oti_combined.f90`.
    """
    if not transformed_source:
        return None
    transformed_source = Path(transformed_source)
    order_file = out_dir / "compile_order.txt"
    if not order_file.is_file():
        return None
    from umat_oti.validation.job_builder import _fixed_form_to_free_form

    chunks: list[str] = []
    for name in (line.strip() for line in order_file.read_text(encoding="utf-8").splitlines()):
        if not name:
            continue
        part = out_dir / name
        if not part.is_file():
            continue
        text = part.read_text(encoding="utf-8", errors="replace")
        if part.suffix.lower() in {".f", ".for", ".ftn"}:
            text = _fixed_form_to_free_form(text)
        chunks.append(f"! ===== {part.name} =====\n{text}\n")
    if not chunks:
        return None
    combined = out_dir / f"{transformed_source.stem}_combined.f90"
    combined.write_text("".join(chunks), encoding="utf-8")
    return combined


def _classify_outcome(result: Any) -> str:
    """Legible outcome category for the transform (robustness/triage aid)."""
    if not result.success:
        return "transform_blocked" if result.blockers else "transform_failed"
    semantic = result.report.get("semantic_checks", {}) if isinstance(result.report, dict) else {}
    if any(value is False for value in semantic.values()):
        return "succeeded_semantic_check_warnings"
    if result.warnings:
        # e.g. a helper passed through instead of OTI-lifted: derivatives may be
        # approximate on that path. Surface it rather than reporting clean success.
        return "succeeded_with_warnings"
    return "succeeded"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the single-config transform path from a compact JSON contract.")
    parser.add_argument("--config", type=Path, required=True, help="Path to the compact JSON file.")
    parser.add_argument(
        "--out",
        type=Path,
        help="Output directory. Defaults to ./umat_oti_workspace/new_user_runs/<config-stem>.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = args.config.expanduser().resolve()
    out_dir = args.out.expanduser().resolve() if args.out is not None else (Path.cwd() / "umat_oti_workspace" / "new_user_runs" / config_path.stem)

    summary, exit_code = run_config_transform(config_path, out_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())