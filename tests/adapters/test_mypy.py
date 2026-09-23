"""
Unit tests for MypyAdapter.parse().

All tests use captured output fixtures from tests/fixtures/captured/mypy/ —
no subprocess invocations, no mypy installation required.

Each test constructs a RawInvocation directly from file content, then calls
parse() and asserts on the resulting NormalizedDiagnostic list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rety.adapters.mypy import MypyAdapter
from rety.schema import RawInvocation, Severity

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CAPTURED_DIR = Path(__file__).parent.parent / "fixtures" / "captured" / "mypy"

ADAPTER = MypyAdapter()


def _make_raw(stdout: str, version: str = "1.x.x") -> RawInvocation:
    return RawInvocation(
        checker="mypy",
        returncode=1,  # non-zero is normal when diagnostics exist
        stdout=stdout,
        stderr="",
        duration_ms=500.0,
        version=version,
    )


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------


def test_parse_single_error_from_captured_fixture() -> None:
    """Parse real (captured) mypy JSON-lines output for untyped_function.py."""
    content = (CAPTURED_DIR / "untyped_function.jsonl").read_text()
    raw = _make_raw(content, version="1.11.2")

    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.checker == "mypy"
    assert d.checker_version == "1.11.2"
    assert d.severity == Severity.error
    assert d.start_line == 22
    assert d.code == "operator"
    assert "str" in d.message and "int" in d.message
    assert d.raw  # raw is non-empty


def test_parse_preserves_end_positions() -> None:
    """end_line and end_col are preserved when present in mypy output."""
    jsonl = json.dumps({
        "file": "foo.py",
        "line": 10,
        "column": 5,
        "end_line": 10,
        "end_column": 20,
        "severity": "error",
        "message": "test error",
        "code": "misc",
    })
    raw = _make_raw(jsonl)
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.start_line == 10
    assert d.start_col == 5
    assert d.end_line == 10
    assert d.end_col == 20


def test_parse_zero_columns_become_none() -> None:
    """
    column=0 and end_column=0 must be converted to None.

    Mypy sometimes emits 0 for columns it doesn't know. We must not let 0
    silently degrade recall by pretending the diagnostic is at column 0.
    """
    jsonl = json.dumps({
        "file": "foo.py",
        "line": 5,
        "column": 0,         # should become None
        "end_line": 0,       # should become None
        "end_column": 0,     # should become None
        "severity": "warning",
        "message": "some warning",
        "code": None,
    })
    raw = _make_raw(jsonl)
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.start_col is None
    assert d.end_line is None
    assert d.end_col is None


def test_parse_empty_output_returns_empty_list() -> None:
    raw = _make_raw("")
    assert ADAPTER.parse(raw) == []


def test_parse_success_output_zero_diagnostics() -> None:
    """mypy with no errors outputs 'Success: no issues found in N source files' as stderr."""
    raw = _make_raw("", version="1.11.2")
    raw_with_success = RawInvocation(
        checker="mypy",
        returncode=0,
        stdout="",
        stderr="Success: no issues found in 3 source files",
        duration_ms=200.0,
        version="1.11.2",
    )
    assert ADAPTER.parse(raw_with_success) == []


def test_parse_severity_note() -> None:
    """Notes are parsed correctly and mapped to Severity.note."""
    jsonl = json.dumps({
        "file": "bar.py",
        "line": 3,
        "column": 1,
        "severity": "note",
        "message": "See: https://mypy.rtfd.io/...",
        "code": None,
    })
    raw = _make_raw(jsonl)
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 1
    assert diagnostics[0].severity == Severity.note


# ---------------------------------------------------------------------------
# Error tolerance tests (known mypy bugs)
# ---------------------------------------------------------------------------


def test_parse_tolerates_non_json_lines(recwarn: pytest.WarningsChecker) -> None:
    """
    Non-JSON lines (syntax-error fallback) must be skipped with a RuntimeWarning,
    not raise an exception.

    This is mypy's known bug: syntax errors emit plain text even under --output=json.
    """
    mixed_output = "\n".join([
        json.dumps({"file": "ok.py", "line": 1, "column": 1, "severity": "error",
                    "message": "real error", "code": "misc"}),
        "foo.py:1: error: invalid syntax",   # plain text fallback line
        json.dumps({"file": "ok2.py", "line": 2, "column": 1, "severity": "error",
                    "message": "another error", "code": "misc"}),
    ])
    raw = _make_raw(mixed_output)
    diagnostics = ADAPTER.parse(raw)

    # Should have parsed the 2 valid JSON lines, skipped the plain-text line
    assert len(diagnostics) == 2
    # Should have warned about the skipped line
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)
    warning_text = str(recwarn.list[0].message)
    assert "non-JSON" in warning_text or "skipped" in warning_text.lower()


def test_parse_all_non_json_produces_empty_with_warning(recwarn: pytest.WarningsChecker) -> None:
    """If all lines are non-JSON (e.g., total syntax failure), return [] with a warning."""
    raw = _make_raw("foo.py:1: error: invalid syntax\nbar.py:2: error: unexpected indent")
    diagnostics = ADAPTER.parse(raw)
    assert diagnostics == []
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)


# ---------------------------------------------------------------------------
# Schema invariant tests
# ---------------------------------------------------------------------------


def test_parse_schema_version_is_set() -> None:
    """schema_version must be SCHEMA_VERSION (1) on every diagnostic."""
    from rety.schema import SCHEMA_VERSION
    jsonl = json.dumps({"file": "x.py", "line": 1, "column": 1,
                        "severity": "error", "message": "e", "code": "c"})
    raw = _make_raw(jsonl)
    for d in ADAPTER.parse(raw):
        assert d.schema_version == SCHEMA_VERSION


def test_parse_checker_name_is_mypy() -> None:
    """All diagnostics from MypyAdapter.parse() have checker='mypy'."""
    jsonl = json.dumps({"file": "x.py", "line": 1, "column": 1,
                        "severity": "error", "message": "e", "code": "c"})
    raw = _make_raw(jsonl)
    for d in ADAPTER.parse(raw):
        assert d.checker == "mypy"


def test_parse_raw_field_is_per_diagnostic_json_line() -> None:
    """
    The `raw` field must be the original JSON line, not something else.
    It must be parseable as JSON (it's the per-diagnostic line).
    """
    line = json.dumps({"file": "x.py", "line": 1, "column": 1,
                       "severity": "error", "message": "e", "code": "c"})
    raw = _make_raw(line)
    diagnostics = ADAPTER.parse(raw)
    assert len(diagnostics) == 1
    # raw should be the original line (parseable as JSON)
    parsed_raw = json.loads(diagnostics[0].raw)
    assert parsed_raw["message"] == "e"


def test_multiple_diagnostics_parsed_correctly() -> None:
    """Multiple JSON-lines are each parsed into separate diagnostics."""
    lines = [
        json.dumps({"file": "a.py", "line": 1, "column": 1,
                    "severity": "error", "message": "first", "code": "e1"}),
        json.dumps({"file": "a.py", "line": 5, "column": 3,
                    "severity": "warning", "message": "second", "code": "w1"}),
        json.dumps({"file": "b.py", "line": 10, "column": 1,
                    "severity": "note", "message": "third", "code": None}),
    ]
    raw = _make_raw("\n".join(lines))
    diagnostics = ADAPTER.parse(raw)

    assert len(diagnostics) == 3
    assert diagnostics[0].severity == Severity.error
    assert diagnostics[1].severity == Severity.warning
    assert diagnostics[2].severity == Severity.note
    assert diagnostics[0].start_line == 1
    assert diagnostics[1].start_line == 5
    assert diagnostics[2].start_line == 10
