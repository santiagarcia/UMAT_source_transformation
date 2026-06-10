from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STANDARD_UMAT_NAMES = {
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
    "JSTEP",
    "KINC",
}

CORE_REQUIRED_ARGUMENTS = (
    "STRESS",
    "STATEV",
    "DDSDDE",
    "STRAN",
    "DSTRAN",
    "PROPS",
    "NTENS",
    "NSTATV",
)


@dataclass(frozen=True)
class FortranLogicalLine:
    text: str
    line_numbers: tuple[int, ...]


@dataclass(frozen=True)
class DeclaredEntity:
    name: str
    dimensions: tuple[str, ...] = ()
    initializer: str | None = None
    raw: str = ""

    @property
    def upper_name(self) -> str:
        return self.name.upper()

    def render(self, include_initializer: bool = True) -> str:
        suffix = ""
        if self.dimensions:
            suffix = "(" + ", ".join(self.dimensions) + ")"
        if include_initializer and self.initializer is not None:
            return f"{self.name}{suffix} = {self.initializer}"
        return f"{self.name}{suffix}"


@dataclass(frozen=True)
class Declaration:
    kind: str
    raw_type: str
    attributes: tuple[str, ...]
    entities: tuple[DeclaredEntity, ...]
    line: str
    line_numbers: tuple[int, ...]

    @property
    def is_real(self) -> bool:
        return self.kind == "real"

    @property
    def has_parameter_attribute(self) -> bool:
        return any(attr.strip().lower() == "parameter" for attr in self.attributes)


@dataclass(frozen=True)
class ParsedSubroutine:
    name: str
    args: tuple[str, ...]
    lines: tuple[FortranLogicalLine, ...]
    declarations: tuple[Declaration, ...]

    @property
    def upper_name(self) -> str:
        return self.name.upper()


@dataclass(frozen=True)
class ParsedFortranSource:
    path: Path
    form: str
    text: str
    logical_lines: tuple[FortranLogicalLine, ...]
    subroutines: tuple[ParsedSubroutine, ...]


@dataclass(frozen=True)
class UmatArgument:
    name: str
    position: int
    kind: str
    raw_type: str
    dimensions: tuple[str, ...] = ()

    @property
    def upper_name(self) -> str:
        return self.name.upper()

    @property
    def lower_name(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class UmatInterface:
    entry_name: str
    arguments: tuple[UmatArgument, ...]
    missing_required: tuple[str, ...]

    def get(self, name: str) -> UmatArgument | None:
        target = name.upper()
        for argument in self.arguments:
            if argument.upper_name == target:
                return argument
        return None

    @property
    def real_arguments(self) -> tuple[UmatArgument, ...]:
        return tuple(arg for arg in self.arguments if arg.kind == "real")

    @property
    def argument_names(self) -> tuple[str, ...]:
        return tuple(arg.upper_name for arg in self.arguments)


@dataclass(frozen=True)
class CallSite:
    caller: str
    callee: str
    line_numbers: tuple[int, ...]
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnsupportedFeature:
    code: str
    message: str
    severity: str
    line_numbers: tuple[int, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "line_numbers": list(self.line_numbers),
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class TransformDecision:
    code: str
    detail: str
    line_numbers: tuple[int, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "line_numbers": list(self.line_numbers),
        }


@dataclass(frozen=True)
class MaterialPoint:
    ntens: int = 6
    ndi: int = 3
    nshr: int = 3
    stress: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    statev: tuple[float, ...] = (0.0,)
    stran: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    dstran: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    props: tuple[float, ...] = ()
    dtime: float = 1.0
    temp: float = 293.15
    dtemp: float = 0.0
    fd_step: float = 1.0e-7
    stress_abs_tol: float = 1.0e-8
    jacobian_abs_tol: float = 1.0e-5
    jacobian_rel_tol: float = 1.0e-5

    @property
    def nstatv(self) -> int:
        return len(self.statev)

    @property
    def nprops(self) -> int:
        return len(self.props)


@dataclass(frozen=True)
class FortranRunResult:
    stress: tuple[float, ...]
    statev: tuple[float, ...]
    ddsdde: tuple[tuple[float, ...], ...]


@dataclass
class TransformResult:
    input_path: Path
    output_dir: Path
    generated_files: list[str] = field(default_factory=list)
    transform_report: dict[str, Any] = field(default_factory=dict)
    unsupported_report: dict[str, Any] = field(default_factory=dict)
    validation_report: dict[str, Any] = field(default_factory=dict)
