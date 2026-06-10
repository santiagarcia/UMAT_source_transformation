from __future__ import annotations

from typing import Any


def direct_umat_driver_plan() -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "message": "Direct UMAT driver validation not implemented yet.",
        "planned_module": "src/umat_oti/validation/material_point_driver.py",
        "planned_checks": [
            "Compile a standalone Fortran driver that calls UMAT directly.",
            "Run original and transformed UMATs with identical STRESS, STATEV, STRAN, DSTRAN, PROPS, NTENS, NSTATV, and NPROPS inputs.",
            "Compare STRESS and STATEV outputs.",
            "Compare DDSDDE directly, including finite-difference material-point checks when needed.",
        ],
    }