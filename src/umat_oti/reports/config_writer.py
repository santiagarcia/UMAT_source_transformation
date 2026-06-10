from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from umat_oti.core.config_loader import build_compact_project_config


def write_config_files(workdir: Path, config: dict[str, Any]) -> dict[str, str]:
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    json_path = config_dir / "otis_project_config.json"
    compact_json_path = json_path
    expanded_json_path = config_dir / "otis_project_config.expanded.json"
    yaml_path = config_dir / "otis_project_config.yaml"
    compact_config = build_compact_project_config(config, base_path=config_dir)
    compact_json_path.write_text(
        json.dumps(compact_config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    expanded_json_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    yaml_path.write_text(to_yaml(compact_config), encoding="utf-8")
    return {
        "json": str(json_path),
        "compact_json": str(compact_json_path),
        "expanded_json": str(expanded_json_path),
        "yaml": str(yaml_path),
    }


def to_yaml(value: Any, indent: int = 0) -> str:
    lines = _yaml_lines(value, indent)
    return "\n".join(lines) + "\n"


def _yaml_lines(value: Any, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key in sorted(value):
            item = value[key]
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{_yaml_scalar(key)}:")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{_yaml_scalar(key)}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}[]"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or any(char in text for char in ":#[]{}\n") or text.strip() != text:
        return json.dumps(text)
    return text
