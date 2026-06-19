from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from umat_oti.core.config import build_project_config
from umat_oti.core.findings_log import build_findings_log
from umat_oti.core.output_layout import migrate_transform_output_dir
from umat_oti.core.pipeline_status import evaluate_pipeline_status
from umat_oti.core.project import file_metadata, sanitize_project_name
from umat_oti.core.roles import suggest_routine_roles, suggest_variable_roles
from umat_oti.core.transformation_settings import DEFAULT_GENERATED_NTENS, build_transformation_settings
from umat_oti.core.transformation_review import build_transformation_review
from umat_oti.fortran.interface_detection import OPTIONAL_GUI_MAPPINGS, REQUIRED_GUI_MAPPINGS
from umat_oti.fortran.scanner import analyze_fortran_source


DEFAULT_METADATA = {
    "model_family": "unknown",
    "primary_kinematic_driver": "unknown",
    "strain_regime": "unknown",
    "stress_update_style": "unknown",
}


def load_project_config_json(payload: bytes, *, origin_path: str | Path | None = None) -> dict[str, Any]:
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Configuration JSON must be UTF-8 encoded.") from exc
    try:
        config = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON configuration: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("Configuration JSON must contain an object at the top level.")
    if _is_compact_project_config(config):
        return _expand_compact_project_config(config, origin_path=origin_path)
    return config


def session_state_from_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    project = _project(config)
    source = _source(config)
    source_metadata = _source_metadata(source)
    analysis = _analysis(config)
    selected_umat = str(source.get("selected_umat_name") or source.get("detected_umat_name") or _first_detected_umat(analysis))
    routine_roles = _routine_roles(config.get("routine_roles", {}))
    region_classifications = _region_classifications(config.get("region_classifications", {}), analysis)
    variable_roles = _variable_roles(config.get("variable_roles", {}))
    mappings = _mappings(config.get("mapping", {}))
    metadata = dict(DEFAULT_METADATA)
    metadata.update(_dict_or_empty(config.get("metadata")))
    source_path = str(source_metadata.get("file_path", ""))
    source_text = ""
    if source_path:
        path = Path(source_path)
        if path.is_file():
            source_text = path.read_text(encoding="utf-8", errors="replace")
        else:
            warnings.append(f"Configured source file was not found on disk: {source_path}")
    findings_log = list(config.get("findings_log", [])) if isinstance(config.get("findings_log"), list) else []
    if not findings_log:
        findings_log = build_findings_log(analysis, source_metadata, routine_roles, region_classifications, variable_roles)
    transformation_review = config.get("transformation_review")
    if not isinstance(transformation_review, dict):
        transformation_review = build_transformation_review(
            analysis,
            selected_umat=selected_umat,
            mappings=mappings,
            routine_roles=routine_roles,
            region_classifications=region_classifications,
            variable_roles=variable_roles,
        )
    transformation_anchors = _dict_or_empty(config.get("transformation_anchors"))
    transformation_settings = _dict_or_empty(config.get("transformation_settings"))
    validation_settings = _dict_or_empty(config.get("validation_settings"))
    transformation_output_dir = str(migrate_transform_output_dir(project.get("workdir", str(Path.cwd() / "umat_oti_workspace")), transformation_settings.get("output_dir")))
    transformation_settings["output_dir"] = transformation_output_dir
    transformation_ntens = transformation_settings.get("ntens", "")
    transformation_order = transformation_settings.get("order", 1)
    state = {
        "analysis": analysis,
        "config_paths": {"imported_json": "uploaded configuration"},
        "file_metadata": source_metadata,
        "findings_log": findings_log,
        "mappings": mappings,
        "metadata": metadata,
        "project": project,
        "project_description": project.get("description", ""),
        "project_description_input": project.get("description", ""),
        "project_name": project.get("name", "umat_oti_project"),
        "project_name_input": project.get("name", "umat_oti_project"),
        "region_classifications": region_classifications,
        "routine_roles": routine_roles,
        "selected_umat": selected_umat,
        "source_path": source_path,
        "source_text": source_text,
        "summary_path": None,
        "transformation_ntens_input": "" if transformation_ntens is None else str(transformation_ntens),
        "transformation_order_input": int(transformation_order or 1),
        "transformation_output_dir": transformation_output_dir,
        "transformation_anchors": transformation_anchors,
        "transformation_settings": transformation_settings,
        "transformation_review": transformation_review,
        "validation_settings": validation_settings,
        "variable_roles": variable_roles,
        "workdir": project.get("workdir", str(Path.cwd() / "umat_oti_workspace")),
        "workdir_input": project.get("workdir", str(Path.cwd() / "umat_oti_workspace")),
    }
    if not analysis:
        warnings.append("Configuration did not contain an analysis section; some GUI sections will remain empty.")
    if not mappings:
        warnings.append("Configuration did not contain standard UMAT mappings.")
    return state, warnings


def _project(config: dict[str, Any]) -> dict[str, str]:
    project = _dict_or_empty(config.get("project"))
    return {
        "description": str(project.get("description", "")),
        "name": str(project.get("name", "umat_oti_project") or "umat_oti_project"),
        "workdir": str(project.get("workdir", Path.cwd() / "umat_oti_workspace")),
    }


def _source(config: dict[str, Any]) -> dict[str, Any]:
    return _dict_or_empty(config.get("source"))


def _source_metadata(source: dict[str, Any]) -> dict[str, object]:
    file_path = str(source.get("selected_umat_file", ""))
    file_name = str(source.get("uploaded_file", "")) or (Path(file_path).name if file_path else "")
    return {
        "extension": Path(file_name).suffix if file_name else "",
        "file_name": file_name,
        "file_path": file_path,
        "file_size": Path(file_path).stat().st_size if file_path and Path(file_path).is_file() else "",
        "sha256": source.get("file_hash", ""),
    }


