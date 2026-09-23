# rety fixture: lambda_inference.py
#
# Lambda type inference and Callable type compatibility.
# Lambdas are unannotated by definition — how each checker infers their types
# and checks them against Callable annotations is a rich source of disagreement.
#
# Expected disagreement pattern:
#   Line 20: triple = lambda x: x * 3 — unannotated lambda.
#            mypy: likely infers x as Any, result as Any
#            Pyright: may infer int → int from context
#            Pyrefly/ty: behavior varies
#   Line 21: result: str = triple(5) — assigning int-returning lambda to str var.
#            Flags depend on whether x and result type are inferred.

from __future__ import annotations

from typing import Callable


# Annotated lambda via Callable type hint
double: Callable[[int], int] = lambda x: x * 2
square: Callable[[int], int] = lambda x: x ** 2  # ** returns int for int inputs

# Unannotated lambda — type inference varies
triple = lambda x: x * 3

# Using unannotated lambda where a type is expected
result: str = triple(5)      # triple(5) is probably int, not str

# Lambda with wrong type for annotated Callable
wrong_lambda: Callable[[int], str] = lambda x: x  # returns int, not str

# Lambda used as argument to a typed function
def apply(fn: Callable[[int], str], x: int) -> str:
    return fn(x)

apply(lambda x: str(x), 42)     # correct — returns str
apply(lambda x: x + 1, 42)      # wrong — returns int, not str
apply(lambda x: x * "a", 42)    # int * str — type error in lambda body


# Default argument capture — a subtle gotcha
funcs: list[Callable[[], int]] = []
for i in range(5):
    funcs.append(lambda: i)  # classic late-binding; not a type error but worth noting

# Lambda with *args
variadic: Callable[..., str] = lambda *args: str(sum(args))  # sum needs Iterable[number]
