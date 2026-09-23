# rety fixture: dataclass_fields.py
#
# Dataclass field type violations.
# Dataclasses are well-supported by all four checkers, but they differ on:
#   - Field assignment type checking (strict vs. lenient)
#   - Default value compatibility
#   - ClassVar vs. instance field distinction
#
# Expected disagreement pattern:
#   Line 23: p.label = None — label is str, not Optional[str]; all should flag.
#   Line 24: p.x = "not a float" — str ≠ float; all should flag.
#   Line 32: ClassVar mutation — some checkers flag this, some don't.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional


@dataclass
class Point:
    x: float
    y: float
    label: str = "origin"
    tags: list[str] = field(default_factory=list)

    # ClassVar — not an instance field
    count: ClassVar[int] = 0


def use_point() -> None:
    p = Point(1.0, 2.0)

    # Type violations — all checkers should flag these
    p.label = None           # str, not Optional[str]
    p.x = "not a float"     # str, not float

    # ClassVar assignment on instance — behavior varies
    p.count = 1              # some checkers flag: ClassVar shouldn't be set on instance
    Point.count = 1          # this is correct

    # tags field accepts str items
    p.tags.append("valid")
    p.tags.append(42)        # int, not str — should be flagged


@dataclass
class Config:
    host: str
    port: int = 8080
    debug: Optional[bool] = None


def make_config() -> None:
    c = Config(host="localhost", port="8080")  # port: str, not int — should error
    c2 = Config(host=42)                       # host: int, not str — should error
