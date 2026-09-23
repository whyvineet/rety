"""
rety CLI — Python type checker cross-comparison tool.

Entry point: `rety` (installed via pyproject.toml [project.scripts]).

Commands:
    rety check  — run type checkers and compare diagnostics

Usage examples:
    rety check src/
    rety check --checker mypy,pyright src/
    rety check --format json --output report.json src/
    rety check --line-tolerance 1 --verbose src/mymodule.py
    rety check --require-all --checker mypy,pyright,pyrefly,ty src/
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import click

from rety import __version__
from rety.adapters import ALL_ADAPTERS
from rety.align import align
from rety.report import json_report, terminal
from rety.runner import CheckerUnavailableError, install_hint, run_checkers
from rety.schema import ComparisonReport


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="rety")
def main() -> None:
    """rety — Python type checker cross-comparison tool.

    Run multiple type checkers on the same Python code and compare their
    diagnostic output. See 'rety check --help' for usage.
    """
    _ensure_utf8_streams()


def _ensure_utf8_streams() -> None:
    """
    Make stdout/stderr UTF-8 capable.

    On Windows, a redirected or piped stdout defaults to the ANSI code page
    (cp1252), which cannot encode the box-drawing and arrow glyphs the
    terminal renderer uses and would raise UnicodeEncodeError. Reconfiguring
    is a no-op on streams that are already UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if reconfigure is None or encoding == "utf8":
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# check command
# ---------------------------------------------------------------------------


@main.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--checker",
    "-c",
    default="mypy,pyright,pyrefly,ty",
    show_default=True,
    metavar="CHECKERS",
    help=(
        "Comma-separated list of checkers to run. "
        "Valid: mypy, pyright, pyrefly, ty. "
        "Example: --checker mypy,pyright"
    ),
)
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["terminal", "json"], case_sensitive=False),
    default="terminal",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    metavar="FILE",
    help="Write JSON output to FILE (only valid with --format json).",
)
@click.option(
    "--line-tolerance",
    "-t",
    type=click.IntRange(min=0),
    default=0,
    show_default=True,
    metavar="N",
    help=(
        "Lines of tolerance for range-overlap matching. "
        "0 = exact overlap only. "
        "1-2 = useful when checkers habitually report adjacent lines for the same issue."
    ),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show alignment signals for each cluster (why it was grouped).",
)
@click.option(
    "--require-all",
    is_flag=True,
    default=False,
    help="Exit with error if any selected checker is not installed.",
)
@click.option(
    "--timeout",
    type=click.FloatRange(min=0),
    default=600.0,
    show_default=True,
    metavar="SECONDS",
    help="Seconds to wait for each checker before giving up on it. 0 = no limit.",
)
def check(
    paths: tuple[str, ...],
    checker: str,
    output_format: str,
    output: Optional[str],
    line_tolerance: int,
    verbose: bool,
    require_all: bool,
    timeout: float,
) -> None:
    """Run type checkers on PATH(s) and compare their diagnostics.

    PATH can be a file or directory. Multiple paths are accepted.

    rety respects each checker's native config discovery: mypy.ini,
    pyrightconfig.json, pyrefly.toml, ty.toml are all found relative to the
    directory where rety is invoked, exactly as if you ran each checker directly.

    \b
    Examples:
        rety check src/
        rety check --checker mypy,pyright src/ tests/
        rety check --format json --output results.json src/
        rety check --line-tolerance 1 --verbose src/api.py
    """
    if output and output_format != "json":
        raise click.UsageError("--output is only valid with --format json.")

    # Capture invocation CWD before any path manipulation.
    # This is the directory all checker subprocesses must use as their working
    # directory, so native config discovery works correctly.
    invocation_cwd = os.getcwd()

    # Parse and validate checker names
    checker_names = _parse_checker_names(checker)
    adapters = [ALL_ADAPTERS[name](timeout=timeout or None) for name in checker_names]

    # Run checkers concurrently
    try:
        results = run_checkers(
            adapters=adapters,
            paths=list(paths),
            cwd=invocation_cwd,
            require_all=require_all,
        )
    except CheckerUnavailableError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    # Tell the user which selected checkers were not found, and how to get them.
    ran = {result.checker_name for result in results}
    skipped = [name for name in checker_names if name not in ran]
    if skipped:
        click.echo(
            f"Skipped {len(skipped)} checker(s) not installed or not on PATH: "
            + ", ".join(skipped),
            err=True,
        )
        for name in skipped:
            click.echo(f"  {name:<8} {install_hint(name)}", err=True)
        click.echo("Use --require-all to fail instead of skipping.", err=True)

    if not results:
        click.echo(
            "No checkers were available. Install at least one of: "
            + ", ".join(checker_names)
            + "\nSee: https://github.com/whyvineet/rety#install",
            err=True,
        )
        sys.exit(1)

    # Report any adapter errors (non-zero returncode is normal; unexpected
    # exceptions are worth surfacing as warnings)
    for result in results:
        if result.error is not None:
            click.echo(
                f"Warning: {result.checker_name} encountered an unexpected error: "
                f"{result.error}",
                err=True,
            )

    # Aggregate results
    checkers_run: list[str] = []
    checker_versions: dict[str, Optional[str]] = {}
    total_diagnostics: dict[str, int] = {}
    all_diagnostics = []

    for result in results:
        name = result.checker_name
        checkers_run.append(name)
        checker_versions[name] = result.invocation.version
        total_diagnostics[name] = len(result.diagnostics)
        all_diagnostics.extend(result.diagnostics)

    # Align diagnostics into clusters
    clusters = align(all_diagnostics, line_tolerance=line_tolerance)

    # Build the comparison report
    report = ComparisonReport(
        checkers_run=checkers_run,
        checker_versions=checker_versions,
        total_diagnostics=total_diagnostics,
        clusters=clusters,
    )

    # Render
    if output_format == "json":
        if output:
            json_report.render_to_file(report, output)
            click.echo(f"JSON report written to: {output}")
        else:
            click.echo(json_report.render(report))
    else:
        terminal.render(report, verbose=verbose)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_checker_names(checker_str: str) -> list[str]:
    """
    Parse a comma-separated checker name string and validate against ALL_ADAPTERS.

    Raises click.BadParameter on unknown checker names.
    """
    names = [name.strip().lower() for name in checker_str.split(",") if name.strip()]

    if not names:
        raise click.BadParameter(
            "Must specify at least one checker.",
            param_hint="--checker",
        )

    unknown = [name for name in names if name not in ALL_ADAPTERS]
    if unknown:
        raise click.BadParameter(
            f"Unknown checker(s): {', '.join(unknown)}. "
            f"Valid choices: {', '.join(sorted(ALL_ADAPTERS))}.",
            param_hint="--checker",
        )

    return names


if __name__ == "__main__":
    main()
