from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from umat_oti.fortran.scanner import analyze_fortran_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect a raw UMAT source before authoring a compact JSON contract.")
    parser.add_argument("source", type=Path, help="Path to the UMAT source file.")
    return parser


def _region_summary_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "region_type": row.get("region type", row.get("region_type", "")),
        "start_line": row.get("start line", row.get("start_line", "")),
        "end_line": row.get("end line", row.get("end_line", "")),
        "preview": row.get("short code preview", row.get("preview", "")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    source_path = args.source.expanduser().resolve()
    analysis = analyze_fortran_source(source_path)
    umat_routines = []
    for row in analysis.get("detected_umat_routines", []) or []:
        if not isinstance(row, dict):
            continue
        umat_routines.append(
            {
                "name": str(row.get("name", "")).upper(),
                "arguments": [str(value).upper() for value in row.get("arguments", []) if str(value).strip()],
            }
        )
    payload = {
        "source": str(source_path),
        "has_subroutine_umat": bool(analysis.get("has_subroutine_umat")),
        "umat_routines": umat_routines,
        "detected_subroutines": [
            str(row.get("name", "")).upper()
            for row in analysis.get("detected_subroutines", [])
            if isinstance(row, dict) and str(row.get("name", "")).strip()
        ],
        "region_summary": analysis.get("region_summary", {}),
        "detected_regions": [
            _region_summary_row(row)
            for row in analysis.get("detected_regions", [])
            if isinstance(row, dict)
        ],
        "warnings": analysis.get("warnings", []),
        "unsupported_features": analysis.get("unsupported_features", []),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())