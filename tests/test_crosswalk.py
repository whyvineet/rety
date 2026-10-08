"""Tests for the error-code crosswalk (rety/crosswalk.py, rety/data/crosswalk.toml)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rety.crosswalk import _load, lookup_code_family

CAPTURED = Path(__file__).parent / "fixtures" / "captured"


def test_known_codes_map_to_a_shared_family() -> None:
    assert lookup_code_family("mypy", "arg-type") == "argument-type"
    assert lookup_code_family("pyright", "reportArgumentType") == "argument-type"
    assert lookup_code_family("pyrefly", "bad-argument-type") == "argument-type"
    assert lookup_code_family("ty", "invalid-argument-type") == "argument-type"


def test_unknown_or_missing_code_has_no_family() -> None:
    assert lookup_code_family("mypy", "misc") is None
    assert lookup_code_family("mypy", None) is None
    assert lookup_code_family("nope", "arg-type") is None


@pytest.mark.parametrize(("checker", "code"), sorted(_load()))
def test_every_mapping_is_backed_by_captured_output(checker: str, code: str) -> None:
    """The table must only contain codes the checker really emitted."""
    captures = "".join(
        p.read_text(encoding="utf-8") for p in (CAPTURED / checker).iterdir() if p.is_file()
    )
    assert code in captures, f"{checker} code {code!r} never appears in its captures"
