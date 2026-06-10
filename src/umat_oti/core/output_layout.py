from __future__ import annotations

from pathlib import Path


TRANSFORM_DIRNAME = "oti_transform"
VALIDATION_DIRNAME = "validation"
LEGACY_GENERATED_TRANSFORM = Path("generated") / TRANSFORM_DIRNAME
LEGACY_GENERATED_VALIDATION = Path("generated") / VALIDATION_DIRNAME


def default_transform_output_dir(project_workdir: str | Path) -> Path:
    return Path(project_workdir) / TRANSFORM_DIRNAME


def default_validation_work_dir(project_workdir: str | Path, transform_output_dir: str | Path | None = None) -> Path:
    if transform_output_dir:
        return Path(transform_output_dir).parent / VALIDATION_DIRNAME
    return Path(project_workdir) / VALIDATION_DIRNAME


def migrate_transform_output_dir(project_workdir: str | Path, output_dir: str | Path | None) -> Path:
    if not output_dir:
        return default_transform_output_dir(project_workdir)
    path = Path(output_dir)
    if _same_path(path, Path(project_workdir) / LEGACY_GENERATED_TRANSFORM):
        return default_transform_output_dir(project_workdir)
    return path


def migrate_validation_work_dir(
    project_workdir: str | Path,
    validation_dir: str | Path | None,
    transform_output_dir: str | Path | None = None,
) -> Path:
    default_dir = default_validation_work_dir(project_workdir, transform_output_dir)
    if not validation_dir:
        return default_dir
    path = Path(validation_dir)
    if _same_path(path, Path(project_workdir) / LEGACY_GENERATED_VALIDATION):
        return default_dir
    return path


def is_legacy_generated_validation_dir(project_workdir: str | Path, validation_dir: str | Path | None) -> bool:
    return bool(validation_dir) and _same_path(Path(validation_dir), Path(project_workdir) / LEGACY_GENERATED_VALIDATION)


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)