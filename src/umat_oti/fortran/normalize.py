from __future__ import annotations

from pathlib import Path


FIXED_FORM_EXTENSIONS = {".f", ".for", ".ftn"}


def detect_source_form(path: Path, text: str) -> str:
    suffix = path.suffix.lower()
    if suffix in FIXED_FORM_EXTENSIONS:
        return "fixed"
    if suffix in {".f90", ".f95", ".f03", ".f08"}:
        return "free"
    for line in text.splitlines()[:20]:
        stripped = line.strip()
        if stripped.endswith("&") or stripped.lower().startswith("subroutine "):
            return "free"
    return "fixed"


def strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "!" and not in_single and not in_double:
            return line[:index]
    return line
