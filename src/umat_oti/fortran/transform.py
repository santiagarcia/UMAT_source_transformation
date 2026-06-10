from __future__ import annotations

import re
from dataclasses import dataclass

from umat_oti.core.model import (
    Declaration,
    ParsedFortranSource,
    TransformDecision,
    UmatArgument,
    UmatInterface,
)
from umat_oti.fortran.parser import parse_declaration_line
from umat_oti.oti.backend import OtiBackend


KERNEL_NAME = "UMAT_OTI_KERNEL"
REAL_OUTPUTS_TO_COPY_BACK = {
    "SSE",
    "SPD",
    "SCD",
    "RPL",
    "DDSDDT",
    "DRPLDE",
    "DRPLDT",
    "PNEWDT",
}


@dataclass(frozen=True)
class TransformedSource:
    source: str
    decisions: tuple[TransformDecision, ...]


def transform_umat_source(
    parsed: ParsedFortranSource, interface: UmatInterface, backend: OtiBackend
) -> TransformedSource:
    routine = _entry_routine(parsed, interface.entry_name)
    decisions: list[TransformDecision] = [
        TransformDecision(
            "stress_update_centered",
            "Generated OTIS wrapper differentiates the implemented STRESS update with respect to seeded DSTRAN.",
        ),
        TransformDecision(
            "ignore_existing_ddsdde",
            "Existing DDSDDE assignments in the input UMAT are stripped from the OTIS kernel and are not the source of truth.",
        ),
        TransformDecision(
            "broad_real_promotion",
            "All non-parameter REAL declarations in the transformed UMAT kernel are promoted to the OTIS scalar type.",
        ),
        TransformDecision(
            "static_backend",
            f"Selected deterministic first-order static backend with {backend.direction_count} directions.",
        ),
    ]
    wrapper = _wrapper_source(interface)
    kernel, kernel_decisions = _kernel_source(routine.lines, routine.name)
    decisions.extend(kernel_decisions)
    source = "\n".join(wrapper + [""] + kernel) + "\n"
    return TransformedSource(source, tuple(decisions))


def _entry_routine(parsed: ParsedFortranSource, entry_name: str):
    for routine in parsed.subroutines:
        if routine.upper_name == entry_name.upper():
            return routine
    raise ValueError(f"Entry routine {entry_name} was not found.")


def _wrapper_source(interface: UmatInterface) -> list[str]:
    args = [arg.lower_name for arg in interface.arguments]
    lines = _wrapped_statement("subroutine", interface.entry_name, args)
    lines.extend(
        [
            "  use umat_oti_backend",
            "  implicit none",
        ]
    )
    for argument in interface.arguments:
        lines.append("  " + _argument_declaration(argument))
    lines.append("  integer :: umat_oti_i, umat_oti_j")
    for argument in interface.real_arguments:
        lines.append("  " + _shadow_declaration(argument))
    lines.append("")
    for argument in interface.real_arguments:
        if argument.upper_name == "DDSDDE":
            lines.append(f"  {shadow_name(argument)} = 0.0d0")
        else:
            lines.append(f"  {shadow_name(argument)} = {argument.lower_name}")
    if interface.get("DSTRAN") is not None:
        dstran_shadow = shadow_name(interface.get("DSTRAN"))
        lines.extend(
            [
                "  do umat_oti_j = 1, min(ntens, OTI_NDIR)",
                f"    call seed_direction({dstran_shadow}(umat_oti_j), umat_oti_j)",
                "  end do",
            ]
        )
    lines.extend(_wrapped_statement("call", KERNEL_NAME, [_call_argument(arg) for arg in interface.arguments]))
    stress = interface.get("STRESS")
    statev = interface.get("STATEV")
    ddsdde = interface.get("DDSDDE")
    if stress is not None:
        lines.append(f"  {stress.lower_name} = real_part({shadow_name(stress)})")
    if statev is not None:
        lines.append(f"  {statev.lower_name} = real_part({shadow_name(statev)})")
    for argument in interface.real_arguments:
        if argument.upper_name in REAL_OUTPUTS_TO_COPY_BACK:
            lines.append(f"  {argument.lower_name} = real_part({shadow_name(argument)})")
    if stress is not None and ddsdde is not None:
        lines.extend(
            [
                f"  {ddsdde.lower_name} = 0.0d0",
                "  do umat_oti_i = 1, ntens",
                "    do umat_oti_j = 1, min(ntens, OTI_NDIR)",
                f"      {ddsdde.lower_name}(umat_oti_i, umat_oti_j) = deriv_part({shadow_name(stress)}(umat_oti_i), umat_oti_j)",
                "    end do",
                "  end do",
            ]
        )
    lines.append(f"end subroutine {interface.entry_name}")
    return lines


