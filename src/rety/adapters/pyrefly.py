"""
Pyrefly adapter for rety.

Invocation:
    pyrefly check --output-format json <paths>

Output format (verified against Pyrefly 1.3.1 on 2026-09-24):
    A single JSON document on stdout:

    {
      "errors": [
        {
          "line": 9,
          "column": 12,
          "stop_line": 9,
          "stop_column": 16,
          "path": "tests/fixtures/basic_errors.py",
          "code": -2,
          "name": "bad-return",
          "description": "Returned type `str` is not assignable to declared return type `int`",
          "concise_description": "Returned type `str` is not assignable to declared return type `int`",
          "severity": "error"
        }
      ]
    }

    - line/column and stop_line/stop_column are 1-indexed. stop_column is an
      exclusive end, the same convention rety uses for mypy (end_column + 1)
      and Pyright (end.character + 1).
    - "name" is the rule name ("bad-return", "unknown-name", ...). "code" is
      an internal integer (-2 in every observed diagnostic) and is NOT the
      rule; the adapter ignores it.
    - "description" may span several lines; "concise_description" is the
      one-line form. rety stores "description".
    - Paths are relative to the invocation cwd when relative paths are given.
    - The "INFO N errors" summary and config notices go to stderr, not stdout.

    Real captured output lives in tests/fixtures/captured/pyrefly/.

    Pyrefly also supports: github, junit-xml, sarif, min-text, full-text,
    omit-errors. sarif is the only native SARIF 2.1.0 emitter among the four
    checkers, which is relevant to a future SARIF renderer.

Cache behavior:
    Pyrefly has no on-disk cache that could carry flags between invocations.
"""  # noqa: E501 -- docstring quotes real checker output verbatim

from __future__ import annotations

import json
import subprocess
import time
import warnings
from typing import Any

from rety.adapters.base import AdapterCapabilities, CheckerAdapter, resolve_path
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

    def detect_version(self) -> str | None:
        """
        Run ``pyrefly --version`` and parse the version string.

        Output format: "pyrefly 1.3.1".
        """
        try:
            result = subprocess.run(
                [self.executable, "--version"],
                capture_output=True,
                text=True,
                timeout=self.version_probe_timeout,
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
        version = self.version()
        start = time.monotonic()

        cmd = [self.executable, "check", "--output-format", "json", *paths]
        result = self._run_subprocess(cmd, cwd)

        duration_ms = (time.monotonic() - start) * 1000
        return RawInvocation(
            checker="pyrefly",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            version=version,
            cwd=cwd,
        )

    def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        """
        Parse Pyrefly's JSON document into NormalizedDiagnostic instances.

        Accepts the documented ``{"errors": [...]}`` shape and, defensively,
        a bare top-level list. Malformed JSON or malformed entries are
        skipped with a RuntimeWarning rather than raising.
        """
        stdout = raw.stdout.strip()
        if not stdout:
            return []

        try:
            doc = json.loads(stdout)
        except json.JSONDecodeError as exc:
            warnings.warn(
                f"pyrefly adapter: stdout is not valid JSON ({exc.msg} at char "
                f"{exc.pos}). This may indicate a Pyrefly version change; compare "
                f"against tests/fixtures/captured/pyrefly/. "
                f"First 120 chars: {stdout[:120]!r}",
                RuntimeWarning,
                stacklevel=2,
            )
            return []

        items = self._extract_items(doc)

        diagnostics: list[NormalizedDiagnostic] = []
        skipped = 0
        for item in items:
            diag = self._parse_single(item, raw.version, raw.cwd)
            if diag is None:
                skipped += 1
            else:
                diagnostics.append(diag)

        if skipped:
            warnings.warn(
                f"pyrefly adapter: {skipped} entr{'y' if skipped == 1 else 'ies'} "
                f"in the JSON output lacked a usable 'line' field and were skipped. "
                f"This may indicate a Pyrefly version change.",
                RuntimeWarning,
                stacklevel=2,
            )

        return diagnostics

    @staticmethod
    def _extract_items(doc: Any) -> list[Any]:
        """Return the list of diagnostic entries from a decoded JSON document."""
        if isinstance(doc, list):
            return doc
        if isinstance(doc, dict):
            for key in ("errors", "diagnostics"):
                value = doc.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _parse_single(
        self,
        obj: Any,
        version: str | None,
        raw_cwd: str | None = None,
    ) -> NormalizedDiagnostic | None:
        """Parse one Pyrefly diagnostic object; None if it has no usable line."""
        if not isinstance(obj, dict):
            return None

        start_line = _positive_int_or_none(obj.get("line"))
        if start_line is None:
            return None

        severity_raw = obj.get("severity") or "error"
        severity = _SEVERITY_MAP.get(str(severity_raw).lower(), Severity.error)

        file_raw = obj.get("path") or ""
        file_path = resolve_path(str(file_raw), raw_cwd) if file_raw else ""

        # 1-indexed already; 0 or negative means unknown.
        start_col = _positive_int_or_none(obj.get("column"))
        end_line = _positive_int_or_none(obj.get("stop_line"))
        end_col = _positive_int_or_none(obj.get("stop_column"))

        # "name" is the rule; the integer "code" field is not.
        name = obj.get("name")
        code = str(name) if name else None

        message = str(
            obj.get("description") or obj.get("concise_description") or ""
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


def _positive_int_or_none(value: Any) -> int | None:
    """Coerce a 1-indexed position to int; None for missing, non-int, or < 1."""
    if value is None or isinstance(value, bool):
        return None
    try:
        v = int(value)
    except (ValueError, TypeError):
        return None
    return v if v >= 1 else None
