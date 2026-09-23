# rety fixture: callable_protocol.py
#
# Protocol structural compatibility for callables.
# Checkers differ on Protocol matching, especially for:
#   - Callable Protocols vs plain Callable types
#   - Covariance/contravariance in Protocol method signatures
#   - Whether a concrete function is compatible with a Protocol
#
# Expected disagreement pattern:
#   Line 31: use_callable(my_func) — my_func matches HasCall structurally.
#            All checkers should accept this, but may emit different notes.
#   Line 32: use_callable(wrong_func) — wrong_func returns None, not str.
#            All should flag this, but line blame varies.
#   Line 35: lambda with Protocol — checkers differ on lambda Protocol compatibility.

from __future__ import annotations

from typing import Protocol


class HasCall(Protocol):
    def __call__(self, x: int) -> str: ...


def use_callable(fn: HasCall) -> str:
    return fn(42)


def my_func(x: int) -> str:
    return str(x)


def wrong_func(x: int) -> None:
    print(x)


# Should work — my_func satisfies HasCall
use_callable(my_func)

# Should fail — wrong_func returns None, not str
use_callable(wrong_func)

# Lambda — Protocol compatibility with lambdas varies significantly
use_callable(lambda x: str(x))  # should work
use_callable(lambda x: x)       # int, not str — should fail
