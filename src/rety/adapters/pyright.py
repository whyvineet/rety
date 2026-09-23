"""
Pyright adapter for rety.

Invocation:
    pyright --outputjson <paths>

Output format:
    Single JSON document (not JSON-lines). Structure:
    {
        "version": "1.1.xxx",
        "generalDiagnostics": [
            {
                "file": "/abs/path/to/file.py",
                "severity": "error" | "warning" | "information",
                "message": "...",
                "rule": "reportArgumentType" | null,
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end":   {"line": 0, "character": 10}
                }
            },
            ...
        ],
        "summary": {"filesAnalyzed": N, "errorCount": N, ...}
    }

CRITICAL: Pyright uses 0-indexed line and character offsets (LSP convention).
    The normalization step converts to 1-indexed before constructing
    NormalizedDiagnostic. This is the most common source of off-by-one errors
    when comparing Pyright output against mypy/Pyrefly/ty.

Cache behavior:
    Pyright has its own incremental cache, but it does not accept a --cache-dir
    flag that rety could ephemeralize. However, Pyright's cache is keyed by
    file hash, not by CLI flags, so stale-flag-override (the mypy problem) is
    not a known issue here. Monitor for analogous issues in CI environments.

Phase 0 note:
    Verify: does --outputjson suppress Pyright's interactive progress output?
    Pyright sometimes emits progress dots to stdout when it thinks it's in a
    TTY. The subprocess capture should prevent TTY detection, but verify with
    real output.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Optional

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

    def detect_version(self) -> Optional[str]:
        """
        Run `pyright --version` and parse the version string.

        Pyright --version output format: "pyright 1.1.380"
        """
        try:
            result = subprocess.run(
                ["pyright", "--version"],
                capture_output=True,
                text=True,
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

        cmd = ["pyright", "--outputjson", *paths]
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
        except json.JSONDecodeError:
            return []

        diagnostics: list[NormalizedDiagnostic] = []

        for diag in doc.get("generalDiagnostics", []):
            severity_str = diag.get("severity", "error").lower()
            severity = _SEVERITY_MAP.get(severity_str, Severity.error)

            # Pyright uses 0-indexed LSP-style ranges → convert to 1-indexed.
            range_obj = diag.get("range", {})
            start_obj = range_obj.get("start", {})
            end_obj = range_obj.get("end", {})

            # line: 0-indexed → 1-indexed; character: 0-indexed → 1-indexed
            start_line: int = start_obj.get("line", 0) + 1
            start_col: Optional[int] = start_obj.get("character", 0) + 1

            # end position: convert only if present
            end_line: Optional[int] = (
                end_obj.get("line", 0) + 1 if end_obj else None
            )
            end_col: Optional[int] = (
                end_obj.get("character", 0) + 1 if end_obj else None
            )

            # Pyright prints absolute paths; resolve anyway to normalize
            # drive-letter case and separators on Windows.
            file_raw: str = diag.get("file", "")
            file_path = resolve_path(file_raw, raw.cwd) if file_raw else ""

            # Store the per-diagnostic dict, not the full generalDiagnostics array.
            raw_str = json.dumps(diag)

            diagnostics.append(
                NormalizedDiagnostic(
                    checker="pyright",
                    checker_version=raw.version,
                    file=file_path,
                    start_line=start_line,
                    start_col=start_col,
                    end_line=end_line,
                    end_col=end_col,
                    severity=severity,
                    code=diag.get("rule") or None,  # rule is nullable
                    message=diag.get("message", ""),
                    raw=raw_str,
                )
            )

        return diagnostics
