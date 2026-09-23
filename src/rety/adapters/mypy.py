"""
mypy adapter for rety.

Invocation:
    mypy --output=json --no-incremental --cache-dir=<tempdir> <paths>

Output format:
    JSON-lines (one JSON object per line, NOT a single JSON document).
    Schema per line: file, line, column, end_line, end_column, message, hint,
                     code, severity.
    Lines are 1-indexed. Columns are 1-indexed.

Known issues handled here:
    1. Syntax errors: mypy falls back to plain-text output even under --output=json
       (open bug as of 1.20.x). The parser wraps every json.loads in try/except
       and emits a RuntimeWarning for each non-JSON line, rather than crashing.

    2. Stale-cache flag override: passing --no-incremental and an ephemeral
       --cache-dir sidesteps this entirely. A stale .mypy_cache from a prior
       invocation with different flags can silently override --output, which would
       make the JSON output disappear without warning. Using a fresh tempdir per
       invocation removes this failure class.

Phase 0 note:
    Verify whether mypy's --cache-dir respects the working directory or requires
    an absolute path. Also verify: does --no-incremental fully suppress all cache
    state, or only flag-inheritance? Check against pinned version in
    tests/CHECKER_VERSIONS.md.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import warnings
from pathlib import Path
from typing import Optional

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

        mypy --version output format: "mypy 1.11.2 (compiled: yes)"
        """
        try:
            result = subprocess.run(
                ["mypy", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # "mypy 1.11.2 (compiled: yes)" → ["mypy", "1.11.2", "(compiled:", "yes)"]
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return parts[1]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def run(self, paths: list[str], cwd: str) -> RawInvocation:
        """
        Invoke mypy with --no-incremental and an ephemeral cache directory.

        --no-incremental: disables mypy's incremental mode so it never reads
        from or writes to its normal .mypy_cache. This prevents the known bug
        where a stale cache from a prior invocation with different flags can
        silently override --output and cause the JSON stream to disappear.

        --cache-dir=<tempdir>: belt-and-suspenders. Even with --no-incremental,
        using a fresh tempdir per invocation prevents any cross-invocation
        state leakage in long-lived CI runners.
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

        Handles the known syntax-error fallback: mypy emits plain-text lines
        (not JSON) for syntax errors even under --output=json. Each non-JSON
        line is skipped with a RuntimeWarning rather than crashing.
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
                # mypy known bug: syntax errors produce plain-text output
                # even under --output=json. Skip and accumulate for warning.
                skipped_lines.append(line[:120])  # truncate long lines in warning
                continue

            severity_str = obj.get("severity", "error").lower()
            severity = _SEVERITY_MAP.get(severity_str, Severity.error)

            # mypy uses 1-indexed lines and columns.
            # Treat 0 as None (missing data, not column 0).
            start_line: int = obj.get("line", 1)
            start_col: Optional[int] = _none_if_zero(obj.get("column"))
            end_line: Optional[int] = _none_if_zero(obj.get("end_line"))
            end_col: Optional[int] = _none_if_zero(obj.get("end_column"))

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
                f"(likely syntax-error plain-text fallback; mypy bug #17660). "
                f"First skipped: {skipped_lines[0]!r}",
                RuntimeWarning,
                stacklevel=2,
            )

        return diagnostics


def _none_if_zero(value: Optional[int]) -> Optional[int]:
    """Return None if value is 0 or None, else return value unchanged."""
    if value is None or value == 0:
        return None
    return value
