from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from umat_oti.core.model import MaterialPoint


def load_material_point_config(directory: Path) -> MaterialPoint | None:
    path = directory / "material_point.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return material_point_from_dict(data)


def material_point_from_dict(data: dict[str, Any]) -> MaterialPoint:
    tolerances = data.get("tolerances", {})
    return MaterialPoint(
        ntens=int(data.get("ntens", 6)),
        ndi=int(data.get("ndi", 3)),
        nshr=int(data.get("nshr", 3)),
        stress=tuple(float(value) for value in data.get("stress", [0.0] * int(data.get("ntens", 6)))),
        statev=tuple(float(value) for value in data.get("statev", [0.0])),
        stran=tuple(float(value) for value in data.get("stran", [0.0] * int(data.get("ntens", 6)))),
        dstran=tuple(float(value) for value in data.get("dstran", [0.0] * int(data.get("ntens", 6)))),
        props=tuple(float(value) for value in data.get("props", [])),
        dtime=float(data.get("dtime", 1.0)),
        temp=float(data.get("temp", 293.15)),
        dtemp=float(data.get("dtemp", 0.0)),
        fd_step=float(data.get("fd_step", 1.0e-7)),
        stress_abs_tol=float(tolerances.get("stress_abs", 1.0e-8)),
        jacobian_abs_tol=float(tolerances.get("jacobian_abs", 1.0e-5)),
        jacobian_rel_tol=float(tolerances.get("jacobian_rel", 1.0e-5)),
    )
