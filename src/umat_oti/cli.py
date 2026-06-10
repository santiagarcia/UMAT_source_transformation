from __future__ import annotations

import argparse
import json
from pathlib import Path

from umat_oti.core.pipeline import transform_umat
from umat_oti.validation.material_point import load_material_point_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="umat-oti")
    subparsers = parser.add_subparsers(dest="command", required=True)
    transform = subparsers.add_parser("transform", help="Generate an OTIS-enabled UMAT.")
    transform.add_argument("source", type=Path, help="Path to the source UMAT.")
    transform.add_argument("--out", type=Path, required=True, help="Output directory for generated files.")
    transform.add_argument("--config", type=Path, help="Optional material_point.json validation config.")
    transform.add_argument("--no-validation", action="store_true", help="Generate files without running validation.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "transform":
        material_point = None
        if args.config is not None:
            material_point = load_material_point_config(args.config.parent)
        result = transform_umat(
            args.source,
            args.out,
            material_point=material_point,
            run_validation=not args.no_validation,
        )
        print(
            json.dumps(
                {
                    "generated_files": result.generated_files,
                    "output_dir": str(result.output_dir),
                    "validation_status": result.validation_report.get("status"),
                    "validation_pass": result.validation_report.get("pass"),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if result.validation_report.get("status") != "failed" else 1
    parser.error(f"Unhandled command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
