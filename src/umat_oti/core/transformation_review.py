from __future__ import annotations

from typing import Any

from umat_oti.core.roles import role_summary


REQUIRED_MAPPING_KEYS = ("stress", "dstran", "ddsdde")


def build_transformation_review(
    analysis: dict[str, Any] | None,
    *,
    selected_umat: str = "",
    mappings: dict[str, str] | None = None,
    routine_roles: list[dict[str, object]] | None = None,
    region_classifications: list[dict[str, object]] | None = None,
    variable_roles: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    analysis = analysis or {}
    mappings = mappings or {}
    routine_roles = routine_roles or []
    region_classifications = region_classifications or list(analysis.get("detected_regions", []))
    variable_roles = variable_roles or []
    summary = analysis.get("region_summary", {}) if isinstance(analysis.get("region_summary"), dict) else {}

    role_groups = role_summary(variable_roles) if variable_roles else _fallback_role_groups(analysis, summary)
    seed_variables = _stable_unique(role_groups.get("seed_variables", []))
    promoted_variables = _stable_unique(role_groups.get("promoted_variables", []))
    constant_variables = _stable_unique(role_groups.get("constant_variables", []))
    keep_real_variables = _stable_unique(role_groups.get("keep_real_variables", []))
    ignored_or_unused_variables = _stable_unique(summary.get("ignored_or_unused_variables", []))

    old_tangent_regions = _regions_of_type(region_classifications, "tangent")
    stress_regions = _regions_of_type(region_classifications, "stress")
    shared_setup_regions = _regions_of_type(region_classifications, "shared_setup")

    ambiguous_items = _ambiguous_items(
        routine_roles=routine_roles,
        region_classifications=region_classifications,
        variable_roles=variable_roles,
        analysis=analysis,
        selected_umat=selected_umat,
    )
    action_needed = _action_needed(
        analysis=analysis,
        selected_umat=selected_umat,
        mappings=mappings,
        seed_variables=seed_variables,
        promoted_variables=promoted_variables,
        old_tangent_regions=old_tangent_regions,
        stress_regions=stress_regions,
        ambiguous_items=ambiguous_items,
    )
    main_review_item_count = len(action_needed) + len(ambiguous_items) + len(old_tangent_regions) + len(stress_regions)
    return {
        "ready_for_transformation": not action_needed,
        "action_needed": action_needed,
        "seed_variables": seed_variables,
        "promoted_variables": promoted_variables,
        "constant_variables": constant_variables,
        "keep_real_variables": keep_real_variables,
        "old_tangent_regions_to_replace": old_tangent_regions,
        "stress_update_regions_to_transform": stress_regions,
        "shared_setup_regions_to_keep": shared_setup_regions,
        "ignored_or_unused_variables": ignored_or_unused_variables,
        "ambiguous_items": ambiguous_items,
        "main_review_item_count": main_review_item_count,
    }


def _fallback_role_groups(analysis: dict[str, Any], summary: dict[str, Any]) -> dict[str, list[str]]:
    variables = {str(row.get("variable_name", "")).upper() for row in analysis.get("detected_variables", [])}
    stress_path = {str(name).upper() for name in summary.get("stress_path_variables", [])}
    constants = {str(name).upper() for name in summary.get("constant_variables", [])}
    tangent_only = {str(name).upper() for name in summary.get("tangent_only_variables", [])}
    seed = ["DSTRAN"] if "DSTRAN" in variables or "DSTRAN" in stress_path else []
    promoted = sorted((stress_path - {"DSTRAN"}) & (variables | {"STRESS"}))
    keep_real = sorted((tangent_only | {"DDSDDE"}) & (variables | {"DDSDDE"}))
    return {
        "seed_variables": seed,
        "promoted_variables": promoted,
        "constant_variables": sorted(constants & variables),
        "keep_real_variables": keep_real,
        "unknown_variables": [],
    }


def _regions_of_type(rows: list[dict[str, object]], region_type: str) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for row in rows:
        if str(row.get("region type", "")) != region_type:
            continue
        classification = str(row.get("user-selected classification") or row.get("suggested classification") or "")
        if classification.lower() == "ignore":
            continue
        selected.append(
            {
                "region_id": str(row.get("region id", "")),
                "start_line": _line_number(row.get("start line")),
                "end_line": _line_number(row.get("end line")),
                "reason": str(row.get("detected reason", "")),
                "classification": classification,
                "variables": _stable_unique(row.get("detected variables", [])),
                "preview": str(row.get("short code preview", "")),
            }
        )
    return selected


def _ambiguous_items(
    *,
    routine_roles: list[dict[str, object]],
    region_classifications: list[dict[str, object]],
    variable_roles: list[dict[str, object]],
    analysis: dict[str, Any],
    selected_umat: str,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    summary = analysis.get("region_summary", {}) if isinstance(analysis.get("region_summary"), dict) else {}
    stress_path_variables = {str(name).upper() for name in summary.get("stress_path_variables", []) or []}
    statev_path_variables = {str(name).upper() for name in summary.get("statev_path_variables", []) or []}
    path_variables = stress_path_variables | statev_path_variables | {"STRESS", "STATEV", "DSTRAN"}
    for row in variable_roles:
        if str(row.get("user-selected OTIS role", row.get("suggested OTIS role", "Unknown"))) == "Unknown":
            name = str(row.get("variable name", "")).upper()
            if path_variables and name not in path_variables:
                continue
            items.append(
                {
                    "kind": "variable",
                    "name": name,
                    "reason": str(row.get("notes", "No deterministic OTIS role matched.")),
                }
            )
    for row in region_classifications:
        if str(row.get("user-selected classification", row.get("suggested classification", "Unknown"))) == "Unknown":
            items.append(
                {
                    "kind": "region",
                    "name": str(row.get("region id", "")),
                    "reason": str(row.get("detected reason", "Region classification is unknown.")),
                }
            )
    for feature in analysis.get("unsupported_features", []):
        code = str(feature.get("code", "unsupported feature"))
        if code in {"data", "io_open", "io_read", "io_write"}:
            continue
        items.append(
            {
                "kind": "unsupported_feature",
                "name": code,
                "reason": str(feature.get("message", "Unsupported Fortran feature detected.")),
            }
        )
    return items


def _action_needed(
    *,
    analysis: dict[str, Any],
    selected_umat: str,
    mappings: dict[str, str],
    seed_variables: list[str],
    promoted_variables: list[str],
    old_tangent_regions: list[dict[str, object]],
    stress_regions: list[dict[str, object]],
    ambiguous_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if not selected_umat:
        actions.append({"kind": "main_umat", "message": "Select the main UMAT routine."})
    if mappings:
        missing_mappings = [key.upper() for key in REQUIRED_MAPPING_KEYS if not mappings.get(key)]
        if missing_mappings:
            actions.append({"kind": "mapping", "message": "Confirm required mappings: " + ", ".join(missing_mappings)})
    if "DSTRAN" not in seed_variables:
        actions.append({"kind": "seed", "message": "DSTRAN must be the only Jacobian seed variable."})
    extra_seeds = [name for name in seed_variables if name != "DSTRAN"]
    if extra_seeds:
        actions.append({"kind": "seed", "message": "Remove extra seed variables: " + ", ".join(extra_seeds)})
    if "STRESS" not in promoted_variables:
        actions.append({"kind": "stress", "message": "STRESS must be on the promoted dependent path."})
    if analysis.get("assignments_to_stress") and not stress_regions:
        actions.append({"kind": "stress_path", "message": "No executable DSTRAN to STRESS region was identified."})
    if analysis.get("assignments_to_ddsdde") and not old_tangent_regions:
        actions.append({"kind": "old_tangent", "message": "DDSDDE assignments exist but no old tangent replacement region was identified."})
    statev_access = analysis.get("statev_accesses", {}) if isinstance(analysis.get("statev_accesses"), dict) else {}
    if statev_access.get("write_count") and "STATEV" not in promoted_variables:
        actions.append({"kind": "statev", "message": "STATEV is written; promote it or document why state updates are intentionally real-only."})
    if ambiguous_items:
        actions.append({"kind": "ambiguity", "message": "Resolve the ambiguous items listed below."})
    return actions


def _stable_unique(values: Any) -> list[str]:
    if values is None:
        return []
    result: list[str] = []
    for value in values:
        text = str(value).upper()
        if text and text not in result:
            result.append(text)
    return sorted(result)


def _line_number(value: object) -> int | str:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return str(value or "")
