from __future__ import annotations

import copy
from typing import Any

from umat_oti.core.findings_log import finding_log_summary
from umat_oti.core.roles import role_summary
from umat_oti.core.transformation_anchors import build_transformation_anchors
from umat_oti.core.transformation_review import build_transformation_review


def build_project_config(
    *,
    project: dict[str, str],
    source_metadata: dict[str, object],
    analysis: dict[str, Any],
    selected_umat: str,
    selected_umat_arguments: list[str],
    mappings: dict[str, str],
    routine_roles: list[dict[str, object]],
    region_classifications: list[dict[str, object]],
    variable_roles: list[dict[str, object]],
    findings_log: list[dict[str, object]],
    metadata: dict[str, str],
    pipeline: dict[str, Any],
    transformation_settings: dict[str, Any] | None = None,
    validation_settings: dict[str, Any] | None = None,
    transformation_anchors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    optional = {key: value for key, value in mappings.items() if key not in _required_mapping_keys() and value}
    transformation_review = build_transformation_review(
        analysis,
        selected_umat=selected_umat,
        mappings=mappings,
        routine_roles=routine_roles,
        region_classifications=region_classifications,
        variable_roles=variable_roles,
    )
    config = {
        "analysis": {
            "assignments_to_ddsdde": analysis.get("assignments_to_ddsdde", []),
            "assignments_to_statev": analysis.get("assignments_to_statev", []),
            "assignments_to_stress": analysis.get("assignments_to_stress", []),
            "branch_conditions": analysis.get("branch_conditions", []),
            "call_targets": analysis.get("call_targets", []),
            "calls": analysis.get("calls", []),
            "detected_calls": analysis.get("calls", []),
            "detected_functions": analysis.get("detected_functions", []),
            "detected_subroutines": analysis.get("detected_subroutines", []),
            "detected_umat_routines": analysis.get("detected_umat_routines", []),
            "detected_variables": analysis.get("detected_variables", []),
            "file_io": analysis.get("file_io", []),
            "finite_strain": analysis.get("finite_strain", {}),
            "form": analysis.get("form", "unknown"),
            "has_subroutine_umat": analysis.get("has_subroutine_umat", False),
            "markers": analysis.get("markers", []),
            "possible_external_or_unsupported_calls": analysis.get("possible_external_or_unsupported_calls", []),
            "plasticity_indicators": analysis.get("plasticity_indicators", {}),
            "region_summary": analysis.get("region_summary", {}),
            "detected_regions": analysis.get("detected_regions", []),
            "routine_effects": analysis.get("routine_effects", []),
            "statev_accesses": analysis.get("statev_accesses", {}),
            "stress_path_helpers": analysis.get("stress_path_helpers", []),
            "unsupported_features": analysis.get("unsupported_features", []),
            "uses": analysis.get("uses", {}),
            "warnings": analysis.get("warnings", []),
        },
        "findings_log": findings_log,
        "findings_log_summary": finding_log_summary(findings_log),
        "jacobian_contract": {
            "constant_material_parameters": ["PROPS"],
            "contract": "DDSDDE(i,j) = d STRESS(i) / d DSTRAN(j)",
            "dependent_variable": "STRESS",
            "independent_variable": "DSTRAN",
            "notes": "For Abaqus DDSDDE extraction, seed DSTRAN. PROPS affect the stress response but remain constants during derivative extraction.",
            "output_variable": "DDSDDE",
        },
        "mapping": {
            "ddsdde": mappings.get("ddsdde", ""),
            "dstran": mappings.get("dstran", ""),
            "nprops": mappings.get("nprops", ""),
            "nstatv": mappings.get("nstatv", ""),
            "ntens": mappings.get("ntens", ""),
            "optional_variables": optional,
            "props": mappings.get("props", ""),
            "statev": mappings.get("statev", ""),
            "stran": mappings.get("stran", ""),
            "stress": mappings.get("stress", ""),
        },
        "metadata": metadata,
        "helper_policy": _helper_policy(analysis),
        "pipeline": pipeline,
        "project": project,
        "role_summary": role_summary(variable_roles),
        "routine_roles": {
            str(row.get("routine_name", "")): {
                "helper_kind": row.get("helper_kind", "Unknown"),
                "notes": row.get("notes", ""),
                "selected_helper_kind": row.get("selected_helper_kind", row.get("helper_kind", "Unknown")),
                "selected_role": row.get("selected_role", "Unknown"),
                "suggested_helper_kind": row.get("suggested_helper_kind", row.get("helper_kind", "Unknown")),
                "suggested_role": row.get("suggested_role", "Unknown"),
            }
            for row in routine_roles
        },
        "region_classifications": {
            str(row.get("region id", "")): {
                "detected_reason": row.get("detected reason", ""),
                "detected_variables": row.get("detected variables", []),
                "end_line": row.get("end line", ""),
                "notes": row.get("notes", ""),
                "preview": row.get("short code preview", ""),
                "region_type": row.get("region type", ""),
                "selected_classification": row.get("user-selected classification", "Unknown"),
                "start_line": row.get("start line", ""),
                "suggested_classification": row.get("suggested classification", "Unknown"),
            }
            for row in (region_classifications or [])
        },
        "source": {
            "detected_umat_arguments": selected_umat_arguments,
            "detected_umat_name": _first_detected_umat(analysis),
            "file_hash": source_metadata.get("sha256", ""),
            "selected_umat_file": source_metadata.get("file_path", ""),
            "selected_umat_name": selected_umat,
            "uploaded_file": source_metadata.get("file_name", ""),
        },
        "transformation_review": transformation_review,
        "transformation_settings": transformation_settings or {},
        "validation_settings": _merged_validation_settings(analysis, validation_settings),
        "variable_roles": {
            str(row.get("variable name", "")): {
                "detected_shape": row.get("detected shape/dimension", ""),
                "detected_type": row.get("detected type", "unknown"),
                "detected_usage": row.get("detected usage", ""),
                "is_argument": row.get("appears in UMAT arguments yes/no", "no") == "yes",
                "notes": row.get("notes", ""),
                "read_write": row.get("read/write/unknown", "unknown"),
                "selected_role": row.get("user-selected OTIS role", "Unknown"),
                "suggested_role": row.get("suggested OTIS role", "Unknown"),
            }
            for row in variable_roles
        },
    }
    config["transformation_anchors"] = transformation_anchors or build_transformation_anchors(
        config,
        _source_text(source_metadata),
    )
    return config


def merge_project_config(base_config: dict[str, Any] | None, updated_config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(base_config, dict) or not base_config:
        return dict(updated_config)
    merged = copy.deepcopy(base_config)
    for key, value in updated_config.items():
        merged[key] = value
    return merged


def _required_mapping_keys() -> set[str]:
    return {"stress", "statev", "ddsdde", "stran", "dstran", "props", "ntens", "nstatv", "nprops"}


def _first_detected_umat(analysis: dict[str, Any]) -> str:
    routines = analysis.get("detected_umat_routines", [])
    if routines:
        return str(routines[0].get("name", ""))
    return ""


def _helper_policy(analysis: dict[str, Any]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[int, ...]]] = set()
    for row in analysis.get("stress_path_helpers", []) or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("callee", "")).upper()
        line_numbers = tuple(_as_int(value) for value in row.get("line_numbers", []) or [] if _as_int(value))
        key = (name, line_numbers)
        if not name or key in seen:
            continue
        seen.add(key)
        calls.append(
            {
                "name": name,
                "classification": "pass_through",
                "reason": "Helper call preserved; promoted arguments rewritten where needed.",
                "blocking": False,
                "line_numbers": list(line_numbers),
                "arguments": list(row.get("arguments", []) or []),
            }
        )
    return {
        "default": "pass_through",
        "helper_calls": calls,
        "notes": "Stress-path helper names are not readiness blockers. Compilation and validation determine whether pass-through is valid.",
    }