def _analysis(config: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(_dict_or_empty(config.get("analysis")))
    if "calls" not in analysis and "detected_calls" in analysis:
        analysis["calls"] = analysis.get("detected_calls", [])
    if "detected_calls" not in analysis and "calls" in analysis:
        analysis["detected_calls"] = analysis.get("calls", [])
    for key in (
        "assignments_to_ddsdde",
        "assignments_to_statev",
        "assignments_to_stress",
        "call_targets",
        "calls",
        "detected_calls",
        "detected_functions",
        "detected_regions",
        "detected_subroutines",
        "detected_umat_routines",
        "detected_variables",
        "file_io",
        "markers",
        "possible_external_or_unsupported_calls",
        "routine_effects",
        "unsupported_features",
        "warnings",
    ):
        if key not in analysis or analysis[key] is None:
            analysis[key] = []
    if "uses" not in analysis or not isinstance(analysis.get("uses"), dict):
        analysis["uses"] = {}
    if "region_summary" not in analysis or not isinstance(analysis.get("region_summary"), dict):
        analysis["region_summary"] = {}
    if "form" not in analysis:
        analysis["form"] = "unknown"
    if "has_subroutine_umat" not in analysis:
        analysis["has_subroutine_umat"] = any(
            str(row.get("name", "")).upper() == "UMAT" for row in analysis.get("detected_subroutines", [])
        )
    if not analysis.get("detected_umat_routines"):
        analysis["detected_umat_routines"] = [
            row for row in analysis.get("detected_subroutines", []) if str(row.get("name", "")).upper() == "UMAT"
        ]
    return analysis


def _mappings(mapping: Any) -> dict[str, str]:
    mapping_dict = _dict_or_empty(mapping)
    result = {
        key: str(value)
        for key, value in mapping_dict.items()
        if key != "optional_variables" and value is not None
    }
    optional = _dict_or_empty(mapping_dict.get("optional_variables"))
    result.update({key: str(value) for key, value in optional.items() if value is not None})
    return result


def _routine_roles(raw_roles: Any) -> list[dict[str, object]]:
    if isinstance(raw_roles, list):
        return [dict(row) for row in raw_roles if isinstance(row, dict)]
    if not isinstance(raw_roles, dict):
        return []
    rows = []
    for name, role in sorted(raw_roles.items()):
        role_dict = _dict_or_empty(role)
        rows.append(
            {
                "notes": role_dict.get("notes", ""),
                "routine_name": str(name),
                "selected_role": role_dict.get("selected_role", "Unknown"),
                "suggested_role": role_dict.get("suggested_role", "Unknown"),
            }
        )
    return rows


def _region_classifications(raw_regions: Any, analysis: dict[str, Any]) -> list[dict[str, object]]:
    if isinstance(raw_regions, list):
        return [dict(row) for row in raw_regions if isinstance(row, dict)]
    if not isinstance(raw_regions, dict):
        return list(analysis.get("detected_regions", []))
    rows = []
    for region_id, region in sorted(raw_regions.items()):
        region_dict = _dict_or_empty(region)
        rows.append(
            {
                "detected reason": region_dict.get("detected_reason", ""),
                "detected variables": region_dict.get("detected_variables", []),
                "end line": region_dict.get("end_line", ""),
                "notes": region_dict.get("notes", ""),
                "region id": str(region_id),
                "region type": region_dict.get("region_type", ""),
                "short code preview": region_dict.get("preview", ""),
                "start line": region_dict.get("start_line", ""),
                "suggested classification": region_dict.get("suggested_classification", "Unknown"),
                "user-selected classification": region_dict.get("selected_classification", "Unknown"),
            }
        )
    return rows


def _variable_roles(raw_roles: Any) -> list[dict[str, object]]:
    if isinstance(raw_roles, list):
        return [dict(row) for row in raw_roles if isinstance(row, dict)]
    if not isinstance(raw_roles, dict):
        return []
    rows = []
    for name, role in sorted(raw_roles.items()):
        role_dict = _dict_or_empty(role)
        rows.append(
            {
                "appears in UMAT arguments yes/no": "yes" if role_dict.get("is_argument") else "no",
                "detected shape/dimension": role_dict.get("detected_shape", ""),
                "detected type": role_dict.get("detected_type", "unknown"),
                "detected usage": role_dict.get("detected_usage", ""),
                "notes": role_dict.get("notes", ""),
                "read/write/unknown": role_dict.get("read_write", "unknown"),
                "suggested OTIS role": role_dict.get("suggested_role", "Unknown"),
                "user-selected OTIS role": role_dict.get("selected_role", "Unknown"),
                "variable name": str(name),
            }
        )
    return rows


def _first_detected_umat(analysis: dict[str, Any]) -> str:
    routines = analysis.get("detected_umat_routines", []) or analysis.get("detected_subroutines", [])
    for routine in routines:
        name = str(routine.get("name", ""))
        if name.upper() == "UMAT":
            return name.upper()
    if routines:
        return str(routines[0].get("name", "")).upper()
    return ""


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def build_compact_project_config(config: dict[str, Any], *, base_path: str | Path | None = None) -> dict[str, Any]:
    project = _project(config)
    source = _source(config)
    mappings = _mappings(config.get("mapping", {}))
    review = _dict_or_empty(config.get("transformation_review"))
    settings = _dict_or_empty(config.get("transformation_settings"))
    anchors = _dict_or_empty(config.get("transformation_anchors"))
    source_file = str(source.get("selected_umat_file") or source.get("uploaded_file") or "")
    compact_source = _compact_source_path(source_file, base_path=base_path)
    selected_umat = str(source.get("selected_umat_name", "")).upper()
    detected_umat = str(source.get("detected_umat_name", "")).upper()
    output_region = _dict_or_empty(_dict_or_empty(anchors.get("old_tangent")).get("output_region"))
    replace_blocks = _compact_replace_blocks(output_region, review)
    compact = {
        "name": project.get("name", "umat_oti_project"),
        "source": compact_source,
        "jacobian": {
            "seed": mappings.get("dstran", "DSTRAN"),
            "output": mappings.get("stress", "STRESS"),
            "target": mappings.get("ddsdde", "DDSDDE"),
        },
        "promote": sorted({str(value).upper() for value in review.get("promoted_variables", [])}),
        "constant": sorted({str(value).upper() for value in review.get("constant_variables", [])}),
        "real": sorted({str(value).upper() for value in review.get("keep_real_variables", [])}),
        "replace": replace_blocks,
        "ntens": settings.get("ntens"),
        "order": int(settings.get("order", 1) or 1),
    }
    if selected_umat and selected_umat != detected_umat:
        compact["umat"] = selected_umat
    description = str(project.get("description", ""))
    if description:
        compact["description"] = description
    validation = _compact_validation_block(config)
    if validation:
        compact["validation"] = validation
    extra_contracts = _compact_extra_jacobian_contracts(config.get("extra_jacobian_contracts"))
    if extra_contracts:
        compact["constitutive_jacobians"] = extra_contracts
    helper_surfaces = _compact_helper_output_surfaces(config.get("helper_output_surfaces"))
    if helper_surfaces:
        compact["helper_surfaces"] = helper_surfaces
    return compact


def _is_compact_project_config(config: dict[str, Any]) -> bool:
    source = config.get("source")
    source_path = str(source).strip() if isinstance(source, str) else str(_dict_or_empty(source).get("file", "")).strip()
    return bool(source_path) and any(
        key in config for key in ("name", "case_name", "jacobian", "promote", "constant", "real", "replace", "ntens", "order", "validation")
    )


def _expand_compact_project_config(config: dict[str, Any], *, origin_path: str | Path | None = None) -> dict[str, Any]:
    _validate_required_compact_project_config(config)
    normalized = _normalize_compact_project_config(config)
    source = _compact_source_payload(normalized)
    source_path = _resolve_source_path(str(source.get("file", "")), origin_path=origin_path)
    if not source_path.is_file():
        raise ValueError(
            "Compact configuration source must point to a UMAT source available on this machine. "
            f"Could not resolve: {source.get('file', '')}"
        )
    source_text = source_path.read_text(encoding="utf-8", errors="replace")
    analysis = analyze_fortran_source(source_path)
    selected_umat = _compact_selected_umat(source, analysis)
    selected_arguments = _selected_umat_arguments(analysis, selected_umat)
    mappings = _compact_mappings(normalized, selected_arguments)
    variable_roles = suggest_variable_roles(analysis)
    _apply_compact_variable_roles(variable_roles, _compact_variables_payload(normalized))
    region_classifications = _compact_region_classifications(
        analysis=analysis,
        source_text=source_text,
        raw_replace=_compact_replace_payload(normalized),
    )
    routine_roles = suggest_routine_roles(analysis)
    source_metadata = file_metadata(source_path)
    project = _compact_project(normalized, source_path)
    findings_log = build_findings_log(analysis, source_metadata, routine_roles, region_classifications, variable_roles)
    transformation_settings = _compact_transformation_settings(
        config=normalized,
        analysis=analysis,
        project=project,
        source_text=source_text,
    )
    metadata = dict(DEFAULT_METADATA)
    metadata.update(_dict_or_empty(normalized.get("metadata")))
    pipeline = evaluate_pipeline_status(
        has_upload=True,
        selected_umat=selected_umat,
        mappings=mappings,
        variable_roles=variable_roles,
        routine_roles=routine_roles,
        region_classifications=region_classifications,
        accept_unknown_routine_warnings=True,
    )
    full_config = build_project_config(
        project=project,
        source_metadata=source_metadata,
        analysis=analysis,
        selected_umat=selected_umat,
        selected_umat_arguments=selected_arguments,
        mappings=mappings,
        routine_roles=routine_roles,
        region_classifications=region_classifications,
        variable_roles=variable_roles,
        findings_log=findings_log,
        metadata=metadata,
        pipeline=pipeline,
        transformation_settings=transformation_settings,
        validation_settings=_compact_validation_settings(normalized),
    )
    full_config["project"]["description"] = str(normalized.get("description", full_config["project"].get("description", "")))
    full_config["project"]["name"] = str(normalized.get("name", normalized.get("case_name", full_config["project"].get("name", "umat_oti_project"))) or "umat_oti_project")
    full_config["source"]["uploaded_file"] = source_path.name
    full_config["source"]["selected_umat_file"] = str(source_path)
    extra_contracts = _expand_compact_extra_jacobian_contracts(normalized.get("extra_jacobian_contracts"))
    if extra_contracts:
        full_config["extra_jacobian_contracts"] = extra_contracts
    helper_surfaces = _expand_compact_helper_output_surfaces(normalized.get("helper_output_surfaces"))
    if helper_surfaces:
        full_config["helper_output_surfaces"] = helper_surfaces
    return full_config


def _compact_extra_jacobian_contracts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    compact: list[dict[str, Any]] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            continue
        if not _contract_has_compactable_structure(entry):
            compact.append(copy.deepcopy(entry))
            continue
        compact.append(_compact_extra_jacobian_contract(_expand_compact_extra_jacobian_contract(entry, index)))
    return compact


def _compact_extra_jacobian_contract(contract: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "id": str(contract.get("id") or "jacobian_request"),
    }
    selected_umat = str(contract.get("selected_umat") or "").upper()
    if selected_umat and selected_umat != "UMAT":
        compact["selected_umat"] = selected_umat
    description = str(contract.get("description") or "")
    if description:
        compact["description"] = description
    seed = _dict_or_empty(contract.get("seed"))
    seed_variable = str(seed.get("variable") or "").upper()
    if seed_variable:
        compact["seed"] = seed_variable
    seed_shape = _normalized_shape_text(seed.get("shape"), seed.get("components"))
    if seed_shape and seed_shape != "scalar":
        compact["seed_shape"] = seed_shape
    if seed.get("directions") not in (None, ""):
        compact["seed_directions"] = _as_int(seed.get("directions"))
    operating_point = str(seed.get("operating_point_expression") or "")
    if operating_point and operating_point.upper() != seed_variable:
        compact["seed_operating_point"] = operating_point
    output = _dict_or_empty(contract.get("output"))
    output_variable = str(output.get("variable") or "").upper()
    if output_variable:
        compact["output"] = output_variable
    output_shape = _normalized_shape_text(output.get("shape"), _contract_output_components(contract, output_variable))
    if output_shape and output_shape != "scalar":
        compact["output_shape"] = output_shape
    loop = _dict_or_empty(contract.get("loop"))
    loop_compact: dict[str, Any] = {}
    loop_top = _as_int(loop.get("loop_top_line"))
    reseed_after = _as_int(loop.get("reseed_after_line"))
    if loop_top:
        loop_compact["top"] = loop_top
    if reseed_after and reseed_after != loop_top:
        loop_compact["reseed_after"] = reseed_after
    loop_exit_label = str(loop.get("loop_exit_label") or "")
    if loop_exit_label:
        loop_compact["exit_label"] = loop_exit_label
    loop_exit_line = _as_int(loop.get("loop_exit_line"))
    if loop_exit_line:
        loop_compact["exit_line"] = loop_exit_line
    if loop_compact:
        compact["loop"] = loop_compact
    extraction = _dict_or_empty(contract.get("extraction"))
    extract_after = _as_int(extraction.get("extract_after_line"))
    if extract_after:
        compact["extract_after"] = extract_after
    extract_kind = str(extraction.get("extract_kind") or "")
    if extract_kind and extract_kind != "diagonal_scalar":
        compact["extract_kind"] = extract_kind
    internal_use = _dict_or_empty(contract.get("internal_use"))
    replace_variable = str(internal_use.get("replace_variable") or "").upper()
    if replace_variable:
        compact["replace_variable"] = replace_variable
    replace_lines = [_as_int(value) for value in (internal_use.get("replace_lines") or []) if _as_int(value)]
    if replace_lines:
        compact["replace_lines"] = replace_lines
    additional = _compact_auxiliary_extractions(
        contract.get("additional_extractions"),
        output_variable=output_variable,
        output_shape=output_shape,
        default_after_line=extract_after,
        default_kind=extract_kind or "component_map",
    )
    if additional:
        compact["additional_extractions"] = additional
    post_loop_restore = _dict_or_empty(contract.get("post_loop_restore"))
    if post_loop_restore.get("enabled"):
        compact["post_loop_restore"] = copy.deepcopy(post_loop_restore)
    debug_dump = _dict_or_empty(contract.get("debug_dump"))
    if debug_dump.get("enabled"):
        compact["debug_dump"] = copy.deepcopy(debug_dump)
    return compact


def _compact_auxiliary_extractions(
    raw_entries: Any,
    *,
    output_variable: str,
    output_shape: str,
    default_after_line: int,
    default_kind: str,
) -> list[Any]:
    expanded = _expand_compact_auxiliary_extractions(
        raw_entries,
        default_after_line=default_after_line,
        default_kind=default_kind,
        default_shape=output_shape,
        default_output_variable=output_variable,
    )
    compact: list[Any] = []
    for entry in expanded:
        target = str(entry.get("target_variable") or "").upper()
        from_output = str(entry.get("from_output_variable") or "").upper()
        if _is_implicit_output_extraction(
            entry,
            output_variable=output_variable,
            output_shape=output_shape,
            default_after_line=default_after_line,
            default_kind=default_kind,
        ):
            continue
        shape = _normalized_shape_text("", entry.get("components")) or output_shape
        after_line = _as_int(entry.get("after_line"))
        extract_kind = str(entry.get("extract_kind") or default_kind)
        if (
            target
            and from_output == target
            and (not shape or shape == output_shape)
            and after_line == default_after_line
            and extract_kind == default_kind
            and _components_match_shape(entry.get("components"), shape or output_shape, scalar_if_empty=True)
        ):
            compact.append(target)
            continue
        item: dict[str, Any] = {"target": target}
        if from_output and from_output != target:
            item["from"] = from_output
        if shape and shape != "scalar":
            item["shape"] = shape
        if after_line and after_line != default_after_line:
            item["after_line"] = after_line
        if extract_kind and extract_kind != default_kind:
            item["extract_kind"] = extract_kind
        if not _components_match_shape(entry.get("components"), shape, scalar_if_empty=True):
            item["components"] = copy.deepcopy(entry.get("components") or [])
        compact.append(item)
    return compact


def _compact_helper_output_surfaces(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    compact: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if not _helper_surface_has_compactable_structure(entry):
            compact.append(copy.deepcopy(entry))
            continue
        helper_name = str(entry.get("helper_name") or entry.get("helper") or "").upper()
        caller_variable = str(entry.get("caller_variable") or entry.get("target") or "").upper()
        source_local = str(entry.get("source_local") or entry.get("source") or "").upper()
        shape = _normalized_shape_text(entry.get("declared_shape") or entry.get("shape"), entry.get("components"))
        row = {
            "helper": helper_name,
            "target": caller_variable,
            "source": source_local,
        }
        if shape and shape != "scalar":
            row["shape"] = shape
        if not _components_match_shape(entry.get("components"), shape, scalar_if_empty=True):
            row["components"] = copy.deepcopy(entry.get("components") or [])
        compact.append(row)
    return compact


def _expand_compact_extra_jacobian_contracts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    expanded: list[dict[str, Any]] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            continue
        if not _contract_has_compactable_structure(entry):
            expanded.append(copy.deepcopy(entry))
            continue
        expanded.append(_expand_compact_extra_jacobian_contract(entry, index))
    return expanded


def _expand_compact_extra_jacobian_contract(entry: dict[str, Any], index: int) -> dict[str, Any]:
    seed = _expand_compact_seed(entry)
    output = _expand_compact_output(entry)
    loop = _expand_compact_loop(entry)
    extraction = _expand_compact_extraction(entry)
    internal_use = _expand_compact_internal_use(entry)
    additional = _expand_compact_auxiliary_extractions(
        entry.get("additional_extractions"),
        default_after_line=_as_int(extraction.get("extract_after_line")),
        default_kind=str(extraction.get("extract_kind") or "component_map"),
        default_shape=str(output.get("shape") or ""),
        default_output_variable=str(output.get("variable") or ""),
    )
    output_variable = str(output.get("variable") or "").upper()
    replace_variable = str(internal_use.get("replace_variable") or "").upper()
    if (
        output_variable
        and not replace_variable
        and _as_int(extraction.get("extract_after_line"))
        and not any(str(row.get("target_variable") or "").upper() == output_variable for row in additional)
    ):
        additional.insert(
            0,
            {
                "target_variable": output_variable,
                "from_output_variable": output_variable,
                "after_line": _as_int(extraction.get("extract_after_line")),
                "extract_kind": str(extraction.get("extract_kind") or "component_map"),
                "components": _default_component_specs(str(output.get("shape") or ""), scalar_if_empty=True),
            },
        )
    expanded = {
        "id": str(entry.get("id") or f"jacobian_request_{index}"),
        "selected_umat": str(entry.get("selected_umat") or "UMAT").upper(),
        "description": str(entry.get("description") or ""),
        "seed": seed,
        "output": output,
        "loop": loop,
        "extraction": extraction,
        "additional_extractions": additional,
        "internal_use": internal_use,
        "post_loop_restore": _expand_compact_post_loop_restore(entry),
        "debug_dump": _expand_compact_debug_dump(entry),
    }
    if isinstance(entry.get("post_loop_extractions"), list):
        expanded["post_loop_extractions"] = copy.deepcopy(entry.get("post_loop_extractions"))
    return expanded


def _expand_compact_seed(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("seed")
    base = _dict_or_empty(raw)
    variable = str(base.get("variable") or entry.get("seed_variable") or (raw if not isinstance(raw, dict) else "")).upper()
    shape = _normalized_shape_text(base.get("shape") or entry.get("seed_shape"), base.get("components")) or "scalar"
    directions_raw = base.get("directions") if isinstance(raw, dict) else entry.get("seed_directions")
    directions = _as_int(directions_raw) if directions_raw not in (None, "") else (1 if variable else 0)
    operating_point = str(base.get("operating_point_expression") or entry.get("seed_operating_point") or variable)
    result = {
        "variable": variable,
        "shape": shape,
        "directions": directions,
        "operating_point_expression": operating_point,
    }
    components = _expand_component_specs(base.get("components"), shape=shape, scalar_if_empty=(shape == "scalar"))
    if components:
        result["components"] = components
    return result


def _expand_compact_output(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("output")
    base = _dict_or_empty(raw)
    variable = str(base.get("variable") or entry.get("output_variable") or (raw if not isinstance(raw, dict) else "")).upper()
    shape = _normalized_shape_text(base.get("shape") or entry.get("output_shape"), base.get("components")) or str(base.get("shape") or entry.get("output_shape") or "scalar")
    return {
        "variable": variable,
        "shape": shape,
    }


def _expand_compact_loop(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("loop")
    base = _dict_or_empty(raw)
    prelude = base.get("reseed_prelude") or entry.get("reseed_prelude") or []
    result = {
        "loop_top_line": _as_int(base.get("loop_top_line") or base.get("top") or entry.get("loop_top") or entry.get("loop_top_line")),
        "reseed_after_line": _as_int(base.get("reseed_after_line") or base.get("reseed_after") or entry.get("reseed_after") or entry.get("reseed_after_line")),
        "loop_exit_label": str(base.get("loop_exit_label") or base.get("exit_label") or entry.get("loop_exit_label") or entry.get("exit_label") or ""),
        "loop_exit_line": _as_int(base.get("loop_exit_line") or base.get("exit_line") or entry.get("loop_exit_line") or entry.get("exit_line")),
    }
    if isinstance(prelude, list) and prelude:
        result["reseed_prelude"] = [str(line) for line in prelude if str(line).strip()]
    preludes = base.get("preludes") or entry.get("preludes") or []
    expanded_preludes: list[dict[str, Any]] = []
    if isinstance(preludes, list):
        for block in preludes:
            block_dict = _dict_or_empty(block)
            after_line = _as_int(block_dict.get("after_line") or block_dict.get("after"))
            statements = [str(line) for line in (block_dict.get("statements") or []) if str(line).strip()]
            if after_line and statements:
                expanded_preludes.append({"after_line": after_line, "statements": statements})
    if expanded_preludes:
        result["preludes"] = expanded_preludes
    return result


def _expand_compact_extraction(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("extraction")
    base = _dict_or_empty(raw)
    return {
        "extract_after_line": _as_int(base.get("extract_after_line") or entry.get("extract_after") or entry.get("extract_after_line")),
        "extract_kind": str(base.get("extract_kind") or base.get("kind") or entry.get("extract_kind") or entry.get("kind") or "diagonal_scalar"),
    }


def _expand_compact_internal_use(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("internal_use")
    base = _dict_or_empty(raw)
    replace_lines = [
        _as_int(value)
        for value in (base.get("replace_lines") or entry.get("replace_lines") or [])
        if _as_int(value)
    ]
    return {
        "replace_variable": str(base.get("replace_variable") or entry.get("replace_variable") or "").upper(),
        "replace_lines": replace_lines,
    }


def _expand_compact_post_loop_restore(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("post_loop_restore")
    if not isinstance(raw, dict):
        return {
            "enabled": False,
            "method": "implicit_function_step",
            "after_line": 0,
            "residual_variable": "",
            "jacobian_variable": "",
        }
    return {
        "enabled": bool(raw.get("enabled", False)),
        "method": str(raw.get("method") or "implicit_function_step"),
        "after_line": _as_int(raw.get("after_line")),
        "residual_variable": str(raw.get("residual_variable") or "").upper(),
        "jacobian_variable": str(raw.get("jacobian_variable") or "").upper(),
    }


def _expand_compact_debug_dump(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("debug_dump")
    if not isinstance(raw, dict):
        return {"enabled": False, "fields": []}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "fields": [str(value) for value in (raw.get("fields") or []) if str(value).strip()],
    }


def _expand_compact_auxiliary_extractions(
    raw_entries: Any,
    *,
    default_after_line: int,
    default_kind: str,
    default_shape: str,
    default_output_variable: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw_entries, list):
        return []
    expanded: list[dict[str, Any]] = []
    for raw_entry in raw_entries:
        if isinstance(raw_entry, str):
            target = str(raw_entry).strip().upper()
            if not target:
                continue
            expanded.append(
                {
                    "target_variable": target,
                    "from_output_variable": target,
                    "after_line": default_after_line,
                    "extract_kind": default_kind or "component_map",
                    "components": _default_component_specs(default_shape, scalar_if_empty=True),
                }
            )
            continue
        if not isinstance(raw_entry, dict):
            continue
        target = str(raw_entry.get("target_variable") or raw_entry.get("target") or "").upper()
        from_output = str(raw_entry.get("from_output_variable") or raw_entry.get("from") or target or default_output_variable).upper()
        shape = _normalized_shape_text(
            raw_entry.get("shape") or raw_entry.get("target_shape") or raw_entry.get("declared_shape"),
            raw_entry.get("components"),
        ) or default_shape
        components = _expand_component_specs(raw_entry.get("components"), shape=shape, scalar_if_empty=True)
        if not components:
            components = [
                {
                    "target_indices": _normalize_index_list(raw_entry.get("target_indices")),
                    "output_indices": _normalize_index_list(raw_entry.get("output_indices")),
                    "seed_direction_offset": _as_int(raw_entry.get("seed_direction_offset")),
                }
            ]
        expanded.append(
            {
                "target_variable": target,
                "from_output_variable": from_output,
                "after_line": _as_int(raw_entry.get("after_line") or raw_entry.get("extract_after_line")) or default_after_line,
                "extract_kind": str(raw_entry.get("extract_kind") or raw_entry.get("kind") or default_kind or "component_map"),
                "components": components,
            }
        )
    return expanded


def _expand_compact_helper_output_surfaces(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    expanded: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if not _helper_surface_has_compactable_structure(entry):
            expanded.append(copy.deepcopy(entry))
            continue
        helper_name = str(entry.get("helper_name") or entry.get("helper") or "").upper()
        caller_variable = str(entry.get("caller_variable") or entry.get("target") or "").upper()
        source_local = str(entry.get("source_local") or entry.get("source") or "").upper()
        declared_shape = _normalized_shape_text(entry.get("declared_shape") or entry.get("shape"), entry.get("components"))
        row = {
            "helper_name": helper_name,
            "caller_variable": caller_variable,
            "source_local": source_local,
        }
        if declared_shape:
            row["declared_shape"] = declared_shape
        components = _expand_component_specs(entry.get("components"), shape=declared_shape, scalar_if_empty=True)
        if components:
            row["components"] = components
        expanded.append(row)
    return expanded


def _contract_has_compactable_structure(entry: dict[str, Any]) -> bool:
    return any(
        key in entry
        for key in (
            "seed",
            "output",
            "loop",
            "extraction",
            "internal_use",
            "post_loop_restore",
            "debug_dump",
            "additional_extractions",
            "post_loop_extractions",
            "seed_shape",
            "seed_directions",
            "seed_operating_point",
            "output_shape",
            "extract_after",
            "extract_kind",
            "replace_variable",
            "replace_lines",
        )
    )


def _helper_surface_has_compactable_structure(entry: dict[str, Any]) -> bool:
    if any(key in entry for key in ("helper", "target", "source", "shape")):
        return True
    return any(key in entry for key in ("declared_shape", "components"))


def _contract_output_components(contract: dict[str, Any], output_variable: str) -> list[dict[str, Any]]:
    if not output_variable:
        return []
    for entry in contract.get("additional_extractions") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("target_variable") or "").upper() == output_variable.upper():
            return list(entry.get("components") or [])
    return []


def _is_implicit_output_extraction(
    entry: dict[str, Any],
    *,
    output_variable: str,
    output_shape: str,
    default_after_line: int,
    default_kind: str,
) -> bool:
    return (
        str(entry.get("target_variable") or "").upper() == output_variable.upper()
        and str(entry.get("from_output_variable") or "").upper() == output_variable.upper()
        and _as_int(entry.get("after_line")) == default_after_line
        and str(entry.get("extract_kind") or default_kind) == default_kind
        and _components_match_shape(entry.get("components"), output_shape, scalar_if_empty=True)
    )


def _normalized_shape_text(shape: Any, components: Any = None) -> str:
    inferred = _shape_from_components(components)
    text = str(shape or "").strip()
    if not text:
        return inferred
    normalized = text.lower().replace(" ", "")
    if normalized == "scalar":
        return "scalar"
    if normalized in {"vector", "matrix"}:
        return inferred or normalized
    normalized = normalized.replace("x", ",")
    parts = [part for part in normalized.split(",") if part]
    if parts and all(part.isdigit() for part in parts):
        return ",".join(parts)
    return normalized


def _shape_from_components(raw_components: Any) -> str:
    if not isinstance(raw_components, list) or not raw_components:
        return ""
    rank = 0
    maxima: list[int] = []
    for component in raw_components:
        if not isinstance(component, dict):
            continue
        indices = [int(value) for value in (component.get("target_indices") or []) if _as_int(value)]
        rank = max(rank, len(indices))
        while len(maxima) < len(indices):
            maxima.append(0)
        for position, value in enumerate(indices):
            maxima[position] = max(maxima[position], value)
    if rank == 0:
        return "scalar"
    if not maxima or any(value <= 0 for value in maxima[:rank]):
        return ""
    return ",".join(str(value) for value in maxima[:rank])


def _expand_component_specs(raw_components: Any, *, shape: str, scalar_if_empty: bool = False) -> list[dict[str, Any]]:
    explicit = _copy_component_specs(raw_components)
    if explicit:
        return explicit
    return _default_component_specs(shape, scalar_if_empty=scalar_if_empty)


def _copy_component_specs(raw_components: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_components, list):
        return []
    copied: list[dict[str, Any]] = []
    for component in raw_components:
        if not isinstance(component, dict):
            continue
        copied.append(
            {
                "target_indices": _normalize_index_list(component.get("target_indices")),
                "output_indices": _normalize_index_list(component.get("output_indices")),
                "seed_direction_offset": _as_int(component.get("seed_direction_offset")),
            }
        )
    return copied


def _default_component_specs(shape: str, *, scalar_if_empty: bool = False) -> list[dict[str, Any]]:
    normalized = _normalized_shape_text(shape)
    if normalized == "scalar" or (not normalized and scalar_if_empty):
        return [{"target_indices": [], "output_indices": [], "seed_direction_offset": 0}]
    dims = [int(value) for value in normalized.split(",") if value.isdigit()]
    if not dims:
        return []
    if len(dims) == 1:
        return [
            {"target_indices": [index], "output_indices": [index], "seed_direction_offset": 0}
            for index in range(1, dims[0] + 1)
        ]
    if len(dims) == 2:
        return [
            {"target_indices": [row, column], "output_indices": [row, column], "seed_direction_offset": 0}
            for row in range(1, dims[0] + 1)
            for column in range(1, dims[1] + 1)
        ]
    return []


def _components_match_shape(raw_components: Any, shape: str, *, scalar_if_empty: bool = False) -> bool:
    actual = _copy_component_specs(raw_components)
    if not actual:
        return False
    expected = _default_component_specs(shape, scalar_if_empty=scalar_if_empty)
    return actual == expected


def _normalize_index_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [_as_int(item) for item in value if _as_int(item)]


def _validate_required_compact_project_config(config: dict[str, Any]) -> None:
    missing: list[str] = []
    if not str(config.get("name", config.get("case_name", ""))).strip():
        missing.append("name")
    source = _compact_source_payload(config)
    if not str(source.get("file", "")).strip():
        missing.append("source")
    jacobian = _dict_or_empty(config.get("jacobian"))
    if not str(jacobian.get("target", "")).strip():
        missing.append("jacobian.target")
    if not str(jacobian.get("output", jacobian.get("dependent", ""))).strip():
        missing.append("jacobian.output")
    if not str(jacobian.get("seed", jacobian.get("independent", ""))).strip():
        missing.append("jacobian.seed")
    # Only `promote` is a required role list. `constant` and `real` are
    # optional overrides: any variable not listed falls back to its
    # auto-suggested role, and Constant vs Keep-real are equivalent in code
    # generation, so omitting them does not change the transformed UMAT.
    for key in ("promote",):
        if not isinstance(config.get(key), list):
            variables = _dict_or_empty(config.get("variables"))
            if key not in variables or not isinstance(variables.get(key), list):
                missing.append(key)
    raw_replace = config.get("replace")
    replace_present = isinstance(raw_replace, list) or (
        isinstance(raw_replace, dict) and isinstance(raw_replace.get("ddsdde_block"), list)
    )
    if not replace_present:
        missing.append("replace")
    ntens = config.get("ntens")
    order = config.get("order")
    otis = _dict_or_empty(config.get("otis"))
    if ntens in (None, "") and otis.get("ntens") in (None, ""):
        missing.append("ntens")
    if order in (None, "") and otis.get("order") in (None, ""):
        missing.append("order")
    if missing:
        raise ValueError(
            "Compact configuration must define the explicit user contract fields: "
            + ", ".join(missing)
        )


def _compact_source_payload(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("source")
    if isinstance(raw, str):
        payload = {"file": raw}
    else:
        payload = _dict_or_empty(raw)
    selected_umat = str(config.get("umat") or payload.get("umat") or payload.get("selected_umat_name") or "").strip().upper()
    if selected_umat:
        payload["umat"] = selected_umat
    return payload


def _compact_variables_payload(config: dict[str, Any]) -> dict[str, Any]:
    variables = _dict_or_empty(config.get("variables"))
    jacobian = _dict_or_empty(config.get("jacobian"))
    seed_name = str(jacobian.get("seed") or jacobian.get("independent") or "").strip().upper()
    seed_values = variables.get("seed") if isinstance(variables.get("seed"), list) else ([seed_name] if seed_name else [])
    return {
        "seed": [str(value).strip().upper() for value in seed_values if str(value).strip()],
        "promote": [str(value).strip().upper() for value in (config.get("promote") if isinstance(config.get("promote"), list) else variables.get("promote") or []) if str(value).strip()],
        "constant": [str(value).strip().upper() for value in (config.get("constant") if isinstance(config.get("constant"), list) else variables.get("constant") or []) if str(value).strip()],
        "real": [str(value).strip().upper() for value in (config.get("real") if isinstance(config.get("real"), list) else variables.get("real") or []) if str(value).strip()],
    }


def _compact_replace_payload(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("replace")
    if isinstance(raw, list):
        return {"ddsdde_block": list(raw)}
    payload = _dict_or_empty(raw)
    if isinstance(payload.get("ddsdde_block"), list):
        return payload
    return {"ddsdde_block": []}


def _compact_validation_block(config: dict[str, Any]) -> dict[str, Any]:
    validation = _dict_or_empty(config.get("validation_settings"))
    if not validation:
        return {}
    defaults = _default_validation_summary(_analysis(config))
    block: dict[str, Any] = {}
    mode = str(validation.get("material_test_mode") or defaults["mode"]).strip()
    if mode and mode != defaults["mode"]:
        block["mode"] = mode
    compare = validation.get("compare_outputs")
    if isinstance(compare, list):
        compare_values = [str(value).upper() for value in compare if str(value).strip()]
        if compare_values != defaults["compare"]:
            block["compare"] = compare_values
    expected_plasticity = bool(validation.get("expected_plasticity", defaults["expected_plasticity"]))
    if expected_plasticity != defaults["expected_plasticity"]:
        block["expected_plasticity"] = expected_plasticity
    finite_strain = bool(validation.get("finite_strain", defaults["finite_strain"]))
    if finite_strain != defaults["finite_strain"]:
        block["finite_strain"] = finite_strain
    for key in (
        "absolute_tolerance",
        "relative_tolerance",
        "ddsdde_absolute_tolerance",
        "ddsdde_relative_tolerance",
    ):
        value = validation.get(key)
        if value not in (None, ""):
            block[key] = value
    if validation.get("enabled") is False:
        block["enabled"] = False
    return block


def _compact_validation_settings(config: dict[str, Any]) -> dict[str, Any]:
    if isinstance(config.get("validation_settings"), dict):
        return _dict_or_empty(config.get("validation_settings"))
    raw = _dict_or_empty(config.get("validation"))
    if not raw:
        return {}
    settings: dict[str, Any] = {}
    mode = str(raw.get("mode") or raw.get("material_test_mode") or "").strip()
    if mode:
        settings["material_test_mode"] = mode
    compare = raw.get("compare") if isinstance(raw.get("compare"), list) else raw.get("compare_outputs")
    if isinstance(compare, list):
        settings["compare_outputs"] = [str(value).upper() for value in compare if str(value).strip()]
    for key in (
        "absolute_tolerance",
        "relative_tolerance",
        "ddsdde_absolute_tolerance",
        "ddsdde_relative_tolerance",
    ):
        value = raw.get(key)
        if value not in (None, ""):
            settings[key] = value
    if "enabled" in raw:
        settings["enabled"] = bool(raw.get("enabled"))
    if "expected_plasticity" in raw:
        settings["expected_plasticity"] = bool(raw.get("expected_plasticity"))
    if "finite_strain" in raw:
        settings["finite_strain"] = bool(raw.get("finite_strain"))
    return settings


def _default_validation_summary(analysis: dict[str, Any]) -> dict[str, Any]:
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
        "compare": ["STRESS", "STATEV", "DDSDDE", "CONVERGENCE"],
        "expected_plasticity": bool(plastic.get("is_plasticity_candidate")),
        "finite_strain": bool(finite.get("dfgrd_driven_stress_update") or finite.get("executable_dfgrd_use")),
        "mode": material_test_mode,
    }


def _normalize_compact_project_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(config)
    if "name" in normalized and "case_name" not in normalized:
        normalized["case_name"] = normalized["name"]
    source = normalized.get("source")
    if isinstance(source, str):
        normalized["source"] = {"file": source}
    elif not isinstance(source, dict):
        normalized["source"] = {}
    if str(normalized.get("umat", "")).strip() and not normalized["source"].get("umat"):
        normalized["source"]["umat"] = str(normalized.get("umat", "")).strip().upper()
    jacobian = _dict_or_empty(normalized.get("jacobian"))
    if jacobian.get("seed") and not jacobian.get("independent"):
        jacobian["independent"] = jacobian.get("seed")
    if jacobian.get("output") and not jacobian.get("dependent"):
        jacobian["dependent"] = jacobian.get("output")
    normalized["jacobian"] = jacobian
    variables = _dict_or_empty(normalized.get("variables"))
    if not isinstance(variables.get("seed"), list):
        seed_value = jacobian.get("seed") or jacobian.get("independent")
        if isinstance(seed_value, list):
            variables["seed"] = [str(value).strip().upper() for value in seed_value if str(value).strip()]
        elif str(seed_value or "").strip():
            variables["seed"] = [str(seed_value).strip().upper()]
    for key in ("promote", "constant", "real"):
        if key in normalized and not isinstance(variables.get(key), list):
            values = normalized.get(key)
            variables[key] = list(values) if isinstance(values, list) else []
    normalized["variables"] = variables
    replace = normalized.get("replace")
    if isinstance(replace, list):
        normalized["replace"] = {"ddsdde_block": copy.deepcopy(replace)}
    elif not isinstance(replace, dict):
        normalized["replace"] = {}
    otis = _dict_or_empty(normalized.get("otis"))
    if normalized.get("ntens") not in (None, "") and otis.get("ntens") in (None, ""):
        otis["ntens"] = normalized.get("ntens")
    if normalized.get("order") not in (None, "") and otis.get("order") in (None, ""):
        otis["order"] = normalized.get("order")
    normalized["otis"] = otis
    if "constitutive_jacobians" in normalized and "extra_jacobian_contracts" not in normalized:
        normalized["extra_jacobian_contracts"] = copy.deepcopy(normalized["constitutive_jacobians"])
    if "helper_surfaces" in normalized and "helper_output_surfaces" not in normalized:
        normalized["helper_output_surfaces"] = copy.deepcopy(normalized["helper_surfaces"])
    if "validation" in normalized and "validation_settings" not in normalized:
        normalized["validation_settings"] = _expand_simple_validation(normalized.get("validation"))
    return normalized


def _expand_simple_validation(raw: Any) -> dict[str, Any]:
    validation = _dict_or_empty(raw)
    if not validation:
        return {}
    expanded: dict[str, Any] = {}
    if str(validation.get("mode", "")).strip():
        expanded["material_test_mode"] = str(validation.get("mode", "")).strip()
    compare = validation.get("compare")
    if isinstance(compare, list):
        expanded["compare_outputs"] = [str(value).upper() for value in compare if str(value).strip()]
    for key in (
        "expected_plasticity",
        "finite_strain",
        "absolute_tolerance",
        "relative_tolerance",
        "ddsdde_absolute_tolerance",
        "ddsdde_relative_tolerance",
    ):
        if key in validation:
            expanded[key] = validation.get(key)
    return expanded


def _resolve_source_path(path_text: str, *, origin_path: str | Path | None = None) -> Path:
    candidate = Path(path_text).expanduser()
    search_paths = []
    if candidate.is_absolute():
        search_paths.append(candidate)
    else:
        origin = Path(origin_path).expanduser() if origin_path else None
        if origin is not None:
            origin_base = origin if origin.is_dir() else origin.parent
            search_paths.append(origin_base / candidate)
        search_paths.extend(
            [
                Path.cwd() / candidate,
                _project_root() / candidate,
            ]
        )
    for path in search_paths:
        if path.is_file():
            return path.resolve()
    return candidate.resolve() if candidate.is_absolute() else candidate


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _compact_source_path(source_file: str, *, base_path: str | Path | None = None) -> str:
    if not source_file:
        return ""
    path = Path(source_file).expanduser()
    if base_path is None:
        return str(path)
    base = Path(base_path).expanduser()
    base_dir = base if base.is_dir() else base.parent
    try:
        return os.path.relpath(path.resolve(), base_dir.resolve())
    except OSError:
        return str(path)


def _compact_selected_umat(source: dict[str, Any], analysis: dict[str, Any]) -> str:
    configured = str(source.get("umat") or source.get("selected_umat_name") or "").strip().upper()
    if configured:
        arguments = _selected_umat_arguments(analysis, configured)
        if arguments:
            return configured
        raise ValueError(f"Compact configuration source.umat was not found in the analyzed source: {configured}")
    selected = _first_detected_umat(analysis)
    if selected:
        return selected
    raise ValueError("Compact configuration source file does not contain a detectable UMAT routine.")


def _selected_umat_arguments(analysis: dict[str, Any], selected_umat: str) -> list[str]:
    for routine in analysis.get("detected_subroutines", []):
        if str(routine.get("name", "")).upper() == selected_umat.upper():
            return [str(argument).upper() for argument in routine.get("arguments", [])]
    return []


def _compact_mappings(config: dict[str, Any], arguments: list[str]) -> dict[str, str]:
    mappings = _default_mappings(arguments)
    jacobian = _dict_or_empty(config.get("jacobian"))
    if jacobian.get("target"):
        mappings["ddsdde"] = str(jacobian.get("target", "")).upper()
    if jacobian.get("dependent"):
        mappings["stress"] = str(jacobian.get("dependent", "")).upper()
    if jacobian.get("independent"):
        mappings["dstran"] = str(jacobian.get("independent", "")).upper()
    optional_mapping = _dict_or_empty(config.get("mapping"))
    for key, value in optional_mapping.items():
        text = str(value).strip().upper()
        if text:
            mappings[str(key).lower()] = text
    return mappings


def _default_mappings(arguments: list[str]) -> dict[str, str]:
    argument_set = {argument.upper() for argument in arguments}
    keys = list(REQUIRED_GUI_MAPPINGS) + list(OPTIONAL_GUI_MAPPINGS)
    return {key.lower(): key if key in argument_set else "" for key in keys}


def _apply_compact_variable_roles(variable_roles: list[dict[str, object]], raw_variables: dict[str, Any]) -> None:
    role_groups = {
        "Seed": _upper_names(raw_variables.get("seed")),
        "Promote": _upper_names(raw_variables.get("promote")),
        "Constant": _upper_names(raw_variables.get("constant")),
        "Keep real": _upper_names(raw_variables.get("real")),
    }
    _validate_compact_variable_roles(role_groups)
    assigned_names = {name for names in role_groups.values() for name in names}
    for row in variable_roles:
        name = str(row.get("variable name", "")).upper()
        for role_name, names in role_groups.items():
            if name in names:
                row["user-selected OTIS role"] = role_name
                assigned_names.discard(name)
                break
    for role_name, names in role_groups.items():
        for name in sorted(names & assigned_names):
            variable_roles.append(
                {
                    "appears in UMAT arguments yes/no": "no",
                    "detected shape/dimension": "",
                    "detected type": "unknown",
                    "detected usage": "configured in compact JSON",
                    "notes": "Added from compact JSON variable role assignment.",
                    "read/write/unknown": "unknown",
                    "suggested OTIS role": role_name,
                    "user-selected OTIS role": role_name,
                    "variable name": name,
                }
            )


def _validate_compact_variable_roles(role_groups: dict[str, set[str]]) -> None:
    assigned: dict[str, str] = {}
    for role_name, names in role_groups.items():
        for name in names:
            previous = assigned.get(name)
            if previous and previous != role_name:
                raise ValueError(f"Compact configuration assigns variable {name} to multiple roles: {previous}, {role_name}")
            assigned[name] = role_name


def _upper_names(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value).strip().upper() for value in values if str(value).strip()}


def _compact_region_classifications(
    *,
    analysis: dict[str, Any],
    source_text: str,
    raw_replace: dict[str, Any],
) -> list[dict[str, object]]:
    rows = [dict(row) for row in analysis.get("detected_regions", []) if isinstance(row, dict)]
    ddsdde_ranges = raw_replace.get("ddsdde_block")
    if not ddsdde_ranges:
        return rows
    spans = _line_spans(ddsdde_ranges, "replace.ddsdde_block")
    direct_lines = _direct_ddsdde_lines(analysis)
    first_stress_start = min(
        (
            _as_int(row.get("start line"))
            for row in rows
            if str(row.get("region type", "")) == "stress"
        ),
        default=0,
    )
    matched_spans: set[tuple[int, int]] = set()
    for row in rows:
        if str(row.get("region type", "")) != "tangent":
            continue
        row_span = (_as_int(row.get("start line")), _as_int(row.get("end line")))
        if _spans_overlap_any(row_span, spans):
            row["user-selected classification"] = "Tangent-only, replace with OTIS extraction"
            row["notes"] = "Configured from compact JSON replace.ddsdde_block."
            matched_spans.add(row_span)
            continue
        if not _region_has_direct_ddsdde(row_span, direct_lines):
            continue
        if first_stress_start and row_span[1] < first_stress_start:
            continue
        row["user-selected classification"] = "Ignore"
        row["notes"] = "Excluded by compact JSON replace.ddsdde_block."
    source_lines = source_text.splitlines()
    for span in spans:
        if any(_spans_overlap(span, matched) for matched in matched_spans):
            continue
        rows.append(
            {
                "detected reason": "Configured from compact JSON replace.ddsdde_block.",
                "detected variables": ["DDSDDE"],
                "end line": span[1],
                "notes": "Configured from compact JSON replace.ddsdde_block.",
                "region id": f"TANGENT-COMPACT-{len(rows) + 1:03d}",
                "region type": "tangent",
                "short code preview": _preview(source_lines, span[0], span[1]),
                "start line": span[0],
                "suggested classification": "Tangent-only, replace with OTIS extraction",
                "user-selected classification": "Tangent-only, replace with OTIS extraction",
            }
        )
    return rows


def _line_spans(values: Any, field_name: str) -> list[tuple[int, int]]:
    if not isinstance(values, list):
        raise ValueError(f"Compact configuration {field_name} must be a list of line ranges like ['83-87'].")
    spans: list[tuple[int, int]] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if "-" in text:
            start_text, end_text = text.split("-", 1)
        else:
            start_text = text
            end_text = text
        try:
            start = int(start_text)
            end = int(end_text)
        except ValueError as exc:
            raise ValueError(f"Compact configuration {field_name} contains an invalid line range: {text}") from exc
        if start <= 0 or end <= 0 or end < start:
            raise ValueError(f"Compact configuration {field_name} contains an invalid line range: {text}")
        spans.append((start, end))
    return spans


def _direct_ddsdde_lines(analysis: dict[str, Any]) -> set[int]:
    lines: set[int] = set()
    for row in analysis.get("assignments_to_ddsdde", []) or []:
        for value in row.get("line_numbers", []) or []:
            number = _as_int(value)
            if number:
                lines.add(number)
    return lines


def _region_has_direct_ddsdde(span: tuple[int, int], direct_lines: set[int]) -> bool:
    return any(span[0] <= line <= span[1] for line in direct_lines)


def _spans_overlap_any(span: tuple[int, int], others: list[tuple[int, int]]) -> bool:
    return any(_spans_overlap(span, other) for other in others)


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def _preview(source_lines: list[str], start_line: int, end_line: int) -> str:
    if not source_lines or start_line <= 0 or end_line <= 0:
        return ""
    selected = source_lines[max(start_line - 1, 0) : min(end_line, len(source_lines))]
    return "\n".join(line.rstrip() for line in selected)


def _compact_project(config: dict[str, Any], source_path: Path) -> dict[str, str]:
    project = _dict_or_empty(config.get("project"))
    case_name = str(config.get("case_name", "") or source_path.stem or "umat_oti_project")
    workdir = project.get("workdir")
    if workdir:
        resolved_workdir = Path(str(workdir)).expanduser()
    else:
        resolved_workdir = Path.cwd() / "umat_oti_workspace" / sanitize_project_name(case_name)
    return {
        "description": str(project.get("description") or config.get("description", "")),
        "name": case_name,
        "workdir": str(resolved_workdir),
    }


def _compact_replace_blocks(output_region: dict[str, Any], review: dict[str, Any]) -> list[str]:
    if output_region:
        start = _as_int(output_region.get("start_line") or output_region.get("start line"))
        end = _as_int(output_region.get("end_line") or output_region.get("end line"))
        if start and end:
            return [_format_span(start, end)]
    result: list[str] = []
    for row in review.get("old_tangent_regions_to_replace", []) or []:
        if not isinstance(row, dict):
            continue
        classification = str(row.get("classification") or row.get("user-selected classification") or "")
        role = str(row.get("role", ""))
        if role not in {"", "ddsdde_output_replace"} and role != "ddsdde_output_replace":
            continue
        if classification and "replace" not in classification.lower() and role != "ddsdde_output_replace":
            continue
        start = _as_int(row.get("start_line") or row.get("start line"))
        end = _as_int(row.get("end_line") or row.get("end line"))
        if start and end:
            result.append(_format_span(start, end))
    return result


def _format_span(start_line: int, end_line: int) -> str:
    return str(start_line) if start_line == end_line else f"{start_line}-{end_line}"


def _compact_transformation_settings(
    *,
    config: dict[str, Any],
    analysis: dict[str, Any],
    project: dict[str, str],
    source_text: str,
) -> dict[str, Any]:
    otis = _dict_or_empty(config.get("otis"))
    output_dir = str(otis.get("output_dir", "")).strip()
    settings = build_transformation_settings(
        analysis=analysis,
        project_workdir=project.get("workdir", Path.cwd() / "umat_oti_workspace"),
        source_text=source_text,
        fallback_ntens=DEFAULT_GENERATED_NTENS,
        output_dir=Path(project.get("workdir", Path.cwd() / "umat_oti_workspace")) / output_dir if output_dir and not Path(output_dir).is_absolute() else output_dir or None,
    )
    if "ntens" in otis and otis.get("ntens") not in (None, ""):
        ntens = _positive_int(otis.get("ntens"), "otis.ntens")
        settings["ntens"] = ntens
        settings["ntens_source"] = "compact JSON otis.ntens"
        settings["ntens_confidence"] = "configured"
        settings["ntens_warning"] = ""
    if "order" in otis and otis.get("order") not in (None, ""):
        order = _positive_int(otis.get("order"), "otis.order")
        # order == 1 -> material tangent DDSDDE only. order > 1 additionally
        # computes the higher-order stress Jacobians (d^k STRESS/dDSTRAN...) and
        # writes them to a side file at runtime; see _higher_order_jacobian_lines.
        settings["order"] = order
    return settings


def _positive_int(value: Any, field_name: str) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Compact configuration {field_name} must be a positive integer.") from exc
    if number <= 0:
        raise ValueError(f"Compact configuration {field_name} must be a positive integer.")
    return number


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0