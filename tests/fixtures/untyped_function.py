# rety fixture: untyped_function.py
#
# Demonstrates the core mypy-vs-others inference gap:
# mypy (by default, without --check-untyped-defs) skips the body of unannotated
# functions entirely. Pyright, Pyrefly, and ty infer types aggressively and flag
# issues even in unannotated code.
#
# Expected disagreement pattern:
#   mypy:    0 errors on add_items body (skips unannotated functions by default)
#            1 error on line 10 (call-site: Unsupported operand types for +)
#   pyright: error on line 3 (or 10, depending on inference) — str + int
#   pyrefly: error on line 3 (or 10) — similar to pyright
#   ty:      error on line 3 (or 10) — similar to pyright
#
# The exact line numbers depend on whether each checker blames the function body
# or the call site. This is precisely the kind of adjacent-range disagreement
# the alignment engine's AST-node merge pass is designed to handle.

def add_items(x, y):
    return x + y  # no type annotations — mypy skips this body by default


result = add_items("hello", 42)  # str + int: type error at the call site
print(result)
