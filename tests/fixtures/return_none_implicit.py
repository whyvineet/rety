# rety fixture: return_none_implicit.py
#
# Functions with annotated return types but implicit None return paths.
# This is a canonical disagreement source: checkers differ on whether an
# implicit `return None` at the end of a function body violates a non-None
# return annotation.
#
# Expected disagreement pattern:
#   get_value(): annotated -> int, but has a path that falls through.
#     mypy: "Missing return statement" — flags the function
#     Pyright: "Function with declared return type 'int' must return value"
#     Pyrefly/ty: may flag on the function def or the missing-return path
#     All four should flag this, but the reported line varies significantly:
#     some blame the `def` line, some blame the last statement, some blame
#     the missing `else` branch.
#
#   format_items(): returns Optional[str] — all None paths are valid.
#     No checker should flag this. Useful as a true-negative control.
#
#   conditional_return(): pathological case — checkers strongly disagree.

from __future__ import annotations

from typing import Optional


def get_value(flag: bool) -> int:
    if flag:
        return 42
    # Implicit return None here — should be flagged by all checkers
    # (return type is int, not Optional[int])


def another_missing(items: list[str]) -> str:
    for item in items:
        if item.startswith("x"):
            return item
    # Falls through if no item starts with "x"
    # Some checkers flag this, some don't (depends on loop analysis)


def format_items(items: list[str]) -> Optional[str]:
    if not items:
        return None      # valid: Optional[str]
    return items[0]      # valid: str is Optional[str]


def conditional_return(x: int) -> str:
    # Multiple return paths — checkers disagree on completeness analysis
    if x > 0:
        return "positive"
    elif x < 0:
        return "negative"
    elif x == 0:
        return "zero"
    # Logically exhaustive, but checkers may not prove it
    # mypy: may or may not flag (depends on version)
    # Pyright: typically flags "not all code paths return a value"
