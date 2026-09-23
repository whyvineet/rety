"""
ty adapter for rety.

Invocation:
    ty check --output-format concise <paths>

Output format:
    ty (Astral, pre-1.0 as of September 2026) has no JSON or SARIF diagnostic
    output as of the current CLI reference (docs dated Sept 21, 2026). This
    adapter uses --output-format concise, which emits one diagnostic per line.

    ⚠ FORMAT UNVERIFIED AGAINST REAL OUTPUT — READ BEFORE EDITING ⚠
    The regex in this file was derived from ty's documented format description
    ("print diagnostics concisely, one per line"), not from captured real bytes.
    Before relying on this parser in production:

        1. Run: ty check --output-format concise tests/fixtures/untyped_function.py
        2. Save the exact output to: tests/fixtures/captured/ty/untyped_function.txt
        3. Verify that _CONCISE_RE matches every line
        4. Update the regex and this docstring accordingly

    This is explicitly a Phase 1 task.

Structured commands used:
    detect_version(): `ty version --output-format json`
        ty provides structured JSON for version information, so no text parsing
        is needed for version detection. This is the correct approach.

    Future crosswalk (v0.2):
        `ty explain rule --output-format json <code>`
        Returns machine-readable rule descriptions. Use this as the data source
        when building the error-code crosswalk table, rather than scraping docs.

Capabilities:
    has_end_col = False  (concise format: no end position)
    has_codes   = True   ([code] suffix is present on most diagnostics)
    uses_json   = False  (diagnostic output is text)
    uses_text_parser = True

Future-proofing:
    When ty adds --output-format json (likely, given Astral's pattern with ruff
    and other tools), the migration path is:
    1. Add a JSON parser method to TyAdapter
    2. Update run() to pass --output-format json
    3. Update capabilities (has_end_col=True, uses_json=True, uses_text_parser=False)
    4. Deprecate _parse_concise (keep for older ty versions behind a version check)
    This adapter is isolated precisely to make that a one-file change.

Cache behavior:
    ty does not appear to have file-system incremental caching that could leak
    flags across rety invocations (it uses Salsa in-memory incremental computation).
    Verify in Phase 0 for any CI-specific state.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import warnings
from pathlib import Path
from typing import Optional

from rety.adapters.base import AdapterCapabilities, CheckerAdapter
from rety.schema import NormalizedDiagnostic, RawInvocation, Severity

# ---------------------------------------------------------------------------
# ty concise format regex
#
# ⚠ UNVERIFIED AGAINST REAL OUTPUT — SEE MODULE DOCSTRING ⚠
#
# Assumed format from ty docs: file:line:col: severity message [code]
# Example (assumed): src/foo.py:10:5: error Missing return statement [return-value]
#
# Named groups:
#   file     — path (may contain colons on Windows, but MVP is Linux/macOS)
#   line     — 1-indexed line number
#   col      — 1-indexed column number
#   severity — "error", "warning", "note", "info" (verify exact vocab in Phase 1)
#   message  — diagnostic message text
#   code     — rule name in brackets, optional
# ---------------------------------------------------------------------------
_CONCISE_RE = re.compile(
    r"^(?P<file>.+?)"                               # file path (non-greedy)
    r":(?P<line>\d+)"                               # :line
    r":(?P<col>\d+)"                                # :col
    r":\s+"                                         # ": "
    r"(?P<severity>error|warning|note|info(?:rmation)?)"  # severity keyword
    r"\s+"                                          # space
    r"(?P<message>.+?)"                             # message (non-greedy)
    r"(?:\s+\[(?P<code>[^\]]+)\])?"                 # optional [code]
    r"\s*$"                                         # end of line
)

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

    The load-bearing fact about ty: no JSON output as of September 2026.
    This adapter is a first-class text-format parser, not a stub or workaround.
    See module docstring for full context.
    """

    @property
    def name(self) -> str:
        return "ty"

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            has_end_col=False,    # concise format has no end position
            has_codes=True,       # [code] suffix present on most diagnostics
            uses_json=False,      # diagnostic output is text, not JSON
            uses_text_parser=True,
        )

    def detect_version(self) -> Optional[str]:
        """
        Use `ty version --output-format json` for structured version detection.

        ty provides machine-readable JSON for version info — use it rather than
        parsing plain text. Falls back to plain `ty version` if JSON fails.
        """
        # Primary: structured JSON output
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

        # Fallback: plain text `ty version`
        try:
            result = subprocess.run(
                ["ty", "version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # Assumed format: "ty 0.0.7" (verify in Phase 0)
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
        )

    def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        """Parse ty's concise text output."""
        return self._parse_concise(raw)

    def _parse_concise(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        """
        Parse ty's --output-format concise text output line by line.

        ⚠ The regex was derived from ty's documented format description, not
        from verified real output. If unmatched lines are seen, update _CONCISE_RE
        after capturing real ty output in tests/fixtures/captured/ty/.
        """
        diagnostics: list[NormalizedDiagnostic] = []
        unmatched: list[str] = []

        for line in raw.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            m = _CONCISE_RE.match(stripped)
            if not m:
                unmatched.append(stripped[:120])
                continue

            severity_str = m.group("severity").lower()
            severity = _SEVERITY_MAP.get(severity_str, Severity.error)

            col_raw = int(m.group("col"))
            start_col: Optional[int] = col_raw if col_raw != 0 else None

            # Resolve relative paths (ty outputs relative paths when invoked with
            # relative paths — this is typical for concise text-format tools)
            file_raw = m.group("file")
            file_path = str(Path(file_raw).resolve())

            diagnostics.append(
                NormalizedDiagnostic(
                    checker="ty",
                    checker_version=raw.version,
                    file=file_path,
                    start_line=int(m.group("line")),
                    start_col=start_col,
                    end_line=None,   # concise format has no end position
                    end_col=None,    # concise format has no end position
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
                f"This likely means the regex needs updating for your ty version. "
                f"Capture real ty output in tests/fixtures/captured/ty/ and update "
                f"_CONCISE_RE. First unmatched line: {unmatched[0]!r}",
                RuntimeWarning,
                stacklevel=2,
            )

        return diagnostics
