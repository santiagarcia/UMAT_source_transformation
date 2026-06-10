from __future__ import annotations

from umat_oti.core.model import Declaration, ParsedSubroutine


def declaration_map(routine: ParsedSubroutine) -> dict[str, Declaration]:
    result: dict[str, Declaration] = {}
    for declaration in routine.declarations:
        for entity in declaration.entities:
            result[entity.upper_name] = declaration
    return result


def entity_dimension_map(routine: ParsedSubroutine) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for declaration in routine.declarations:
        for entity in declaration.entities:
            result[entity.upper_name] = entity.dimensions
    return result