def _kernel_source(lines, original_name: str) -> tuple[list[str], list[TransformDecision]]:
    if not lines:
        raise ValueError("Cannot transform an empty routine.")
    first = lines[0].text
    match = re.match(r"^\s*subroutine\s+\w+\s*\((.*)\)", first, flags=re.IGNORECASE)
    if not match:
        raise ValueError("Entry line is not a subroutine declaration.")
    args = [arg.strip().lower() for arg in match.group(1).split(",") if arg.strip()]
    output = _wrapped_statement("subroutine", KERNEL_NAME, args)
    output.append("  use umat_oti_backend")
    decisions: list[TransformDecision] = [
        TransformDecision("rename_kernel", f"Renamed source routine {original_name} to {KERNEL_NAME}.", lines[0].line_numbers)
    ]
    for logical_line in lines[1:]:
        text = logical_line.text
        if re.match(r"^\s*end\s*(subroutine(\s+\w+)?)?\s*$", text, flags=re.IGNORECASE):
            output.append(f"end subroutine {KERNEL_NAME}")
            continue
        declaration = parse_declaration_line(logical_line)
        if declaration and declaration.is_real and not declaration.has_parameter_attribute:
            output.append("  " + _promoted_declaration(declaration))
            decisions.append(
                TransformDecision(
                    "promote_real_declaration",
                    "Promoted REAL declaration to OTIS type in the kernel.",
                    logical_line.line_numbers,
                )
            )
            continue
        if _is_ddsdde_assignment(text):
            output.append("  ! umat_oti: stripped original DDSDDE assignment")
            decisions.append(
                TransformDecision(
                    "strip_ddsdde_assignment",
                    "Removed an existing DDSDDE assignment from the OTIS stress-update kernel.",
                    logical_line.line_numbers,
                )
            )
            continue
        output.append("  " + text)
    return output, decisions


def shadow_name(argument: UmatArgument | None) -> str:
    if argument is None:
        raise ValueError("Cannot create a shadow name for a missing argument.")
    return f"{argument.lower_name}_oti"


def _call_argument(argument: UmatArgument) -> str:
    if argument.kind == "real":
        return shadow_name(argument)
    return argument.lower_name


def _argument_declaration(argument: UmatArgument) -> str:
    name = argument.lower_name + _dimension_suffix(argument.dimensions)
    if argument.kind == "real":
        return f"real(8) :: {name}"
    if argument.kind == "integer":
        return f"integer :: {name}"
    if argument.kind == "character":
        raw_type = argument.raw_type if argument.raw_type else "character(len=80)"
        return f"{raw_type} :: {name}"
    if argument.kind == "logical":
        return f"logical :: {name}"
    return f"real(8) :: {name}"


def _shadow_declaration(argument: UmatArgument) -> str:
    return f"type(otis_t) :: {shadow_name(argument)}{_dimension_suffix(argument.dimensions)}"


def _promoted_declaration(declaration: Declaration) -> str:
    rendered = ", ".join(entity.render(include_initializer=False) for entity in declaration.entities)
    return f"type(otis_t) :: {rendered}"


def _dimension_suffix(dimensions: tuple[str, ...]) -> str:
    if not dimensions:
        return ""
    return "(" + ", ".join(dimensions).lower() + ")"


def _is_ddsdde_assignment(text: str) -> bool:
    return re.match(r"^\s*ddsdde\s*(?:\([^=]*\))?\s*=", text, flags=re.IGNORECASE) is not None


def _wrapped_statement(kind: str, name: str, args: list[str], chunk_size: int = 6) -> list[str]:
    prefix = f"{kind} {name}"
    if not args:
        return [f"{prefix}()"]
    lines = [f"{prefix}( &"]
    chunks = [args[index : index + chunk_size] for index in range(0, len(args), chunk_size)]
    for index, chunk in enumerate(chunks):
        suffix = ", &" if index < len(chunks) - 1 else ")"
        lines.append("  " + ", ".join(chunk) + suffix)
    return lines