def _validation_settings(analysis: dict[str, Any]) -> dict[str, Any]:
    finite = analysis.get("finite_strain", {}) if isinstance(analysis.get("finite_strain"), dict) else {}
    plastic = analysis.get("plasticity_indicators", {}) if isinstance(analysis.get("plasticity_indicators"), dict) else {}
    if plastic.get("is_plasticity_candidate") and finite.get("executable_dfgrd_use"):
        material_test_mode = "single element plastic finite strain tension"
    elif plastic.get("is_plasticity_candidate"):
        material_test_mode = "single element plastic tension"
    elif finite.get("executable_dfgrd_use"):
        material_test_mode = "single element plastic finite strain tension"
    else:
        material_test_mode = "single element tension"
    return {
        "enabled": True,
        "material_test_mode": material_test_mode,
        "jacobian_contract": "DDSDDE(i,j) = d STRESS(i) / d DSTRAN(j)",
        "compare_outputs": ["STRESS", "STATEV", "DDSDDE", "convergence"],
        "expected_plasticity": bool(plastic.get("is_plasticity_candidate")),
        "finite_strain": bool(finite.get("dfgrd_driven_stress_update") or finite.get("executable_dfgrd_use")),
    }


def _merged_validation_settings(analysis: dict[str, Any], configured: dict[str, Any] | None) -> dict[str, Any]:
    defaults = _validation_settings(analysis)
    if not isinstance(configured, dict) or not configured:
        return defaults
    merged = dict(defaults)
    merged.update(configured)
    compare_outputs = configured.get("compare_outputs")
    if isinstance(compare_outputs, list):
        merged["compare_outputs"] = [str(value) for value in compare_outputs if str(value).strip()]
    return merged


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _source_text(source_metadata: dict[str, object]) -> str:
    path_text = str(source_metadata.get("file_path", ""))
    if not path_text:
        return ""
    try:
        from pathlib import Path

        path = Path(path_text)
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""
