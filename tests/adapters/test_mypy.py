"""
Unit tests for MypyAdapter.parse().

The captured fixtures in tests/fixtures/captured/mypy/ are real mypy output
(mypy 2.3.1, see tests/CHECKER_VERSIONS.md). No subprocess is invoked here.

Critical invariant under test: mypy's JSON columns are 0-indexed (unlike its
text output). The adapter must convert them to 1-indexed so a token reported
by mypy and Pyright lands on the same column.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rety.adapters.mypy import MypyAdapter
from rety.schema import RawInvocation, Severity

CAPTURED_DIR = Path(__file__).parent.parent / "fixtures" / "captured" / "mypy"

ADAPTER = MypyAdapter()


def _make_raw(stdout: str, version: str = "2.3.1") -> RawInvocation:
    return RawInvocation(
        checker="mypy",
        returncode=1,  # non-zero is normal when diagnostics exist
        stdout=stdout,
        stderr="Found 3 errors in 1 file (checked 1 source file)",
        duration_ms=500.0,
        version=version,
    )


def _line(**fields: object) -> str:
    base: dict[str, object] = {
        "file": "foo.py", "line": 1, "column": 0, "end_line": 1, "end_column": 1,
        "message": "m", "hint": None, "code": "misc", "severity": "error",
    }
    base.update(fields)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# Captured fixture tests (real mypy output)
# ---------------------------------------------------------------------------


def test_parse_basic_errors_from_captured_fixture(recwarn: pytest.WarningsChecker) -> None:
    content = (CAPTURED_DIR / "basic_errors.jsonl").read_text()
    diagnostics = ADAPTER.parse(_make_raw(content))

    assert len(diagnostics) == 3
    assert not recwarn.list

    by_line = {d.start_line: d for d in diagnostics}
    assert set(by_line) == {9, 12, 13}

    d = by_line[9]
    assert d.checker == "mypy"
    assert d.checker_version == "2.3.1"
    assert d.severity == Severity.error
    assert d.code == "return-value"
    # mypy JSON says column 11 / end_column 15 (0-indexed) → 12 / 16
    assert d.start_col == 12
    assert d.end_line == 9
    assert d.end_col == 16
    assert d.message == 'Incompatible return value type (got "str", expected "int")'
    assert Path(d.file).name == "basic_errors.py"
    assert Path(d.file).is_absolute()
    assert json.loads(d.raw)["code"] == "return-value"

    assert by_line[12].code == "assignment"
    assert by_line[12].start_col == 10
    assert by_line[12].end_col == 13
    assert by_line[13].code == "name-defined"
    assert by_line[13].start_col == 5
    assert by_line[13].end_col == 19


def test_parse_untyped_function_from_captured_fixture() -> None:
    """A diagnostic at column 0 is a real position (start of line), not unknown."""
    content = (CAPTURED_DIR / "untyped_function.jsonl").read_text()
    diagnostics = ADAPTER.parse(_make_raw(content))

    assert [d.code for d in diagnostics] == ["no-untyped-def", "no-untyped-call"]
    d = diagnostics[0]
    assert d.start_line == 20
    assert d.start_col == 1  # column 0 in mypy JSON
    assert d.end_line == 21
    assert d.end_col == 17
    assert diagnostics[1].start_line == 24
    assert diagnostics[1].start_col == 10


# ---------------------------------------------------------------------------
# Column conversion
# ---------------------------------------------------------------------------


def test_zero_column_is_start_of_line_not_unknown() -> None:
    (d,) = ADAPTER.parse(_make_raw(_line(column=0, end_column=0)))
    assert d.start_col == 1
    assert d.end_col == 1


def test_negative_column_means_unknown() -> None:
    (d,) = ADAPTER.parse(_make_raw(_line(column=-1, end_line=-1, end_column=-1)))
    assert d.start_col is None
    assert d.end_line is None
    assert d.end_col is None


def test_missing_end_positions_are_none() -> None:
    obj = {"file": "foo.py", "line": 3, "column": 4, "message": "m",
           "code": "misc", "severity": "error"}
    (d,) = ADAPTER.parse(_make_raw(json.dumps(obj)))
    assert d.start_col == 5
    assert d.end_line is None
    assert d.end_col is None


def test_end_positions_preserved_and_shifted() -> None:
    (d,) = ADAPTER.parse(_make_raw(_line(line=10, column=5, end_line=10, end_column=20)))
    assert d.start_line == 10
    assert d.start_col == 6
    assert d.end_line == 10
    assert d.end_col == 21


# ---------------------------------------------------------------------------
# Output edge cases
# ---------------------------------------------------------------------------


def test_parse_empty_output_returns_empty_list() -> None:
    assert ADAPTER.parse(_make_raw("")) == []


def test_parse_success_run_has_no_stdout() -> None:
    """A clean run prints 'Success: ...' on stderr and nothing on stdout."""
    raw = RawInvocation(
        checker="mypy",
        returncode=0,
        stdout="",
        stderr="Success: no issues found in 3 source files",
        duration_ms=200.0,
        version="2.3.1",
    )
    assert ADAPTER.parse(raw) == []


def test_parse_severity_note() -> None:
    (d,) = ADAPTER.parse(_make_raw(_line(severity="note", code=None)))
    assert d.severity == Severity.note
    assert d.code is None


def test_parse_tolerates_non_json_lines(recwarn: pytest.WarningsChecker) -> None:
    """Plain-text lines (older mypy syntax-error fallback) are skipped with a warning."""
    mixed_output = "\n".join([
        _line(file="ok.py", message="real error"),
        "foo.py:1: error: invalid syntax",
        _line(file="ok2.py", line=2, message="another error"),
    ])
    diagnostics = ADAPTER.parse(_make_raw(mixed_output))

    assert len(diagnostics) == 2
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)
    assert "mypy adapter" in str(recwarn.list[0].message)


def test_parse_all_non_json_produces_empty_with_warning(recwarn: pytest.WarningsChecker) -> None:
    raw = _make_raw("foo.py:1: error: invalid syntax\nbar.py:2: error: unexpected indent")
    assert ADAPTER.parse(raw) == []
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)


# ---------------------------------------------------------------------------
# Schema invariant tests
# ---------------------------------------------------------------------------


def test_parse_schema_version_is_set() -> None:
    from rety.schema import SCHEMA_VERSION

    for d in ADAPTER.parse(_make_raw(_line())):
        assert d.schema_version == SCHEMA_VERSION


def test_parse_checker_name_is_mypy() -> None:
    for d in ADAPTER.parse(_make_raw(_line())):
        assert d.checker == "mypy"


def test_parse_raw_field_is_per_diagnostic_json_line() -> None:
    (d,) = ADAPTER.parse(_make_raw(_line(message="e")))
    assert json.loads(d.raw)["message"] == "e"


def test_multiple_diagnostics_parsed_in_order() -> None:
    lines = [
        _line(file="a.py", line=1, severity="error", message="first", code="e1"),
        _line(file="a.py", line=5, severity="warning", message="second", code="w1"),
        _line(file="b.py", line=10, severity="note", message="third", code=None),
    ]
    diagnostics = ADAPTER.parse(_make_raw("\n".join(lines)))

    assert [d.severity for d in diagnostics] == [
        Severity.error, Severity.warning, Severity.note,
    ]
    assert [d.start_line for d in diagnostics] == [1, 5, 10]


def test_relative_file_resolves_against_invocation_cwd(tmp_path: Path) -> None:
    """Paths are resolved against the cwd the checker ran in, not the process cwd."""
    raw = RawInvocation(
        checker="mypy", returncode=1, stdout=_line(file="pkg/mod.py"), stderr="",
        duration_ms=1.0, version="2.3.1", cwd=str(tmp_path),
    )
    (d,) = ADAPTER.parse(raw)
    assert d.file == str((tmp_path / "pkg" / "mod.py").resolve())
