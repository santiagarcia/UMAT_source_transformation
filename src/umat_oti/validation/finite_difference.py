from __future__ import annotations

import tempfile
from pathlib import Path

from umat_oti.core.model import MaterialPoint, UnsupportedFeature
from umat_oti.validation.abaqus import (
    compile_original,
    compile_transformed,
    find_fortran_compiler,
    run_material_point,
    validation_driver_source,
)


def validate_material_point(
    original_source: Path,
    transformed_source: Path,
    backend_source: Path,
    source_form: str,
    material_point: MaterialPoint,
    unsupported: tuple[UnsupportedFeature, ...],
) -> dict[str, object]:
    compiler = find_fortran_compiler()
    base_report: dict[str, object] = {
        "compiler": compiler,
        "schema_version": 1,
        "unsupported_features": [feature.to_json() for feature in unsupported],
    }
    if compiler is None:
        return {
            **base_report,
            "pass": False,
            "status": "skipped",
            "reason": "No Fortran compiler was found on PATH.",
        }
    with tempfile.TemporaryDirectory(prefix="umat_oti_validate_") as temp_name:
        build_dir = Path(temp_name)
        driver = build_dir / "material_point_driver.f90"
        driver.write_text(validation_driver_source(material_point), encoding="utf-8")
        original_ok, original_exe, original_log = compile_original(
            compiler, build_dir, original_source, driver, source_form
        )
        transformed_ok, transformed_exe, transformed_log = compile_transformed(
            compiler, build_dir, backend_source, transformed_source, driver
        )
        if not original_ok or not transformed_ok or original_exe is None or transformed_exe is None:
            return {
                **base_report,
                "compile": {
                    "original": {"ok": original_ok, "log": original_log},
                    "transformed": {"ok": transformed_ok, "log": transformed_log},
                },
                "pass": False,
                "status": "failed",
                "reason": "Compilation failed.",
            }
        original_base = run_material_point(original_exe, material_point)
        transformed_base = run_material_point(transformed_exe, material_point)
        fd = _finite_difference_jacobian(original_exe, material_point)
        stress_error = _max_abs_difference(original_base.stress, transformed_base.stress)
        jacobian_abs_error = _max_matrix_abs_difference(fd, transformed_base.ddsdde)
        fd_scale = max(_max_matrix_abs(fd), 1.0)
        jacobian_rel_error = jacobian_abs_error / fd_scale
        passed = (
            stress_error <= material_point.stress_abs_tol
            and (
                jacobian_abs_error <= material_point.jacobian_abs_tol
                or jacobian_rel_error <= material_point.jacobian_rel_tol
            )
        )
        return {
            **base_report,
            "compile": {
                "original": {"ok": True, "log": original_log},
                "transformed": {"ok": True, "log": transformed_log},
            },
            "finite_difference_step": material_point.fd_step,
            "jacobian_abs_error": jacobian_abs_error,
            "jacobian_rel_error": jacobian_rel_error,
            "max_abs_fd_jacobian": _max_matrix_abs(fd),
            "pass": passed,
            "status": "passed" if passed else "failed",
            "stress_equivalence_error": stress_error,
            "transformed_ddsdde": _matrix_to_lists(transformed_base.ddsdde),
            "finite_difference_ddsdde": _matrix_to_lists(fd),
        }


def _finite_difference_jacobian(exe: Path, point: MaterialPoint) -> tuple[tuple[float, ...], ...]:
    columns: list[tuple[float, ...]] = []
    h = point.fd_step
    for index in range(point.ntens):
        plus = list(point.dstran)
        minus = list(point.dstran)
        plus[index] += h
        minus[index] -= h
        plus_result = run_material_point(exe, point, tuple(plus))
        minus_result = run_material_point(exe, point, tuple(minus))
        columns.append(tuple((plus_result.stress[i] - minus_result.stress[i]) / (2.0 * h) for i in range(point.ntens)))
    rows = []
    for i in range(point.ntens):
        rows.append(tuple(columns[j][i] for j in range(point.ntens)))
    return tuple(rows)


def _max_abs_difference(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return max((abs(a - b) for a, b in zip(left, right)), default=0.0)


def _max_matrix_abs_difference(left: tuple[tuple[float, ...], ...], right: tuple[tuple[float, ...], ...]) -> float:
    return max(
        (abs(left[i][j] - right[i][j]) for i in range(len(left)) for j in range(len(left[i]))),
        default=0.0,
    )


def _max_matrix_abs(matrix: tuple[tuple[float, ...], ...]) -> float:
    return max((abs(value) for row in matrix for value in row), default=0.0)


def _matrix_to_lists(matrix: tuple[tuple[float, ...], ...]) -> list[list[float]]:
    return [list(row) for row in matrix]
