# rety fixture: assignment_narrowing.py
#
# Assignment narrowing and type widening after reassignment.
# Different checkers take different views on:
#   - What type `x` has after `x = 42` when x was `int | str`
#   - Whether Literal types are inferred from literals
#   - Whether reassignment in a branch narrows for subsequent code
#
# Expected disagreement pattern:
#   Line 21: reveal_type(x) after `x = 42` — some reveal Literal[42], some int
#   Line 30: reveal_type after conditional reassignment — significant disagreement
#   Line 37: the assignment `y: int = lst[0]` after appending str — should error

from __future__ import annotations


def assignment_basic() -> None:
    x: int | str = "hello"
    x = 42
    reveal_type(x)  # mypy: int, pyright: int, pyrefly: ?, ty: ?
    # All should agree x is int at this point, but Literal vs int varies.


def assignment_conditional(flag: bool) -> None:
    x: int | str = "hello"
    if flag:
        x = 42    # reassign to int
    # After the if block: x is int | str (flag might be False)
    # reveal_type here should be int | str — do all checkers agree?
    reveal_type(x)


def list_mutation() -> None:
    lst: list[int | str] = [1, 2, 3]
    lst.append("text")     # valid: list[int | str] accepts str

    # Accessing an element — the type is int | str
    item = lst[0]
    reveal_type(item)      # should be int | str

    # Assignment to narrower type — should error
    y: int = lst[0]        # lst[0] is int | str, not int


def repeated_assignment() -> str:
    result: str = ""
    for i in range(5):
        result = result + str(i)   # result stays str — should be fine
    return result
