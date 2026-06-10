from __future__ import annotations

from umat_oti.core.model import ParsedFortranSource


EXPECTED_ABAQUS_UMAT_ARGUMENTS = (
    "STRESS",
    "STATEV",
    "DDSDDE",
    "SSE",
    "SPD",
    "SCD",
    "RPL",
    "DDSDDT",
    "DRPLDE",
    "DRPLDT",
    "STRAN",
    "DSTRAN",
    "TIME",
    "DTIME",
    "TEMP",
    "DTEMP",
    "PREDEF",
    "DPRED",
    "CMNAME",
    "NDI",
    "NSHR",
    "NTENS",
    "NSTATV",
    "PROPS",
    "NPROPS",
    "COORDS",
    "DROT",
    "PNEWDT",
    "CELENT",
    "DFGRD0",
    "DFGRD1",
    "NOEL",
    "NPT",
    "LAYER",
    "KSPT",
    "KSTEP",
    "JSTEP",
    "KINC",
)

REQUIRED_GUI_MAPPINGS = (
    "STRESS",
    "STATEV",
    "DDSDDE",
    "STRAN",
    "DSTRAN",
    "PROPS",
    "NTENS",
    "NSTATV",
    "NPROPS",
)

OPTIONAL_GUI_MAPPINGS = (
    "TIME",
    "DTIME",
    "TEMP",
    "DTEMP",
    "PREDEF",
    "DPRED",
    "DFGRD0",
    "DFGRD1",
    "COORDS",
    "DROT",
)


def umat_like_routines(parsed: ParsedFortranSource) -> list[dict[str, object]]:
    routines: list[dict[str, object]] = []
    expected = set(EXPECTED_ABAQUS_UMAT_ARGUMENTS)
    for routine in parsed.subroutines:
        args = tuple(arg.upper() for arg in routine.args)
        score = len(set(args) & expected)
        if routine.upper_name == "UMAT" or score >= 6:
            routines.append(
                {
                    "argument_count": len(args),
                    "arguments": list(args),
                    "line_numbers": list(routine.lines[0].line_numbers if routine.lines else ()),
                    "name": routine.name,
                    "score": score,
                }
            )
    return sorted(routines, key=lambda item: (-int(item["score"]), str(item["name"]).upper()))


def expected_argument_report(arguments: list[str] | tuple[str, ...]) -> dict[str, object]:
    arg_set = {arg.upper() for arg in arguments}
    expected = set(EXPECTED_ABAQUS_UMAT_ARGUMENTS)
    return {
        "extra_arguments": sorted(arg for arg in arg_set if arg not in expected),
        "found": [arg for arg in EXPECTED_ABAQUS_UMAT_ARGUMENTS if arg in arg_set],
        "missing": [arg for arg in EXPECTED_ABAQUS_UMAT_ARGUMENTS if arg not in arg_set],
    }
