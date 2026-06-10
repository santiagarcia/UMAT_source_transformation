from __future__ import annotations

from typing import Any

from umat_oti.fortran.interface_detection import REQUIRED_GUI_MAPPINGS
from umat_oti.core.roles import role_summary


def evaluate_pipeline_status(
    *,
    has_upload: bool,
    selected_umat: str | None,
    mappings: dict[str, str],
    variable_roles: list[dict[str, object]],
    routine_roles: list[dict[str, object]],
    region_classifications: list[dict[str, object]] | None = None,
    accept_unknown_routine_warnings: bool = False,
) -> dict[str, Any]:
    required_missing = [name for name in REQUIRED_GUI_MAPPINGS if not mappings.get(name.lower())]
    summary = role_summary(variable_roles)
    seed_ok = "DSTRAN" in summary["seed_variables"]
    stress_ok = "STRESS" in summary["promoted_variables"]
    ddsdde_ok = "DDSDDE" in summary["keep_real_variables"]
    mapped_unknown = [
        variable.upper()
        for variable in mappings.values()
        if variable and variable.upper() in summary["unknown_variables"]
    ]
    unknown_helpers = [
        str(row.get("routine_name", ""))
        for row in routine_roles
        if row.get("selected_role") == "Unknown"
    ]
    unknown_regions = [
        str(row.get("region id", ""))
        for row in (region_classifications or [])
        if row.get("user-selected classification") == "Unknown"
    ]
    statuses = {
        "upload_status": "Complete" if has_upload else "Not started",
        "analysis_status": "Complete" if has_upload else "Not started",
        "mapping_status": "Complete" if not required_missing else "Needs input",
        "routine_classification_status": (
            "Warning" if unknown_helpers and accept_unknown_routine_warnings else "Needs input" if unknown_helpers else "Complete"
        ),
        "region_classification_status": "Needs input" if unknown_regions else "Complete",
        "selected_umat_status": "Complete" if selected_umat else "Needs input",
        "variable_role_status": "Complete" if seed_ok and stress_ok and ddsdde_ok and not mapped_unknown else "Needs input",
    }
    ready = (
        has_upload
        and bool(selected_umat)
        and not required_missing
        and seed_ok
        and stress_ok
        and ddsdde_ok
        and not mapped_unknown
        and (not unknown_helpers or accept_unknown_routine_warnings)
        and not unknown_regions
    )
    statuses["ready_for_transformation"] = ready
    statuses["ready_status"] = "Complete" if ready else "Needs input"
    statuses["missing_information"] = _missing_information(
        required_missing,
        seed_ok,
        stress_ok,
        ddsdde_ok,
        mapped_unknown,
        unknown_helpers,
        unknown_regions,
        accept_unknown_routine_warnings,
    )
    return statuses


def _missing_information(
    required_missing: list[str],
    seed_ok: bool,
    stress_ok: bool,
    ddsdde_ok: bool,
    mapped_unknown: list[str],
    unknown_helpers: list[str],
    unknown_regions: list[str],
    accept_unknown_routine_warnings: bool,
) -> list[str]:
    missing: list[str] = []
    if required_missing:
        missing.append("Required mappings missing: " + ", ".join(required_missing))
    if not seed_ok:
        missing.append("DSTRAN must be assigned role Seed.")
    if not stress_ok:
        missing.append("STRESS must be assigned role Promote.")
    if not ddsdde_ok:
        missing.append("DDSDDE must be assigned role Keep real.")
    if mapped_unknown:
        missing.append("Mapped variables cannot remain Unknown: " + ", ".join(sorted(set(mapped_unknown))))
    if unknown_helpers and not accept_unknown_routine_warnings:
        missing.append("Helper routines remain Unknown: " + ", ".join(sorted(set(unknown_helpers))))
    if unknown_regions:
        missing.append("Code regions remain Unknown: " + ", ".join(sorted(set(unknown_regions))))
    return missing
