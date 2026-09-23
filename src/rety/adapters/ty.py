"""
ty adapter for rety.

Invocation:
    ty check --output-format concise <paths>

Output format (verified against ty 0.0.83 on 2026-09-24):
    One diagnostic per line on stdout:

        <path>:<line>:<col>: <severity>[<rule>] <message>

    For example:

        tests/fixtures/basic_errors.py:9:12: error[invalid-return-type] Return type does not match returned value: expected `int`, found `str`

    Lines and columns are 1-indexed. There is no end position. After the
    diagnostics ty prints a summary line on stdout ("Found 3 diagnostics" or
    "All checks passed!"), which the parser skips.

    ty has no JSON diagnostic format that carries positions in a checker-
    neutral shape (``--output-format gitlab`` is GitLab Code Quality JSON;
    ``github`` and ``junit`` are CI-oriented). ``concise`` is the most direct
    format for this adapter. Real captured output lives in
    tests/fixtures/captured/ty/.

Structured commands used:
    detect_version(): ``ty version --output-format json`` returns
    ``{"version": "0.0.83", "commit_info": {...}}``. Falls back to plain
    ``ty version`` ("ty 0.0.83 (9c214798c 2026-09-21)").

    Future crosswalk (v0.2):
        ``ty explain rule --output-format json <code>`` returns machine-
        readable rule descriptions.

Capabilities:
    has_end_col = False  (concise format has no end position)
    has_codes   = True   (the [rule] suffix is present on every diagnostic)
    uses_json   = False  (diagnostic output is text)
    uses_text_parser = True

Cache behavior:
    ty keeps its incremental state in memory (Salsa); there is no on-disk
    cache that could leak flags between rety invocations.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import warnings
from typing import Optional

from rety.adapters.base import AdapterCapabilities, CheckerAdapter, resolve_path
from rety.schema import NormalizedDiagnostic, RawInvocation, Severity

# ---------------------------------------------------------------------------
# ty concise format regex
#
# Verified against real output (tests/fixtures/captured/ty/basic_errors.txt):
#   tests/fixtures/basic_errors.py:9:12: error[invalid-return-type] Return type ...
#
# Named groups:
#   file     — path; non-greedy so a Windows drive-letter colon stays in the path
#   line     — 1-indexed line number
#   col      — 1-indexed column number
#   severity — "error", "warning", "note", "info"/"information"
#   code     — rule name in the brackets directly after the severity (optional,
#              so a future code-less line still parses)
#   message  — the rest of the line
# ---------------------------------------------------------------------------
_CONCISE_RE = re.compile(
    r"^(?P<file>.+?)"
    r":(?P<line>\d+)"
    r":(?P<col>\d+)"
    r":\s+"
    r"(?P<severity>error|warning|note|info(?:rmation)?)"
    r"(?:\[(?P<code>[^\]]+)\])?"
    r":?\s*"
    r"(?P<message>.*?)"
    r"\s*$"
)

# Summary lines ty prints on stdout after the diagnostics. Not diagnostics.
_SUMMARY_RE = re.compile(r"^(All checks passed!|Found \d+ diagnostics?)$")

_SEVERITY_MAP: dict[str, Severity] = {
    "error": Severity.error,
    "warning": Severity.warning,
    "note": Severity.note,
    "info": Severity.information,
    "information": Severity.information,
}


class TyAdapter(CheckerAdapter):
    """
    Adapter for ty (https://github.com/astral-sh/ty).

    ty has no positional JSON output, so this adapter is a first-class text
    parser for the ``concise`` format. See the module docstring.
    """

    @property
    def name(self) -> str:
        return "ty"

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            has_end_col=False,
            has_codes=True,
            uses_json=False,
            uses_text_parser=True,
        )

    def detect_version(self) -> Optional[str]:
        """
        Use ``ty version --output-format json`` for structured version detection.

        Falls back to plain ``ty version`` if the JSON form fails.
        """
        try:
            result = subprocess.run(
                ["ty", "version", "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                doc = json.loads(result.stdout)
                version = doc.get("version")
                if version:
                    return str(version)
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
            pass

        try:
            result = subprocess.run(
                ["ty", "version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # "ty 0.0.83 (9c214798c 2026-09-21)"
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return parts[1]
                return result.stdout.strip() or None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return None

    def run(self, paths: list[str], cwd: str) -> RawInvocation:
        """Invoke ty with --output-format concise."""
        version = self.detect_version()
        start = time.monotonic()

        cmd = ["ty", "check", "--output-format", "concise", *paths]
        result = self._run_subprocess(cmd, cwd)

        duration_ms = (time.monotonic() - start) * 1000
        return RawInvocation(
            checker="ty",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            version=version,
            cwd=cwd,
        )

    def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        """
        Parse ty's ``--output-format concise`` text output line by line.

        Summary lines ("Found N diagnostics", "All checks passed!") are
        skipped silently. Any other line that does not match the concise
        format is skipped with a RuntimeWarning so a format change in a new
        ty release is visible rather than silent.
        """
        diagnostics: list[NormalizedDiagnostic] = []
        unmatched: list[str] = []

        for line in raw.stdout.splitlines():
            stripped = line.strip()
            if not stripped or _SUMMARY_RE.match(stripped):
                continue

            m = _CONCISE_RE.match(stripped)
            if not m:
                unmatched.append(stripped[:120])
                continue

            severity = _SEVERITY_MAP.get(m.group("severity").lower(), Severity.error)

            col_raw = int(m.group("col"))
            start_col: Optional[int] = col_raw if col_raw != 0 else None

            # ty prints paths relative to its cwd when given relative paths;
            # resolve against the invocation cwd recorded on RawInvocation.
            file_path = resolve_path(m.group("file"), raw.cwd)

            diagnostics.append(
                NormalizedDiagnostic(
                    checker="ty",
                    checker_version=raw.version,
                    file=file_path,
                    start_line=int(m.group("line")),
                    start_col=start_col,
                    end_line=None,  # concise format has no end position
                    end_col=None,
                    severity=severity,
                    code=m.group("code") or None,
                    message=m.group("message").strip(),
                    raw=line,  # original text line
                )
            )

        if unmatched:
            warnings.warn(
                f"ty adapter: {len(unmatched)} line(s) did not match the concise "
                f"format regex (_CONCISE_RE in rety/adapters/ty.py). "
                f"This likely means the format changed in your ty version. "
                f"Capture real ty output in tests/fixtures/captured/ty/ and update "
                f"_CONCISE_RE. First unmatched line: {unmatched[0]!r}",
                RuntimeWarning,
                stacklevel=2,
            )

        return diagnostics
