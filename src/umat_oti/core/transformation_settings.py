from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from umat_oti.core.output_layout import default_transform_output_dir


DEFAULT_OTI_ORDER = 1
DEFAULT_GENERATED_NTENS = 6


def build_transformation_settings(
    *,
    analysis: dict[str, Any],
    project_workdir: str | Path,
    source_text: str = "",
    fallback_ntens: int | None = DEFAULT_GENERATED_NTENS,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    inferred = infer_ntens(analysis, source_text=source_text, fallback_ntens=fallback_ntens)
    target_dir = Path(output_dir) if output_dir else default_transform_output_dir(project_workdir)
    return {
        "ntens": inferred["ntens"],
        "ntens_source": inferred["source"],
        "ntens_confidence": inferred["confidence"],
        "ntens_warning": inferred["warning"],
        "order": DEFAULT_OTI_ORDER,
        "output_dir": str(target_dir),
    }


def infer_ntens(
    analysis: dict[str, Any],
    *,
    source_text: str = "",
    fallback_ntens: int | None = None,
) -> dict[str, Any]:
    shape_candidates = _numeric_mapped_shapes(analysis)
    if shape_candidates:
        return _result(max(shape_candidates), "detected numeric mapped variable shape", "high")
    explicit_indices = _explicit_component_indices(analysis, source_text)
    if explicit_indices:
        return _result(max(explicit_indices), "explicit component indices in UMAT source", "medium")
    if fallback_ntens is not None and fallback_ntens > 0:
        return _result(
            fallback_ntens,
            "generated JSON default",
            "default",
            f"NTENS was not explicit in the source; generated config prefilled {fallback_ntens}. Review if this UMAT is not 3D/six-component.",
        )
    return _result(None, "missing", "missing", "Enter NTENS, the number of strain/stress components for this UMAT.")


def _numeric_mapped_shapes(analysis: dict[str, Any]) -> list[int]:
    candidates: list[int] = []
    for row in analysis.get("detected_variables", []) or []:
        name = str(row.get("variable_name", "")).upper()
        if name not in {"DSTRAN", "STRESS", "DDSDDE"}:
            continue
        shape = str(row.get("detected_shape", ""))
        for value in _numeric_shape_values(shape):
            candidates.append(value)
    return candidates


def _numeric_shape_values(shape: str) -> list[int]:
    values: list[int] = []
    for part in shape.split(","):
        stripped = part.strip()
        if stripped.isdigit():
            values.append(int(stripped))
    return values


def _explicit_component_indices(analysis: dict[str, Any], source_text: str) -> list[int]:
    texts: list[str] = []
    for key in ("assignments_to_stress", "assignments_to_ddsdde"):
        for row in analysis.get(key, []) or []:
            texts.append(str(row.get("text", "")))
    for region in analysis.get("detected_regions", []) or []:
        texts.append(str(region.get("short code preview", "")))
    if source_text:
        texts.append(source_text)
    joined = "\n".join(texts)
    values: list[int] = []
    for variable in ("DSTRAN", "STRESS", "DDSDDE"):
        pattern = re.compile(rf"\b{variable}\s*\(([^)]*)\)", flags=re.IGNORECASE)
        for match in pattern.finditer(joined):
            for part in match.group(1).split(","):
                stripped = part.strip()
                if stripped.isdigit():
                    values.append(int(stripped))
    return values


def _result(ntens: int | None, source: str, confidence: str, warning: str = "") -> dict[str, Any]:
    return {"ntens": ntens, "source": source, "confidence": confidence, "warning": warning}
