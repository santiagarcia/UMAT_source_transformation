from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CompileCheckResult:
    status: str
    compiler: str | None = None
    script_path: Path | None = None
    object_dir: Path | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "compiler": self.compiler,
            "script_path": str(self.script_path or ""),
            "object_dir": str(self.object_dir or ""),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def generated_fortran_files(generated_dir: Path, transformed_source: Path, ntens: int) -> list[Path]:
    generated_dir = Path(generated_dir)
    compile_order = generated_dir / "compile_order.txt"
    if compile_order.is_file():
        files = [generated_dir / line.strip() for line in compile_order.read_text(encoding="utf-8").splitlines() if line.strip()]
        if files:
            return files
    return [
        generated_dir / "master_parameters.f90",
        generated_dir / "real_utils.f90",
        generated_dir / f"otim{ntens}n1.f90",
        Path(transformed_source),
    ]


def write_compile_script(validation_dir: Path, generated_dir: Path, transformed_source: Path, ntens: int) -> Path:
    validation_dir = Path(validation_dir)
    validation_dir.mkdir(parents=True, exist_ok=True)
    files = generated_fortran_files(generated_dir, transformed_source, ntens)
    script = validation_dir / "compile_generated_umat.sh"
    object_dir = validation_dir / "compile_smoke_objects"
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "FC=${FC:-gfortran}",
        f"OBJDIR={_shell_quote(str(object_dir))}",
        "mkdir -p \"$OBJDIR\"",
    ]
    for index, path in enumerate(files):
        suffix = path.suffix.lower()
        fixed_form = suffix in {".f", ".for", ".ftn"}
        form_flags = "-ffixed-form -ffixed-line-length-none" if fixed_form else "-ffree-form -ffree-line-length-none"
        include_flags = '-I"$OBJDIR" -J"$OBJDIR"' if index else '-J"$OBJDIR"'
        lines.append(
            f"\"$FC\" -c {form_flags} {include_flags} {_shell_quote(str(path))} -o \"$OBJDIR/{path.stem}.o\""
        )
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def run_gfortran_smoke(
    validation_dir: Path,
    generated_dir: Path,
    transformed_source: Path,
    ntens: int,
    timeout_seconds: int = 120,
) -> CompileCheckResult:
    validation_dir = Path(validation_dir)
    script = write_compile_script(validation_dir, generated_dir, transformed_source, ntens)
    missing = [path for path in generated_fortran_files(generated_dir, transformed_source, ntens) if not path.is_file()]
    if missing:
        return CompileCheckResult(
            status="failed",
            script_path=script,
            object_dir=validation_dir / "compile_smoke_objects",
            errors=["Missing generated Fortran file: " + str(path) for path in missing],
        )
    source_text = Path(transformed_source).read_text(encoding="utf-8", errors="replace")
    if "ABA_PARAM.INC" in source_text.upper() and not _aba_param_available(validation_dir, generated_dir, Path(transformed_source).parent):
        return CompileCheckResult(
            status="skipped",
            script_path=script,
            object_dir=validation_dir / "compile_smoke_objects",
            warnings=["Transformed UMAT includes ABA_PARAM.INC; gfortran smoke compile was skipped because the Abaqus compiler environment is required."],
        )
    compiler = shutil.which("gfortran")
    if compiler is None:
        return CompileCheckResult(
            status="skipped",
            script_path=script,
            object_dir=validation_dir / "compile_smoke_objects",
            warnings=["gfortran was not found; non-Abaqus compile smoke test was skipped."],
        )
    try:
        process = subprocess.run(
            [str(script)],
            cwd=validation_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return CompileCheckResult(
            status="timeout",
            compiler=compiler,
            script_path=script,
            object_dir=validation_dir / "compile_smoke_objects",
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            errors=[f"gfortran smoke compile timed out after {timeout_seconds} seconds."],
        )
    return CompileCheckResult(
        status="passed" if process.returncode == 0 else "failed",
        compiler=compiler,
        script_path=script,
        object_dir=validation_dir / "compile_smoke_objects",
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
        errors=[] if process.returncode == 0 else ["gfortran smoke compile failed; Abaqus user-subroutine compile may still be required."],
    )


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _aba_param_available(*directories: Path) -> bool:
    names = {"ABA_PARAM.INC", "aba_param.inc"}
    for directory in directories:
        for name in names:
            if (Path(directory) / name).is_file():
                return True
    return False