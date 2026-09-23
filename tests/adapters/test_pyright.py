"""
Unit tests for PyrightAdapter.parse().

Critical invariant under test: Pyright uses 0-indexed LSP-style line/character
offsets. The adapter must convert to 1-indexed before returning diagnostics.
Off-by-one errors here are the most common normalization bug when comparing
Pyright output against mypy/Pyrefly/ty.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rety.adapters.pyright import PyrightAdapter
from rety.schema import RawInvocation, Severity

CAPTURED_DIR = Path(__file__).parent.parent / "fixtures" / "captured" / "pyright"

ADAPTER = PyrightAdapter()


def _make_raw(stdout: str, version: str = "1.1.380") -> RawInvocation:
    return RawInvocation(
        checker="pyright",
        returncode=1,
        stdout=stdout,
        stderr="",
        duration_ms=380.0,
        version=version,
    )


# ---------------------------------------------------------------------------
# Index conversion tests — THE critical invariant for Pyright
# ---------------------------------------------------------------------------


def test_parse_0indexed_converted_to_1indexed() -> None:
    """
    Pyright's 0-indexed ranges must be converted to 1-indexed.

    This is the most important invariant: Pyright's "line": 0 means line 1
    in 1-indexed terms. A silent failure here would cause every Pyright
    diagnostic to align to the wrong line vs. mypy/Pyrefly/ty.
    """
    doc = {
        "version": "1.1.380",
        "generalDiagnostics": [{
            "file": "/path/to/file.py",
            "severity": "error",
            "message": "Type mismatch",
            "rule": "reportArgumentType",
            "range": {
                "start": {"line": 0, "character": 0},   # 0-indexed → should become line 1, col 1
                "end":   {"line": 0, "character": 10},  # 0-indexed → should become line 1, col 11
            }
        }],
        "summary": {"filesAnalyzed": 1, "errorCount": 1, "warningCount": 0, "informationCount": 0}
    }
    raw = _make_raw(json.dumps(doc))
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.start_line == 1    # 0 + 1
    assert d.start_col == 1     # 0 + 1
    assert d.end_line == 1      # 0 + 1
    assert d.end_col == 11      # 10 + 1


def test_parse_line_5_in_0indexed_becomes_6() -> None:
    """A non-zero 0-indexed line should produce the correct 1-indexed result."""
    doc = {
        "version": "1.1.380",
        "generalDiagnostics": [{
            "file": "/path/to/file.py",
            "severity": "warning",
            "message": "Unused variable",
            "rule": "reportUnusedVariable",
            "range": {
                "start": {"line": 5, "character": 4},
                "end":   {"line": 5, "character": 8},
            }
        }],
        "summary": {}
    }
    raw = _make_raw(json.dumps(doc))
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.start_line == 6    # 5 + 1
    assert d.start_col == 5     # 4 + 1
    assert d.end_col == 9       # 8 + 1


# ---------------------------------------------------------------------------
# Captured fixture test
# ---------------------------------------------------------------------------


def test_parse_from_captured_fixture() -> None:
    """Parse the real (captured) Pyright output for untyped_function.py."""
    content = (CAPTURED_DIR / "untyped_function.json").read_text()
    raw = _make_raw(content)

    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.checker == "pyright"
    assert d.severity == Severity.error
    # Pyright output has line 21 (0-indexed) → should become 22 (1-indexed)
    assert d.start_line == 22
    assert d.code == "reportOperatorIssue"


# ---------------------------------------------------------------------------
# Schema and structure tests
# ---------------------------------------------------------------------------


def test_parse_empty_output_returns_empty() -> None:
    raw = _make_raw("")
    assert ADAPTER.parse(raw) == []


def test_parse_no_diagnostics_returns_empty() -> None:
    doc = {
        "version": "1.1.380",
        "generalDiagnostics": [],
        "summary": {"filesAnalyzed": 1, "errorCount": 0}
    }
    raw = _make_raw(json.dumps(doc))
    assert ADAPTER.parse(raw) == []


def test_parse_rule_nullable_becomes_none() -> None:
    """Pyright's 'rule' field is nullable — None must be preserved, not converted to 'None'."""
    doc = {
        "version": "1.1.380",
        "generalDiagnostics": [{
            "file": "/path/to/file.py",
            "severity": "error",
            "message": "Unknown error",
            "rule": None,
            "range": {
                "start": {"line": 3, "character": 0},
                "end":   {"line": 3, "character": 5},
            }
        }],
        "summary": {}
    }
    raw = _make_raw(json.dumps(doc))
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    assert diagnostics[0].code is None


def test_parse_raw_field_is_per_diagnostic_subobject() -> None:
    """
    The raw field must be the per-diagnostic JSON object, not the full document.

    Storing the full document (with all diagnostics repeated per row) wastes
    space proportionally to the number of diagnostics. This test verifies the
    correct behavior.
    """
    diag_obj = {
        "file": "/path/to/file.py",
        "severity": "error",
        "message": "Test",
        "rule": "reportTest",
        "range": {
            "start": {"line": 0, "character": 0},
            "end":   {"line": 0, "character": 5},
        }
    }
    doc = {"version": "1.1.380", "generalDiagnostics": [diag_obj], "summary": {}}
    raw = _make_raw(json.dumps(doc))
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    # The raw field should parse to the diag_obj, not the whole document
    raw_parsed = json.loads(diagnostics[0].raw)
    assert "generalDiagnostics" not in raw_parsed
    assert raw_parsed["message"] == "Test"


def test_parse_severity_information_mapped_correctly() -> None:
    """Pyright uses 'information' not 'note' — must map to Severity.information."""
    doc = {
        "version": "1.1.380",
        "generalDiagnostics": [{
            "file": "/path/to/file.py",
            "severity": "information",
            "message": "Info message",
            "rule": "reportSomething",
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}}
        }],
        "summary": {}
    }
    raw = _make_raw(json.dumps(doc))
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    assert diagnostics[0].severity == Severity.information


def test_parse_malformed_json_returns_empty() -> None:
    """Malformed JSON output (e.g., if Pyright crashes) must not raise."""
    raw = _make_raw("this is not json at all")
    result = ADAPTER.parse(raw)
    assert result == []


def test_parse_checker_name_is_pyright() -> None:
    doc = {
        "version": "1.1.380",
        "generalDiagnostics": [{
            "file": "/f.py", "severity": "error", "message": "e",
            "rule": "r", "range": {"start": {"line": 0, "character": 0},
                                    "end":   {"line": 0, "character": 5}}
        }],
        "summary": {}
    }
    raw = _make_raw(json.dumps(doc))
    for d in ADAPTER.parse(raw):
        assert d.checker == "pyright"
