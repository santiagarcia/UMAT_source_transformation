from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print a source-file range with 1-based line numbers.")
    parser.add_argument("source", type=Path, help="Path to the source file.")
    parser.add_argument("--start", type=int, default=1, help="First line to print, inclusive.")
    parser.add_argument("--end", type=int, default=200, help="Last line to print, inclusive.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.start < 1:
        parser.error("--start must be at least 1")
    if args.end < args.start:
        parser.error("--end must be greater than or equal to --start")
    source_path = args.source.expanduser().resolve()
    lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_number in range(args.start, min(args.end, len(lines)) + 1):
        print(f"{line_number:6d}: {lines[line_number - 1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())