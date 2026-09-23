"""
Pyrefly adapter for rety.

Invocation:
    pyrefly check --output-format json <paths>

Output format:
    Pyrefly (1.0 released May 2026, monthly cadence) emits JSON. The exact
    schema is verified against the pinned version in tests/CHECKER_VERSIONS.md.
    This adapter is written defensively because Pyrefly's schema stability
    across monthly releases is unproven at time of writing.

    Known output variants handled:
    - Top-level JSON array of diagnostic objects
    - Top-level JSON object with a "diagnostics" or "errors" key
    - JSON-lines (fallback, in case a future version changes the format)

    Pyrefly also supports: github, junit-xml, sarif, min-text, full-text,
    omit-errors. The "sarif" format is the only native SARIF 2.1.0 emitter
    among the four checkers — worth noting for v0.2 SARIF renderer work.

    Pyrefly also exposes `pyrefly coverage report` for annotation-completeness
    metrics, and baseline files for regression detection. Neither is used by
    rety in v0.1, but the baseline mechanism is relevant to the planned
    regression-detection feature in v0.3+.

Cache behavior:
    Verify in Phase 0 whether Pyrefly has incremental caching that could leak
    flags across long-lived CI runners (similar to mypy's stale-cache bug).

Phase 0 note:
    Run `pyrefly check --output-format json tests/fixtures/untyped_function.py`
    and capture the raw bytes to tests/fixtures/captured/pyrefly/. Update this
    adapter's field names to match actual output before relying on parse().
"""

from __future__ import annotations

import json
import subprocess
import time
import warnings
from pathlib import Path
from typing import Any, Optional

from rety.adapters.base import AdapterCapabilities, CheckerAdapter
from rety.schema import NormalizedDiagnostic, RawInvocation, Severity

_SEVERITY_MAP: dict[str, Severity] = {
    "error": Severity.error,
    "warning": Severity.warning,
    "note": Severity.note,
    "information": Severity.information,
    "info": Severity.information,
}


class PyreflyAdapter(CheckerAdapter):
    """Adapter for Pyrefly (https://pyrefly.org/)."""

    @property
    def name(self) -> str:
        return "pyrefly"

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
        Run `pyrefly --version` and parse the version string.

        Pyrefly --version output format: "pyrefly 1.3.0" (verify in Phase 0).
        """
        try:
            result = subprocess.run(
                ["pyrefly", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return parts[1]
                return result.stdout.strip() or None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def run(self, paths: list[str], cwd: str) -> RawInvocation:
        """Invoke Pyrefly with --output-format json."""
        version = self.detect_version()
        start = time.monotonic()

        cmd = ["pyrefly", "check", "--output-format", "json", *paths]
        result = self._run_subprocess(cmd, cwd)

        duration_ms = (time.monotonic() - start) * 1000
        return RawInvocation(
            checker="pyrefly",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            version=version,
        )

    def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        """
        Parse Pyrefly JSON output into NormalizedDiagnostic instances.

        Handles three output variants: JSON array, JSON object with a key,
        and JSON-lines fallback. Defensive field access because Pyrefly's
        schema may change across monthly releases.
        """
        stdout = raw.stdout.strip()
        if not stdout:
            return []

        # Try single-document JSON first (most common)
        try:
            doc = json.loads(stdout)
            return self._parse_document(doc, raw.version)
        except json.JSONDecodeError:
            pass

        # Fall back to JSON-lines
        diagnostics: list[NormalizedDiagnostic] = []
        parse_errors = 0
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                diag = self._parse_single(obj, raw.version)
                if diag is not None:
                    diagnostics.append(diag)
            except json.JSONDecodeError:
                parse_errors += 1

        if parse_errors:
            warnings.warn(
                f"pyrefly adapter: {parse_errors} line(s) could not be parsed as JSON. "
                f"This may indicate a Pyrefly version change. "
                f"Check tests/fixtures/captured/pyrefly/ for expected output samples.",
                RuntimeWarning,
                stacklevel=2,
            )

        return diagnostics

    def _parse_document(
        self,
        doc: Any,
        version: Optional[str],
    ) -> list[NormalizedDiagnostic]:
        """Parse a single decoded JSON document (array or object)."""
        if isinstance(doc, list):
            items = doc
        elif isinstance(doc, dict):
            # Try common top-level keys for the diagnostic list
            items = doc.get("diagnostics", doc.get("errors", doc.get("results", None)))
            if items is None:
                # Maybe the dict IS a single diagnostic
                items = [doc]
        else:
            return []

        diagnostics = []
        for item in items:
            diag = self._parse_single(item, version)
            if diag is not None:
                diagnostics.append(diag)
        return diagnostics

    def _parse_single(
        self,
        obj: Any,
        version: Optional[str],
    ) -> Optional[NormalizedDiagnostic]:
        """
        Parse a single Pyrefly diagnostic object.

        Field names are accessed defensively using multiple fallback keys
        because Pyrefly's schema is young and field names are unconfirmed
        against real output. Update to use exact field names after Phase 0.
        """
        if not isinstance(obj, dict):
            return None

        # Severity — try multiple field name patterns
        severity_raw = (
            obj.get("severity")
            or obj.get("kind")
            or obj.get("level")
            or "error"
        )
        severity = _SEVERITY_MAP.get(str(severity_raw).lower(), Severity.error)

        # File path
        file_raw: str = (
            obj.get("path") or obj.get("file") or obj.get("filename") or ""
        )
        file_path = str(Path(file_raw).resolve()) if file_raw else ""

        # Line and column — try multiple field name patterns
        start_line = int(obj.get("line", obj.get("start_line", obj.get("row", 1))))
        start_col_raw = obj.get("col", obj.get("column", obj.get("start_col", obj.get("start_column"))))
        start_col: Optional[int] = _coerce_col(start_col_raw)

        end_line_raw = obj.get("end_line", obj.get("end_row"))
        end_line: Optional[int] = _coerce_col(end_line_raw)

        end_col_raw = obj.get("end_col", obj.get("end_column", obj.get("end_character")))
        end_col: Optional[int] = _coerce_col(end_col_raw)

        # Error code
        code_raw = (
            obj.get("code")
            or obj.get("error_code")
            or obj.get("rule")
            or obj.get("name")
        )
        code = str(code_raw) if code_raw is not None else None

        # Message
        message = str(
            obj.get("message", obj.get("description", obj.get("text", "")))
        )

        return NormalizedDiagnostic(
            checker="pyrefly",
            checker_version=version,
            file=file_path,
            start_line=start_line,
            start_col=start_col,
            end_line=end_line,
            end_col=end_col,
            severity=severity,
            code=code,
            message=message,
            raw=json.dumps(obj),  # per-diagnostic sub-object
        )


def _coerce_col(value: Any) -> Optional[int]:
    """
    Convert a column/line value to int|None, treating 0 as None.
    Pyrefly's schema is unconfirmed — defensive coercion prevents crashes.
    """
    if value is None:
        return None
    try:
        v = int(value)
        return None if v == 0 else v
    except (ValueError, TypeError):
        return None
