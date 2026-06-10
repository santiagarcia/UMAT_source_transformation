from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


WORKSPACE_SUBDIRS = ("uploaded", "analysis", "config", "reports", "oti_transform", "validation", "generated")
ACCEPTED_EXTENSIONS = {".f", ".for", ".f90"}


@dataclass(frozen=True)
class ProjectInfo:
    name: str
    workdir: Path
    description: str = ""

    def to_json(self) -> dict[str, str]:
        return {
            "description": self.description,
            "name": self.name,
            "workdir": str(self.workdir),
        }


def sanitize_project_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "umat_oti_project"


def create_project_workspace(name: str, workdir: Path | str, description: str = "") -> ProjectInfo:
    root = Path(workdir).expanduser().resolve()
    for subdir in WORKSPACE_SUBDIRS:
        (root / subdir).mkdir(parents=True, exist_ok=True)
    return ProjectInfo(sanitize_project_name(name), root, description.strip())


def is_accepted_source_name(filename: str) -> bool:
    return Path(filename).suffix.lower() in ACCEPTED_EXTENSIONS


def file_metadata(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "extension": path.suffix,
        "file_name": path.name,
        "file_path": str(path),
        "file_size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def save_uploaded_bytes(project: ProjectInfo, filename: str, content: bytes) -> tuple[Path, dict[str, object]]:
    if not is_accepted_source_name(filename):
        raise ValueError("Accepted extensions are .f, .for, .f90, .F, .FOR, and .F90.")
    destination = project.workdir / "uploaded" / Path(filename).name
    destination.write_bytes(content)
    return destination, file_metadata(destination)
