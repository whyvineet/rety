"""
Unit tests for PyreflyAdapter.parse().

The captured fixtures in tests/fixtures/captured/pyrefly/ are real Pyrefly
output (Pyrefly 1.3.1, see tests/CHECKER_VERSIONS.md). No subprocess is
invoked here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rety.adapters.pyrefly import PyreflyAdapter
from rety.schema import RawInvocation, Severity

CAPTURED_DIR = Path(__file__).parent.parent / "fixtures" / "captured" / "pyrefly"

ADAPTER = PyreflyAdapter()


def _make_raw(stdout: str, version: str = "1.3.1") -> RawInvocation:
    return RawInvocation(
        checker="pyrefly",
        returncode=1,
        stdout=stdout,
        stderr=" INFO 3 errors",
        duration_ms=120.0,
        version=version,
    )


# ---------------------------------------------------------------------------
# Captured fixture tests (real Pyrefly output)
# ---------------------------------------------------------------------------


def test_parse_basic_errors_from_captured_fixture(recwarn: pytest.WarningsChecker) -> None:
    content = (CAPTURED_DIR / "basic_errors.json").read_text()
    diagnostics = ADAPTER.parse(_make_raw(content))

    assert len(diagnostics) == 3
    assert not recwarn.list

    by_line = {d.start_line: d for d in diagnostics}
    assert set(by_line) == {9, 12, 13}

    d = by_line[9]
    assert d.checker == "pyrefly"
    assert d.checker_version == "1.3.1"
    assert d.severity == Severity.error
    assert d.start_col == 12
    assert d.end_line == 9
    assert d.end_col == 16
    assert d.code == "bad-return"  # from "name", not the integer "code"
    assert d.message == "Returned type `str` is not assignable to declared return type `int`"
    assert Path(d.file).name == "basic_errors.py"
    assert Path(d.file).is_absolute()

    assert by_line[12].code == "bad-assignment"
    assert by_line[12].start_col == 10
    assert by_line[12].end_col == 13
    assert by_line[13].code == "unknown-name"
    assert by_line[13].message == "Could not find name `undefined_name`"


def test_parse_untyped_function_from_captured_fixture() -> None:
    content = (CAPTURED_DIR / "untyped_function.json").read_text()
    diagnostics = ADAPTER.parse(_make_raw(content))

    assert [d.code for d in diagnostics] == [
        "unannotated-return",
        "implicit-any-parameter",
        "implicit-any-parameter",
    ]
    assert all(d.start_line == 20 for d in diagnostics)
    assert [d.start_col for d in diagnostics] == [5, 15, 18]


def test_integer_code_field_is_never_used_as_rule() -> None:
    """Pyrefly's 'code' is an internal int (-2); the rule is 'name'."""
    data = {"errors": [{"line": 1, "column": 1, "path": "f.py", "code": -2,
                        "name": "bad-argument-type", "description": "x",
                        "severity": "error"}]}
    (d,) = ADAPTER.parse(_make_raw(json.dumps(data)))
    assert d.code == "bad-argument-type"
    assert d.code != "-2"


def test_missing_name_gives_none_code() -> None:
    data = {"errors": [{"line": 1, "column": 1, "path": "f.py", "code": -2,
                        "description": "x", "severity": "error"}]}
    (d,) = ADAPTER.parse(_make_raw(json.dumps(data)))
    assert d.code is None


# ---------------------------------------------------------------------------
# Shape tolerance
# ---------------------------------------------------------------------------


def test_parse_bare_list_is_accepted() -> None:
    data = [{"line": 10, "column": 5, "stop_line": 10, "stop_column": 20,
             "path": "tests/fixtures/foo.py", "name": "bad-argument-type",
             "description": "Expected `int`, got `str`", "severity": "error"}]
    (d,) = ADAPTER.parse(_make_raw(json.dumps(data)))
    assert d.start_line == 10
    assert d.start_col == 5
    assert d.end_col == 20


def test_parse_warning_severity() -> None:
    data = {"errors": [{"line": 5, "column": 1, "path": "foo.py",
                        "name": "possibly-undefined",
                        "description": "Variable might be undefined",
                        "severity": "warning"}]}
    (d,) = ADAPTER.parse(_make_raw(json.dumps(data)))
    assert d.severity == Severity.warning


def test_parse_zero_or_negative_positions_become_none() -> None:
    data = {"errors": [{"line": 1, "column": 0, "stop_line": -1, "stop_column": 0,
                        "path": "f.py", "name": "r", "description": "e",
                        "severity": "error"}]}
    (d,) = ADAPTER.parse(_make_raw(json.dumps(data)))
    assert d.start_col is None
    assert d.end_line is None
    assert d.end_col is None


def test_parse_entry_without_line_is_skipped_with_warning(
    recwarn: pytest.WarningsChecker,
) -> None:
    data = {"errors": [
        {"column": 1, "path": "f.py", "name": "r", "description": "no line"},
        {"line": 3, "column": 1, "path": "f.py", "name": "r", "description": "ok"},
    ]}
    diagnostics = ADAPTER.parse(_make_raw(json.dumps(data)))
    assert [d.start_line for d in diagnostics] == [3]
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)


def test_parse_description_falls_back_to_concise_description() -> None:
    data = {"errors": [{"line": 1, "column": 1, "path": "f.py", "name": "r",
                        "concise_description": "short", "severity": "error"}]}
    (d,) = ADAPTER.parse(_make_raw(json.dumps(data)))
    assert d.message == "short"


def test_parse_empty_output_returns_empty() -> None:
    assert ADAPTER.parse(_make_raw("")) == []


def test_parse_no_errors_document_returns_empty(recwarn: pytest.WarningsChecker) -> None:
    assert ADAPTER.parse(_make_raw('{"errors": []}')) == []
    assert not recwarn.list


def test_parse_malformed_json_returns_empty_with_warning(
    recwarn: pytest.WarningsChecker,
) -> None:
    result = ADAPTER.parse(_make_raw("not json at all\nalso not json"))
    assert result == []
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)
    assert "pyrefly adapter" in str(recwarn.list[0].message)


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


def test_parse_checker_name_is_pyrefly() -> None:
    data = {"errors": [{"line": 1, "path": "f.py", "description": "e"}]}
    for d in ADAPTER.parse(_make_raw(json.dumps(data))):
        assert d.checker == "pyrefly"


def test_parse_raw_is_per_diagnostic_subobject() -> None:
    entry = {"line": 1, "column": 1, "path": "f.py", "name": "r",
             "description": "test error", "severity": "error"}
    (d,) = ADAPTER.parse(_make_raw(json.dumps({"errors": [entry]})))
    raw_parsed = json.loads(d.raw)
    assert "errors" not in raw_parsed
    assert raw_parsed["description"] == "test error"
