from __future__ import annotations

from umat_oti.core.model import ParsedSubroutine


def stress_assignment_lines(routine: ParsedSubroutine) -> tuple[int, ...]:
    line_numbers: list[int] = []
    for line in routine.lines:
        if line.text.strip().lower().startswith("stress") and "=" in line.text:
            line_numbers.extend(line.line_numbers)
    return tuple(sorted(set(line_numbers)))
