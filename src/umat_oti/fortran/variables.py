from __future__ import annotations

import re
from dataclasses import dataclass, field

from umat_oti.core.model import ParsedFortranSource
from umat_oti.fortran.parser import parse_entity, split_top_level


ASSIGNMENT_RE = re.compile(r"^\s*(?P<lhs>[A-Za-z_]\w*)\s*(?:\([^=]*\))?\s*=\s*(?P<rhs>.+)$")
TOKEN_RE = re.compile(r"\b[A-Za-z_]\w*\b")
FORTRAN_KEYWORDS = {
    "AND",
    "CALL",
    "CHARACTER",
    "CONTAINS",
    "CONTINUE",
    "DO",
    "DOUBLE",
    "ELSE",
    "ELSEIF",
    "END",
    "ENDIF",
    "FALSE",
    "FUNCTION",
    "IF",
    "IMPLICIT",
    "INTEGER",
    "INTENT",
    "LOGICAL",
    "NONE",
    "NOT",
    "OR",
    "PARAMETER",
    "PRECISION",
    "REAL",
    "RETURN",
    "SUBROUTINE",
    "THEN",
    "TRUE",
    "USE",
}
FORTRAN_INTRINSICS = {
    "ABS",
    "DABS",
    "DEXP",
    "DIM",
    "DMAX1",
    "DMIN1",
    "DSQRT",
    "EXP",
    "LOG",
    "MAX",
    "MIN",
    "SIGN",
    "SQRT",
}


@dataclass
class VariableRecord:
    name: str
    detected_type: str = "unknown"
    detected_shape: str = ""
    is_argument: bool = False
    access: set[str] = field(default_factory=set)
    usage: set[str] = field(default_factory=set)
    assigned_from: set[str] = field(default_factory=set)
    routines: set[str] = field(default_factory=set)

    @property
    def upper_name(self) -> str:
        return self.name.upper()

    def to_json(self) -> dict[str, object]:
        access = "/".join(sorted(self.access)) if self.access else "unknown"
        return {
            "appears_in_umat_arguments": self.is_argument,
            "assigned_from": sorted(self.assigned_from),
            "detected_shape": self.detected_shape,
            "detected_type": self.detected_type,
            "detected_usage": sorted(self.usage),
            "read_write": access,
            "routines": sorted(self.routines),
            "variable_name": self.name,
        }


def collect_variables(parsed: ParsedFortranSource) -> dict[str, VariableRecord]:
    records: dict[str, VariableRecord] = {}
    for routine in parsed.subroutines:
        routine_args = {arg.upper(): arg for arg in routine.args}
        for arg in routine.args:
            record = _record(records, arg)
            record.is_argument = True
            record.access.add("unknown")
            record.usage.add("argument")
            record.routines.add(routine.name)
        for declaration in routine.declarations:
            for entity in declaration.entities:
                record = _record(records, entity.name)
                record.detected_type = declaration.raw_type
                record.detected_shape = ", ".join(entity.dimensions)
                record.is_argument = record.is_argument or entity.upper_name in routine_args
                record.routines.add(routine.name)
        for line in routine.lines:
            dimension_match = re.match(r"^\s*dimension\s+(.+)$", line.text, flags=re.IGNORECASE)
            if not dimension_match:
                continue
            for item in split_top_level(dimension_match.group(1)):
                entity = parse_entity(item)
                record = _record(records, entity.name)
                if entity.dimensions and not record.detected_shape:
                    record.detected_shape = ", ".join(entity.dimensions)
                record.is_argument = record.is_argument or entity.upper_name in routine_args
                record.routines.add(routine.name)
        for line in routine.lines:
            text = line.text
            assignment = ASSIGNMENT_RE.match(text)
            if assignment:
                lhs = assignment.group("lhs")
                rhs = assignment.group("rhs")
                lhs_record = _record(records, lhs)
                lhs_record.access.discard("unknown")
                lhs_record.access.add("write")
                lhs_record.usage.add("assignment-target")
                lhs_record.assigned_from.update(_tokens(rhs))
                lhs_record.routines.add(routine.name)
                for token in _tokens(rhs):
                    token_record = _record(records, token)
                    token_record.access.discard("unknown")
                    token_record.access.add("read")
                    token_record.routines.add(routine.name)
            for token in _tokens(text):
                if token in records:
                    records[token].routines.add(routine.name)
        _mark_named_usage(records)
    _mark_props_derived(records)
    _mark_stress_path(records)
    return dict(sorted(records.items()))


def _record(records: dict[str, VariableRecord], name: str) -> VariableRecord:
    upper = name.upper()
    if upper not in records:
        records[upper] = VariableRecord(name=upper)
    return records[upper]


def _tokens(text: str) -> set[str]:
    result: set[str] = set()
    for token in TOKEN_RE.findall(text):
        upper = token.upper()
        if upper in FORTRAN_KEYWORDS or upper in FORTRAN_INTRINSICS:
            continue
        if re.match(r"^[DE]\d+$", upper):
            continue
        result.add(upper)
    return result


def _mark_named_usage(records: dict[str, VariableRecord]) -> None:
    for name, record in records.items():
        if name == "DSTRAN":
            record.usage.add("seed-candidate")
        elif name == "STRESS":
            record.usage.add("dependent-stress")
        elif name == "DDSDDE":
            record.usage.add("tangent-output")
        elif name == "STATEV":
            record.usage.add("state-history")
        elif name == "PROPS":
            record.usage.add("material-properties")


def _mark_props_derived(records: dict[str, VariableRecord]) -> None:
    changed = True
    props_derived = {"PROPS"}
    while changed:
        changed = False
        for name, record in records.items():
            if name in props_derived:
                continue
            if record.assigned_from and record.assigned_from <= props_derived | {name}:
                props_derived.add(name)
                changed = True
    for name in props_derived:
        if name in records:
            records[name].usage.add("props-derived")


def _mark_stress_path(records: dict[str, VariableRecord]) -> None:
    stress_path = {"STRESS", "DSTRAN"}
    changed = True
    while changed:
        changed = False
        for name, record in records.items():
            if name in stress_path:
                continue
            if record.assigned_from & stress_path:
                stress_path.add(name)
                changed = True
    for name in stress_path:
        if name in records:
            records[name].usage.add("stress-update-path")
