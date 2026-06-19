from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
import tempfile
import types
from dataclasses import dataclass
from math import comb
from pathlib import Path
from typing import Any


class OtilibGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OtilibModuleResult:
    module_name: str
    type_name: str
    module_path: Path
    master_parameters_path: Path
    real_utils_path: Path
    warnings: tuple[str, ...] = ()


def generate_otilib_module(
    *,
    output_dir: Path,
    ntens: int,
    order: int = 1,
    support_dir: Path | None = None,
) -> OtilibModuleResult:
    if order < 1:
        raise OtilibGenerationError("OTILIB module generation unavailable: order must be a positive integer.")
    if ntens <= 0:
        raise OtilibGenerationError("OTILIB module generation unavailable: NTENS must be a positive integer.")

    support = _find_support_dir(support_dir)
    fmod_writer_path = support / "fmod_writer.py"
    master_path = support / "master_parameters.f90"
    real_utils_path = support / "real_utils.f90"
    missing = [path.name for path in (fmod_writer_path, master_path, real_utils_path) if not path.is_file()]
    if missing:
        raise OtilibGenerationError("OTILIB module generation unavailable: missing " + ", ".join(missing))

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_master = output_dir / "master_parameters.f90"
    generated_real_utils = output_dir / "real_utils.f90"
    shutil.copyfile(master_path, generated_master)
    shutil.copyfile(real_utils_path, generated_real_utils)

    module_name = f"otim{ntens}n{order}"
    type_name = f"ONUMM{ntens}N{order}"
    module_path = output_dir / f"{module_name}.f90"
    warnings: list[str] = []

    template_dir = _find_template_dir()
    if template_dir is None:
        warnings.append("Using minimal local pyoti templates because upstream pyoti templates were not found.")

    try:
        _run_fmod_writer(
            fmod_writer_path=fmod_writer_path,
            output_path=module_path,
            ntens=ntens,
            order=order,
            template_dir=template_dir,
        )
    except Exception as exc:
        raise OtilibGenerationError(f"OTILIB module generation unavailable: {exc}") from exc

    source = module_path.read_text(encoding="utf-8", errors="replace")
    module_path.write_text(_post_fix_module(source, ntens, order), encoding="utf-8")
    return OtilibModuleResult(
        module_name=module_name,
        type_name=type_name,
        module_path=module_path,
        master_parameters_path=generated_master,
        real_utils_path=generated_real_utils,
        warnings=tuple(warnings),
    )


