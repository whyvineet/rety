# rety fixture: overload_arity.py
#
# @overload with an ambiguous or invalid call site.
# Checkers implement overload resolution differently, especially for:
#   - Calls that match no overload (should always error — but on which line?)
#   - Calls that match multiple overloads (ambiguous)
#   - The implementation function's relationship to the overloads
#
# Expected disagreement pattern:
#   Line 24: process(3.14) — float doesn't match int or str overload.
#            All checkers should flag this, but line numbers may differ:
#            some blame the call, some blame the argument expression.
#   Line 25: process(None) — None matches neither overload.
#            Similar to above.
#   Line 31: The implementation's body is unchecked by some checkers when
#            --check-untyped-defs is off (mypy default).

from typing import overload


@overload
def process(x: int) -> str: ...
@overload
def process(x: str) -> int: ...
def process(x: int | str) -> str | int:
    if isinstance(x, int):
        return str(x)
    return len(x)


# Valid calls
valid_str: str = process(42)      # int → str, fine
valid_int: int = process("hello") # str → int, fine

# Invalid calls — all checkers should flag these
bad_float = process(3.14)   # float matches neither overload
bad_none = process(None)    # None matches neither overload

# Wrong return type annotation
wrong_type: int = process(42)  # process(int) returns str, not int
