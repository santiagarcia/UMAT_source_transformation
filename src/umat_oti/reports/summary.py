from __future__ import annotations

from pathlib import Path
from typing import Any


def write_setup_summary(workdir: Path, config: dict[str, Any]) -> Path:
    report_dir = workdir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "otis_gui_setup_summary.md"
    path.write_text(render_setup_summary(config), encoding="utf-8")
    return path


def render_setup_summary(config: dict[str, Any]) -> str:
    project = config.get("project", {})
    source = config.get("source", {})
    roles = config.get("role_summary", {})
    pipeline = config.get("pipeline", {})
    mapping = config.get("mapping", {})
    routine_roles = config.get("routine_roles", {})
    region_classifications = config.get("region_classifications", {})
    findings_log = config.get("findings_log", [])
    findings_summary = config.get("findings_log_summary", {})
    analysis = config.get("analysis", {})
    review = config.get("transformation_review", {})
    anchors = config.get("transformation_anchors", {})
    lines = [
        f"# OTIS GUI Setup Summary",
        "",
        f"Project name: {project.get('name', '')}",
        f"Uploaded UMAT file: {source.get('uploaded_file', '')}",
        f"Selected UMAT: {source.get('selected_umat_name', '')}",
        "",
        "## Detected Arguments",
        _bullet_list(source.get("detected_umat_arguments", [])),
        "",
        "## Detected Helper Routines",
        _routine_role_lines(routine_roles),
        "",
        "## Detected Stress and Tangent Regions",
        _region_role_lines(region_classifications),
        "",
        "## Old DDSDDE Replacement Report",
        _bullet_list(analysis.get("region_summary", {}).get("report_messages", [])),
        "",
        "## Transformation Review",
        f"Ready: {review.get('ready_for_transformation', False)}",
        "",
        "Action needed:",
        _review_action_lines(review.get("action_needed", [])),
        "",
        "Stress update regions:",
        _review_region_lines(review.get("stress_update_regions_to_transform", [])),
        "",
        "Old tangent regions:",
        _review_region_lines(review.get("old_tangent_regions_to_replace", [])),
        "",
        "## Transformation Anchors",
        f"Status: {anchors.get('status', 'missing') if isinstance(anchors, dict) else 'missing'}",
        "Completion issues:",
        _review_action_lines(anchors.get("completion_issues", []) if isinstance(anchors, dict) else []),
        "Stress update anchors:",
        _review_region_lines((anchors.get("stress_update", {}) or {}).get("regions", []) if isinstance(anchors, dict) else []),
        "Tangent helper skip anchors:",
        _review_region_lines((anchors.get("old_tangent", {}) or {}).get("helper_regions", []) if isinstance(anchors, dict) else []),
        "",
        "## Findings Log",
        _findings_summary_lines(findings_summary),
        _finding_lines(findings_log),
        "",
        "## Variable Mappings",
        _mapping_lines(mapping),
        "",
        "## Seeded Variables",
        _bullet_list(roles.get("seed_variables", [])),
        "",
        "## Promoted Variables",
        _bullet_list(roles.get("promoted_variables", [])),
        "",
        "## Constants",
        _bullet_list(roles.get("constant_variables", [])),
        "",
        "## Kept-Real Variables",
        _bullet_list(roles.get("keep_real_variables", [])),
        "",
        "## Unknown Variables",
        _bullet_list(roles.get("unknown_variables", [])),
        "",
        "## Warnings",
        _bullet_list(analysis.get("warnings", [])),
        "",
        f"Ready for transformation: {pipeline.get('ready_for_transformation', False)}",
        "",
        "## Missing Information",
        _bullet_list(pipeline.get("missing_information", [])),
        "",
    ]
    return "\n".join(lines)


def _bullet_list(items: Any) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


def _routine_role_lines(routine_roles: dict[str, Any]) -> str:
    if not routine_roles:
        return "- None"
    return "\n".join(
        f"- {name}: {role.get('selected_role', 'Unknown')}"
        for name, role in sorted(routine_roles.items())
    )


def _region_role_lines(region_classifications: dict[str, Any]) -> str:
    if not region_classifications:
        return "- None"
    return "\n".join(
        f"- {region_id}: {region.get('selected_classification', 'Unknown')} "
        f"(lines {region.get('start_line', '')}-{region.get('end_line', '')})"
        for region_id, region in sorted(region_classifications.items())
    )


def _findings_summary_lines(summary: dict[str, Any]) -> str:
    if not summary:
        return "- None"
    return "\n".join(
        [
            f"- Total: {summary.get('total', 0)}",
            f"- Open: {summary.get('open', 0)}",
            f"- Action needed: {summary.get('action_needed', 0)}",
            f"- Warnings: {summary.get('warnings', 0)}",
            f"- Errors: {summary.get('errors', 0)}",
        ]
    )


def _finding_lines(findings_log: list[dict[str, Any]]) -> str:
    if not findings_log:
        return "- None"
    return "\n".join(
        f"- {row.get('log id', '')}: [{row.get('severity', '')}] {row.get('finding', '')} "
        f"({row.get('status', 'Open')})"
        for row in findings_log
    )


def _mapping_lines(mapping: dict[str, Any]) -> str:
    rows = []
    for key, value in sorted(mapping.items()):
        if key == "optional_variables" and isinstance(value, dict):
            for optional_key, optional_value in sorted(value.items()):
                rows.append(f"- {optional_key}: {optional_value}")
        elif key != "optional_variables":
            rows.append(f"- {key}: {value}")
    return "\n".join(rows) if rows else "- None"


def _review_action_lines(actions: Any) -> str:
    if not actions:
        return "- None"
    return "\n".join(f"- {item.get('message', item)}" if isinstance(item, dict) else f"- {item}" for item in actions)


def _review_region_lines(regions: Any) -> str:
    if not regions:
        return "- None"
    rows = []
    for region in regions:
        if not isinstance(region, dict):
            rows.append(f"- {region}")
            continue
        rows.append(
            f"- {region.get('region_id', '')}: lines {region.get('start_line', '')}-{region.get('end_line', '')}; "
            f"{region.get('classification', '')}"
        )
    return "\n".join(rows)
