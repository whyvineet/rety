"""
mypy adapter for rety.

Invocation:
    mypy --output=json --no-incremental --cache-dir=<tempdir> <paths>

Output format (verified against mypy 2.3.1 on 2026-09-24):
    JSON-lines (one JSON object per line, NOT a single JSON document):

        {"file": "tests/fixtures/basic_errors.py", "line": 9, "column": 11,
         "end_line": 9, "end_column": 15,
         "message": "Incompatible return value type (got \\"str\\", expected \\"int\\")",
         "hint": null, "code": "return-value", "severity": "error"}

    - line and end_line are 1-indexed.
    - column is 0-indexed (mypy's text output adds 1 for display; the JSON
      does not). end_column is a 0-indexed exclusive end. rety converts both
      to 1-indexed, so mypy and Pyright report the same token at the same
      column. A negative column means "unknown" and becomes None.
    - Paths are relative to the invocation cwd when relative paths are given.
    - "Success: no issues found" and "Found N errors" go to stderr, so stdout
      is pure JSON-lines.

    Real captured output lives in tests/fixtures/captured/mypy/.

Known issues handled here:
    1. Syntax errors: older mypy releases (1.x) fell back to plain-text output
       for syntax errors even under --output=json (mypy bug #17660). mypy 2.x
       emits JSON with code "syntax". The parser still wraps every json.loads
       in try/except and emits a RuntimeWarning for non-JSON lines, so older
       versions degrade gracefully.

    2. Stale-cache flag override: a stale .mypy_cache from a prior invocation
       with different flags can silently override --output. Passing
       --no-incremental and an ephemeral --cache-dir sidesteps this entirely.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any, Optional

from rety.adapters.base import AdapterCapabilities, CheckerAdapter
from rety.schema import NormalizedDiagnostic, RawInvocation, Severity

# mypy severity strings → Severity enum
_SEVERITY_MAP: dict[str, Severity] = {
    "error": Severity.error,
    "warning": Severity.warning,
    "note": Severity.note,
}


class MypyAdapter(CheckerAdapter):
    """Adapter for mypy (https://mypy-lang.org/)."""

    @property
    def name(self) -> str:
        return "mypy"

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            has_end_col=True,
            has_codes=True,
            uses_json=True,
            uses_text_parser=False,
        )

    def detect_version(self) -> Optional[str]:
        """
        Run `mypy --version` and parse the version string.

        mypy --version output format: "mypy 2.3.1 (compiled: yes)"
        """
        try:
            result = subprocess.run(
                ["mypy", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return parts[1]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def run(self, paths: list[str], cwd: str) -> RawInvocation:
        """
        Invoke mypy with --no-incremental and an ephemeral cache directory.

        --no-incremental disables mypy's incremental mode so it never reads
        from or writes to its normal .mypy_cache. This prevents a stale cache
        from a prior invocation with different flags from silently overriding
        --output and making the JSON stream disappear.

        --cache-dir=<tempdir> is belt-and-suspenders: a fresh tempdir per
        invocation prevents any cross-invocation state leakage on long-lived
        CI runners.
        """
        version = self.detect_version()
        start = time.monotonic()

        with tempfile.TemporaryDirectory(prefix="rety_mypy_cache_") as cache_dir:
            cmd = [
                "mypy",
                "--output=json",
                "--no-incremental",
                f"--cache-dir={cache_dir}",
                *paths,
            ]
            result = self._run_subprocess(cmd, cwd)

        duration_ms = (time.monotonic() - start) * 1000
        return RawInvocation(
            checker="mypy",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            version=version,
        )

    def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        """
        Parse mypy JSON-lines output into NormalizedDiagnostic instances.

        Non-JSON lines (the syntax-error plain-text fallback of older mypy
        versions) are skipped with a RuntimeWarning rather than crashing.
        """
        diagnostics: list[NormalizedDiagnostic] = []
        skipped_lines: list[str] = []

        for line in raw.stdout.splitlines():
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                skipped_lines.append(line[:120])  # truncate long lines in warning
                continue

            severity_str = str(obj.get("severity", "error")).lower()
            severity = _SEVERITY_MAP.get(severity_str, Severity.error)

            # Lines are 1-indexed; columns are 0-indexed and converted here.
            start_line: int = max(_as_int(obj.get("line"), default=1), 1)
            start_col: Optional[int] = _col_to_1indexed(obj.get("column"))
            end_line: Optional[int] = _line_or_none(obj.get("end_line"))
            end_col: Optional[int] = _col_to_1indexed(obj.get("end_column"))

            # Resolve relative paths against the Python process CWD.
            # (mypy outputs relative paths when invoked with relative path args.)
            file_raw: str = obj.get("file", "")
            file_path = str(Path(file_raw).resolve()) if file_raw else ""

            diagnostics.append(
                NormalizedDiagnostic(
                    checker="mypy",
                    checker_version=raw.version,
                    file=file_path,
                    start_line=start_line,
                    start_col=start_col,
                    end_line=end_line,
                    end_col=end_col,
                    severity=severity,
                    code=obj.get("code") or None,
                    message=obj.get("message", ""),
                    raw=line,  # per-diagnostic JSON line, not the full output
                )
            )

        if skipped_lines:
            warnings.warn(
                f"mypy adapter: {len(skipped_lines)} non-JSON line(s) skipped "
                f"(likely the syntax-error plain-text fallback of mypy < 2.0; "
                f"mypy bug #17660). First skipped: {skipped_lines[0]!r}",
                RuntimeWarning,
                stacklevel=2,
            )

        return diagnostics


def _as_int(value: Any, default: int) -> int:
    """Coerce to int, returning default for None or non-numeric values."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _col_to_1indexed(value: Any) -> Optional[int]:
    """
    Convert mypy's 0-indexed column to 1-indexed.

    None or a negative value means mypy did not know the column → None.
    0 is a real position (start of line) and becomes 1.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        v = int(value)
    except (ValueError, TypeError):
        return None
    return v + 1 if v >= 0 else None


def _line_or_none(value: Any) -> Optional[int]:
    """Return a 1-indexed line as-is; None for missing, non-numeric, or < 1."""
    if value is None or isinstance(value, bool):
        return None
    try:
        v = int(value)
    except (ValueError, TypeError):
        return None
    return v if v >= 1 else None
