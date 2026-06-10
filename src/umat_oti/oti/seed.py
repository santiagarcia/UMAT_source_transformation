from __future__ import annotations


def seed_plan(ntens: int, direction_count: int = 6) -> tuple[tuple[int, int], ...]:
    """Return one-based DSTRAN component to OTIS direction pairs."""
    limit = min(ntens, direction_count)
    return tuple((component, component) for component in range(1, limit + 1))
