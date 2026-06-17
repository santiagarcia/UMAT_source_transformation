"""Local Abaqus validation smoke test.

Mirrors run_json_pipeline.py's validation path but uses LOCAL Abaqus settings
(no `module load`, no `srun` prefix, plain `abaqus` command) so it runs on a
workstation with Abaqus installed directly. Untracked helper for verification.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from umat_oti.cli_json import run_config_transform
from umat_oti.core.config_loader import load_project_config_json
from umat_oti.core.transformation_anchors import merge_completed_anchors_into_config
from umat_oti.validation.abaqus_runner import extract_results, run_both_jobs
from umat_oti.validation.compare_results import compare_validation_results
from umat_oti.validation.job_builder import build_validation_workspace

# --- LOCAL Abaqus settings (override the ARC cluster defaults) ---
ABAQUS_COMMAND = "abaqus"
ABAQUS_MODULES = ""      # empty -> generated script skips `module load`
RUN_PREFIX = ""          # empty -> generated script skips `srun ...`

CONFIG_PATH = ROOT / "examples" / "elastic_minimal.json"
OUTPUT_DIR = ROOT / "umat_oti_workspace" / "abaqus_local_check"


def _load_resolved_config(config_path: Path):
    config = load_project_config_json(config_path.read_bytes(), origin_path=config_path)
    source = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
    source_path = Path(str(source.get("selected_umat_file", ""))).expanduser()
    source_text = source_path.read_text(encoding="utf-8", errors="replace") if source_path.is_file() else ""
    if source_text:
        config = merge_completed_anchors_into_config(config, source_text)
    settings = config.get("transformation_settings", {}) if isinstance(config.get("transformation_settings"), dict) else {}
    ntens = int(settings.get("ntens") or 0)
    return config, source_path, ntens


def main() -> int:
    print(f"Config: {CONFIG_PATH}")
    print(f"Output: {OUTPUT_DIR}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Transform (known-good path)
    summary, exit_code = run_config_transform(CONFIG_PATH, OUTPUT_DIR)
    print(f"[transform] exit_code={exit_code} success={summary.get('transform_success')}")
    if exit_code != 0:
        print(json.dumps(summary, indent=2))
        return exit_code

    # 2. Build validation workspace with LOCAL settings
    config, source_path, ntens = _load_resolved_config(CONFIG_PATH)
    transform_dir = Path(str(summary.get("out_dir", ""))).resolve()
    transformed_source = Path(str(summary.get("transformed_source", ""))).resolve()
    validation_dir = transform_dir / "validation"
    print(f"[validate] ntens={ntens} source={source_path}")
    print(f"[validate] transformed={transformed_source}")

    build_validation_workspace(
        validation_dir=validation_dir,
        original_umat=source_path,
        transformed_umat=transformed_source,
        generated_dir=transform_dir,
        project_config=config,
        ntens=ntens,
        abaqus_command=ABAQUS_COMMAND,
        abaqus_modules=ABAQUS_MODULES,
        run_prefix=RUN_PREFIX,
        material_test_mode="single element tension",
        run_compile_smoke=False,
    )
    print(f"[validate] workspace built at {validation_dir}")

    # 3. Run both Abaqus jobs
    run_both_jobs(validation_dir, ABAQUS_COMMAND, ABAQUS_MODULES, RUN_PREFIX, timeout_seconds=1800)
    print("[validate] both Abaqus jobs finished")

    # 4. Extract ODB results
    extract_results(validation_dir, ABAQUS_COMMAND, ABAQUS_MODULES, RUN_PREFIX, timeout_seconds=600)
    print("[validate] results extracted")

    # 5. Compare
    compare_validation_results(validation_dir)
    report_path = validation_dir / "comparison_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print("=" * 60)
    print(f"comparison pass: {report.get('pass')}")
    print(f"comparison status: {report.get('status')}")
    print(f"report: {report_path}")
    print("=" * 60)
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
