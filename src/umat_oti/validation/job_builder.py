from __future__ import annotations

import ast
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from umat_oti.validation.compiler_check import run_gfortran_smoke, write_compile_script


ABAQUS_NOT_FOUND_MESSAGE = "Abaqus executable not found. Validation layer was configured but not run."
DEFAULT_ABAQUS_MODULES = "abaqus/2024 intel/oneapi/2024.2.0.634"
DEFAULT_ABAQUS_RUN_PREFIX = "srun --partition=compute1 --ntasks=1 --cpus-per-task=2 --time=00-00:30:00"


@dataclass
class ValidationBuildResult:
    validation_dir: Path
    files: dict[str, str]
    report: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def infer_validation_dimensions_from_source(
    source_text: str,
    *,
    statev_name: str = "STATEV",
    props_name: str = "PROPS",
    default_nstatv: int = 1,
    default_nprops: int = 2,
    ntens: int | None = None,
) -> tuple[int, int]:
    symbol_values = _validation_symbol_values(ntens)
    inferred_nstatv = _infer_max_symbolic_subscript(source_text, statev_name, symbol_values) or default_nstatv
    inferred_nprops = _infer_max_constant_subscript(source_text, props_name) or default_nprops
    return max(inferred_nstatv, 1), max(inferred_nprops, 1)


def infer_validation_ntens_from_source(source_text: str, *, fallback_ntens: int) -> tuple[int, str]:
    if fallback_ntens in {3, 4, 6}:
        return int(fallback_ntens), "transformation ntens"
    explicit_tensor_ntens = _infer_max_tensor_component_index(
        source_text,
        ("DSTRAN", "STRAN", "STRESS", "DDSDDE", "EELAS", "EPLAS", "DSTRESS", "DS", "STSREL", "XBACK"),
    )
    if explicit_tensor_ntens in {3, 4, 6}:
        return explicit_tensor_ntens, "explicit tensor component indices"
    comment_ntens = _infer_ntens_from_statev_layout_comments(source_text)
    if comment_ntens in {3, 4, 6}:
        return comment_ntens, "state variable layout comments"
    return max(int(fallback_ntens), 1), "transformation ntens"


def build_validation_workspace(
    *,
    validation_dir: Path,
    original_umat: Path,
    transformed_umat: Path,
    generated_dir: Path,
    project_config: dict[str, Any] | None = None,
    ntens: int,
    abaqus_command: str = "abaqus",
    abaqus_modules: str = DEFAULT_ABAQUS_MODULES,
    run_prefix: str = DEFAULT_ABAQUS_RUN_PREFIX,
    material_test_mode: str = "single element tension",
    nstatv: int | None = None,
    nprops: int | None = None,
    statev_name: str = "STATEV",
    ddsdde_name: str = "DDSDDE",
    run_compile_smoke: bool = True,
    compare_outputs: list[str] | None = None,
    comparison_abs_tolerance: float | None = None,
    comparison_rel_tolerance: float | None = None,
    comparison_ddsdde_abs_tolerance: float | None = None,
    comparison_ddsdde_rel_tolerance: float | None = None,
) -> ValidationBuildResult:
    validation_dir = Path(validation_dir)
    validation_dir.mkdir(parents=True, exist_ok=True)
    original_umat = Path(original_umat)
    transformed_umat = Path(transformed_umat)
    generated_dir = Path(generated_dir)
    source_text = original_umat.read_text(encoding="utf-8", errors="replace") if original_umat.is_file() else ""
    validation_ntens, validation_ntens_source = infer_validation_ntens_from_source(source_text, fallback_ntens=ntens)
    inferred_nstatv, inferred_nprops = infer_validation_dimensions_from_source(source_text, statev_name=statev_name, ntens=validation_ntens)
    nstatv = inferred_nstatv if nstatv is None or nstatv < 1 else nstatv
    nprops = inferred_nprops if nprops is None or nprops < 1 else nprops
    ddsdde_slot_count = max(validation_ntens, 0) * max(validation_ntens, 0)
    normalized_compare_outputs = [str(value).upper() for value in (compare_outputs or ["STRESS", "STATEV", "DDSDDE", "convergence"])]
    constitutive_validation = _constitutive_validation_artifacts(project_config, source_text, nstatv, ddsdde_slot_count)
    constitutive_slot_count = int(constitutive_validation.get("component_count") or 0)
    validation_nstatv = nstatv + ddsdde_slot_count + constitutive_slot_count
    expected_plasticity, expected_finite_geometry = _validation_expectations(project_config)
    load_case = _load_case(
        material_test_mode,
        expected_plasticity=expected_plasticity,
        expected_finite_geometry=expected_finite_geometry,
    )
    files: dict[str, str] = {}
    warnings: list[str] = []

    comparable_constitutive_artifacts = constitutive_validation.get("comparable_artifacts") if isinstance(constitutive_validation.get("comparable_artifacts"), list) else []
    files["instrumented_original_user"] = str(
        _write_instrumented_original_user(
            validation_dir,
            original_umat,
            validation_ntens,
            nstatv,
            statev_name,
            ddsdde_name,
            comparable_constitutive_artifacts,
        )
    )
    files["original_inp"] = str(_write_input_deck(validation_dir / "original_umat_validation.inp", validation_ntens, material_test_mode, validation_nstatv, nprops, source_text))
    files["otis_inp"] = str(_write_input_deck(validation_dir / "otis_umat_validation.inp", validation_ntens, material_test_mode, validation_nstatv, nprops, source_text))
    files["combined_oti_user"] = str(
        _write_combined_oti_user(
            validation_dir,
            generated_dir,
            transformed_umat,
            ntens,
            validation_ntens,
            nstatv,
            statev_name,
            ddsdde_name,
            comparable_constitutive_artifacts,
        )
    )
    files["compile_script"] = str(write_compile_script(validation_dir, generated_dir, transformed_umat, ntens))
    files.update(_write_run_scripts(validation_dir, abaqus_command, Path(files["instrumented_original_user"]), abaqus_modules, run_prefix))
    files["extract_results_script"] = str(
        _write_extract_results_script(
            validation_dir / "extract_results.py",
            nstatv,
            validation_ntens,
            comparable_constitutive_artifacts,
        )
    )
    files["original_results_json"] = str(validation_dir / "original_results.json")
    files["otis_results_json"] = str(validation_dir / "otis_results.json")
    files["comparison_report_json"] = str(validation_dir / "comparison_report.json")
    files["comparison_report_md"] = str(validation_dir / "comparison_report.md")
    files["validation_report_json"] = str(validation_dir / "validation_report.json")
    files["validation_report_md"] = str(validation_dir / "validation_report.md")

    compile_result = run_gfortran_smoke(validation_dir, generated_dir, transformed_umat, ntens).to_json() if run_compile_smoke else {
        "status": "configured",
        "script_path": files["compile_script"],
        "warnings": ["Compile script generated; compile smoke was not run."],
        "errors": [],
    }
    warnings.extend(compile_result.get("warnings", []))
    abaqus_status = _abaqus_status(abaqus_command, abaqus_modules)
    if abaqus_status["status"] == "not_found":
        warnings.append(ABAQUS_NOT_FOUND_MESSAGE)
    constitutive_report = dict(constitutive_validation)
    constitutive_report["enabled"] = "CONSTITUTIVE_JACOBIANS" in normalized_compare_outputs
    constitutive_report["method"] = (
        "validation-only STATEV instrumentation for caller-visible constitutive outputs; "
        "helper-local surfaces remain preview-only unless the target variable already exists in the original UMAT scope."
    )
    constitutive_report.pop("comparable_artifacts", None)
    report = {
        "schema_version": 2,
        "final_pass": False,
        "status": "configured",
        "original_umat_path": str(original_umat),
        "transformed_umat_path": str(transformed_umat),
        "generated_otilib_dir": str(generated_dir),
        "validation_dir": str(validation_dir),
        "abaqus_command": abaqus_command,
        "abaqus_modules": abaqus_modules,
        "abaqus_run_prefix": run_prefix,
        "abaqus_status": abaqus_status,
        "load_case": load_case,
        "material_test_mode": material_test_mode,
        "ntens": ntens,
        "validation_ntens": validation_ntens,
        "validation_ntens_source": validation_ntens_source,
        "nstatv": nstatv,
        "validation_nstatv": validation_nstatv,
        "nprops": nprops,
        "generated_files": files,
        "semantic_checks": {
            "abaqus_validation_scripts_generated": all(
                Path(files[key]).is_file()
                for key in ("original_inp", "otis_inp", "run_original_script", "run_otis_script", "extract_results_script")
            ),
        },
        "compile_check": compile_result,
        "original_run_status": {"status": "not_run"},
        "transformed_run_status": {"status": "not_run"},
        "extraction_status": {"status": "not_run"},
        "comparison_status": {"status": "not_run"},
        "activation_check": {
            "status": "not_run",
            "expected_plasticity": load_case["expected_plasticity"],
            "expected_finite_geometry": load_case["nlgeom"] == "YES",
        },
        "comparison_settings": {
            "compare_outputs": normalized_compare_outputs,
            "absolute_tolerance": comparison_abs_tolerance,
            "relative_tolerance": comparison_rel_tolerance,
            "ddsdde_absolute_tolerance": comparison_ddsdde_abs_tolerance,
            "ddsdde_relative_tolerance": comparison_ddsdde_rel_tolerance,
        },
        "ddsdde_validation": {
            "status": "configured",
            "enabled": "DDSDDE" in normalized_compare_outputs,
            "method": "validation-only STATEV instrumentation",
            "state_variable_name": statev_name,
            "ddsdde_name": ddsdde_name,
            "user_state_variable_count": nstatv,
            "validation_state_variable_count": validation_nstatv,
            "statev_start_index": nstatv + 1,
            "statev_end_index": validation_nstatv,
            "component_count": ddsdde_slot_count,
            "component_order": "row-major DDSDDE(i,j): i varies slowest, j varies fastest",
            "message": "DDSDDE is copied into extra SDV slots in validation-only UMAT copies; comparison behavior is controlled by comparison_settings.compare_outputs.",
        },
        "constitutive_validation": constitutive_report,
        "warnings": warnings,
        "errors": compile_result.get("errors", []),
    }
    write_validation_report(validation_dir, report)
    return ValidationBuildResult(validation_dir, files, report, warnings)


