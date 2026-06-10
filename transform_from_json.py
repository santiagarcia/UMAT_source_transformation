from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from umat_oti.cli_json import main as cli_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transform a UMAT from a compact JSON contract.")
    parser.add_argument("config", type=Path, help="Path to the compact JSON file.")
    parser.add_argument(
        "--out",
        type=Path,
        help="Output directory. Defaults to ./umat_oti_workspace/new_user_runs/<config-stem>.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    forwarded = ["--config", str(args.config)]
    if args.out is not None:
        forwarded.extend(["--out", str(args.out)])
    return cli_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())