# rety fixture: generic_variance.py
#
# Generic type variance — a classic source of checker disagreement.
# Python's type system uses invariant generics by default for mutable containers.
# Checkers differ on:
#   - Whether Box[int] is assignable to Box[float] (invariant: no)
#   - Whether list[int] is assignable to list[float] (invariant: no)
#   - TypeVar bounds and covariance annotation
#
# Expected disagreement pattern:
#   Line 30: Box[float] = get_number() — Box is invariant, Box[int] ≠ Box[float]
#            All checkers should flag this, but the error message and line may differ.
#   Line 31: list[float] from list[int] — similar invariance issue
#   Line 38: covariant BoxOut — should be acceptable in read-only context

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class Box(Generic[T]):
    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value

    def set(self, value: T) -> None:
        self.value = value


class BoxOut(Generic[T_co]):
    """Read-only box — covariant in T_co."""
    def __init__(self, value: T_co) -> None:
        self._value = value

    def get(self) -> T_co:
        return self._value


def get_number() -> Box[int]:
    return Box(42)


# Invariance violations — all checkers should flag these
result_float: Box[float] = get_number()   # Box[int] is not Box[float]

int_list: list[int] = [1, 2, 3]
float_list: list[float] = int_list        # list[int] is not list[float]

# Covariant — should be acceptable (BoxOut[int] is BoxOut[float] because covariant)
def get_number_out() -> BoxOut[int]:
    return BoxOut(42)

result_out: BoxOut[float] = get_number_out()  # OK with covariance
