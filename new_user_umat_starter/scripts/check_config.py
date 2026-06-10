from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from umat_oti.core.config_loader import load_project_config_json
from umat_oti.core.transformation_anchors import anchor_completion_status, merge_completed_anchors_into_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load-check a compact JSON contract and report anchor status.")
    parser.add_argument("--config", type=Path, required=True, help="Path to the compact JSON file.")
    parser.add_argument("--write-expanded", type=Path, help="Optional path to write the expanded internal config JSON.")
    return parser


def _selected_umat(config: dict[str, object]) -> str:
    source = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
    if isinstance(source, dict):
        return str(source.get("selected_umat_name") or source.get("detected_umat_name") or "")
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = args.config.expanduser().resolve()
    try:
        config = load_project_config_json(config_path.read_bytes(), origin_path=config_path)
    except Exception as exc:
        print(json.dumps({"config": str(config_path), "error": f"{type(exc).__name__}: {exc}"}, indent=2, sort_keys=True))
        return 1

    if args.write_expanded is not None:
        expanded_path = args.write_expanded.expanduser().resolve()
        expanded_path.parent.mkdir(parents=True, exist_ok=True)
        expanded_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    source = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
    source_path = Path(str(source.get("selected_umat_file", ""))).expanduser()
    source_text = source_path.read_text(encoding="utf-8", errors="replace") if source_path.is_file() else ""
    merged_config = merge_completed_anchors_into_config(config, source_text) if source_text else config
    completion = anchor_completion_status(merged_config)
    settings = merged_config.get("transformation_settings", {}) if isinstance(merged_config.get("transformation_settings"), dict) else {}
    mapping = merged_config.get("mapping", {}) if isinstance(merged_config.get("mapping"), dict) else {}
    payload = {
        "config": str(config_path),
        "name": str((merged_config.get("project", {}) or {}).get("name", "")),
        "source": str(source_path),
        "selected_umat": _selected_umat(merged_config),
        "ntens": settings.get("ntens"),
        "order": settings.get("order"),
        "mapping": {
            "seed": mapping.get("dstran"),
            "output": mapping.get("stress"),
            "target": mapping.get("ddsdde"),
        },
        "anchor_status": completion.get("status"),
        "completion_issues": completion.get("completion_issues", []),
    }
    if args.write_expanded is not None:
        payload["expanded_json"] = str(args.write_expanded.expanduser().resolve())
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if completion.get("status") != "needs_json_completion" else 2


if __name__ == "__main__":
    raise SystemExit(main())