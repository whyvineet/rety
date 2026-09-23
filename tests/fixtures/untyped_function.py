"""rety fixture: untyped_function.py

Demonstrates the core inference gap around unannotated functions. What each
checker reports here depends heavily on its configuration:

- mypy skips the body of an unannotated function by default and reports
  nothing. With strict settings (as in this repository's pyproject.toml) it
  reports the missing annotation on the def and the untyped call.
- Pyright, Pyrefly and ty infer through the unannotated body. In practice
  none of them flags the str + int call below, because ``x + y`` on unknown
  operands is accepted. Pyrefly with strict-style settings reports the
  missing parameter and return annotations instead.

Do not start a comment line with a checker name followed by a colon: Pyright
parses ``# pyright:`` comments as directives and reports unknown ones.
Real captured output lives in tests/fixtures/captured/<checker>/.
"""


def add_items(x, y):
    return x + y  # no type annotations


result = add_items("hello", 42)  # str + int at the call site
print(result)
