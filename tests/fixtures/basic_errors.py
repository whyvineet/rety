"""rety fixture: basic_errors.py

Three unambiguous errors that every checker reports. Used to capture real
checker output for the adapter parser tests in tests/adapters/.
"""


def greet(name: str) -> int:
    return name  # returns str where int is declared


x: int = "a"  # str assigned to an int-annotated variable
y = undefined_name  # name is never defined
