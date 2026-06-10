from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from umat_oti.core.diagnostics import scan_unsupported_features, unsupported_report
from umat_oti.core.model import MaterialPoint, TransformResult
from umat_oti.fortran.callgraph import build_call_graph
from umat_oti.fortran.parser import parse_fortran_file
from umat_oti.fortran.transform import transform_umat_source
from umat_oti.oti.otilib_static import StaticFirstOrderBackend
from umat_oti.umat.interface import detect_umat_interface
from umat_oti.validation.abaqus import validation_driver_source
from umat_oti.validation.material_point import load_material_point_config
from umat_oti.validation.finite_difference import validate_material_point


def stable_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json(data), encoding="utf-8")


def transform_umat(
    input_path: Path | str,
    output_dir: Path | str,
    material_point: MaterialPoint | None = None,
    run_validation: bool = True,
) -> TransformResult:
    input_path = Path(input_path).resolve()
    output_dir = Path(output_dir).resolve()
    parsed = parse_fortran_file(input_path)
    entry = detect_umat_interface(parsed)
    call_sites = build_call_graph(parsed, entry.entry_name)
    unsupported = scan_unsupported_features(parsed.logical_lines, call_sites)

    backend = StaticFirstOrderBackend(direction_count=6)
    transformed = transform_umat_source(parsed, entry, backend)

    source_original = output_dir / "source" / "original"
    source_transformed = output_dir / "source" / "transformed"
    reports_dir = output_dir / "reports"
    validation_dir = output_dir / "validation"
    for directory in (source_original, source_transformed, reports_dir, validation_dir):
        directory.mkdir(parents=True, exist_ok=True)

    original_copy = source_original / input_path.name
    shutil.copyfile(input_path, original_copy)
    backend_path = source_transformed / "umat_oti_backend.f90"
    transformed_path = source_transformed / "umat_otis.f90"
    backend_path.write_text(backend.module_source(), encoding="utf-8")
    transformed_path.write_text(transformed.source, encoding="utf-8")

    if material_point is None:
        material_point = load_material_point_config(input_path.parent)

    driver_path = validation_dir / "material_point_driver.f90"
    driver_path.write_text(
        validation_driver_source(material_point or MaterialPoint()), encoding="utf-8"
    )
    fd_check_path = validation_dir / "fd_check.py"
    fd_check_path.write_text(_fd_check_script(), encoding="utf-8")

    generated_files = sorted(
        [
            "source/original/" + input_path.name,
            "source/transformed/umat_oti_backend.f90",
            "source/transformed/umat_otis.f90",
            "validation/fd_check.py",
            "validation/material_point_driver.f90",
        ]
    )

    transform_report = {
        "call_graph": [
            {"callee": call.callee, "caller": call.caller, "line_numbers": list(call.line_numbers)}
            for call in call_sites
        ],
        "decisions": [decision.to_json() for decision in transformed.decisions],
        "differentiation_contract": {
            "dependent": "STRESS(1:NTENS)",
            "independent": "DSTRAN(1:NTENS)",
            "output": "DDSDDE(i,j) = d STRESS(i) / d DSTRAN(j)",
        },
        "entry_point": entry.entry_name,
        "fortran_form": parsed.form,
        "generated_files": generated_files,
        "input_file": input_path.name,
        "missing_required_arguments": list(entry.missing_required),
        "schema_version": 1,
    }
    unsupported_json = unsupported_report(unsupported)

    validation_report: dict[str, Any]
    if material_point is None:
        validation_report = {
            "pass": False,
            "schema_version": 1,
            "status": "skipped",
            "reason": "No material_point.json was supplied next to the UMAT source.",
        }
    elif run_validation:
        validation_report = validate_material_point(
            original_source=original_copy,
            transformed_source=transformed_path,
            backend_source=backend_path,
            source_form=parsed.form,
            material_point=material_point,
            unsupported=unsupported,
        )
    else:
        validation_report = {
            "pass": False,
            "schema_version": 1,
            "status": "skipped",
            "reason": "Validation was disabled by the caller.",
        }

    write_json(reports_dir / "transform_report.json", transform_report)
    write_json(reports_dir / "unsupported_features.json", unsupported_json)
    write_json(reports_dir / "validation_report.json", validation_report)

    generated_files.extend(
        sorted(
            [
                "reports/transform_report.json",
                "reports/unsupported_features.json",
                "reports/validation_report.json",
            ]
        )
    )
    transform_report["generated_files"] = sorted(generated_files)
    write_json(reports_dir / "transform_report.json", transform_report)

    return TransformResult(
        input_path=input_path,
        output_dir=output_dir,
        generated_files=sorted(generated_files),
        transform_report=transform_report,
        unsupported_report=unsupported_json,
        validation_report=validation_report,
    )


def _fd_check_script() -> str:
    return """#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

report = Path(__file__).resolve().parent.parent / "reports" / "validation_report.json"
print(json.dumps(json.loads(report.read_text(encoding="utf-8")), indent=2, sort_keys=True))
"""
