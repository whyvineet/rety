# rety fixture: gradual_any.py
#
# Demonstrates divergent Any-propagation behavior.
# mypy and Pyright differ on how far Any "infects" surrounding code.
# mypy is strict about Any assignments; Pyright/Pyrefly are more lenient in some
# paths but stricter in others.
#
# Expected disagreement pattern:
#   Line 22: assigning Any-typed result to a typed variable — checkers differ
#            on whether this is an error or silently allowed via gradual typing.
#   Line 26: calling a method on an Any-typed value — some checkers warn,
#            others treat it as safe under the gradual guarantee.

from typing import Any


def process(data: Any) -> str:
    # Returning data (of type Any) where str is expected.
    # mypy: accepted (Any is compatible with str under gradual typing)
    # Pyright: accepted (same reasoning)
    # Pyrefly: accepted
    # ty: accepted
    # → No disagreement here (all accept Any → str)
    return data.strip()


def caller() -> None:
    result = process(42)     # process accepts Any, 42 is fine

    # Assigning Any-typed return to a typed variable.
    # Checkers differ on strictness here.
    x: str = process(object())  # object is not str, but process returns Any

    # Any propagates: y is Any, so y.nonexistent_method() may or may not warn.
    y: Any = "hello"
    z = y.nonexistent_method()  # some checkers warn, others trust Any

    # Passing Any to a specifically-typed parameter.
    requires_int(y)  # y is Any — is this an error?


def requires_int(n: int) -> int:
    return n * 2
