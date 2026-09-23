# rety fixture: exception_handling.py
#
# Exception handling and implicit returns from try/except blocks.
# Try/except introduces non-obvious control flow that checkers analyze differently.
#
# Expected disagreement pattern:
#   Line 19: risky() — may return None implicitly (except branch has no return).
#            All checkers should flag the missing return, but line numbers vary:
#            some blame the `def` line, some blame the `pass` statement,
#            some blame the function exit.
#   Line 29: bare except — some checkers flag this as bad practice.
#   Line 42: exception variable scope — `e` goes out of scope after except block
#            in Python 3; checkers may or may not track this.

from __future__ import annotations

from typing import Optional


def risky() -> int:
    try:
        return int("not a number")
    except ValueError:
        pass  # implicit return None here — should be flagged
        # return -1  # this would fix it


def risky_with_finally() -> str:
    try:
        result = "hello"
    except Exception:
        result = "error"
    finally:
        pass
    return result  # is result always defined here? checkers differ


def bare_except_usage() -> str:
    try:
        return "ok"
    except:  # bare except — some checkers warn about this
        return "caught"


def exception_variable_scope() -> str:
    try:
        raise ValueError("test")
    except ValueError as e:
        msg = str(e)
    # After except block, `e` is deleted (Python 3 semantics)
    # `msg` is defined only if exception was raised — is it in scope?
    return msg  # some checkers flag: msg may be unbound


def catch_and_reraise(items: list[str]) -> list[int]:
    result = []
    for item in items:
        try:
            result.append(int(item))
        except (ValueError, TypeError) as e:
            raise RuntimeError(f"Failed to convert {item!r}") from e
    return result
