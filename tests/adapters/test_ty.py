"""
Unit tests for TyAdapter.parse().

The captured fixtures in tests/fixtures/captured/ty/ are real ty output
(ty 0.0.83, see tests/CHECKER_VERSIONS.md). No subprocess is invoked here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rety.adapters.ty import TyAdapter
from rety.schema import RawInvocation, Severity

CAPTURED_DIR = Path(__file__).parent.parent / "fixtures" / "captured" / "ty"

ADAPTER = TyAdapter()


def _make_raw(stdout: str, version: str = "0.0.83") -> RawInvocation:
    return RawInvocation(
        checker="ty",
        returncode=1,
        stdout=stdout,
        stderr="",
        duration_ms=45.0,
        version=version,
    )


# ---------------------------------------------------------------------------
# Captured fixture tests (real ty output)
# ---------------------------------------------------------------------------


def test_parse_basic_errors_from_captured_fixture(recwarn: pytest.WarningsChecker) -> None:
    """Three real diagnostics plus the 'Found 3 diagnostics' summary line."""
    content = (CAPTURED_DIR / "basic_errors.txt").read_text()
    diagnostics = ADAPTER.parse(_make_raw(content))

    assert len(diagnostics) == 3
    assert not recwarn.list, "summary line must not trigger the unmatched-line warning"

    by_line = {d.start_line: d for d in diagnostics}
    assert set(by_line) == {9, 12, 13}

    d = by_line[9]
    assert d.checker == "ty"
    assert d.checker_version == "0.0.83"
    assert d.severity == Severity.error
    assert d.start_col == 12
    assert d.end_line is None and d.end_col is None
    assert d.code == "invalid-return-type"
    assert d.message == (
        "Return type does not match returned value: expected `int`, found `str`"
    )
    assert Path(d.file).name == "basic_errors.py"
    assert Path(d.file).is_absolute()

    assert by_line[12].code == "invalid-assignment"
    assert by_line[12].start_col == 10
    assert by_line[13].code == "unresolved-reference"
    assert by_line[13].start_col == 5


def test_parse_clean_run_from_captured_fixture(recwarn: pytest.WarningsChecker) -> None:
    """'All checks passed!' is a summary line: no diagnostics, no warning."""
    content = (CAPTURED_DIR / "untyped_function.txt").read_text()
    assert ADAPTER.parse(_make_raw(content)) == []
    assert not recwarn.list


# ---------------------------------------------------------------------------
# Concise format parsing
# ---------------------------------------------------------------------------


def test_parse_severity_and_code_are_split() -> None:
    line = "src/foo.py:10:5: error[invalid-return-type] Missing return"
    (d,) = ADAPTER.parse(_make_raw(line))
    assert d.severity == Severity.error
    assert d.code == "invalid-return-type"
    assert d.message == "Missing return"
    assert d.start_line == 10
    assert d.start_col == 5


def test_parse_warning_severity() -> None:
    line = "src/bar.py:3:1: warning[unused-ignore-comment] Unused blanket `type: ignore`"
    (d,) = ADAPTER.parse(_make_raw(line))
    assert d.severity == Severity.warning
    assert d.code == "unused-ignore-comment"


def test_parse_line_without_code_has_none() -> None:
    line = "src/foo.py:7:2: error Some error without a rule"
    (d,) = ADAPTER.parse(_make_raw(line))
    assert d.code is None
    assert d.message == "Some error without a rule"


def test_parse_windows_absolute_path_keeps_drive_letter() -> None:
    line = r"C:\work\proj\src\foo.py:2:12: error[invalid-assignment] Bad"
    (d,) = ADAPTER.parse(_make_raw(line))
    assert d.start_line == 2
    assert d.start_col == 12
    assert Path(d.file).name == "foo.py"
    assert d.raw == line


def test_parse_end_positions_are_always_none() -> None:
    (d,) = ADAPTER.parse(_make_raw("src/foo.py:5:3: error[some-rule] Something"))
    assert d.end_line is None
    assert d.end_col is None


def test_parse_summary_lines_are_skipped_silently(recwarn: pytest.WarningsChecker) -> None:
    output = "\n".join(
        [
            "src/a.py:1:1: error[rule-a] First",
            "Found 1 diagnostic",
        ]
    )
    assert len(ADAPTER.parse(_make_raw(output))) == 1
    assert not recwarn.list


def test_parse_multiple_lines_and_blank_lines() -> None:
    output = "\n".join(
        [
            "src/a.py:1:1: error[rule-a] First error",
            "",
            "   ",
            "src/b.py:5:3: warning[rule-b] Second warning",
            "src/a.py:10:1: note[rule-c] Third note",
            "Found 3 diagnostics",
        ]
    )
    diagnostics = ADAPTER.parse(_make_raw(output))
    assert [d.start_line for d in diagnostics] == [1, 5, 10]
    assert diagnostics[2].severity == Severity.note


def test_parse_empty_output_returns_empty() -> None:
    assert ADAPTER.parse(_make_raw("")) == []


# ---------------------------------------------------------------------------
# RuntimeWarning for unmatched lines
# ---------------------------------------------------------------------------


def test_parse_unmatched_lines_emit_warning(recwarn: pytest.WarningsChecker) -> None:
    result = ADAPTER.parse(_make_raw("this is not a valid ty output line at all"))
    assert result == []
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)
    warning_text = str(recwarn.list[0].message)
    assert "ty adapter" in warning_text


def test_parse_partial_match_warns_and_parses_valid(recwarn: pytest.WarningsChecker) -> None:
    output = "\n".join(
        [
            "src/ok.py:5:1: error[rule] Valid error",
            "this is garbage and should not parse",
            "src/ok2.py:10:2: warning[r2] Another warning",
        ]
    )
    diagnostics = ADAPTER.parse(_make_raw(output))
    assert len(diagnostics) == 2
    assert any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


def test_checker_name_is_ty() -> None:
    for d in ADAPTER.parse(_make_raw("src/f.py:1:1: error[r] Test")):
        assert d.checker == "ty"


def test_raw_is_original_text_line() -> None:
    original_line = "src/foo.py:1:1: error[rule] Something"
    (d,) = ADAPTER.parse(_make_raw(original_line))
    assert d.raw == original_line
