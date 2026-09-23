"""
Unit tests for PyreflyAdapter.parse().

Note: Pyrefly's JSON schema is young (1.0 released May 2026) and the field
names in these tests reflect the captured fixture, which itself is a best-guess
placeholder until real Pyrefly output is captured in Phase 0.

When Phase 0 updates tests/fixtures/captured/pyrefly/ with real output, these
tests should be updated to match actual field names and values.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rety.adapters.pyrefly import PyreflyAdapter
from rety.schema import RawInvocation, Severity

CAPTURED_DIR = Path(__file__).parent.parent / "fixtures" / "captured" / "pyrefly"

ADAPTER = PyreflyAdapter()


def _make_raw(stdout: str, version: str = "1.3.0") -> RawInvocation:
    return RawInvocation(
        checker="pyrefly",
        returncode=1,
        stdout=stdout,
        stderr="",
        duration_ms=120.0,
        version=version,
    )


# ---------------------------------------------------------------------------
# Captured fixture test
# ---------------------------------------------------------------------------


def test_parse_from_captured_fixture() -> None:
    """
    Parse captured Pyrefly output for untyped_function.py.

    ⚠ The captured fixture is a placeholder — field names are guesses.
    Update this test (and the fixture) after Phase 0 captures real output.
    """
    content = (CAPTURED_DIR / "untyped_function.json").read_text()
    raw = _make_raw(content)

    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.checker == "pyrefly"
    assert d.severity == Severity.error
    assert d.start_line == 22
    assert "str" in d.message or "int" in d.message


# ---------------------------------------------------------------------------
# Format variant tests
# ---------------------------------------------------------------------------


def test_parse_json_array_format() -> None:
    """Pyrefly JSON array format (list of diagnostic objects)."""
    data = [
        {
            "severity": "error",
            "path": "tests/fixtures/foo.py",
            "line": 10,
            "col": 5,
            "end_line": 10,
            "end_col": 20,
            "code": "bad-argument-type",
            "message": "Expected `int`, got `str`",
        }
    ]
    raw = _make_raw(json.dumps(data))
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.severity == Severity.error
    assert d.start_line == 10
    assert d.start_col == 5


def test_parse_json_object_with_diagnostics_key() -> None:
    """Pyrefly JSON object with 'diagnostics' key."""
    data = {
        "diagnostics": [
            {
                "severity": "warning",
                "path": "foo.py",
                "line": 5,
                "col": 1,
                "code": "possibly-undefined",
                "message": "Variable might be undefined",
            }
        ]
    }
    raw = _make_raw(json.dumps(data))
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    assert diagnostics[0].severity == Severity.warning


def test_parse_zero_col_becomes_none() -> None:
    """Column value of 0 must become None (missing data, not column 0)."""
    data = [{"severity": "error", "path": "f.py", "line": 1, "col": 0, "message": "e"}]
    raw = _make_raw(json.dumps(data))
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    assert diagnostics[0].start_col is None


def test_parse_empty_output_returns_empty() -> None:
    raw = _make_raw("")
    assert ADAPTER.parse(raw) == []


def test_parse_malformed_json_returns_empty_with_warning(
    recwarn: pytest.WarningsChecker,
) -> None:
    """Malformed JSON (no valid JSON at all) returns [] with a RuntimeWarning."""
    raw = _make_raw("not json at all\nalso not json")
    result = ADAPTER.parse(raw)
    # Should be empty (neither line is JSON)
    assert result == []
    # Should have warned
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)


def test_parse_checker_name_is_pyrefly() -> None:
    data = [{"severity": "error", "path": "f.py", "line": 1, "message": "e"}]
    raw = _make_raw(json.dumps(data))
    for d in ADAPTER.parse(raw):
        assert d.checker == "pyrefly"


def test_parse_raw_is_per_diagnostic_subobject() -> None:
    """raw field must be the per-diagnostic dict, not the full document."""
    diag = {"severity": "error", "path": "f.py", "line": 1, "message": "test error"}
    data = [diag]
    raw = _make_raw(json.dumps(data))
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    raw_parsed = json.loads(diagnostics[0].raw)
    assert raw_parsed["message"] == "test error"
