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
        return {"config": str(config_path), "error": f"Source file not found: {source_path}"}, 1

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
        return summary, 2

    out_dir.mkdir(parents=True, exist_ok=True)
    result = transform_umat_to_oti_from_config(source_text, config, out_dir, ntens)
    summary.update(
        {
            "transform_success": result.success,
            "blockers": result.blockers,
            "warnings": result.warnings,
            "report_path": str(result.report_path or ""),
            "transformed_source": str(result.transformed_source_path or ""),
            "semantic_checks": result.report.get("semantic_checks", {}),
        }
    )
    return summary, 0 if result.success else 1


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