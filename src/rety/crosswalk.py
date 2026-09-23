"""
rety error-code crosswalk table — v0.2 target, stub in v0.1.

This module maps checker-native error codes to cross-checker "code family" names.
A code family is a short string (e.g., "arg-type", "return-type", "missing-annotation")
that groups semantically equivalent codes across checkers.

v0.1 status:
    lookup_code_family() always returns None. The return type is locked here so
    the alignment engine's interface does not need to change when this is populated
    in v0.2.

v0.2 plan:
    - CROSSWALK dict maps (checker, code) -> code_family
    - Populated from a hand-curated TOML data file (data/crosswalk.toml), versioned
      per checker-version pair, not as a static Python constant
    - ty rule descriptions available via: ty explain rule --output-format json <code>
    - The crosswalk is used as a confidence booster in align.py, never as the sole
      clustering key

Contribution guide (v0.2+):
    To add an entry: edit data/crosswalk.toml. One line per mapping.
    Format: [[mapping]]\n checker = "mypy"\n code = "arg-type"\n family = "argument-type"
"""

from __future__ import annotations

# Crosswalk table — empty in v0.1, populated from data/crosswalk.toml in v0.2.
# Type: dict[(checker_name, code), code_family]
_CROSSWALK: dict[tuple[str, str], str] = {}


def lookup_code_family(checker: str, code: str | None) -> str | None:
    """
    Return the cross-checker code family for a checker-native error code.

    Args:
        checker: Checker name ("mypy", "pyright", "pyrefly", "ty").
        code:    Checker-native error/rule code, or None.

    Returns:
        A code family string (e.g., "argument-type"), or None if no mapping
        exists or code is None.

    Note:
        Always returns None in v0.1. Return type is locked so the alignment
        engine interface is stable for v0.2.
    """
    if code is None:
        return None
    return _CROSSWALK.get((checker, code))
