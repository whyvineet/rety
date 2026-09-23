# rety fixture: narrowing_disagreement.py
#
# Type narrowing is one of the richest sources of checker disagreement.
# Checkers implement narrowing differently, especially for:
#   - isinstance checks with union types
#   - truthiness narrowing (if x:)
#   - assignment narrowing after conditionals
#
# Expected disagreement pattern:
#   Line 26: after `if isinstance(x, str)`, some checkers narrow `x` in the
#            else branch to `int`, others may keep it as `int | str`.
#   Line 41: after assignment `x = 42`, checkers differ on whether `x` is
#            narrowed to `Literal[42]`, `int`, or remains `int | str`.
#   Line 52: truthiness narrowing — some checkers narrow Optional[str] to str
#            after `if value:`, others require explicit `if value is not None:`.

from __future__ import annotations

from typing import Optional


def narrowing_isinstance(x: int | str) -> str:
    if isinstance(x, str):
        return x.upper()     # x: str — all checkers agree here
    else:
        # x should be int here (narrowed) — most checkers agree
        # but some may keep it as int | str in complex control flow
        length = x.bit_length()  # int method — error if x is still str
        return str(length)


def narrowing_assignment(x: int | str) -> int:
    if isinstance(x, str):
        x = len(x)  # x is now int (reassigned)
    # After reassignment: x should be int.
    # mypy: may or may not narrow after reassignment depending on flow
    # Pyright/Pyrefly: typically narrow aggressively
    return x  # should be int — is it?


def narrowing_truthiness(value: Optional[str]) -> str:
    if value:
        # value is str | "" here — some checkers narrow to str,
        # others to `str` excluding empty string (Literal behavior varies)
        return value.upper()
    # Implicit return None here — missing return for the falsy case
    return ""


def narrowing_none_check(items: list[Optional[str]]) -> list[str]:
    result = []
    for item in items:
        if item is not None:
            result.append(item)   # item: str — should be agreed
        # else: item is None, skipped
    return result


def narrowing_walrus(data: list[int]) -> Optional[int]:
    # Walrus operator narrowing — newer syntax, more variation in support
    if first := data[0] if data else None:
        return first  # first is int here? or int | None?
    return None
