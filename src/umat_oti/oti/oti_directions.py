"""Canonical OTI imaginary-direction enumeration.

Single source of truth shared by the OTI module generator (which names the type
members and builds the multiplication tables) and the derivative extraction in
the source transform. The ordering is *nbases-independent* ("graded by largest
basis index, then lexicographic"), which has two properties the rest of the
pipeline relies on:

* ``fulldir(idx, order)`` needs no ``nbases`` argument (matching the upstream
  pyoti ``get_fulldir`` contract that ``fmod_writer.py`` calls).
* The first ``ndir_order(nbases, order)`` directions of a given order are exactly
  the multisets over bases ``1..nbases``. So the writer's
  ``for j in range(ndir_order(nbases, order))`` loop and the flat ``GETIM`` index
  line up with this enumeration.

For order 1 this reduces to ``fulldir(j, 1) == (j + 1,)``, identical to the old
first-order shim, so existing first-order modules are unchanged byte-for-byte.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations_with_replacement
from math import comb, factorial
from typing import Iterator, Sequence

# Must match UMATs/OTI/fmod_writer.py `valid_chars` exactly: '0'..'9' then 'A'..'Z'.
# A basis index b (1-indexed) is rendered as VALID_CHARS[b]; member names are
# "E" followed by the rendered bases, e.g. E1, E2 (order 1), E11, E12, E22 (order 2).
VALID_CHARS = [chr(i) for i in range(48, 58)] + [chr(i) for i in range(65, 91)]


def ndir_order(nbases: int, order: int) -> int:
    """Number of imaginary directions of exactly ``order`` over ``nbases`` bases."""
    if order == 0:
        return 1
    return comb(nbases + order - 1, order)


def ndir_total(nbases: int, order: int) -> int:
    """Total imaginary directions up to ``order`` (including the order-0 real part)."""
    return sum(ndir_order(nbases, k) for k in range(order + 1))


def _gen_dirs(order: int) -> Iterator[tuple[int, ...]]:
    """Yield size-``order`` multisets (sorted, 1-indexed bases) in the canonical
    nbases-independent order: grouped by largest basis index, lexicographic within."""
    if order == 0:
        yield ()
        return
    maxbase = 1
    while True:
        for rest in combinations_with_replacement(range(1, maxbase + 1), order - 1):
            yield tuple(sorted(rest + (maxbase,)))
        maxbase += 1


def fulldir(idx: int, order: int) -> tuple[int, ...]:
    """The ``idx``-th (0-based) imaginary direction of the given ``order``."""
    for i, direction in enumerate(_gen_dirs(order)):
        if i == idx:
            return direction
    raise IndexError(f"direction index {idx} out of range for order {order}")


def dir_index(multiset: Sequence[int]) -> int:
    """Inverse of :func:`fulldir`: the within-order index of a multiset of bases."""
    target = tuple(sorted(multiset))
    order = len(target)
    for i, direction in enumerate(_gen_dirs(order)):
        if direction == target:
            return i
        if direction[-1] > target[-1]:
            break
    raise ValueError(f"multiset {multiset} not found in order-{order} enumeration")


def flat_index(nbases: int, order: int, idx: int) -> int:
    """Flat 1-based GETIM index of within-order direction ``idx`` of ``order``.

    GETIM lays members out as: 0 -> real, then all order-1 directions, then all
    order-2 directions, etc., each block sized ``ndir_order(nbases, k)``.
    """
    return sum(ndir_order(nbases, k) for k in range(1, order)) + idx + 1


def member_name(multiset: Sequence[int]) -> str:
    """Fortran member name for a direction, e.g. (1, 2) -> 'E12'."""
    return "E" + "".join(VALID_CHARS[b] for b in multiset)


def deriv_factor(multiset: Sequence[int]) -> int:
    """Factor converting a stored OTI imaginary coefficient to the true partial
    derivative: ``derivative = coefficient * prod(multiplicity!)``.

    OTI stores the Taylor coefficient (derivative divided by the multiplicities'
    factorials), so e.g. the e_i^2 member holds ``f_ii / 2`` and the mixed e_i e_j
    member holds ``f_ij`` directly.
    """
    factor = 1
    for multiplicity in Counter(multiset).values():
        factor *= factorial(multiplicity)
    return factor


def imaginary_directions(nbases: int, order: int) -> list[dict]:
    """All imaginary directions for ``(nbases, order)`` as dicts with keys:
    ``order``, ``idx`` (within-order), ``bases`` (1-indexed tuple), ``name``
    (Fortran member), ``flat`` (GETIM index), ``factor`` (derivative factor)."""
    out: list[dict] = []
    for k in range(1, order + 1):
        for idx in range(ndir_order(nbases, k)):
            bases = fulldir(idx, k)
            out.append(
                {
                    "order": k,
                    "idx": idx,
                    "bases": bases,
                    "name": member_name(bases),
                    "flat": flat_index(nbases, k, idx),
                    "factor": deriv_factor(bases),
                }
            )
    return out
