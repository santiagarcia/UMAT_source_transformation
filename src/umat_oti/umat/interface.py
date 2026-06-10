from __future__ import annotations

from umat_oti.core.model import (
    CORE_REQUIRED_ARGUMENTS,
    ParsedFortranSource,
    ParsedSubroutine,
    UmatArgument,
    UmatInterface,
)
from umat_oti.fortran.symbols import declaration_map, entity_dimension_map


STANDARD_INTEGER_ARGUMENTS = {
    "NDI",
    "NSHR",
    "NTENS",
    "NSTATV",
    "NPROPS",
    "NOEL",
    "NPT",
    "LAYER",
    "KSPT",
    "JSTEP",
    "KINC",
}


def detect_umat_interface(parsed: ParsedFortranSource) -> UmatInterface:
    routine = _find_entry(parsed)
    declarations = declaration_map(routine)
    dimensions = entity_dimension_map(routine)
    arguments: list[UmatArgument] = []
    for position, name in enumerate(routine.args, start=1):
        upper = name.upper()
        declaration = declarations.get(upper)
        if declaration is not None:
            kind = declaration.kind
            raw_type = declaration.raw_type
        elif upper == "CMNAME":
            kind = "character"
            raw_type = "character(len=80)"
        elif upper in STANDARD_INTEGER_ARGUMENTS:
            kind = "integer"
            raw_type = "integer"
        else:
            kind = "real"
            raw_type = "real(8)"
        arguments.append(
            UmatArgument(
                name=name,
                position=position,
                kind=kind,
                raw_type=raw_type,
                dimensions=dimensions.get(upper, ()),
            )
        )
    found = {argument.upper_name for argument in arguments}
    missing = tuple(name for name in CORE_REQUIRED_ARGUMENTS if name not in found)
    return UmatInterface(routine.name, tuple(arguments), missing)


def _find_entry(parsed: ParsedFortranSource) -> ParsedSubroutine:
    for routine in parsed.subroutines:
        if routine.upper_name == "UMAT":
            return routine
    if len(parsed.subroutines) == 1:
        return parsed.subroutines[0]
    names = ", ".join(routine.name for routine in parsed.subroutines) or "none"
    raise ValueError(f"Could not detect UMAT entry point. Found subroutines: {names}.")
