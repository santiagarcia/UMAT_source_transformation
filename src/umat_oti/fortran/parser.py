from __future__ import annotations

import re
from pathlib import Path

from umat_oti.core.model import (
    Declaration,
    DeclaredEntity,
    FortranLogicalLine,
    ParsedFortranSource,
    ParsedSubroutine,
)
from umat_oti.fortran.normalize import detect_source_form, strip_inline_comment


TYPE_PATTERN = (
    r"double\s+precision"
    r"|real(?:\s*\*\s*\d+|\s*\([^)]*\))?"
    r"|integer(?:\s*\*\s*\d+|\s*\([^)]*\))?"
    r"|character(?:\s*\*\s*\d+|\s*\([^)]*\))?"
    r"|logical(?:\s*\*\s*\d+|\s*\([^)]*\))?"
)


def parse_fortran_file(path: Path) -> ParsedFortranSource:
    text = path.read_text(encoding="utf-8")
    form = detect_source_form(path, text)
    logical_lines = logical_lines_from_text(text, form)
    subroutines = parse_subroutines(logical_lines)
    return ParsedFortranSource(path, form, text, logical_lines, subroutines)


def logical_lines_from_text(text: str, form: str) -> tuple[FortranLogicalLine, ...]:
    if form == "fixed":
        return _fixed_logical_lines(text)
    return _free_logical_lines(text)


def _free_logical_lines(text: str) -> tuple[FortranLogicalLine, ...]:
    result: list[FortranLogicalLine] = []
    pending = ""
    numbers: list[int] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = strip_inline_comment(raw).rstrip()
        if not stripped.strip():
            continue
        continuation = stripped.rstrip().endswith("&")
        part = stripped.rstrip()
        if continuation:
            part = part[:-1].rstrip()
        if pending:
            part = part.lstrip()
            if part.startswith("&"):
                part = part[1:].lstrip()
            pending = pending + " " + part
            numbers.append(number)
        else:
            pending = part.strip()
            numbers = [number]
        if not continuation:
            result.append(FortranLogicalLine(_collapse_spaces(pending), tuple(numbers)))
            pending = ""
            numbers = []
    if pending:
        result.append(FortranLogicalLine(_collapse_spaces(pending), tuple(numbers)))
    return tuple(result)


def _fixed_logical_lines(text: str) -> tuple[FortranLogicalLine, ...]:
    result: list[FortranLogicalLine] = []
    pending = ""
    numbers: list[int] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw:
            continue
        marker = raw[0]
        if marker in {"c", "C", "*", "!"}:
            continue
        body = strip_inline_comment(raw[6:] if len(raw) > 6 else "").rstrip()
        if not body.strip():
            continue
        is_continuation = len(raw) >= 6 and raw[5].strip() not in {"", "0"}
        if is_continuation and pending:
            pending = pending + " " + body.strip()
            numbers.append(number)
        else:
            if pending:
                result.append(FortranLogicalLine(_collapse_spaces(pending), tuple(numbers)))
            pending = body.strip()
            numbers = [number]
    if pending:
        result.append(FortranLogicalLine(_collapse_spaces(pending), tuple(numbers)))
    return tuple(result)


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def parse_subroutines(logical_lines: tuple[FortranLogicalLine, ...]) -> tuple[ParsedSubroutine, ...]:
    routines: list[ParsedSubroutine] = []
    index = 0
    while index < len(logical_lines):
        line = logical_lines[index]
        match = re.match(r"^\s*subroutine\s+(\w+)\s*\((.*)\)\s*$", line.text, flags=re.IGNORECASE)
        if not match:
            index += 1
            continue
        name = match.group(1)
        args = tuple(arg.strip() for arg in split_top_level(match.group(2)) if arg.strip())
        routine_lines = [line]
        index += 1
        while index < len(logical_lines):
            routine_lines.append(logical_lines[index])
            if re.match(
                r"^\s*end\s*(subroutine(\s+\w+)?)?\s*$",
                logical_lines[index].text,
                flags=re.IGNORECASE,
            ):
                break
            index += 1
        declarations = tuple(
            declaration
            for declaration in (parse_declaration_line(item) for item in routine_lines)
            if declaration is not None
        )
        routines.append(ParsedSubroutine(name, args, tuple(routine_lines), declarations))
        index += 1
    return tuple(routines)


def parse_declaration_line(line: FortranLogicalLine | str) -> Declaration | None:
    if isinstance(line, FortranLogicalLine):
        text = line.text
        line_numbers = line.line_numbers
    else:
        text = line
        line_numbers = ()
    stripped = text.strip()
    with_colons = re.match(
        rf"^(?P<type>{TYPE_PATTERN})(?P<attrs>(?:\s*,\s*[^:]+)*)\s*::\s*(?P<vars>.+)$",
        stripped,
        flags=re.IGNORECASE,
    )
    if with_colons:
        raw_type = _normalize_type(with_colons.group("type"))
        attributes = tuple(
            attr.strip() for attr in with_colons.group("attrs").split(",") if attr.strip()
        )
        entities = tuple(parse_entity(item) for item in split_top_level(with_colons.group("vars")))
        return Declaration(_kind(raw_type), raw_type, attributes, entities, text, line_numbers)
    old_style = re.match(
        rf"^(?P<type>{TYPE_PATTERN})\s+(?P<vars>.+)$",
        stripped,
        flags=re.IGNORECASE,
    )
    if not old_style:
        return None
    raw_type = _normalize_type(old_style.group("type"))
    entities = tuple(parse_entity(item) for item in split_top_level(old_style.group("vars")))
    return Declaration(_kind(raw_type), raw_type, (), entities, text, line_numbers)


def parse_entity(text: str) -> DeclaredEntity:
    raw = text.strip()
    before_init, initializer = split_initializer(raw)
    match = re.match(r"^(?P<name>\w+)\s*(?:\((?P<dims>.*)\))?$", before_init.strip())
    if not match:
        return DeclaredEntity(before_init.strip(), (), initializer, raw)
    dims = match.group("dims")
    dimensions = tuple(item.strip() for item in split_top_level(dims)) if dims else ()
    return DeclaredEntity(match.group("name"), dimensions, initializer, raw)


def split_initializer(text: str) -> tuple[str, str | None]:
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "=" and depth == 0:
            return text[:index].strip(), text[index + 1 :].strip()
    return text.strip(), None


def split_top_level(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    result: list[str] = []
    start = 0
    depth = 0
    in_single = False
    in_double = False
    for index, char in enumerate(text):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if char == "(":
                depth += 1
            elif char == ")" and depth:
                depth -= 1
            elif char == "," and depth == 0:
                result.append(text[start:index].strip())
                start = index + 1
    result.append(text[start:].strip())
    return tuple(item for item in result if item)


def _normalize_type(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip().lower())


def _kind(raw_type: str) -> str:
    lowered = raw_type.lower()
    if lowered.startswith("real") or lowered.startswith("double precision"):
        return "real"
    if lowered.startswith("integer"):
        return "integer"
    if lowered.startswith("character"):
        return "character"
    if lowered.startswith("logical"):
        return "logical"
    return "unknown"
