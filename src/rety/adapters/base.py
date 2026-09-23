"""
rety CheckerAdapter base class and AdapterCapabilities descriptor.

All checker adapters implement the CheckerAdapter abstract base class.
The capabilities descriptor advertises what structured data the adapter
can produce, so callers can adjust expectations (e.g., don't expect
end_col from ty's concise format).

Extension point:
    To add a new checker adapter (e.g., basedpyright, Zuban):
    1. Create rety/adapters/<name>.py implementing CheckerAdapter
    2. Add it to ALL_ADAPTERS in rety/adapters/__init__.py
    3. Add captured output fixtures in tests/fixtures/captured/<name>/
    4. Add adapter unit tests in tests/adapters/test_<name>.py
    A formal plugin/entry-point system is not provided in v0.1.
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rety.schema import NormalizedDiagnostic, RawInvocation


def resolve_path(file_raw: str, cwd: Optional[str]) -> str:
    """
    Resolve a checker-reported path to an absolute, normalized path.

    Relative paths are resolved against `cwd` (the directory the checker
    subprocess ran in, carried on RawInvocation.cwd), not against the rety
    process's own working directory. Library callers that pass a different
    cwd to run_checkers() therefore get correct file paths. With cwd None the
    process cwd is used.
    """
    path = Path(file_raw)
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return str(path.resolve())


@dataclass(frozen=True)
class AdapterCapabilities:
    """
    Describes what structured data a checker adapter can produce.

    Used by the alignment engine and renderers to adjust expectations —
    e.g., don't penalize a ty cluster for missing end_col when ty's
    concise format doesn't emit end positions.
    """

    has_end_col: bool = False
    """True if the checker reliably reports end_col for most diagnostics."""

    has_codes: bool = False
    """True if the checker emits machine-readable error/rule codes."""

    uses_json: bool = True
    """
    True if diagnostic output is JSON (or JSON-lines). False for checkers
    that use a text format as their primary structured output (ty today).
    """

    uses_text_parser: bool = False
    """
    True if the adapter uses a regex/line parser on text output rather than
    a JSON deserializer. Mutually exclusive concern from uses_json — a checker
    could have JSON for some output types and text for diagnostics.
    """


class CheckerAdapter(ABC):
    """
    Abstract base class for a single type checker integration.

    Each concrete subclass wraps one checker's subprocess invocation and
    output parsing, and returns NormalizedDiagnostic instances that conform
    to the shared schema in rety.schema.

    Lifecycle per rety run:
        1. is_available() — detect_version() returns non-None
        2. run(paths, cwd) — invoke the checker subprocess
        3. parse(raw) — parse raw output into normalized diagnostics

    Adapters must be stateless — instantiated once, usable multiple times.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Checker name: "mypy", "pyright", "pyrefly", or "ty"."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        """Structured description of what this adapter can produce."""
        ...

    @abstractmethod
    def detect_version(self) -> Optional[str]:
        """
        Detect the installed version of this checker.

        Returns:
            Version string (e.g. "1.11.2") if the checker is installed
            and version detection succeeded; None otherwise.

        Must not raise — callers use None to mean "checker unavailable".
        """
        ...

    @abstractmethod
    def run(self, paths: list[str], cwd: str) -> RawInvocation:
        """
        Invoke the checker subprocess on the given paths.

        Args:
            paths: File or directory paths to analyze. These are the literal
                   arguments passed to the checker CLI.
            cwd:   Working directory for the subprocess — must be the directory
                   where `rety` was invoked, so the checker's native config
                   discovery (pyrightconfig.json, mypy.ini, etc.) finds the
                   same config it would if invoked directly from that shell.

        Returns:
            RawInvocation with stdout, stderr, returncode, and timing.
            A non-zero returncode is normal when diagnostics are found.
        """
        ...

    @abstractmethod
    def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        """
        Parse the raw subprocess output into normalized diagnostics.

        Args:
            raw: The RawInvocation returned by run().

        Returns:
            List of NormalizedDiagnostic. May be empty if the checker found
            no issues or if parsing fails gracefully.

        Must not raise on malformed input — emit a warning and return partial
        results instead. The caller cannot distinguish "checker had no issues"
        from "parse failed silently," so warnings are the only signal.
        """
        ...

    def is_available(self) -> bool:
        """Return True if this checker is installed and detectable."""
        return self.detect_version() is not None

    def _run_subprocess(
        self,
        cmd: list[str],
        cwd: str,
        *,
        timeout: Optional[int] = None,
    ) -> subprocess.CompletedProcess[str]:
        """
        Shared subprocess runner used by concrete adapters.

        Captures stdout and stderr as text. Does not raise on non-zero
        returncode — type checkers exit non-zero when diagnostics are found,
        which is expected and normal.
        """
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