def _find_support_dir(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    project_root = Path(__file__).resolve().parents[3]
    candidates = [project_root / "OTI", project_root / "UMATs" / "OTI"]
    for candidate in candidates:
        if (candidate / "fmod_writer.py").is_file():
            return candidate
    return candidates[0]


def _find_template_dir() -> Path | None:
    env_value = os.environ.get("OTILIB_TEMPLATE_DIR")
    candidates: list[Path] = []
    if env_value:
        candidates.append(Path(env_value))
    project_root = Path(__file__).resolve().parents[3]
    search_roots = [project_root, *project_root.parents]
    for root in search_roots:
        candidates.extend(
            [
                root / "vendor" / "otilib" / "src" / "python" / "pyoti" / "python",
                root / "vendor" / "_otilib_upstream" / "src" / "python" / "pyoti" / "python",
            ]
        )
    sibling_root = project_root.parent / "progressive_umat_oti"
    candidates.extend(
        [
            sibling_root / "vendor" / "otilib" / "src" / "python" / "pyoti" / "python",
            sibling_root / "vendor" / "_otilib_upstream" / "src" / "python" / "pyoti" / "python",
        ]
    )
    for candidate in candidates:
        if (candidate / "core_functions.f90").is_file() and (candidate / "base_derivs_fortran.f90").is_file():
            return candidate
    return None


def _run_fmod_writer(
    *,
    fmod_writer_path: Path,
    output_path: Path,
    ntens: int,
    order: int,
    template_dir: Path | None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="umat_oti_pyoti_") as tmp:
        template_path = template_dir or _create_minimal_templates(Path(tmp))
        installed_modules = _install_pyoti_shim(template_path)
        previous_modules = {name: sys.modules.get(name) for name in installed_modules}
        sys.modules.update(installed_modules)
        try:
            spec = importlib.util.spec_from_file_location("umat_oti_fmod_writer", fmod_writer_path)
            if spec is None or spec.loader is None:
                raise OtilibGenerationError("could not load fmod_writer.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            writer = module.writer(nbases=ntens, order=order, coeff_type="REAL(DP)")
            cwd_before = Path.cwd()
            try:
                os.chdir(output_path.parent)
                writer.write_file(filename=output_path.name)
            finally:
                os.chdir(cwd_before)
        finally:
            for name, previous in previous_modules.items():
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous


def _install_pyoti_shim(template_dir: Path) -> dict[str, types.ModuleType]:
    pyoti_module = types.ModuleType("pyoti")
    core_module = types.ModuleType("pyoti.core")
    where_module = types.ModuleType("pyoti.whereotilib")

    import numpy as np

    from umat_oti.oti import oti_directions as _dirs

    class DHelp:
        """Arbitrary-order OTI direction algebra, backed by the canonical
        nbases-independent enumeration in :mod:`umat_oti.oti.oti_directions`."""

        def get_ndir_total(self, nbases: int, order: int) -> int:
            return _dirs.ndir_total(nbases, order)

        def get_ndir_order(self, nbases: int, order: int) -> int:
            return _dirs.ndir_order(nbases, order)

        def get_fulldir(self, idx: int, order: int) -> Any:
            if order == 0:
                return np.zeros(1, dtype=np.uint16)
            return np.array(_dirs.fulldir(idx, order), dtype=np.uint16)

        def mult_dir(self, j: int, ordj: int, k: int, ordk: int) -> Any:
            combined = tuple(_dirs.fulldir(j, ordj)) + tuple(_dirs.fulldir(k, ordk))
            total_order = ordj + ordk
            return _dirs.dir_index(combined), total_order

        def get_deriv_factor(self, indx: int, order: int) -> float:
            return float(_dirs.deriv_factor(_dirs.fulldir(indx, order)))

    dhelp = DHelp()

    def get_dHelp() -> DHelp:
        return dhelp

    def get_deriv_factor(hum_dir: Any) -> float:
        return 1.0

    def ndir_total(nbases: int, order: int) -> int:
        return dhelp.get_ndir_total(nbases, order)

    def ndir_order(nbases: int, order: int) -> int:
        return dhelp.get_ndir_order(nbases, order)

    def getpath() -> str:
        return str(template_dir) + "/"

    where_module.getpath = getpath  # type: ignore[attr-defined]
    core_module.get_dHelp = get_dHelp  # type: ignore[attr-defined]
    core_module.get_deriv_factor = get_deriv_factor  # type: ignore[attr-defined]
    core_module.ndir_total = ndir_total  # type: ignore[attr-defined]
    core_module.ndir_order = ndir_order  # type: ignore[attr-defined]
    core_module.whereotilib = where_module  # type: ignore[attr-defined]
    core_module.np = np  # type: ignore[attr-defined]
    pyoti_module.core = core_module  # type: ignore[attr-defined]
    pyoti_module.whereotilib = where_module  # type: ignore[attr-defined]
    return {"pyoti": pyoti_module, "pyoti.core": core_module, "pyoti.whereotilib": where_module}


def _create_minimal_templates(template_dir: Path) -> Path:
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "core_functions.f90").write_text("\n", encoding="utf-8")
    (template_dir / "base_derivs_fortran.f90").write_text("\n", encoding="utf-8")
    return template_dir


_BACK_KW_RE = re.compile(r",\s*BACK\s*=\s*BACK_DEF")
_CONTAINS_RE = re.compile(r"^([ \t]*)CONTAINS\s*$", re.MULTILINE)
_END_MODULE_RE = re.compile(r"^([ \t]*)END\s+MODULE\b.*$", re.MULTILINE | re.IGNORECASE)
_MASTER_PARAMETERS_USE_RE = re.compile(r"^([ \t]*)USE\s+master_parameters\s*$", re.MULTILINE | re.IGNORECASE)
_REAL_UTILS_USE_RE = re.compile(r"^([ \t]*)USE\s+real_utils\s*$", re.MULTILINE | re.IGNORECASE)
_REAL_UTILS_ONLY = "PPRINT, det2x2, det3x3, det4x4, inv2x2, inv3x3, inv4x4"


def _post_fix_module(source: str, ntens: int, order: int = 1) -> str:
    source = _BACK_KW_RE.sub("", source)
    source = _MASTER_PARAMETERS_USE_RE.sub(lambda match: f"{match.group(1)}USE master_parameters, ONLY: DP", source, count=1)
    source = _REAL_UTILS_USE_RE.sub(lambda match: f"{match.group(1)}USE real_utils, ONLY: {_REAL_UTILS_ONLY}", source, count=1)
    interface_block, body = _extra_overloads(ntens, order)
    source = _CONTAINS_RE.sub(lambda match: interface_block + match.group(0), source, count=1)
    source = _END_MODULE_RE.sub(lambda match: body + match.group(0), source, count=1)
    return source


def _extra_overloads(ntens: int, order: int = 1) -> tuple[str, str]:
    from umat_oti.oti import oti_directions as _dirs

    type_name = f"ONUMM{ntens}N{order}"
    members = [d["name"] for d in _dirs.imaginary_directions(ntens, order)]
    abs_direction_lines = "\n      ".join(f"RES%{m} = SGN * A%{m}" for m in members)
    norm_terms = " + ".join(["A%R*A%R"] + [f"A%{m}*A%{m}" for m in members])
    interface_block = (
        "  INTERFACE ABS\n"
        f"    MODULE PROCEDURE {type_name}_ABS\n"
        "  END INTERFACE ABS\n"
        "  INTERFACE KOTI_NORM\n"
        f"    MODULE PROCEDURE KOTI_NORM_{type_name}\n"
        "  END INTERFACE KOTI_NORM\n"
    )
    body = f"""
  FUNCTION {type_name}_ABS(A) RESULT(RES)
      IMPLICIT NONE
      TYPE({type_name}), INTENT(IN) :: A
      TYPE({type_name}) :: RES
      REAL(DP) :: SGN
      RES%R = ABS(A%R)
      IF (A%R > 0.0_dp) THEN
        SGN = 1.0_dp
      ELSE IF (A%R < 0.0_dp) THEN
        SGN = -1.0_dp
      ELSE
        SGN = 0.0_dp
      END IF
      {abs_direction_lines}
  END FUNCTION {type_name}_ABS

  FUNCTION KOTI_NORM_{type_name}(A) RESULT(RES)
      IMPLICIT NONE
      TYPE({type_name}), INTENT(IN) :: A
      REAL(DP) :: RES
      RES = SQRT({norm_terms})
  END FUNCTION KOTI_NORM_{type_name}
"""
    return interface_block, body
