"""
Unit tests for PyrightAdapter.parse().

The captured fixtures in tests/fixtures/captured/pyright/ are real Pyright
output (Pyright 1.1.414, see tests/CHECKER_VERSIONS.md). No subprocess is
invoked here.

Critical invariant under test: Pyright uses 0-indexed LSP-style line/character
offsets. The adapter must convert to 1-indexed before returning diagnostics.
"""

from __future__ import annotations

import json
from pathlib import Path

from rety.adapters.pyright import PyrightAdapter
from rety.schema import RawInvocation, Severity

CAPTURED_DIR = Path(__file__).parent.parent / "fixtures" / "captured" / "pyright"

ADAPTER = PyrightAdapter()


def _make_raw(stdout: str, version: str = "1.1.414") -> RawInvocation:
    return RawInvocation(
        checker="pyright",
        returncode=1,
        stdout=stdout,
        stderr="",
        duration_ms=380.0,
        version=version,
    )


def _doc(*diags: dict[str, object]) -> str:
    return json.dumps({"version": "1.1.414", "generalDiagnostics": list(diags), "summary": {}})


def _diag(line: int, char: int, end_line: int, end_char: int, **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "file": "/path/to/file.py",
        "severity": "error",
        "message": "Type mismatch",
        "rule": "reportArgumentType",
        "range": {
            "start": {"line": line, "character": char},
            "end": {"line": end_line, "character": end_char},
        },
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Captured fixture tests (real Pyright output)
# ---------------------------------------------------------------------------


def test_parse_basic_errors_from_captured_fixture() -> None:
    content = (CAPTURED_DIR / "basic_errors.json").read_text()
    diagnostics = ADAPTER.parse(_make_raw(content))

    assert len(diagnostics) == 3
    by_line = {d.start_line: d for d in diagnostics}
    assert set(by_line) == {9, 12, 13}  # 0-indexed 8, 11, 12 in the JSON

    d = by_line[9]
    assert d.checker == "pyright"
    assert d.checker_version == "1.1.414"
    assert d.severity == Severity.error
    assert d.code == "reportReturnType"
    assert d.start_col == 12  # character 11 → 12
    assert d.end_line == 9
    assert d.end_col == 16  # character 15 → 16
    assert d.message.startswith('Type "str" is not assignable to return type "int"')
    assert "\n" in d.message  # Pyright messages can be multi-line
    assert Path(d.file).name == "basic_errors.py"
    assert Path(d.file).is_absolute()

    assert by_line[12].code == "reportAssignmentType"
    assert by_line[12].start_col == 10
    assert by_line[12].end_col == 13
    assert by_line[13].code == "reportUndefinedVariable"
    assert by_line[13].start_col == 5
    assert by_line[13].end_col == 19


def test_parse_clean_run_from_captured_fixture() -> None:
    """Pyright reports nothing on untyped_function.py under default settings."""
    content = (CAPTURED_DIR / "untyped_function.json").read_text()
    assert ADAPTER.parse(_make_raw(content)) == []


# ---------------------------------------------------------------------------
# Index conversion tests — THE critical invariant for Pyright
# ---------------------------------------------------------------------------


def test_parse_0indexed_converted_to_1indexed() -> None:
    (d,) = ADAPTER.parse(_make_raw(_doc(_diag(0, 0, 0, 10))))
    assert d.start_line == 1
    assert d.start_col == 1
    assert d.end_line == 1
    assert d.end_col == 11


def test_parse_line_5_in_0indexed_becomes_6() -> None:
    (d,) = ADAPTER.parse(_make_raw(_doc(_diag(5, 4, 5, 8))))
    assert d.start_line == 6
    assert d.start_col == 5
    assert d.end_col == 9


# ---------------------------------------------------------------------------
# Schema and structure tests
# ---------------------------------------------------------------------------


def test_parse_empty_output_returns_empty() -> None:
    assert ADAPTER.parse(_make_raw("")) == []


def test_parse_no_diagnostics_returns_empty() -> None:
    assert ADAPTER.parse(_make_raw(_doc())) == []


def test_parse_null_rule_becomes_none() -> None:
    (d,) = ADAPTER.parse(_make_raw(_doc(_diag(3, 0, 3, 5, rule=None))))
    assert d.code is None


def test_parse_absent_rule_becomes_none() -> None:
    """Real Pyright omits 'rule' entirely for some diagnostics (e.g. bad directives)."""
    diag = _diag(3, 0, 3, 5)
    del diag["rule"]
    (d,) = ADAPTER.parse(_make_raw(_doc(diag)))
    assert d.code is None


def test_parse_raw_field_is_per_diagnostic_subobject() -> None:
    (d,) = ADAPTER.parse(_make_raw(_doc(_diag(0, 0, 0, 5, message="Test"))))
    raw_parsed = json.loads(d.raw)
    assert "generalDiagnostics" not in raw_parsed
    assert raw_parsed["message"] == "Test"


def test_parse_severity_information_mapped_correctly() -> None:
    (d,) = ADAPTER.parse(_make_raw(_doc(_diag(0, 0, 0, 5, severity="information"))))
    assert d.severity == Severity.information


def test_parse_severity_warning_mapped_correctly() -> None:
    (d,) = ADAPTER.parse(_make_raw(_doc(_diag(0, 0, 0, 5, severity="warning"))))
    assert d.severity == Severity.warning


def test_parse_malformed_json_returns_empty() -> None:
    assert ADAPTER.parse(_make_raw("this is not json at all")) == []


def test_parse_checker_name_is_pyright() -> None:
    for d in ADAPTER.parse(_make_raw(_doc(_diag(0, 0, 0, 5)))):
        assert d.checker == "pyright"
