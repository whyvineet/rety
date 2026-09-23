"""
Unit tests for TyAdapter.parse() — specifically _parse_concise().

⚠ IMPORTANT: The concise format regex in rety/adapters/ty.py was derived from
ty's format DESCRIPTION, not from verified real output. These tests use an
ASSUMED format. Before relying on this adapter in production:

    1. Run: ty check --output-format concise tests/fixtures/untyped_function.py
    2. Save output to: tests/fixtures/captured/ty/untyped_function.txt
    3. Verify _CONCISE_RE matches the actual output
    4. Update this test file and the fixture accordingly

If the format doesn't match, the adapter will emit RuntimeWarnings (which these
tests also check for — an unmatched warning is itself a signal to go update the regex).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rety.adapters.ty import TyAdapter
from rety.schema import RawInvocation, Severity

CAPTURED_DIR = Path(__file__).parent.parent / "fixtures" / "captured" / "ty"

ADAPTER = TyAdapter()


def _make_raw(stdout: str, version: str = "0.0.7") -> RawInvocation:
    return RawInvocation(
        checker="ty",
        returncode=1,
        stdout=stdout,
        stderr="",
        duration_ms=45.0,
        version=version,
    )


# ---------------------------------------------------------------------------
# Captured fixture test
# ---------------------------------------------------------------------------


def test_parse_from_captured_fixture() -> None:
    """
    Parse captured ty output for untyped_function.py.

    ⚠ The captured fixture contains ASSUMED format output, not real ty output.
    This test will fail if the regex doesn't match. That failure is intentional
    signal to go capture real output and update the regex.
    """
    content = (CAPTURED_DIR / "untyped_function.txt").read_text()
    raw = _make_raw(content)

    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.checker == "ty"
    assert d.severity == Severity.error
    assert d.start_line == 22
    assert d.code is not None  # [unsupported-operator] is expected


# ---------------------------------------------------------------------------
# Concise format parsing tests (using assumed format)
# ---------------------------------------------------------------------------


def test_parse_error_with_code() -> None:
    """Parse a diagnostic line with [code] suffix."""
    line = "src/foo.py:10:5: error Missing return statement [return-value]"
    raw = _make_raw(line)
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.severity == Severity.error
    assert d.start_line == 10
    assert d.start_col == 5
    assert d.code == "return-value"
    assert "Missing return statement" in d.message


def test_parse_warning_with_code() -> None:
    line = "src/bar.py:3:1: warning Unused import [unused-import]"
    raw = _make_raw(line)
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.severity == Severity.warning
    assert d.start_line == 3
    assert d.code == "unused-import"


def test_parse_no_code_suffix() -> None:
    """A line without [code] bracket should parse with code=None."""
    line = "src/foo.py:7:2: error Some error without a code"
    raw = _make_raw(line)
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    assert diagnostics[0].code is None
    assert diagnostics[0].message == "Some error without a code"


def test_parse_end_positions_are_always_none() -> None:
    """ty's concise format never emits end positions — must be None, not 0."""
    line = "src/foo.py:5:3: error Something [some-rule]"
    raw = _make_raw(line)
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.end_line is None
    assert d.end_col is None


def test_parse_multiple_lines() -> None:
    """Multiple diagnostic lines each become a separate NormalizedDiagnostic."""
    output = "\n".join([
        "src/a.py:1:1: error First error [rule-a]",
        "src/b.py:5:3: warning Second warning [rule-b]",
        "src/a.py:10:1: note Third note [rule-c]",
    ])
    raw = _make_raw(output)
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 3
    assert diagnostics[0].start_line == 1
    assert diagnostics[1].start_line == 5
    assert diagnostics[2].start_line == 10
    assert diagnostics[2].severity == Severity.note


def test_parse_empty_lines_skipped() -> None:
    output = "src/foo.py:1:1: error First [rule]\n\n   \nsrc/foo.py:2:1: error Second [rule2]"
    raw = _make_raw(output)
    diagnostics = ADAPTER.parse(raw)
    assert len(diagnostics) == 2


def test_parse_empty_output_returns_empty() -> None:
    raw = _make_raw("")
    assert ADAPTER.parse(raw) == []


# ---------------------------------------------------------------------------
# RuntimeWarning for unmatched lines
# ---------------------------------------------------------------------------


def test_parse_unmatched_lines_emit_warning(recwarn: pytest.WarningsChecker) -> None:
    """
    Lines that don't match _CONCISE_RE should emit a RuntimeWarning naming the
    ty adapter and suggesting to update the regex.
    """
    raw = _make_raw("this is not a valid ty output line at all")
    result = ADAPTER.parse(raw)
    assert result == []
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)
    warning_text = str(recwarn.list[0].message)
    assert "ty" in warning_text.lower() or "concise" in warning_text.lower()


def test_parse_partial_match_warns_and_parses_valid(recwarn: pytest.WarningsChecker) -> None:
    """Valid lines parse correctly even when mixed with invalid lines."""
    output = "\n".join([
        "src/ok.py:5:1: error Valid error [rule]",
        "this is garbage and should not parse",
        "src/ok2.py:10:2: warning Another warning [r2]",
    ])
    raw = _make_raw(output)
    diagnostics = ADAPTER.parse(raw)

    # Should have parsed the 2 valid lines
    assert len(diagnostics) == 2
    # Should have warned about the garbage line
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


def test_checker_name_is_ty() -> None:
    raw = _make_raw("src/f.py:1:1: error Test [r]")
    for d in ADAPTER.parse(raw):
        assert d.checker == "ty"


def test_raw_is_original_text_line() -> None:
    """raw must be the original text line, not JSON."""
    original_line = "src/foo.py:1:1: error Something [rule]"
    raw = _make_raw(original_line)
    diagnostics = ADAPTER.parse(raw)
    assert len(diagnostics) == 1
    assert diagnostics[0].raw == original_line
