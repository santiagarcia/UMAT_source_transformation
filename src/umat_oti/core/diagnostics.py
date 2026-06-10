from __future__ import annotations

import re

from umat_oti.core.model import CallSite, FortranLogicalLine, UnsupportedFeature


UNSUPPORTED_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("common_block", r"^\s*common\b", "COMMON blocks are not supported in the MVP transformer."),
    ("equivalence", r"^\s*equivalence\b", "EQUIVALENCE storage aliasing is not supported."),
    ("save", r"^\s*save\b", "SAVE state is not supported by deterministic material point validation."),
    ("data", r"^\s*data\b", "DATA initialization is not rewritten for OTIS shadow variables."),
    ("io_open", r"^\s*open\b", "Runtime file I/O is not supported in transformed UMAT kernels."),
    ("io_read", r"^\s*read\b", "Runtime input I/O is not supported in transformed UMAT kernels."),
    ("io_write", r"^\s*write\b", "Runtime output I/O is not supported in transformed UMAT kernels."),
)
def scan_unsupported_features(
    logical_lines: tuple[FortranLogicalLine, ...], call_sites: tuple[CallSite, ...]
) -> tuple[UnsupportedFeature, ...]:
    features: list[UnsupportedFeature] = []
    for line in logical_lines:
        text = line.text.strip()
        for code, pattern, message in UNSUPPORTED_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                features.append(UnsupportedFeature(code, message, "error", line.line_numbers))
        if re.search(r"\bif\s*\(.*\bdstran\b", text, flags=re.IGNORECASE):
            features.append(
                UnsupportedFeature(
                    "active_dstran_branch",
                    "Branch conditions depending directly on DSTRAN are not supported in the MVP.",
                    "warning",
                    line.line_numbers,
                )
            )
    return tuple(features)


def unsupported_report(features: tuple[UnsupportedFeature, ...]) -> dict[str, object]:
    return {
        "features": [feature.to_json() for feature in features],
        "has_errors": any(feature.severity == "error" for feature in features),
        "schema_version": 1,
    }
