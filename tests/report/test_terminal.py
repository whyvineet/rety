"""
Tests for the Rich terminal renderer (rety/report/terminal.py).

The renderer is exercised through an in-memory Console so no terminal is
needed. Markup handling is the main thing under test: checker messages
routinely contain square brackets, which Rich treats as markup tags unless
escaped.
"""

from __future__ import annotations

import io

from rich.console import Console

from rety.align import align
from rety.report.terminal import render
from rety.schema import ComparisonReport, NormalizedDiagnostic, Severity

FAKE_FILE = "/nonexistent/path/to/file.py"


def _diag(checker: str, line: int, message: str, code: str | None = None) -> NormalizedDiagnostic:
    return NormalizedDiagnostic(
        checker=checker,
        checker_version="1.0",
        file=FAKE_FILE,
        start_line=line,
        severity=Severity.error,
        code=code,
        message=message,
        raw="raw",
    )


def _render_to_text(report: ComparisonReport, *, verbose: bool = False) -> str:
    buf = io.StringIO()
    console = Console(file=buf, width=200, color_system=None, force_terminal=False)
    render(report, verbose=verbose, console=console)
    return buf.getvalue()


def _report(*diags: NormalizedDiagnostic, checkers: list[str] | None = None) -> ComparisonReport:
    checkers = checkers or sorted({d.checker for d in diags})
    return ComparisonReport(
        checkers_run=checkers,
        checker_versions={c: "1.0" for c in checkers},
        total_diagnostics={c: sum(1 for d in diags if d.checker == c) for c in checkers},
        clusters=align(list(diags)),
    )


def test_brackets_in_messages_are_printed_verbatim() -> None:
    msg = 'expression has type "list[str]", variable has type "list[int]"'
    out = _render_to_text(_report(_diag("mypy", 4, msg, code="assignment")))
    assert "list[str]" in out
    assert "list[int]" in out
    assert "[assignment]" in out


def test_closing_tag_lookalike_in_message_does_not_raise() -> None:
    out = _render_to_text(_report(_diag("pyright", 4, "x [/bold] y")))
    assert "x [/bold] y" in out


def test_node_type_context_is_shown_in_brackets() -> None:
    report = _report(_diag("mypy", 1, "m"))
    # Force an enclosing node type onto the cluster to exercise the display path.
    cluster = report.clusters[0].model_copy(update={"enclosing_node_type": "Call"})
    report = report.model_copy(update={"clusters": [cluster]})
    assert "[Call]" in _render_to_text(report)


def test_verbose_signals_with_brackets_are_escaped() -> None:
    report = _report(_diag("mypy", 1, "m"))
    cluster = report.clusters[0].model_copy(
        update={"alignment_signals": ["same enclosing [Call]: mypy ↔ pyright"]}
    )
    report = report.model_copy(update={"clusters": [cluster]})
    assert "same enclosing [Call]" in _render_to_text(report, verbose=True)


def test_n_over_m_uses_checkers_actually_run() -> None:
    report = _report(_diag("mypy", 4, "a"), _diag("pyright", 4, "b"), checkers=["mypy", "pyright"])
    out = _render_to_text(report)
    assert "2/2" in out
    assert "2/4" not in out


def test_multiline_message_is_collapsed_to_one_line() -> None:
    msg = (
        'Type "str" is not assignable to return type "int"'
        '\n\xa0\xa0"str" is not assignable to "int"'
    )
    out = _render_to_text(_report(_diag("pyright", 2, msg, code="reportReturnType")))
    assert 'return type "int" "str" is not assignable' in out
    assert "\xa0" not in out
