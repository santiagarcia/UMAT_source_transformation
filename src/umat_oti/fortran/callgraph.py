from __future__ import annotations

import re

from umat_oti.core.model import CallSite, ParsedFortranSource


def build_call_graph(parsed: ParsedFortranSource, entry_name: str) -> tuple[CallSite, ...]:
    calls: list[CallSite] = []
    for routine in parsed.subroutines:
        if routine.upper_name != entry_name.upper():
            continue
        for line in routine.lines:
            match = re.search(r"\bcall\s+(\w+)\s*\(", line.text, flags=re.IGNORECASE)
            if match:
                calls.append(CallSite(routine.name, match.group(1), line.line_numbers))
    return tuple(sorted(calls, key=lambda item: (item.caller.upper(), item.callee.upper(), item.line_numbers)))
