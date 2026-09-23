"""rety fixture: multiline_call.py

Calls whose arguments span several lines. Checkers disagree on where to
report an argument error: some blame the call expression on its first line,
others blame the offending argument on the argument's own line. The
alignment engine merges those through the shared enclosing Call node.

The three assignments at the end are unrelated one-line statements of the
same kind; diagnostics on them must never be merged.
"""


def process(items: list[int], label: str) -> None:
    pass


process(
    ["a", "b"],
    42,
)

process(
    list(map(str, [1, 2])),
    "label",
)

first = 1
second = "two"
third = 3.0
