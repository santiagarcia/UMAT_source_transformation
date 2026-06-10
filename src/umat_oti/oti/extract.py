from __future__ import annotations


def derivative_index(stress_component: int, strain_component: int) -> tuple[int, int]:
    """Return one-based DDSDDE indices for d STRESS(i) / d DSTRAN(j)."""
    if stress_component < 1 or strain_component < 1:
        raise ValueError("DDSDDE indices are one-based and must be positive.")
    return stress_component, strain_component
