"""
Pyright adapter for rety.

Invocation:
    pyright --outputjson <paths>

Output format (verified against Pyright 1.1.414 on 2026-09-24):
    Single JSON document (not JSON-lines) on stdout:
    {
        "version": "1.1.414",
        "time": "1790187993411",
        "generalDiagnostics": [
            {
                "file": "/abs/path/to/basic_errors.py",
                "severity": "error" | "warning" | "information",
                "message": "...",                # may contain newlines
                "range": {
                    "start": {"line": 8, "character": 11},
                    "end":   {"line": 8, "character": 15}
                },
                "rule": "reportReturnType"       # absent (not null) when there is no rule
            },
            ...
        ],
        "summary": {"filesAnalyzed": 1, "errorCount": 3, "warningCount": 0,
                    "informationCount": 0, "timeInSec": 0.4}
    }
    Paths are absolute. Real captured output lives in tests/fixtures/captured/pyright/.

CRITICAL: Pyright uses 0-indexed line and character offsets (LSP convention).
    The normalization step converts to 1-indexed before constructing
    NormalizedDiagnostic. This is the most common source of off-by-one errors
    when comparing Pyright output against mypy/Pyrefly/ty.

Cache behavior:
    Pyright has its own incremental cache, but it does not accept a --cache-dir
    flag that rety could ephemeralize. However, Pyright's cache is keyed by
    file hash, not by CLI flags, so stale-flag-override (the mypy problem) is
    not a known issue here. Monitor for analogous issues in CI environments.

Progress output:
    --outputjson suppresses Pyright's progress output entirely (verified):
    stdout is exactly one JSON document, so the parser never has to skip
    leading noise.
"""

from __future__ import annotations

import json
import subprocess
import time
import warnings
from typing import Any

from rety.adapters.base import AdapterCapabilities, CheckerAdapter, resolve_path
from rety.schema import NormalizedDiagnostic, RawInvocation, Severity

# Pyright severity strings → Severity enum
_SEVERITY_MAP: dict[str, Severity] = {
    "error": Severity.error,
    "warning": Severity.warning,
    "information": Severity.information,
}


class PyrightAdapter(CheckerAdapter):
    """Adapter for Pyright (https://github.com/microsoft/pyright)."""

    @property
    def name(self) -> str:
        return "pyright"

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            has_end_col=True,
            has_codes=True,
            uses_json=True,
            uses_text_parser=False,
        )

    def detect_version(self) -> str | None:
        """
        Run `pyright --version` and parse the version string.

        Pyright --version output format: "pyright 1.1.414"
        """
        try:
            result = subprocess.run(
                [self.executable, "--version"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.version_probe_timeout,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return parts[1]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def run(self, paths: list[str], cwd: str) -> RawInvocation:
        """Invoke Pyright with --outputjson."""
        version = self.version()
        start = time.monotonic()

        cmd = [self.executable, "--outputjson", *paths]
        result = self._run_subprocess(cmd, cwd)

        duration_ms = (time.monotonic() - start) * 1000
        return RawInvocation(
            checker="pyright",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            version=version,
            cwd=cwd,
        )

    def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        """
        Parse Pyright's single-document JSON output.

        Converts 0-indexed (line, character) ranges to 1-indexed (line, col).
        Stores a per-diagnostic JSON sub-object in `raw`, not the full document.
        """
        if not raw.stdout.strip():
            return []

        try:
            doc = json.loads(raw.stdout)
        except json.JSONDecodeError as exc:
            warnings.warn(
                f"pyright adapter: stdout is not valid JSON ({exc.msg} at char "
                f"{exc.pos}). Pyright may have failed before analysis (config "
                f"error, bad argument) or changed its output format. "
                f"First 120 chars: {raw.stdout.strip()[:120]!r}",
                RuntimeWarning,
                stacklevel=2,
            )
            return []

        entries = doc.get("generalDiagnostics") if isinstance(doc, dict) else None
        if not isinstance(entries, list):
            warnings.warn(
                "pyright adapter: JSON output has no 'generalDiagnostics' list. "
                "This may indicate a Pyright version change; compare against "
                "tests/fixtures/captured/pyright/.",
                RuntimeWarning,
                stacklevel=2,
            )
            return []

        diagnostics: list[NormalizedDiagnostic] = []
        skipped = 0
        for entry in entries:
            diag = self._parse_single(entry, raw.version, raw.cwd)
            if diag is None:
                skipped += 1
            else:
                diagnostics.append(diag)

        if skipped:
            warnings.warn(
                f"pyright adapter: {skipped} entr{'y' if skipped == 1 else 'ies'} "
                f"in the JSON output lacked a usable range and were skipped. "
                f"This may indicate a Pyright version change.",
                RuntimeWarning,
                stacklevel=2,
            )

        return diagnostics

    @staticmethod
    def _parse_single(
        obj: Any,
        version: str | None,
        raw_cwd: str | None,
    ) -> NormalizedDiagnostic | None:
        """Parse one Pyright diagnostic; None if it has no usable start position."""
        if not isinstance(obj, dict):
            return None

        range_obj = obj.get("range")
        if not isinstance(range_obj, dict):
            return None

        # Pyright uses 0-indexed LSP-style ranges → convert to 1-indexed.
        start_line, start_col = _position(range_obj.get("start")) or (None, None)
        if start_line is None:
            return None  # never invent a position
        end_line, end_col = _position(range_obj.get("end")) or (None, None)

        severity_str = str(obj.get("severity") or "error").lower()
        severity = _SEVERITY_MAP.get(severity_str, Severity.error)

        # Pyright prints absolute paths; resolve anyway to normalize
        # drive-letter case and separators on Windows.
        file_raw = obj.get("file") or ""
        file_path = resolve_path(str(file_raw), raw_cwd) if file_raw else ""

        rule = obj.get("rule")  # absent when there is no rule

        return NormalizedDiagnostic(
            checker="pyright",
            checker_version=version,
            file=file_path,
            start_line=start_line,
            start_col=start_col,
            end_line=end_line,
            end_col=end_col,
            severity=severity,
            code=str(rule) if rule else None,
            message=str(obj.get("message") or ""),
            raw=json.dumps(obj),  # per-diagnostic sub-object, not the full document
        )


def _position(obj: Any) -> tuple[int | None, int | None] | None:
    """
    Convert a 0-indexed LSP position {"line", "character"} to 1-indexed
    (line, col). None if obj is not a position; a missing or non-integer
    field becomes None rather than a guessed 1.
    """
    if not isinstance(obj, dict):
        return None
    return _zero_to_one(obj.get("line")), _zero_to_one(obj.get("character"))


def _zero_to_one(value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value + 1
