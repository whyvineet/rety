"""
rety error-code crosswalk.

Maps checker-native error codes to cross-checker "code family" names. A code
family is a short string (e.g. "argument-type", "return-type") that groups
codes meaning the same kind of problem across checkers.

The table lives in rety/data/crosswalk.toml. Every mapping there is backed by
real captured checker output (see the comments in that file); codes too broad
to belong to one family are left out on purpose.

How it is used:
    The alignment engine sets NormalizedDiagnostic.code_family from this table
    and treats a shared family as extra evidence when scoring a cluster: an
    alignment signal, and a LOW -> MEDIUM boost for cross-checker pairs that
    were otherwise joined only by a shared statement or --line-tolerance. It
    is never the sole clustering key.

Contribution guide:
    Add a code to an existing [family] in data/crosswalk.toml, or add a new
    family, with a comment naming the fixture and line whose captured output
    shows the codes together. A (checker, code) pair may appear in only one
    family; tests/test_crosswalk.py enforces that.
"""

from __future__ import annotations

import tomllib
from functools import cache
from importlib import resources


@cache
def _load() -> dict[tuple[str, str], str]:
    """Load data/crosswalk.toml into {(checker, code): family}."""
    text = resources.files("rety").joinpath("data/crosswalk.toml").read_text(encoding="utf-8")
    table: dict[tuple[str, str], str] = {}
    for family, checkers in tomllib.loads(text).items():
        for checker, codes in checkers.items():
            for code in codes:
                key = (checker, code)
                if key in table:
                    raise ValueError(
                        f"crosswalk.toml: {checker} code {code!r} is in both "
                        f"{table[key]!r} and {family!r}"
                    )
                table[key] = family
    return table


def lookup_code_family(checker: str, code: str | None) -> str | None:
    """
    Return the cross-checker code family for a checker-native error code.

    Args:
        checker: Checker name ("mypy", "pyright", "pyrefly", "ty").
        code:    Checker-native error/rule code, or None.

    Returns:
        A code family string (e.g. "argument-type"), or None if no mapping
        exists or code is None.
    """
    if code is None:
        return None
    return _load().get((checker, code))
