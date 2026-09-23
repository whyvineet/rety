# rety fixture: starred_args.py
#
# Starred arguments (*args, **kwargs) passed to typed functions.
# Checkers differ significantly on:
#   - Whether *tuple unpacking is typed precisely enough for the callee
#   - Whether **dict spreading is checked against TypedDict
#   - Runtime-valid but statically-ambiguous patterns
#
# Expected disagreement pattern:
#   Line 20: takes_two(*args) — args is tuple[int, str], should match.
#            mypy: sometimes accepts, sometimes flags depending on version.
#            Pyright: typically accepts typed tuples precisely.
#            Pyrefly/ty: behavior unverified — capture in Phase 1.
#   Line 22: takes_two(*dynamic_args) — args is tuple[Any, ...], should warn.

from __future__ import annotations

from typing import Any


def takes_two(a: int, b: str) -> str:
    return str(a) + b


def takes_kwargs(**kwargs: str) -> dict[str, str]:
    return dict(kwargs)


def starred_tuple() -> None:
    # Fixed-length tuple — precisely typed
    args: tuple[int, str] = (1, "hello")
    takes_two(*args)          # should work — precise type known

    # Homogeneous tuple — imprecise length
    dynamic_args: tuple[int, ...] = (1, 2, 3)
    takes_two(*dynamic_args)  # ambiguous — checkers disagree


def starred_dict() -> None:
    kwargs: dict[str, str] = {"a": "value"}
    takes_kwargs(**kwargs)    # should be fine

    mixed: dict[str, Any] = {"key": 42}
    takes_kwargs(**mixed)     # Any values — some checkers flag, some don't


def pass_through(*args: int, **kwargs: str) -> None:
    # Forwarding args/kwargs — type information preservation varies
    takes_two(*args, **kwargs)   # likely a type error: args is int..., not (int, str)
