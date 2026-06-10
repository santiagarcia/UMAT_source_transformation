from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from umat_oti.core.model import FortranRunResult, MaterialPoint


def find_fortran_compiler() -> str | None:
    for candidate in ("gfortran", "ifort", "ifx"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def validation_driver_source(point: MaterialPoint) -> str:
    ntens = point.ntens
    nstatv = max(point.nstatv, 1)
    nprops = max(point.nprops, 1)
    return f"""program material_point_driver
  implicit none
  integer, parameter :: ntens = {ntens}
  integer, parameter :: ndi = {point.ndi}
  integer, parameter :: nshr = {point.nshr}
  integer, parameter :: nstatv = {nstatv}
  integer, parameter :: nprops = {nprops}
  real(8) :: stress(ntens), statev(nstatv), ddsdde(ntens, ntens)
  real(8) :: sse, spd, scd, rpl, ddsddt(ntens), drplde(ntens), drpldt
  real(8) :: stran(ntens), dstran(ntens), time(2), dtime, temp, dtemp
  real(8) :: predef(1), dpred(1), props(nprops), coords(3), drot(3,3)
  real(8) :: pnewdt, celent, dfgrd0(3,3), dfgrd1(3,3)
  character(len=80) :: cmname
  integer :: noel, npt, layer, kspt, jstep(4), kinc
  integer :: i, j

  call read_vector(stress, ntens)
  call read_vector(statev, nstatv)
  call read_vector(stran, ntens)
  call read_vector(dstran, ntens)
  call read_vector(props, nprops)
  ddsdde = 0.0d0
  sse = 0.0d0
  spd = 0.0d0
  scd = 0.0d0
  rpl = 0.0d0
  ddsddt = 0.0d0
  drplde = 0.0d0
  drpldt = 0.0d0
  time = 0.0d0
  dtime = {point.dtime:.17e}
  temp = {point.temp:.17e}
  dtemp = {point.dtemp:.17e}
  predef = 0.0d0
  dpred = 0.0d0
  coords = 0.0d0
  drot = 0.0d0
  dfgrd0 = 0.0d0
  dfgrd1 = 0.0d0
  do i = 1, 3
    drot(i,i) = 1.0d0
    dfgrd0(i,i) = 1.0d0
    dfgrd1(i,i) = 1.0d0
  end do
  pnewdt = 1.0d0
  celent = 1.0d0
  cmname = 'UMAT_OTI_MATERIAL'
  noel = 1
  npt = 1
  layer = 1
  kspt = 1
  jstep = 0
  kinc = 1

  call UMAT( &
    stress, statev, ddsdde, sse, spd, scd, rpl, ddsddt, &
    drplde, drpldt, stran, dstran, time, dtime, temp, dtemp, &
    predef, dpred, cmname, ndi, nshr, ntens, nstatv, props, &
    nprops, coords, drot, pnewdt, celent, dfgrd0, dfgrd1, noel, &
    npt, layer, kspt, jstep, kinc)

  write(*,'(A)') 'STRESS'
  do i = 1, ntens
    write(*,'(ES25.16E3)') stress(i)
  end do
  write(*,'(A)') 'STATEV'
  do i = 1, nstatv
    write(*,'(ES25.16E3)') statev(i)
  end do
  write(*,'(A)') 'DDSDDE'
  do i = 1, ntens
    do j = 1, ntens
      write(*,'(ES25.16E3)') ddsdde(i,j)
    end do
  end do

contains
  subroutine read_vector(values, count)
    integer, intent(in) :: count
    real(8), intent(out) :: values(count)
    integer :: k
    do k = 1, count
      read(*,*) values(k)
    end do
  end subroutine read_vector
end program material_point_driver
"""


def compile_original(
    compiler: str, build_dir: Path, source: Path, driver: Path, source_form: str
) -> tuple[bool, Path | None, str]:
    exe = build_dir / "original_driver"
    objects = [build_dir / "driver_original.o", build_dir / "original.o"]
    commands = [
        [compiler, "-c", "-ffree-form", "-ffree-line-length-none", str(driver), "-o", str(objects[0])],
        [compiler, "-c", _form_flag(source_form), "-ffree-line-length-none", str(source), "-o", str(objects[1])],
        [compiler, str(objects[0]), str(objects[1]), "-o", str(exe)],
    ]
    return _run_compile_commands(commands, exe)


def compile_transformed(
    compiler: str, build_dir: Path, backend: Path, transformed: Path, driver: Path
) -> tuple[bool, Path | None, str]:
    exe = build_dir / "transformed_driver"
    objects = [
        build_dir / "backend.o",
        build_dir / "transformed.o",
        build_dir / "driver_transformed.o",
    ]
    commands = [
        [compiler, "-c", "-ffree-form", "-ffree-line-length-none", str(backend), "-o", str(objects[0])],
        [
            compiler,
            "-c",
            "-ffree-form",
            "-ffree-line-length-none",
            "-I",
            str(build_dir),
            str(transformed),
            "-o",
            str(objects[1]),
        ],
        [compiler, "-c", "-ffree-form", "-ffree-line-length-none", str(driver), "-o", str(objects[2])],
        [compiler, str(objects[0]), str(objects[1]), str(objects[2]), "-o", str(exe)],
    ]
    return _run_compile_commands(commands, exe)


def run_material_point(exe: Path, point: MaterialPoint, dstran: tuple[float, ...] | None = None) -> FortranRunResult:
    process = subprocess.run(
        [str(exe)],
        input=_stdin_for_point(point, dstran),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip())
    return _parse_driver_output(process.stdout, point.ntens, max(point.nstatv, 1))


def _run_compile_commands(commands: list[list[str]], exe: Path) -> tuple[bool, Path | None, str]:
    logs: list[str] = []
    for command in commands:
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        logs.append(process.stdout)
        logs.append(process.stderr)
        if process.returncode != 0:
            return False, None, "".join(logs).strip()
    return True, exe, "".join(logs).strip()


def _form_flag(source_form: str) -> str:
    return "-ffixed-form" if source_form == "fixed" else "-ffree-form"


def _stdin_for_point(point: MaterialPoint, dstran: tuple[float, ...] | None = None) -> str:
    values: list[float] = []
    values.extend(point.stress)
    values.extend(point.statev)
    values.extend(point.stran)
    values.extend(dstran if dstran is not None else point.dstran)
    props = point.props if point.props else (0.0,)
    values.extend(props)
    return "\n".join(f"{value:.17e}" for value in values) + "\n"


def _parse_driver_output(text: str, ntens: int, nstatv: int) -> FortranRunResult:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    stress_start = lines.index("STRESS") + 1
    statev_start = lines.index("STATEV") + 1
    ddsdde_start = lines.index("DDSDDE") + 1
    stress = tuple(float(value) for value in lines[stress_start : stress_start + ntens])
    statev = tuple(float(value) for value in lines[statev_start : statev_start + nstatv])
    flat = [float(value) for value in lines[ddsdde_start : ddsdde_start + ntens * ntens]]
    matrix = tuple(tuple(flat[i * ntens + j] for j in range(ntens)) for i in range(ntens))
    return FortranRunResult(stress, statev, matrix)
