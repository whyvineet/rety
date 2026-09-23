"""
Rich-based terminal renderer for rety comparison reports.

Agreement language always uses N/M where:
    N = len(cluster.checkers_present)   — checkers that found something here
    M = len(report.checkers_run)        — checkers actually invoked

M is never hardcoded as 4. A run with --checker mypy,pyright shows "2/2",
not "2/4", which would be misleading if only two checkers were ever run.

Every string that originates from a checker or from the user's code (messages,
file paths, scope names, alignment signals) is passed through rich.markup.escape
before printing. Type checker messages routinely contain "list[str]" or
"[arg-type]", which Rich would otherwise consume as markup tags.

Output structure:
    ┌─ Header ─────────────────────────────────────────────────────┐
    │  rety — Python type checker cross-comparison                  │
    │  mypy 1.x.x  pyright 1.x.x  pyrefly 1.x.x  ty 0.x.x        │
    └──────────────────────────────────────────────────────────────┘
    [Summary table: checker × severity counts]
    ── src/api.py ──────────────────────────────────────────────────
      3/4 ● HIGH  L42  in handle_request [Call]
           mypy     error  Argument 1 ... [arg-type]
           pyright  error  Argument of type ... [reportArgumentType]
           pyrefly  error  Expected `int`, got `str` [bad-argument-type]
      ...
    [Footer: cluster count summary]
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rich import box
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from rety.schema import (
    ComparisonReport,
    Confidence,
    DiagnosticCluster,
    NormalizedDiagnostic,
    Severity,
)

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

_CHECKER_STYLES: dict[str, str] = {
    "mypy": "#7EC8E3 bold",
    "pyright": "#68A6E1 bold",
    "pyrefly": "#F0A500 bold",
    "ty": "#E06C75 bold",
}

_SEVERITY_STYLES: dict[Severity, str] = {
    Severity.error: "bold red",
    Severity.warning: "yellow",
    Severity.note: "blue",
    Severity.information: "cyan",
}

_CONFIDENCE_BADGE: dict[Confidence, str] = {
    Confidence.HIGH: "* HIGH",
    Confidence.MEDIUM: "~ MED ",
    Confidence.LOW: "- LOW ",
}

_CONFIDENCE_STYLES: dict[Confidence, str] = {
    Confidence.HIGH: "bold green",
    Confidence.MEDIUM: "yellow",
    Confidence.LOW: "dim",
}

_AGREEMENT_STYLE_FULL = "bold green"
_AGREEMENT_STYLE_PARTIAL = "yellow"
_AGREEMENT_STYLE_SINGLE = "dim"


# ---------------------------------------------------------------------------
# Public render function
# ---------------------------------------------------------------------------


def render(
    report: ComparisonReport,
    *,
    verbose: bool = False,
    console: Console | None = None,
) -> None:
    """
    Render a ComparisonReport to the terminal using Rich.

    Args:
        report:  The comparison report to render.
        verbose: If True, print alignment_signals for each cluster.
        console: Rich Console to write to. Defaults to a new Console() which
                 writes to stdout. Inject a Console(file=...) for testing.
    """
    con = console or Console()
    m = len(report.checkers_run)  # checkers actually run — the M in N/M

    _render_header(con, report)
    _render_summary_table(con, report)
    _render_clusters(con, report, m=m, verbose=verbose)
    _render_footer(con, report, m=m)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_header(con: Console, report: ComparisonReport) -> None:
    checker_parts: list[str] = []
    for checker in report.checkers_run:
        style = _CHECKER_STYLES.get(checker, "bold")
        version = report.checker_versions.get(checker) or "?"
        checker_parts.append(
            f"[{style}]{rich_escape(checker)}[/] {rich_escape(version)}"
        )

    checkers_str = "   ".join(checker_parts)
    con.print()
    con.print(
        Panel(
            f"[bold white]rety[/] [dim]--[/] Python type checker cross-comparison\n\n"
            f"{checkers_str}",
            border_style="bright_black",
            padding=(0, 2),
        )
    )


def _render_summary_table(con: Console, report: ComparisonReport) -> None:
    """Print per-checker diagnostic count breakdown."""
    # Gather counts from clusters
    counts: dict[str, dict[Severity, int]] = defaultdict(lambda: defaultdict(int))
    for cluster in report.clusters:
        for diag in cluster.diagnostics:
            counts[diag.checker][diag.severity] += 1

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold bright_black",
        padding=(0, 1),
    )
    table.add_column("Checker", style="bold", min_width=10)
    table.add_column("Errors", justify="right", min_width=7)
    table.add_column("Warnings", justify="right", min_width=8)
    table.add_column("Notes", justify="right", min_width=7)
    table.add_column("Total", justify="right", style="bold", min_width=7)

    for checker in report.checkers_run:
        checker_counts = counts.get(checker, {})
        errors = checker_counts.get(Severity.error, 0)
        warnings_ = checker_counts.get(Severity.warning, 0)
        notes = checker_counts.get(Severity.note, 0) + checker_counts.get(
            Severity.information, 0
        )
        total = errors + warnings_ + notes
        c_style = _CHECKER_STYLES.get(checker, "bold")

        table.add_row(
            Text(checker, style=c_style),
            Text(str(errors), style="red" if errors else "dim"),
            Text(str(warnings_), style="yellow" if warnings_ else "dim"),
            Text(str(notes), style="blue" if notes else "dim"),
            str(total),
        )

    con.print(table)


def _render_clusters(
    con: Console,
    report: ComparisonReport,
    m: int,
    verbose: bool,
) -> None:
    """Print all clusters, grouped by file."""
    # Group clusters by file (they're already sorted by file then line)
    by_file: dict[str, list[DiagnosticCluster]] = defaultdict(list)
    for cluster in report.clusters:
        by_file[cluster.file].append(cluster)

    for file_path, file_clusters in by_file.items():
        # Use a short display path: relative to cwd if possible
        display_path = _short_path(file_path)
        con.print(Rule(f"[bold]{rich_escape(display_path)}[/]", style="bright_black"))

        for cluster in file_clusters:
            _render_cluster(con, cluster, m=m, verbose=verbose)

        con.print()


def _render_cluster(
    con: Console,
    cluster: DiagnosticCluster,
    m: int,
    verbose: bool,
) -> None:
    """Render a single cluster."""
    n = len(cluster.checkers_present)

    # N/M agreement color
    if n == m:
        agreement_style = _AGREEMENT_STYLE_FULL
    elif n > 1:
        agreement_style = _AGREEMENT_STYLE_PARTIAL
    else:
        agreement_style = _AGREEMENT_STYLE_SINGLE

    conf_badge = _CONFIDENCE_BADGE[cluster.confidence]
    conf_style = _CONFIDENCE_STYLES[cluster.confidence]

    # Line range display
    start, end = cluster.representative_range
    line_str = f"L{start}" if start == end else f"L{start}–{end}"

    # AST context
    context_parts: list[str] = []
    if cluster.enclosing_scope:
        context_parts.append(f"in [italic]{rich_escape(cluster.enclosing_scope)}[/]")
    if cluster.enclosing_node_type:
        context_parts.append(rich_escape(f"[{cluster.enclosing_node_type}]"))
    context = "  " + "  ".join(context_parts) if context_parts else ""

    con.print(
        f"  [{agreement_style}]{n}/{m}[/]  "
        f"[{conf_style}]{conf_badge}[/]  "
        f"[bright_black]{line_str}[/]{context}"
    )

    # Individual diagnostics
    for diag in cluster.diagnostics:
        _render_diagnostic_line(con, diag)

    # Alignment signals (verbose mode)
    if verbose and cluster.alignment_signals:
        for signal in cluster.alignment_signals:
            con.print(f"         [dim italic]↳ {rich_escape(signal)}[/]")

    con.print()


def _render_diagnostic_line(con: Console, diag: NormalizedDiagnostic) -> None:
    checker_style = _CHECKER_STYLES.get(diag.checker, "bold")
    sev_style = _SEVERITY_STYLES.get(diag.severity, "white")
    code_suffix = f" [dim]{rich_escape(f'[{diag.code}]')}[/]" if diag.code else ""
    col_hint = f":{diag.start_col}" if diag.start_col is not None else ""

    con.print(
        f"      [{checker_style}]{diag.checker:<10}[/]"
        f"[{sev_style}]{diag.severity.value:<9}[/]"
        f"[dim]{diag.start_line}{col_hint}[/]  "
        f"{rich_escape(diag.message)}{code_suffix}"
    )


def _render_footer(con: Console, report: ComparisonReport, m: int) -> None:
    total = len(report.clusters)
    multi = sum(1 for c in report.clusters if len(c.checkers_present) > 1)
    all_agree = sum(1 for c in report.clusters if len(c.checkers_present) == m)
    unique = total - multi

    parts = [f"[dim]{total} cluster(s)"]
    if multi:
        parts.append(f"{multi} seen by multiple checkers")
    if all_agree and m > 1:
        parts.append(f"{all_agree} where all {m} agree")
    if unique:
        parts.append(f"{unique} seen by only one checker")

    con.print("  ".join(parts) + "[/]")
    con.print()


def _short_path(path: str) -> str:
    """Return a display-friendly path (relative to cwd if shorter)."""
    try:
        return str(Path(path).relative_to(Path.cwd()))
    except ValueError:
        return path
