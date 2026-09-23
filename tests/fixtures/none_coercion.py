# rety fixture: none_coercion.py
#
# None flowing into typed contexts — how checkers handle optional values
# that are used as if they were non-optional.
#
# Expected disagreement pattern:
#   Line 20: str(None) — produces "None" string, technically valid Python but
#            some checkers warn about it as a likely mistake.
#   Line 25: len(None) — None has no __len__; all checkers should flag this.
#   Line 28: item.strip() inside a comprehension — None has no .strip();
#            depends on whether checker sees through list comprehension types.

from __future__ import annotations

from typing import Optional


def stringify(x: int | None) -> str:
    # str() on None is legal Python but arguably a mistake
    # mypy: accepted (str(None) is valid Python)
    # Pyright/Pyrefly/ty: may warn or accept depending on strictness
    return str(x)


def list_with_nones() -> None:
    items: list[str | None] = ["a", None, "b", None, "c"]

    # len(None) — should be flagged by all, but line/message differ
    for item in items:
        n = len(item)  # item is str | None; None has no len()

    # Comprehension over Optional — should flag item.strip() when item is None
    upper = [item.strip() for item in items]  # item: str | None

    # Filtered comprehension — all checkers should NOT flag this
    filtered = [item.strip() for item in items if item is not None]  # correct


def optional_chaining(value: Optional[str]) -> Optional[int]:
    # Attribute access on Optional — should error
    length = value.strip()  # value might be None
    return len(length)
