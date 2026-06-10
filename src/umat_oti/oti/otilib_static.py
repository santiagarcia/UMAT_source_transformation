from __future__ import annotations

from dataclasses import dataclass

from textwrap import dedent

from umat_oti.oti.backend import OtiBackend


@dataclass(frozen=True)
class StaticFirstOrderBackend(OtiBackend):
    """Small deterministic first-order OTIS-like backend for the MVP."""

    direction_count: int = 6

    name: str = "umat_oti_backend"


    def scalar_type(self) -> str:
        return "type(otis_t)"

    def module_name(self) -> str:
        return self.name

    def seed_call(self, variable: str, direction: str) -> str:
        return f"call seed_direction({variable}, {direction})"

    def real_part(self, expression: str) -> str:
        return f"real_part({expression})"

    def derivative_part(self, expression: str, direction: str) -> str:
        return f"deriv_part({expression}, {direction})"

    def module_source(self) -> str:
        return dedent(
            f"""
            module {self.name}
              implicit none
              private
              integer, parameter, public :: OTI_NDIR = {self.direction_count}

              type, public :: otis_t
                real(8) :: r = 0.0d0
                real(8) :: e(OTI_NDIR) = 0.0d0
              end type otis_t

              public :: otis_from_real, seed_direction, real_part, deriv_part
              public :: assignment(=), operator(+), operator(-), operator(*), operator(/), operator(**)
              public :: sqrt, exp, log, sin, cos, abs, max, min

              interface assignment(=)
                module procedure assign_real_to_otis
                module procedure assign_integer_to_otis
              end interface

              interface operator(+)
                module procedure add_oo, add_or, add_ro, add_oi, add_io, unary_plus_o
              end interface

              interface operator(-)
                module procedure sub_oo, sub_or, sub_ro, sub_oi, sub_io, unary_minus_o
              end interface

              interface operator(*)
                module procedure mul_oo, mul_or, mul_ro, mul_oi, mul_io
              end interface

              interface operator(/)
                module procedure div_oo, div_or, div_ro, div_oi, div_io
              end interface

              interface operator(**)
                module procedure pow_oi, pow_or
              end interface

              interface sqrt
                module procedure sqrt_o
              end interface

              interface exp
                module procedure exp_o
              end interface

              interface log
                module procedure log_o
              end interface

              interface sin
                module procedure sin_o
              end interface

              interface cos
                module procedure cos_o
              end interface

              interface abs
                module procedure abs_o
              end interface

              interface max
                module procedure max_oo, max_or, max_ro
              end interface

              interface min
                module procedure min_oo, min_or, min_ro
              end interface

            contains
              elemental function otis_from_real(value) result(out)
                real(8), intent(in) :: value
                type(otis_t) :: out
                out%r = value
                out%e = 0.0d0
              end function otis_from_real

              elemental subroutine assign_real_to_otis(lhs, rhs)
                type(otis_t), intent(out) :: lhs
                real(8), intent(in) :: rhs
                lhs%r = rhs
                lhs%e = 0.0d0
              end subroutine assign_real_to_otis

              elemental subroutine assign_integer_to_otis(lhs, rhs)
                type(otis_t), intent(out) :: lhs
                integer, intent(in) :: rhs
                lhs%r = real(rhs, 8)
                lhs%e = 0.0d0
              end subroutine assign_integer_to_otis

              subroutine seed_direction(value, direction)
                type(otis_t), intent(inout) :: value
                integer, intent(in) :: direction
                if (direction >= 1 .and. direction <= OTI_NDIR) then
                  value%e(direction) = 1.0d0
                end if
              end subroutine seed_direction

              elemental function real_part(value) result(out)
                type(otis_t), intent(in) :: value
                real(8) :: out
                out = value%r
              end function real_part

              pure function deriv_part(value, direction) result(out)
                type(otis_t), intent(in) :: value
                integer, intent(in) :: direction
                real(8) :: out
                if (direction >= 1 .and. direction <= OTI_NDIR) then
                  out = value%e(direction)
                else
                  out = 0.0d0
                end if
              end function deriv_part

              elemental function unary_plus_o(a) result(c)
                type(otis_t), intent(in) :: a
                type(otis_t) :: c
                c = a
              end function unary_plus_o

              elemental function unary_minus_o(a) result(c)
                type(otis_t), intent(in) :: a
                type(otis_t) :: c
                c%r = -a%r
                c%e = -a%e
              end function unary_minus_o

              elemental function add_oo(a, b) result(c)
                type(otis_t), intent(in) :: a, b
                type(otis_t) :: c
                c%r = a%r + b%r
                c%e = a%e + b%e
              end function add_oo

              elemental function add_or(a, b) result(c)
                type(otis_t), intent(in) :: a
                real(8), intent(in) :: b
                type(otis_t) :: c
                c%r = a%r + b
                c%e = a%e
              end function add_or

              elemental function add_ro(a, b) result(c)
                real(8), intent(in) :: a
                type(otis_t), intent(in) :: b
                type(otis_t) :: c
                c%r = a + b%r
                c%e = b%e
              end function add_ro

              elemental function add_oi(a, b) result(c)
                type(otis_t), intent(in) :: a
                integer, intent(in) :: b
                type(otis_t) :: c
                c = a + real(b, 8)
              end function add_oi

              elemental function add_io(a, b) result(c)
                integer, intent(in) :: a
                type(otis_t), intent(in) :: b
                type(otis_t) :: c
                c = real(a, 8) + b
              end function add_io

              elemental function sub_oo(a, b) result(c)
                type(otis_t), intent(in) :: a, b
                type(otis_t) :: c
                c%r = a%r - b%r
                c%e = a%e - b%e
              end function sub_oo

              elemental function sub_or(a, b) result(c)
                type(otis_t), intent(in) :: a
                real(8), intent(in) :: b
                type(otis_t) :: c
                c%r = a%r - b
                c%e = a%e
              end function sub_or

              elemental function sub_ro(a, b) result(c)
                real(8), intent(in) :: a
                type(otis_t), intent(in) :: b
                type(otis_t) :: c
                c%r = a - b%r
                c%e = -b%e
              end function sub_ro

              elemental function sub_oi(a, b) result(c)
                type(otis_t), intent(in) :: a
                integer, intent(in) :: b
                type(otis_t) :: c
                c = a - real(b, 8)
              end function sub_oi

              elemental function sub_io(a, b) result(c)
                integer, intent(in) :: a
                type(otis_t), intent(in) :: b
                type(otis_t) :: c
                c = real(a, 8) - b
              end function sub_io

              elemental function mul_oo(a, b) result(c)
                type(otis_t), intent(in) :: a, b
                type(otis_t) :: c
                c%r = a%r * b%r
                c%e = a%e * b%r + b%e * a%r
              end function mul_oo

              elemental function mul_or(a, b) result(c)
                type(otis_t), intent(in) :: a
                real(8), intent(in) :: b
                type(otis_t) :: c
                c%r = a%r * b
                c%e = a%e * b
              end function mul_or

              elemental function mul_ro(a, b) result(c)
                real(8), intent(in) :: a
                type(otis_t), intent(in) :: b
                type(otis_t) :: c
                c%r = a * b%r
                c%e = a * b%e
              end function mul_ro

              elemental function mul_oi(a, b) result(c)
                type(otis_t), intent(in) :: a
                integer, intent(in) :: b
                type(otis_t) :: c
                c = a * real(b, 8)
              end function mul_oi

              elemental function mul_io(a, b) result(c)
                integer, intent(in) :: a
                type(otis_t), intent(in) :: b
                type(otis_t) :: c
                c = real(a, 8) * b
              end function mul_io

              elemental function div_oo(a, b) result(c)
                type(otis_t), intent(in) :: a, b
                type(otis_t) :: c
                c%r = a%r / b%r
                c%e = (a%e * b%r - a%r * b%e) / (b%r * b%r)
              end function div_oo

              elemental function div_or(a, b) result(c)
                type(otis_t), intent(in) :: a
                real(8), intent(in) :: b
                type(otis_t) :: c
                c%r = a%r / b
                c%e = a%e / b
              end function div_or

              elemental function div_ro(a, b) result(c)
                real(8), intent(in) :: a
                type(otis_t), intent(in) :: b
                type(otis_t) :: c
                c%r = a / b%r
                c%e = -a * b%e / (b%r * b%r)
              end function div_ro

              elemental function div_oi(a, b) result(c)
                type(otis_t), intent(in) :: a
                integer, intent(in) :: b
                type(otis_t) :: c
                c = a / real(b, 8)
              end function div_oi

              elemental function div_io(a, b) result(c)
                integer, intent(in) :: a
                type(otis_t), intent(in) :: b
                type(otis_t) :: c
                c = real(a, 8) / b
              end function div_io

              elemental function pow_oi(a, b) result(c)
                type(otis_t), intent(in) :: a
                integer, intent(in) :: b
                type(otis_t) :: c
                c%r = a%r ** b
                if (b == 0) then
                  c%e = 0.0d0
                else
                  c%e = real(b, 8) * (a%r ** (b - 1)) * a%e
                end if
              end function pow_oi

              elemental function pow_or(a, b) result(c)
                type(otis_t), intent(in) :: a
                real(8), intent(in) :: b
                type(otis_t) :: c
                c%r = a%r ** b
                c%e = b * (a%r ** (b - 1.0d0)) * a%e
              end function pow_or

              elemental function sqrt_o(a) result(c)
                type(otis_t), intent(in) :: a
                type(otis_t) :: c
                c%r = sqrt(a%r)
                c%e = a%e / (2.0d0 * c%r)
              end function sqrt_o

              elemental function exp_o(a) result(c)
                type(otis_t), intent(in) :: a
                type(otis_t) :: c
                c%r = exp(a%r)
                c%e = c%r * a%e
              end function exp_o

              elemental function log_o(a) result(c)
                type(otis_t), intent(in) :: a
                type(otis_t) :: c
                c%r = log(a%r)
                c%e = a%e / a%r
              end function log_o

              elemental function sin_o(a) result(c)
                type(otis_t), intent(in) :: a
                type(otis_t) :: c
                c%r = sin(a%r)
                c%e = cos(a%r) * a%e
              end function sin_o

              elemental function cos_o(a) result(c)
                type(otis_t), intent(in) :: a
                type(otis_t) :: c
                c%r = cos(a%r)
                c%e = -sin(a%r) * a%e
              end function cos_o

              elemental function abs_o(a) result(c)
                type(otis_t), intent(in) :: a
                type(otis_t) :: c
                c%r = abs(a%r)
                if (a%r < 0.0d0) then
                  c%e = -a%e
                else
                  c%e = a%e
                end if
              end function abs_o

              elemental function max_oo(a, b) result(c)
                type(otis_t), intent(in) :: a, b
                type(otis_t) :: c
                if (a%r >= b%r) then
                  c = a
                else
                  c = b
                end if
              end function max_oo

              elemental function max_or(a, b) result(c)
                type(otis_t), intent(in) :: a
                real(8), intent(in) :: b
                type(otis_t) :: c
                if (a%r >= b) then
                  c = a
                else
                  c = b
                end if
              end function max_or

              elemental function max_ro(a, b) result(c)
                real(8), intent(in) :: a
                type(otis_t), intent(in) :: b
                type(otis_t) :: c
                if (a >= b%r) then
                  c = a
                else
                  c = b
                end if
              end function max_ro

              elemental function min_oo(a, b) result(c)
                type(otis_t), intent(in) :: a, b
                type(otis_t) :: c
                if (a%r <= b%r) then
                  c = a
                else
                  c = b
                end if
              end function min_oo

              elemental function min_or(a, b) result(c)
                type(otis_t), intent(in) :: a
                real(8), intent(in) :: b
                type(otis_t) :: c
                if (a%r <= b) then
                  c = a
                else
                  c = b
                end if
              end function min_or

              elemental function min_ro(a, b) result(c)
                real(8), intent(in) :: a
                type(otis_t), intent(in) :: b
                type(otis_t) :: c
                if (a <= b%r) then
                  c = a
                else
                  c = b
                end if
              end function min_ro
            end module {self.name}
            """
        ).lstrip()