def load_validation_report(validation_dir: Path) -> dict[str, Any]:
    path = Path(validation_dir) / "validation_report.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def update_validation_report(validation_dir: Path, updates: dict[str, Any]) -> dict[str, Any]:
    report = load_validation_report(validation_dir)
    report.update(updates)
    write_validation_report(validation_dir, report)
    return report


def write_validation_report(validation_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    validation_dir = Path(validation_dir)
    validation_dir.mkdir(parents=True, exist_ok=True)
    json_path = validation_dir / "validation_report.json"
    md_path = validation_dir / "validation_report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_validation_markdown(report), encoding="utf-8")
    return json_path, md_path


def _write_combined_oti_user(
    validation_dir: Path,
    generated_dir: Path,
    transformed_umat: Path,
    transform_ntens: int,
    validation_ntens: int,
    nstatv: int,
    statev_name: str,
    ddsdde_name: str,
    constitutive_artifacts: list[dict[str, Any]] | None = None,
) -> Path:
    compile_order = generated_dir / "compile_order.txt"
    if compile_order.is_file():
        parts = [generated_dir / line.strip() for line in compile_order.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        parts = [
            generated_dir / "master_parameters.f90",
            generated_dir / "real_utils.f90",
            generated_dir / f"otim{transform_ntens}n1.f90",
            transformed_umat,
        ]
    combined = validation_dir / "combined_oti_user.f90"
    chunks: list[str] = []
    transformed_resolved = transformed_umat.resolve()
    for path in parts:
        chunks.append(f"! ===== BEGIN {path.name} =====\n")
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.resolve() == transformed_resolved:
                text = _instrument_umat_for_ddsdde_state_output(
                    text,
                    validation_ntens,
                    nstatv,
                    statev_name,
                    ddsdde_name,
                    constitutive_artifacts,
                )
            fixed_form = path.suffix.lower() in {".f", ".for", ".ftn"}
            chunks.append(_fixed_form_to_free_form(text) if fixed_form else text)
        else:
            chunks.append(f"! MISSING FILE: {path}\n")
        if not chunks[-1].endswith("\n"):
            chunks[-1] += "\n"
        chunks.append(f"! ===== END {path.name} =====\n\n")
    combined_text = _append_common_validation_helpers("".join(chunks))
    combined.write_text(combined_text, encoding="utf-8")
    return combined


def _write_instrumented_original_user(
    validation_dir: Path,
    original_umat: Path,
    validation_ntens: int,
    nstatv: int,
    statev_name: str,
    ddsdde_name: str,
    constitutive_artifacts: list[dict[str, Any]] | None = None,
) -> Path:
    suffix = original_umat.suffix if original_umat.suffix else ".for"
    output_path = validation_dir / f"original_umat_with_ddsdde_sdv{suffix}"
    source_text = original_umat.read_text(encoding="utf-8", errors="replace") if original_umat.is_file() else ""
    _copy_source_local_includes(validation_dir, original_umat.parent, source_text)
    source_text = _append_common_validation_helpers(source_text)
    output_path.write_text(
        _instrument_umat_for_ddsdde_state_output(
            source_text,
            validation_ntens,
            nstatv,
            statev_name,
            ddsdde_name,
            constitutive_artifacts,
        ),
        encoding="utf-8",
    )
    return output_path


def _copy_source_local_includes(validation_dir: Path, source_dir: Path, source_text: str) -> None:
    for include_name in sorted(set(re.findall(r"^\s*INCLUDE\s*['\"]([^'\"]+)['\"]", source_text, flags=re.IGNORECASE | re.MULTILINE))):
        include_path = Path(include_name)
        if include_path.is_absolute() or ".." in include_path.parts:
            continue
        source_include = source_dir / include_path
        if source_include.is_file():
            target = validation_dir / include_path.name
            if source_include.resolve() != target.resolve():
                shutil.copy2(source_include, target)


def _append_common_validation_helpers(source_text: str) -> str:
    additions: list[str] = []
    needs_kclear = _calls_helper(source_text, "KCLEAR") and not _has_subroutine_definition(source_text, "KCLEAR")
    needs_kmmult = _calls_helper(source_text, "KMMULT") and not _has_subroutine_definition(source_text, "KMMULT")
    needs_kmavec = _calls_helper(source_text, "KMAVEC") and not _has_subroutine_definition(source_text, "KMAVEC")
    needs_kmatsub = _calls_helper(source_text, "KMATSUB") and not _has_subroutine_definition(source_text, "KMATSUB")
    needs_kmtran = _calls_helper(source_text, "KMTRAN") and not _has_subroutine_definition(source_text, "KMTRAN")
    needs_ksmult = _calls_helper(source_text, "KSMULT") and not _has_subroutine_definition(source_text, "KSMULT")
    needs_kupdvec = _calls_helper(source_text, "KUPDVEC") and not _has_subroutine_definition(source_text, "KUPDVEC")
    needs_kclearv = (needs_kmavec or _calls_helper(source_text, "KCLEARV")) and not _has_subroutine_definition(source_text, "KCLEARV")
    if needs_kclear:
        additions.append(_validation_helper_kclear())
    if needs_kclearv:
        additions.append(_validation_helper_kclearv())
    if needs_kmmult:
        additions.append(_validation_helper_kmmult())
    if needs_kmavec:
        additions.append(_validation_helper_kmavec())
    if needs_kmatsub:
        additions.append(_validation_helper_kmatsub())
    if needs_kmtran:
        additions.append(_validation_helper_kmtran())
    if needs_ksmult:
        additions.append(_validation_helper_ksmult())
    if needs_kupdvec:
        additions.append(_validation_helper_kupdvec())
    if not additions:
        return source_text
    text = source_text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n" + "\n".join(additions)


def _instrument_umat_for_ddsdde_state_output(
    source_text: str,
    ntens: int,
    nstatv: int,
    statev_name: str,
    ddsdde_name: str,
    constitutive_artifacts: list[dict[str, Any]] | None = None,
) -> str:
    if "OTIS-VALIDATION-DDSDDE-STATEV" in source_text and "OTIS-VALIDATION-CONSTITUTIVE-STATEV" in source_text:
        return source_text
    lines = source_text.splitlines()
    instrumented: list[str] = []
    in_umat = False
    inserted = False
    for line in lines:
        statement = _fortran_statement(line)
        if _is_umat_start(statement):
            in_umat = True
            inserted = False
        if in_umat and _is_return_statement(statement):
            instrumented.extend(_validation_statev_assignment_lines(ntens, nstatv, statev_name, ddsdde_name, constitutive_artifacts))
            inserted = True
        if in_umat and _is_routine_end(statement):
            if not inserted:
                instrumented.extend(_validation_statev_assignment_lines(ntens, nstatv, statev_name, ddsdde_name, constitutive_artifacts))
                inserted = True
            in_umat = False
        instrumented.append(line)
    if in_umat and not inserted:
        instrumented.extend(_validation_statev_assignment_lines(ntens, nstatv, statev_name, ddsdde_name, constitutive_artifacts))
    return "\n".join(instrumented) + ("\n" if source_text.endswith("\n") or source_text else "")


def _validation_statev_assignment_lines(
    ntens: int,
    nstatv: int,
    statev_name: str,
    ddsdde_name: str,
    constitutive_artifacts: list[dict[str, Any]] | None,
) -> list[str]:
    lines = _ddsdde_statev_assignment_lines(ntens, nstatv, statev_name, ddsdde_name)
    lines.extend(_constitutive_statev_assignment_lines(statev_name, constitutive_artifacts))
    return lines


def _ddsdde_statev_assignment_lines(ntens: int, nstatv: int, statev_name: str, ddsdde_name: str) -> list[str]:
    lines = ["      ! OTIS-VALIDATION-DDSDDE-STATEV-BEGIN"]
    slot = nstatv
    for row in range(1, ntens + 1):
        for column in range(1, ntens + 1):
            slot += 1
            lines.append(f"      {statev_name}({slot}) = {ddsdde_name}({row},{column})")
    lines.append("      ! OTIS-VALIDATION-DDSDDE-STATEV-END")
    return lines


def _constitutive_statev_assignment_lines(statev_name: str, constitutive_artifacts: list[dict[str, Any]] | None) -> list[str]:
    artifacts = constitutive_artifacts if isinstance(constitutive_artifacts, list) else []
    if not artifacts:
        return []
    lines = ["      ! OTIS-VALIDATION-CONSTITUTIVE-STATEV-BEGIN"]
    for artifact in artifacts:
        start = int(artifact.get("statev_start_index") or 0)
        expressions = artifact.get("component_expressions") if isinstance(artifact.get("component_expressions"), list) else []
        if start < 1 or not expressions:
            continue
        lines.append(f"      ! {artifact.get('id', artifact.get('target_variable', 'constitutive_artifact'))}")
        for offset, expression in enumerate(expressions):
            lines.append(f"      {statev_name}({start + offset}) = {expression}")
    lines.append("      ! OTIS-VALIDATION-CONSTITUTIVE-STATEV-END")
    return lines


def _calls_helper(source_text: str, name: str) -> bool:
        pattern = rf"\bCALL\s+{re.escape(name)}\b"
        return any(re.search(pattern, _fortran_statement(line), flags=re.IGNORECASE) for line in source_text.splitlines())


def _has_subroutine_definition(source_text: str, name: str) -> bool:
        pattern = rf"^(?:\d+\s+)?SUBROUTINE\s+{re.escape(name)}\b"
        return any(re.match(pattern, _fortran_statement(line), flags=re.IGNORECASE) for line in source_text.splitlines())


def _constitutive_validation_artifacts(
    project_config: dict[str, Any] | None,
    source_text: str,
    user_nstatv: int,
    ddsdde_slot_count: int,
) -> dict[str, Any]:
    comparable_artifacts: list[dict[str, Any]] = []
    preview_artifacts: list[dict[str, Any]] = []
    slot_cursor = user_nstatv + ddsdde_slot_count
    if not isinstance(project_config, dict):
        return {
            "status": "not_configured",
            "available": False,
            "component_count": 0,
            "artifact_count": 0,
            "comparable_artifact_count": 0,
            "preview_only_artifact_count": 0,
            "statev_start_index": None,
            "statev_end_index": None,
            "artifacts": [],
            "comparable_artifacts": [],
        }
    umat_scope = _umat_scope_statements(source_text)
    seen_keys: set[tuple[str, tuple[str, ...]]] = set()
    for artifact in _constitutive_validation_candidates(project_config):
        target_variable = str(artifact.get("target_variable") or "").strip().upper()
        expressions = artifact.get("component_expressions") if isinstance(artifact.get("component_expressions"), list) else []
        key = (target_variable, tuple(str(value) for value in expressions))
        if not target_variable or not expressions or key in seen_keys:
            continue
        seen_keys.add(key)
        component_count = len(expressions)
        artifact_report = {
            "id": str(artifact.get("id") or target_variable),
            "artifact_class": str(artifact.get("artifact_class") or "constitutive_artifact"),
            "target_variable": str(artifact.get("target_variable") or ""),
            "target_shape": str(artifact.get("target_shape") or ""),
            "source_variable": str(artifact.get("source_variable") or ""),
            "source_kind": str(artifact.get("source_kind") or ""),
            "description": str(artifact.get("description") or ""),
            "component_count": component_count,
            "component_labels": list(artifact.get("component_labels") or []),
        }
        if not _variable_used_in_umat_scope(umat_scope, target_variable):
            preview_artifacts.append(
                {
                    **artifact_report,
                    "comparison_status": "preview_only",
                    "message": "Target variable is not visible in the original UMAT scope; this artifact is shown in the preview but not copied into validation STATEV slots.",
                }
            )
            continue
        slot_start = slot_cursor + 1
        slot_cursor += component_count
        comparable_artifact = {
            **artifact,
            "component_count": component_count,
            "statev_start_index": slot_start,
            "statev_end_index": slot_cursor,
        }
        comparable_artifacts.append(comparable_artifact)
        preview_artifacts.append(
            {
                **artifact_report,
                "comparison_status": "comparable",
                "statev_start_index": slot_start,
                "statev_end_index": slot_cursor,
                "message": "Copied into validation-only STATEV slots for Abaqus comparison.",
            }
        )
    return {
        "status": "configured" if preview_artifacts else "not_applicable",
        "available": bool(comparable_artifacts),
        "component_count": sum(int(artifact.get("component_count") or 0) for artifact in comparable_artifacts),
        "artifact_count": len(preview_artifacts),
        "comparable_artifact_count": len(comparable_artifacts),
        "preview_only_artifact_count": sum(1 for artifact in preview_artifacts if artifact.get("comparison_status") != "comparable"),
        "statev_start_index": comparable_artifacts[0]["statev_start_index"] if comparable_artifacts else None,
        "statev_end_index": comparable_artifacts[-1]["statev_end_index"] if comparable_artifacts else None,
        "artifacts": preview_artifacts,
        "comparable_artifacts": comparable_artifacts,
    }


def _constitutive_validation_candidates(project_config: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for contract in project_config.get("extra_jacobian_contracts", []) if isinstance(project_config.get("extra_jacobian_contracts"), list) else []:
        if not isinstance(contract, dict):
            continue
        candidates.extend(_extra_jacobian_contract_candidates(contract))
    for index, surface in enumerate(project_config.get("helper_output_surfaces", []) if isinstance(project_config.get("helper_output_surfaces"), list) else [], start=1):
        if not isinstance(surface, dict):
            continue
        candidates.extend(_helper_output_surface_candidates(surface, index))
    return candidates


def _extra_jacobian_contract_candidates(contract: dict[str, Any]) -> list[dict[str, Any]]:
    contract_id = str(contract.get("id") or "contract")
    description = str(contract.get("description") or contract_id)
    candidates: list[dict[str, Any]] = []
    output = contract.get("output") if isinstance(contract.get("output"), dict) else {}
    internal_use = contract.get("internal_use") if isinstance(contract.get("internal_use"), dict) else {}
    replacement_variable = str(internal_use.get("replace_variable") or "").strip()
    extraction = contract.get("extraction") if isinstance(contract.get("extraction"), dict) else {}
    if replacement_variable:
        candidates.append(
            {
                "id": contract_id,
                "artifact_class": "contract_output_replacement",
                "target_variable": replacement_variable,
                "target_shape": "scalar",
                "source_variable": str(output.get("variable") or replacement_variable),
                "source_kind": str(extraction.get("extract_kind") or "scalar"),
                "description": description,
                "component_expressions": [replacement_variable],
                "component_labels": [replacement_variable],
            }
        )
    elif not _has_additional_target(contract, str(output.get("variable") or "")):
        target_variable = str(output.get("variable") or "").strip()
        if target_variable:
            expressions, labels = _artifact_component_expressions(target_variable, declared_shape=str(output.get("shape") or ""))
            if expressions:
                candidates.append(
                    {
                        "id": contract_id,
                        "artifact_class": "contract_output",
                        "target_variable": target_variable,
                        "target_shape": str(output.get("shape") or ""),
                        "source_variable": target_variable,
                        "source_kind": str(extraction.get("extract_kind") or "scalar"),
                        "description": description,
                        "component_expressions": expressions,
                        "component_labels": labels,
                    }
                )
    for index, row in enumerate(contract.get("additional_extractions", []) if isinstance(contract.get("additional_extractions"), list) else [], start=1):
        if not isinstance(row, dict):
            continue
        target_variable = str(row.get("target_variable") or "").strip()
        if not target_variable:
            continue
        expressions, labels = _artifact_component_expressions(
            target_variable,
            components=row.get("components") if isinstance(row.get("components"), list) else None,
        )
        if not expressions and target_variable:
            expressions, labels = [target_variable], [target_variable]
        candidates.append(
            {
                "id": f"{contract_id}::aux::{index}",
                "artifact_class": "contract_auxiliary_extraction",
                "target_variable": target_variable,
                "target_shape": "matrix" if len(expressions) > 1 else "scalar",
                "source_variable": str(row.get("from_output_variable") or target_variable),
                "source_kind": str(row.get("extract_kind") or "component_map"),
                "description": description,
                "component_expressions": expressions,
                "component_labels": labels,
            }
        )
    return candidates


def _helper_output_surface_candidates(surface: dict[str, Any], index: int) -> list[dict[str, Any]]:
    target_variable = str(surface.get("caller_variable") or "").strip()
    if not target_variable:
        return []
    expressions, labels = _artifact_component_expressions(
        target_variable,
        components=surface.get("components") if isinstance(surface.get("components"), list) else None,
        declared_shape=str(surface.get("declared_shape") or ""),
    )
    if not expressions:
        return []
    helper_name = str(surface.get("helper_name") or "helper")
    return [
        {
            "id": f"{helper_name}::helper_surface::{index}",
            "artifact_class": "helper_output_surface",
            "target_variable": target_variable,
            "target_shape": "matrix" if len(expressions) > 1 else "scalar",
            "source_variable": str(surface.get("source_local") or target_variable),
            "source_kind": "helper_local_surface",
            "description": f"Surface helper-local {surface.get('source_local', '')} from {helper_name}",
            "component_expressions": expressions,
            "component_labels": labels,
        }
    ]


def _artifact_component_expressions(
    target_variable: str,
    *,
    components: list[dict[str, Any]] | None = None,
    declared_shape: str = "",
) -> tuple[list[str], list[str]]:
    variable_name = str(target_variable or "").strip()
    if not variable_name:
        return [], []
    if isinstance(components, list) and components:
        expressions: list[str] = []
        labels: list[str] = []
        for component in components:
            if not isinstance(component, dict):
                continue
            indices = component.get("target_indices") if isinstance(component.get("target_indices"), list) else []
            if not indices:
                continue
            index_text = ",".join(str(int(value)) for value in indices)
            expression = f"{variable_name}({index_text})"
            expressions.append(expression)
            labels.append(expression)
        return expressions, labels
    normalized_shape = declared_shape.strip().lower()
    if not normalized_shape or normalized_shape == "scalar":
        return [variable_name], [variable_name]
    dimensions = [int(value.strip()) for value in declared_shape.split(",") if value.strip().isdigit()]
    if not dimensions:
        return [], []
    if len(dimensions) == 1:
        expressions = [f"{variable_name}({index})" for index in range(1, dimensions[0] + 1)]
        return expressions, list(expressions)
    if len(dimensions) == 2:
        expressions = [
            f"{variable_name}({row},{column})"
            for row in range(1, dimensions[0] + 1)
            for column in range(1, dimensions[1] + 1)
        ]
        return expressions, list(expressions)
    return [], []


def _has_additional_target(contract: dict[str, Any], target_variable: str) -> bool:
    target = str(target_variable or "").strip().upper()
    if not target:
        return False
    rows = contract.get("additional_extractions") if isinstance(contract.get("additional_extractions"), list) else []
    return any(str(row.get("target_variable") or "").strip().upper() == target for row in rows if isinstance(row, dict))


def _umat_scope_statements(source_text: str) -> str:
    lines: list[str] = []
    in_umat = False
    for raw_line in source_text.splitlines():
        statement = _fortran_statement(raw_line)
        if _is_umat_start(statement):
            in_umat = True
        if in_umat and statement:
            lines.append(statement)
        if in_umat and _is_routine_end(statement):
            break
    return "\n".join(lines)


def _variable_used_in_umat_scope(umat_scope: str, variable_name: str) -> bool:
    scope = str(umat_scope or "")
    name = str(variable_name or "").strip()
    if not scope or not name:
        return False
    return bool(re.search(rf"\b{re.escape(name)}\b", scope, flags=re.IGNORECASE))


def _validation_helper_kclearv() -> str:
        return """
            SUBROUTINE KCLEARV(A,N)
            IMPLICIT REAL*8 (A-H,O-Z)
            DIMENSION A(N)
            DO I=1,N
                A(I)=0.0D0
            END DO
            RETURN
            END
""".strip("\n")


def _validation_helper_kclear() -> str:
        return """
            SUBROUTINE KCLEAR(A,N,M)
            IMPLICIT REAL*8 (A-H,O-Z)
            DIMENSION A(N,M)
            DO I=1,N
                DO J=1,M
                    A(I,J)=0.0D0
                END DO
            END DO
            RETURN
            END
""".strip("\n")


def _validation_helper_kmavec() -> str:
        return """
            SUBROUTINE KMAVEC(A,NRA,NCA,B,C)
            IMPLICIT REAL*8 (A-H,O-Z)
            DIMENSION A(NRA,NCA),B(NCA),C(NRA)
            CALL KCLEARV(C,NRA)
            DO K1=1,NRA
                DO K2=1,NCA
                    C(K1)=C(K1)+A(K1,K2)*B(K2)
                END DO
            END DO
            RETURN
            END
""".strip("\n")


def _validation_helper_kmmult() -> str:
        return """
            SUBROUTINE KMMULT(A,NRA,NCA,B,NRB,NCB,C)
            IMPLICIT REAL*8 (A-H,O-Z)
            PARAMETER (ZERO=0.0D0)
            DIMENSION A(NRA,NCA),B(NRB,NCB),C(NRA,NCB)
            CALL KCLEAR(C,NRA,NCB)
            DUM=ZERO
            DO I=1,NRA
                DO J=1,NCB
                    DO K=1,NCA
                        DUM=DUM+A(I,K)*B(K,J)
                    END DO
                    C(I,J)=DUM
                    DUM=ZERO
                END DO
            END DO
            RETURN
            END
""".strip("\n")


def _validation_helper_kmatsub() -> str:
        return """
            SUBROUTINE KMATSUB(A,NRA,NCA,B,C,IFLAG)
            IMPLICIT REAL*8 (A-H,O-Z)
            PARAMETER (ONE=1.0D0, ONENEG=-1.0D0)
            DIMENSION A(NRA,NCA),B(NRA,NCA),C(NRA,NCA)
            CALL KCLEAR(C,NRA,NCA)
            SCALAR=ONENEG
            IF (IFLAG.EQ.1) SCALAR=ONE
            DO I=1,NRA
                DO J=1,NCA
                    C(I,J)=A(I,J)+B(I,J)*SCALAR
                END DO
            END DO
            RETURN
            END
""".strip("\n")


def _validation_helper_kmtran() -> str:
        return """
            SUBROUTINE KMTRAN(A,NRA,NCA,B)
            IMPLICIT REAL*8 (A-H,O-Z)
            DIMENSION A(NRA,NCA),B(NCA,NRA)
            CALL KCLEAR(B,NCA,NRA)
            DO I=1,NRA
                DO J=1,NCA
                    B(J,I)=A(I,J)
                END DO
            END DO
            RETURN
            END
""".strip("\n")


def _validation_helper_ksmult() -> str:
        return """
            SUBROUTINE KSMULT(A,NR,NC,S)
            IMPLICIT REAL*8 (A-H,O-Z)
            DIMENSION A(NR,NC)
            DO I=1,NR
                DO J=1,NC
                    DUM=A(I,J)
                    A(I,J)=S*DUM
                    DUM=0.0D0
                END DO
            END DO
            RETURN
            END
""".strip("\n")


def _validation_helper_kupdvec() -> str:
        return """
            SUBROUTINE KUPDVEC(A,NR,B)
            IMPLICIT REAL*8 (A-H,O-Z)
            DIMENSION A(NR),B(NR)
            DO I=1,NR
                A(I)=A(I)+B(I)
            END DO
            RETURN
            END
""".strip("\n")


def _fortran_statement(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("!"):
        return ""
    if line[:1] in {"C", "c", "*"}:
        return ""
    return re.sub(r"!.*$", "", stripped).strip()


def _is_umat_start(statement: str) -> bool:
    return bool(re.match(r"^(?:\d+\s+)?SUBROUTINE\s+UMAT\b", statement, flags=re.IGNORECASE))


def _is_return_statement(statement: str) -> bool:
    return bool(re.match(r"^(?:\d+\s+)?RETURN\b", statement, flags=re.IGNORECASE))


def _is_routine_end(statement: str) -> bool:
    return bool(re.match(r"^(?:\d+\s+)?END(?:\s+SUBROUTINE(?:\s+UMAT)?)?\s*$", statement, flags=re.IGNORECASE))


def _fixed_form_to_free_form(source_text: str) -> str:
    lines = source_text.splitlines()
    converted: list[str] = []
    for original_line in lines:
        line = _normalize_fixed_form_tab_line(original_line)
        if not line.strip():
            converted.append("")
            continue
        if line.strip().upper().startswith("USE OTIM") and "," not in line:
            converted.append(line.rstrip())
            continue
        first = line[0]
        if first in {"C", "c", "*"}:
            converted.append("!" + line[1:])
            continue
        continuation = len(line) > 5 and line[5] not in {" ", "0"}
        if continuation:
            while converted and converted[-1].strip() == "":
                converted.pop()
            if converted and not converted[-1].rstrip().endswith("&"):
                converted[-1] = converted[-1].rstrip() + " &"
            converted.append("     & " + line[6:].lstrip())
            continue
        converted.append(line.rstrip())
    return "\n".join(converted) + "\n"


def _normalize_fixed_form_tab_line(line: str) -> str:
    if not line.startswith("\t"):
        return line
    rest = line.lstrip("\t")
    if not rest:
        return ""
    first = rest[0]
    if first == "&" or (first.isdigit() and first != "0"):
        return "     " + first + rest[1:]
    return "      " + rest


def _restricted_oti_use_lines(module_name: str, source_text: str) -> list[str]:
    type_match = re.fullmatch(r"otim(\d+)n(\d+)", module_name, flags=re.IGNORECASE)
    type_name = f"ONUMM{type_match.group(1)}N{type_match.group(2)}" if type_match else "ONUMM4N1"
    seed_names = sorted(set(re.findall(r"\bE\d+\b", source_text)), key=lambda name: int(name[1:]))
    names = [type_name, *seed_names, "GETIM", "REAL", "ABS", "SQRT", "EXP", "LOG", "SIN", "COS", "ASSIGNMENT(=)", "OPERATOR(+)", "OPERATOR(-)", "OPERATOR(*)", "OPERATOR(/)"]
    lines: list[str] = []
    current = f"      USE {module_name}, ONLY: "
    for index, name in enumerate(names):
        token = name if index == 0 else f", {name}"
        if len(current) + len(token) > 100:
            lines.append(current.rstrip() + ", &")
            current = f"     & {name}"
        else:
            current += token
    lines.append(current)
    return lines


def _infer_max_constant_subscript(source_text: str, variable_name: str) -> int:
    if not source_text.strip() or not variable_name.strip():
        return 0
    pattern = re.compile(rf"\b{re.escape(variable_name)}\s*\(\s*(\d+)\s*\)", flags=re.IGNORECASE)
    return max((int(match.group(1)) for match in pattern.finditer(source_text)), default=0)


def _infer_max_tensor_component_index(source_text: str, variable_names: tuple[str, ...]) -> int:
    if not source_text.strip() or not variable_names:
        return 0
    variable_pattern = re.compile(rf"\b(?:{'|'.join(re.escape(name) for name in variable_names)})\s*\(([^()]*)\)", flags=re.IGNORECASE)
    do_pattern = re.compile(r"^(?:\d+\s+)?DO\s+(\w+)\s*=\s*([^,]+)\s*,\s*([^,\n!]+)", flags=re.IGNORECASE)
    end_do_pattern = re.compile(r"^(?:\d+\s+)?END\s*DO\b", flags=re.IGNORECASE)
    max_index = 0
    loop_stack: list[tuple[str, int]] = []

    for raw_line in source_text.splitlines():
        statement = _fortran_statement(raw_line)
        if not statement:
            continue
        do_match = do_pattern.match(statement)
        if do_match:
            loop_var = do_match.group(1).upper()
            upper_bound = _safe_integer_expression(do_match.group(3), {})
            if upper_bound is not None:
                loop_stack.append((loop_var, upper_bound))
        for match in variable_pattern.finditer(statement):
            scoped_symbols = {loop_var: upper_bound for loop_var, upper_bound in loop_stack}
            for expression in match.group(1).split(","):
                value = _safe_integer_expression(expression, scoped_symbols)
                if value is not None:
                    max_index = max(max_index, value)
        if end_do_pattern.match(statement) and loop_stack:
            loop_stack.pop()
    return max_index


def _infer_ntens_from_statev_layout_comments(source_text: str) -> int:
    if not source_text.strip():
        return 0
    range_pattern = re.compile(r"^\s*[Cc*!]\s*(\d+)\s*-\s*(\d+)\s*:\s*(.+)$")
    ranges: list[tuple[int, int, str]] = []
    for line in source_text.splitlines():
        match = range_pattern.match(line)
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2))
        description = match.group(3).strip().upper()
        ranges.append((start, end, description))

    elastic = next((row for row in ranges if "ELASTIC STRAIN" in row[2]), None)
    plastic = next((row for row in ranges if "PLASTIC STRAIN" in row[2]), None)
    if elastic is not None:
        elastic_size = elastic[1] - elastic[0] + 1
        if elastic[0] == 1 and elastic_size in {3, 4, 6}:
            if plastic is None:
                return elastic_size
            plastic_size = plastic[1] - plastic[0] + 1
            if plastic[0] == elastic[1] + 1 and plastic_size == elastic_size:
                return elastic_size
    return 0


def _validation_symbol_values(ntens: int | None) -> dict[str, int]:
    if ntens is None or ntens < 1:
        return {}
    if ntens == 4:
        return {"NTENS": 4, "NDI": 3, "NSHR": 1}
    if ntens == 6:
        return {"NTENS": 6, "NDI": 3, "NSHR": 3}
    if ntens == 3:
        return {"NTENS": 3, "NDI": 3, "NSHR": 0}
    return {"NTENS": ntens}


def _infer_max_symbolic_subscript(source_text: str, variable_name: str, symbol_values: dict[str, int]) -> int:
    if not source_text.strip() or not variable_name.strip():
        return 0
    lines = source_text.splitlines()
    variable_pattern = re.compile(rf"\b{re.escape(variable_name)}\s*\(\s*([^()]+)\s*\)", flags=re.IGNORECASE)
    do_pattern = re.compile(r"^\s*DO\s+(\w+)\s*=\s*([^,]+)\s*,\s*([^,\n!]+)", flags=re.IGNORECASE)
    end_do_pattern = re.compile(r"^\s*END\s*DO\b", flags=re.IGNORECASE)
    max_index = 0
    loop_stack: list[tuple[str, int]] = []

    for raw_line in lines:
        line = raw_line.split("!", 1)[0]
        do_match = do_pattern.match(line)
        if do_match:
            loop_var = do_match.group(1).upper()
            upper_bound = _safe_integer_expression(do_match.group(3), symbol_values)
            if upper_bound is not None:
                loop_stack.append((loop_var, upper_bound))
        for match in variable_pattern.finditer(line):
            expression = match.group(1)
            scoped_symbols = dict(symbol_values)
            for loop_var, upper_bound in loop_stack:
                scoped_symbols[loop_var] = upper_bound
            value = _safe_integer_expression(expression, scoped_symbols)
            if value is not None:
                max_index = max(max_index, value)
        if end_do_pattern.match(line) and loop_stack:
            loop_stack.pop()

    return max_index


def _safe_integer_expression(expression: str, symbol_values: dict[str, int]) -> int | None:
    candidate = expression.strip().upper()
    if not candidate or not re.fullmatch(r"[A-Z0-9_+\-*/(). ]+", candidate):
        return None
    try:
        node = ast.parse(candidate, mode="eval")
    except SyntaxError:
        return None

    def evaluate(current: ast.AST) -> float:
        if isinstance(current, ast.Expression):
            return evaluate(current.body)
        if isinstance(current, ast.Constant) and isinstance(current.value, (int, float)):
            return float(current.value)
        if isinstance(current, ast.Name) and current.id in symbol_values:
            return float(symbol_values[current.id])
        if isinstance(current, ast.UnaryOp) and isinstance(current.op, (ast.UAdd, ast.USub)):
            value = evaluate(current.operand)
            return value if isinstance(current.op, ast.UAdd) else -value
        if isinstance(current, ast.BinOp) and isinstance(current.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left = evaluate(current.left)
            right = evaluate(current.right)
            if isinstance(current.op, ast.Add):
                return left + right
            if isinstance(current.op, ast.Sub):
                return left - right
            if isinstance(current.op, ast.Mult):
                return left * right
            if right == 0.0:
                raise ZeroDivisionError
            return left / right
        raise ValueError

    try:
        value = evaluate(node)
    except (ValueError, ZeroDivisionError):
        return None
    rounded = int(round(value))
    return rounded if abs(value - rounded) < 1.0e-9 else None


def _write_input_deck(path: Path, ntens: int, mode: str, nstatv: int, nprops: int, source_text: str) -> Path:
    deck = _plane_strain_deck(mode, nstatv, nprops, source_text) if ntens == 4 else _three_dimensional_deck(mode, nstatv, nprops, source_text)
    path.write_text(deck, encoding="utf-8")
    return path


def _plane_strain_deck(mode: str, nstatv: int, nprops: int, source_text: str) -> str:
    load_case = _load_case(mode)
    boundary = _plane_boundary_conditions(mode, load_case)
    depvar = f"*Depvar\n{nstatv}\n" if nstatv > 0 else ""
    props = _props_lines(nprops, source_text)
    return f"""*Heading
UMAT OTIS validation scaffold: CPE4 single element
*Preprint, echo=NO, model=NO, history=NO, contact=NO
*Node
1, 0., 0.
2, 1., 0.
3, 1., 1.
4, 0., 1.
*Element, type=CPE4, elset=EALL
1, 1, 2, 3, 4
*Nset, nset=LEFT
1, 4
*Nset, nset=RIGHT
2, 3
*Nset, nset=BOTTOM
1, 2
*Nset, nset=TOP
3, 4
*Solid Section, elset=EALL, material=USERMAT
1.0,
*Material, name=USERMAT
*User Material, constants={max(nprops, 1)}, type=MECHANICAL
{props}{depvar}*Step, name={load_case['step_name']}, nlgeom={load_case['nlgeom']}
*Static
{load_case['initial_increment']}, 1.0
{boundary}*Output, field, frequency=1
*Element Output, elset=EALL
S, E, SDV
*Node Output
U
*End Step
"""


def _three_dimensional_deck(mode: str, nstatv: int, nprops: int, source_text: str) -> str:
    load_case = _load_case(mode)
    boundary = _three_dimensional_boundary_conditions(mode, load_case)
    depvar = f"*Depvar\n{nstatv}\n" if nstatv > 0 else ""
    props = _props_lines(nprops, source_text)
    return f"""*Heading
UMAT OTIS validation scaffold: C3D8 single element
*Preprint, echo=NO, model=NO, history=NO, contact=NO
*Node
1, 0., 0., 0.
2, 1., 0., 0.
3, 1., 1., 0.
4, 0., 1., 0.
5, 0., 0., 1.
6, 1., 0., 1.
7, 1., 1., 1.
8, 0., 1., 1.
*Element, type=C3D8, elset=EALL
1, 1, 2, 3, 4, 5, 6, 7, 8
*Nset, nset=X0
1, 4, 5, 8
*Nset, nset=X1
2, 3, 6, 7
*Nset, nset=Y0
1, 2, 5, 6
*Nset, nset=Y1
3, 4, 7, 8
*Nset, nset=Z0
1, 2, 3, 4
*Nset, nset=Z1
5, 6, 7, 8
*Solid Section, elset=EALL, material=USERMAT
1.0,
*Material, name=USERMAT
*User Material, constants={max(nprops, 1)}, type=MECHANICAL
{props}{depvar}*Step, name={load_case['step_name']}, nlgeom={load_case['nlgeom']}
*Static
{load_case['initial_increment']}, 1.0
{boundary}*Output, field, frequency=1
*Element Output, elset=EALL
S, E, SDV
*Node Output
U
*End Step
"""


def _plane_boundary_conditions(mode: str, load_case: dict[str, Any]) -> str:
    base = "*Boundary\nLEFT, 1, 1, 0.0\nBOTTOM, 2, 2, 0.0\n"
    if mode == "single element shear":
        return base + f"TOP, 1, 1, {load_case['shear_displacement']}\n"
    if mode == "single element mixed strain":
        return base + f"RIGHT, 1, 1, {load_case['axial_displacement']}\nTOP, 2, 2, {load_case['transverse_displacement']}\nTOP, 1, 1, {load_case['mixed_shear_displacement']}\n"
    return base + f"RIGHT, 1, 1, {load_case['axial_displacement']}\n"


def _three_dimensional_boundary_conditions(mode: str, load_case: dict[str, Any]) -> str:
    base = "*Boundary\nX0, 1, 1, 0.0\nY0, 2, 2, 0.0\nZ0, 3, 3, 0.0\n"
    if mode == "single element shear":
        return base + f"Y1, 1, 1, {load_case['shear_displacement']}\n"
    if mode == "single element mixed strain":
        return base + f"X1, 1, 1, {load_case['axial_displacement']}\nY1, 2, 2, {load_case['transverse_displacement']}\nY1, 1, 1, {load_case['mixed_shear_displacement']}\nZ1, 3, 3, {load_case['transverse_displacement']}\n"
    return base + f"X1, 1, 1, {load_case['axial_displacement']}\n"


def _validation_expectations(project_config: dict[str, Any] | None) -> tuple[bool | None, bool | None]:
    if not isinstance(project_config, dict):
        return None, None
    expected_plasticity: bool | None = None
    expected_finite_geometry: bool | None = None
    validation = project_config.get("validation") if isinstance(project_config.get("validation"), dict) else {}
    if "expected_plasticity" in validation:
        expected_plasticity = bool(validation.get("expected_plasticity"))
    if "finite_strain" in validation:
        expected_finite_geometry = bool(validation.get("finite_strain"))
    analysis = project_config.get("analysis") if isinstance(project_config.get("analysis"), dict) else {}
    plastic = analysis.get("plasticity_indicators") if isinstance(analysis.get("plasticity_indicators"), dict) else {}
    finite = analysis.get("finite_strain") if isinstance(analysis.get("finite_strain"), dict) else {}
    if expected_plasticity is None and "is_plasticity_candidate" in plastic:
        expected_plasticity = bool(plastic.get("is_plasticity_candidate"))
    if expected_finite_geometry is None and finite:
        expected_finite_geometry = bool(finite.get("dfgrd_driven_stress_update") or finite.get("executable_dfgrd_use"))
    return expected_plasticity, expected_finite_geometry


def _load_case(
    mode: str,
    *,
    expected_plasticity: bool | None = None,
    expected_finite_geometry: bool | None = None,
) -> dict[str, Any]:
    normalized = mode.lower()
    requested_plasticity = "plastic" in normalized or "yield" in normalized
    requested_finite_geometry = any(token in normalized for token in ("finite", "large", "nlgeom"))
    nlgeom = "YES" if requested_finite_geometry else "NO"
    axial = "3.0E-3" if requested_plasticity else "1.0E-4"
    shear = "1.5E-3" if requested_plasticity else "1.0E-4"
    transverse = "1.5E-3" if requested_plasticity else "5.0E-5"
    mixed_shear = "7.5E-4" if requested_plasticity else "2.5E-5"
    step_name = "FINITE_PLASTIC_PROBE" if requested_plasticity and nlgeom == "YES" else "PLASTIC_PROBE" if requested_plasticity else "SMALL_STRAIN"
    return {
        "mode": mode,
        "step_name": step_name,
        "nlgeom": nlgeom,
        "requested_plasticity": requested_plasticity,
        "requested_finite_geometry": requested_finite_geometry,
        "expected_plasticity": requested_plasticity if expected_plasticity is None else bool(expected_plasticity),
        "expected_finite_geometry": requested_finite_geometry if expected_finite_geometry is None else bool(expected_finite_geometry),
        "axial_displacement": axial,
        "shear_displacement": shear,
        "transverse_displacement": transverse,
        "mixed_shear_displacement": mixed_shear,
        "initial_increment": "0.25" if requested_plasticity else "1.0",
    }


def _props_lines(nprops: int, source_text: str) -> str:
    values = ["1.0"] * max(nprops, 1)
    for index in _poisson_ratio_prop_indices(source_text, len(values)):
        values[index - 1] = "0.3"
    lines = []
    for index in range(0, len(values), 8):
        lines.append(", ".join(values[index : index + 8]) + ",")
    return "\n".join(lines) + "\n"


def _poisson_ratio_prop_indices(source_text: str, nprops: int) -> set[int]:
    if not source_text.strip() or nprops < 1:
        return set()
    pattern = re.compile(r"\b([A-Z][A-Z0-9_]*)\s*=\s*PROPS\(\s*(\d+)\s*\)", flags=re.IGNORECASE)
    indices: set[int] = set()
    for match in pattern.finditer(source_text):
        name = match.group(1).upper()
        index = int(match.group(2))
        if 1 <= index <= nprops and _looks_like_poisson_ratio_name(name):
            indices.add(index)
    return indices


def _looks_like_poisson_ratio_name(name: str) -> bool:
    return name in {"ENU", "POISSON", "POISS", "PR", "XNU", "ANU", "ENU0"} or name.endswith("NU") or "POIS" in name


def _write_run_scripts(validation_dir: Path, abaqus_command: str, original_umat: Path, abaqus_modules: str, run_prefix: str) -> dict[str, str]:
    combined = validation_dir / "combined_oti_user.f90"
    scripts = {
        "run_original_script": validation_dir / "run_original_abaqus.sh",
        "run_otis_script": validation_dir / "run_otis_abaqus.sh",
        "run_both_script": validation_dir / "run_both_abaqus.sh",
    }
    scripts["run_original_script"].write_text(_run_script(abaqus_command, abaqus_modules, run_prefix, "original_umat_validation", original_umat.resolve()), encoding="utf-8")
    scripts["run_otis_script"].write_text(_run_script(abaqus_command, abaqus_modules, run_prefix, "otis_umat_validation", combined.resolve()), encoding="utf-8")
    scripts["run_both_script"].write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")\"\n./run_original_abaqus.sh\n./run_otis_abaqus.sh\n",
        encoding="utf-8",
    )
    for script in scripts.values():
        script.chmod(0o755)
    return {key: str(path) for key, path in scripts.items()}


def _run_script(abaqus_command: str, abaqus_modules: str, run_prefix: str, job_name: str, user_file: Path) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "SCRIPT_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
        f"ABAQUS_CMD=\"${{ABAQUS_CMD:-{abaqus_command}}}\"\n"
        f"ABAQUS_MODULES=\"${{ABAQUS_MODULES:-{abaqus_modules}}}\"\n"
        f"ABAQUS_RUN_PREFIX=\"${{ABAQUS_RUN_PREFIX:-{run_prefix}}}\"\n"
        "if [[ \"${1:-}\" != \"--inside-compute\" && -n \"$ABAQUS_RUN_PREFIX\" && -z \"${SLURM_JOB_ID:-}\" ]]; then\n"
        "  exec $ABAQUS_RUN_PREFIX \"$0\" --inside-compute\n"
        "fi\n"
        "cd \"$SCRIPT_DIR\"\n"
        "if [[ -n \"$ABAQUS_MODULES\" ]]; then\n"
        "  module load $ABAQUS_MODULES\n"
        "fi\n"
        f"$ABAQUS_CMD job={job_name} user='{str(user_file)}' double=both interactive\n"
    )


def _write_extract_results_script(
    path: Path,
    user_nstatv: int,
    ntens: int,
    constitutive_artifacts: list[dict[str, Any]] | None = None,
) -> Path:
    script = """# Runs under the Abaqus-bundled Python interpreter, which is Python 2.7 in
# Abaqus <= 2022 and Python 3 in Abaqus >= 2023. Keep this script compatible
# with both: no annotations, no py3-only open() kwargs.
import json
import sys

from odbAccess import openOdb


USER_STATE_COUNT = __USER_NSTATV__
NTENS = __NTENS__
DDSDDE_SLOT_COUNT = NTENS * NTENS
CONSTITUTIVE_ARTIFACTS = __CONSTITUTIVE_ARTIFACTS__


def float_list(values):
    try:
        return [float(value) for value in values]
    except TypeError:
        return [float(values)]


def scalar_or_first(value):
    try:
        return float(value)
    except TypeError:
        return float(value[0])


def field_values(frame, key):
    if key not in frame.fieldOutputs:
        return []
    values = frame.fieldOutputs[key].values
    return values if values else []


def state_variable_values(frame):
    keys = sorted(frame.fieldOutputs.keys())
    if 'SDV' in frame.fieldOutputs:
        values = field_values(frame, 'SDV')
        if values:
            return float_list(values[0].data), 'SDV'
    sdv_keys = sorted(
        [key for key in keys if key.upper().startswith('SDV') and key[3:].isdigit()],
        key=lambda key: int(key[3:]),
    )
    result = []
    for key in sdv_keys:
        values = field_values(frame, key)
        if values:
            result.append(scalar_or_first(values[0].data))
    return result, ','.join(sdv_keys)


def ddsdde_values(state_values):
    start = USER_STATE_COUNT
    end = USER_STATE_COUNT + DDSDDE_SLOT_COUNT
    if len(state_values) < end:
        return []
    return [float(value) for value in state_values[start:end]]


def matrix_from_flat(values):
    return [values[index:index + NTENS] for index in range(0, len(values), NTENS)]


def constitutive_outputs(state_values):
    result = {}
    for artifact in CONSTITUTIVE_ARTIFACTS:
        start = int(artifact.get('statev_start_index') or 0)
        end = int(artifact.get('statev_end_index') or 0)
        if start < 1 or end < start or len(state_values) < end:
            values = []
        else:
            values = [float(value) for value in state_values[start - 1:end]]
        key = str(artifact.get('id') or artifact.get('target_variable') or len(result) + 1)
        result[key] = {
            'id': key,
            'target_variable': str(artifact.get('target_variable') or ''),
            'target_shape': str(artifact.get('target_shape') or ''),
            'source_variable': str(artifact.get('source_variable') or ''),
            'source_kind': str(artifact.get('source_kind') or ''),
            'component_count': int(artifact.get('component_count') or len(values)),
            'component_labels': list(artifact.get('component_labels') or []),
            'statev_start_index': start or None,
            'statev_end_index': end or None,
            'values': values,
        }
    return result


def frame_record(frame, frame_index):
    stress_values = field_values(frame, 'S')
    strain_key = 'E' if 'E' in frame.fieldOutputs else 'LE' if 'LE' in frame.fieldOutputs else ''
    strain_values = field_values(frame, strain_key) if strain_key else []
    state_values, state_key = state_variable_values(frame)
    ddsdde_flat = ddsdde_values(state_values)
    constitutive = constitutive_outputs(state_values)
    user_state_values = state_values[:USER_STATE_COUNT]
    increment_number = getattr(frame, 'incrementNumber', frame_index)
    return {
        'frame_index': int(frame_index),
        'increment_number': int(increment_number),
        'frame_value': float(getattr(frame, 'frameValue', 0.0)),
        'description': str(getattr(frame, 'description', '')),
        'field_output_keys': sorted(frame.fieldOutputs.keys()),
        'stress': float_list(stress_values[0].data) if stress_values else [],
        'strain': float_list(strain_values[0].data) if strain_values else [],
        'state_variables': user_state_values,
        'raw_state_variables': state_values,
        'strain_output_key': strain_key,
        'state_variable_output_key': state_key,
        'ddsdde_flat': ddsdde_flat,
        'ddsdde': matrix_from_flat(ddsdde_flat),
        'ddsdde_statev_start_index': USER_STATE_COUNT + 1 if ddsdde_flat else None,
        'ddsdde_statev_end_index': USER_STATE_COUNT + len(ddsdde_flat) if ddsdde_flat else None,
        'constitutive_outputs': constitutive,
    }


def main():
    odb_path = sys.argv[1]
    output_path = sys.argv[2]
    odb = openOdb(odb_path, readOnly=True)
    try:
        step = list(odb.steps.values())[-1]
        frame_records = [frame_record(frame, index) for index, frame in enumerate(step.frames)]
        increment_records = [record for record in frame_records if record['frame_index'] > 0 or record['increment_number'] > 0]
        if not increment_records:
            increment_records = frame_records
        final_record = increment_records[-1] if increment_records else {}
        result = {
            'odb_path': odb_path,
            'job_completed': True,
            'field_output_keys': final_record.get('field_output_keys', []),
            'final_stress': final_record.get('stress', []),
            'final_strain': final_record.get('strain', []),
            'final_state_variables': final_record.get('state_variables', []),
            'final_raw_state_variables': final_record.get('raw_state_variables', []),
            'final_ddsdde_flat': final_record.get('ddsdde_flat', []),
            'final_ddsdde': final_record.get('ddsdde', []),
            'final_constitutive_outputs': final_record.get('constitutive_outputs', {}),
            'strain_output_key': final_record.get('strain_output_key', ''),
            'state_variable_output_key': final_record.get('state_variable_output_key', ''),
            'user_state_variable_count': USER_STATE_COUNT,
            'ddsdde_statev_start_index': USER_STATE_COUNT + 1,
            'ddsdde_statev_end_index': USER_STATE_COUNT + DDSDDE_SLOT_COUNT,
            'ddsdde_component_count': DDSDDE_SLOT_COUNT,
            'constitutive_artifacts': CONSTITUTIVE_ARTIFACTS,
            'increments': increment_records,
        }
    finally:
        odb.close()
    with open(output_path, 'w') as handle:
        json.dump(result, handle, indent=2, sort_keys=True)


if __name__ == '__main__':
    main()
"""
    serialized_artifacts = []
    for artifact in constitutive_artifacts if isinstance(constitutive_artifacts, list) else []:
        if not isinstance(artifact, dict):
            continue
        serialized_artifacts.append(
            {
                "id": str(artifact.get("id") or artifact.get("target_variable") or "artifact"),
                "target_variable": str(artifact.get("target_variable") or ""),
                "target_shape": str(artifact.get("target_shape") or ""),
                "source_variable": str(artifact.get("source_variable") or ""),
                "source_kind": str(artifact.get("source_kind") or ""),
                "component_count": int(artifact.get("component_count") or 0),
                "component_labels": list(artifact.get("component_labels") or []),
                "statev_start_index": int(artifact.get("statev_start_index") or 0),
                "statev_end_index": int(artifact.get("statev_end_index") or 0),
            }
        )
    script = (
        script.replace("__USER_NSTATV__", str(int(user_nstatv)))
        .replace("__NTENS__", str(int(ntens)))
        .replace("__CONSTITUTIVE_ARTIFACTS__", json.dumps(serialized_artifacts, indent=2, sort_keys=True))
    )
    path.write_text(script, encoding="utf-8")
    return path


def _abaqus_status(command: str, abaqus_modules: str = DEFAULT_ABAQUS_MODULES) -> dict[str, Any]:
    executable = command.split()[0] if command.strip() else "abaqus"
    resolved = shutil.which(executable)
    if resolved:
        return {
            "command": command,
            "modules": abaqus_modules,
            "resolved_executable": resolved,
            "status": "available",
            "message": "",
        }
    module_probe = _probe_module_loaded_command(executable, abaqus_modules)
    if module_probe["status"] != "not_found":
        return {"command": command, "modules": abaqus_modules, **module_probe}
    return {
        "command": command,
        "modules": abaqus_modules,
        "resolved_executable": resolved or "",
        "status": "available" if resolved else "not_found",
        "message": "" if resolved else ABAQUS_NOT_FOUND_MESSAGE,
    }


def _probe_module_loaded_command(executable: str, abaqus_modules: str) -> dict[str, str]:
    import subprocess

    if not abaqus_modules.strip():
        return {"status": "not_found", "resolved_executable": "", "message": ABAQUS_NOT_FOUND_MESSAGE}
    command = f"module load {abaqus_modules} >/dev/null && command -v {executable}"
    process = subprocess.run(["bash", "-lc", command], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
    if process.returncode == 0 and process.stdout.strip():
        return {"status": "available_after_module_load", "resolved_executable": process.stdout.strip().splitlines()[-1], "message": ""}
    stderr = process.stderr.strip()
    if "job nodes" in stderr.lower() or "srun" in stderr.lower():
        return {
            "status": "compute_node_required",
            "resolved_executable": "",
            "message": "Abaqus module is available only on job nodes. Use the generated srun run scripts or allocate a compute node before running Abaqus.",
        }
    return {"status": "not_found", "resolved_executable": "", "message": ABAQUS_NOT_FOUND_MESSAGE}


def _validation_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Abaqus Validation Report",
        "",
        f"Final pass: `{report.get('final_pass', False)}`",
        f"Status: `{report.get('status', 'unknown')}`",
        f"Abaqus command: `{report.get('abaqus_command', '')}`",
        f"Original UMAT: `{report.get('original_umat_path', '')}`",
        f"Transformed UMAT: `{report.get('transformed_umat_path', '')}`",
        f"Validation directory: `{report.get('validation_dir', '')}`",
        "",
        "## Load Case",
        f"Mode: `{report.get('material_test_mode', '')}`",
        f"Validation NTENS: `{report.get('validation_ntens', report.get('ntens', ''))}` ({report.get('validation_ntens_source', 'unknown')})",
        f"Step: `{(report.get('load_case', {}) or {}).get('step_name', '')}`",
        f"NLGEOM: `{(report.get('load_case', {}) or {}).get('nlgeom', '')}`",
        f"Expected plasticity: `{(report.get('load_case', {}) or {}).get('expected_plasticity', False)}`",
        "",
        "## Run Status",
        f"Original: `{report.get('original_run_status', {}).get('status', 'not_run')}`",
        f"Transformed: `{report.get('transformed_run_status', {}).get('status', 'not_run')}`",
        f"Extraction: `{report.get('extraction_status', {}).get('status', 'not_run')}`",
        f"Comparison: `{report.get('comparison_status', {}).get('status', 'not_run')}`",
        "",
        "## Stress Comparison",
    ]
    comparison = report.get("stress_comparison", {}) if isinstance(report.get("stress_comparison"), dict) else {}
    if comparison:
        lines.extend(
            [
                f"Max absolute difference: `{comparison.get('max_abs_difference', '')}`",
                f"Max relative difference: `{comparison.get('max_rel_difference', '')}`",
                f"Pass: `{comparison.get('pass', False)}`",
            ]
        )
    else:
        lines.append("Not run yet.")
    ddsdde_validation = report.get("ddsdde_validation", {}) if isinstance(report.get("ddsdde_validation"), dict) else {}
    if ddsdde_validation:
        lines.extend(
            [
                "",
                "## DDSDDE Validation",
                f"Status: `{ddsdde_validation.get('status', 'unknown')}`",
                f"Method: `{ddsdde_validation.get('method', '')}`",
                f"STATEV slots: `{ddsdde_validation.get('statev_start_index', '')}` to `{ddsdde_validation.get('statev_end_index', '')}`",
            ]
        )
    ddsdde_comparison = report.get("ddsdde_comparison", {}) if isinstance(report.get("ddsdde_comparison"), dict) else {}
    if ddsdde_comparison:
        lines.extend(
            [
                "",
                "## DDSDDE Comparison",
                f"Status: `{ddsdde_comparison.get('status', 'unknown')}`",
                f"Compared increments: `{ddsdde_comparison.get('compared_increment_count', '')}`",
                f"Max absolute difference: `{ddsdde_comparison.get('max_abs_difference', '')}`",
                f"Max relative difference: `{ddsdde_comparison.get('max_rel_difference', '')}`",
                f"Pass: `{ddsdde_comparison.get('pass', False)}`",
            ]
        )
    state_comparison = report.get("state_variable_comparison", {}) if isinstance(report.get("state_variable_comparison"), dict) else {}
    if state_comparison:
        lines.extend(
            [
                "",
                "## State Variables",
                f"Status: `{state_comparison.get('status', 'available')}`",
                f"Pass: `{state_comparison.get('pass', False)}`",
            ]
        )
    convergence_comparison = report.get("convergence_comparison", {}) if isinstance(report.get("convergence_comparison"), dict) else {}
    if convergence_comparison:
        lines.extend(
            [
                "",
                "## Convergence",
                f"Status: `{convergence_comparison.get('status', 'available')}`",
                f"Pass: `{convergence_comparison.get('pass', False)}`",
            ]
        )
    activation_check = report.get("activation_check", {}) if isinstance(report.get("activation_check"), dict) else {}
    if activation_check:
        lines.extend(
            [
                "",
                "## Activation",
                f"Status: `{activation_check.get('status', 'unknown')}`",
                f"Expected plasticity: `{activation_check.get('expected_plasticity', False)}`",
                f"Expected finite geometry: `{activation_check.get('expected_finite_geometry', False)}`",
                f"Pass: `{activation_check.get('pass', False)}`",
            ]
        )
    warnings = report.get("warnings", []) or []
    errors = report.get("errors", []) or []
    if warnings:
        lines.extend(["", "## Warnings", *[f"- {warning}" for warning in warnings]])
    if errors:
        lines.extend(["", "## Errors", *[f"- {error}" for error in errors]])
    return "\n".join(lines) + "\n"