"""
Tests for the JSON renderer (rety/report/json_report.py).

The JSON document is the stable machine-readable contract of rety, so these
tests pin its shape: top-level keys, enum serialization, tuple-as-list, the
schema_version stamp, round-tripping, and determinism.
"""

from __future__ import annotations

import json
from pathlib import Path

from rety.align import align
from rety.report import json_report
from rety.schema import SCHEMA_VERSION, ComparisonReport, NormalizedDiagnostic, Severity

FAKE_FILE = "/nonexistent/path/to/file.py"


def _diag(checker: str, line: int, code: str = "c") -> NormalizedDiagnostic:
    return NormalizedDiagnostic(
        checker=checker,
        checker_version="1.0",
        file=FAKE_FILE,
        start_line=line,
        start_col=3,
        end_line=line,
        end_col=9,
        severity=Severity.error,
        code=code,
        message=f"{checker} says [{code}]",
        raw="{}",
    )


def _report() -> ComparisonReport:
    diags = [
        _diag("mypy", 4, "arg-type"),
        _diag("pyright", 4, "reportArgumentType"),
        _diag("ty", 9),
    ]
    return ComparisonReport(
        checkers_run=["mypy", "pyright", "ty"],
        checker_versions={"mypy": "2.3.1", "pyright": "1.1.414", "ty": None},
        total_diagnostics={"mypy": 1, "pyright": 1, "ty": 1},
        clusters=align(diags),
    )


def test_render_produces_documented_top_level_shape() -> None:
    doc = json.loads(json_report.render(_report()))

    assert list(doc) == [
        "schema_version",
        "checkers_run",
        "checker_versions",
        "total_diagnostics",
        "clusters",
    ]
    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["checkers_run"] == ["mypy", "pyright", "ty"]
    assert doc["checker_versions"]["ty"] is None
    assert len(doc["clusters"]) == 2


def test_enums_and_tuples_serialize_as_plain_json() -> None:
    doc = json.loads(json_report.render(_report()))
    cluster = doc["clusters"][0]

    assert cluster["confidence"] == "high"
    assert cluster["representative_range"] == [4, 4]
    assert cluster["checkers_present"] == ["mypy", "pyright"]
    assert cluster["diagnostics"][0]["severity"] == "error"
    assert cluster["diagnostics"][0]["schema_version"] == SCHEMA_VERSION


def test_render_is_deterministic() -> None:
    assert json_report.render(_report()) == json_report.render(_report())


def test_render_round_trips_through_the_model() -> None:
    report = _report()
    restored = ComparisonReport.model_validate_json(json_report.render(report))
    assert restored == report


def test_render_to_file_writes_utf8_with_trailing_newline(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    json_report.render_to_file(_report(), str(out))

    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "↔" in text  # alignment signals keep their unicode arrow
    assert json.loads(text)["schema_version"] == SCHEMA_VERSION
