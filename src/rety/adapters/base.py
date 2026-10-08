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

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rety.schema import NormalizedDiagnostic, RawInvocation

_WINDOWS_ABS_PATH_RE = re.compile(r"^[A-Za-z]:[/\\]")


def resolve_path(file_raw: str, cwd: str | None) -> str:
    """
    Resolve a checker-reported path to an absolute, normalized path.

    Relative paths are resolved against `cwd` (the directory the checker
    subprocess ran in, carried on RawInvocation.cwd), not against the rety
    process's own working directory. Library callers that pass a different
    cwd to run_checkers() therefore get correct file paths. With cwd None the
    process cwd is used.

    Every path goes through Path.resolve(), so on Windows the drive letter
    case and separators are normalized: Pyright reports "d:\\x.py" where the
    other checkers report "D:\\x.py", and the alignment engine groups by exact
    path string. On other hosts a Windows-style path (from captured fixtures)
    cannot be resolved meaningfully and is returned unchanged.
    """
    if os.name != "nt" and _WINDOWS_ABS_PATH_RE.match(file_raw):
        return file_raw
    path = Path(file_raw)
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return str(path.resolve())


@dataclass(frozen=True)
class AdapterCapabilities:
    """
    Describes what structured data a checker adapter can produce.

    Informational, for library callers and adapter authors. The alignment
    engine does not read it: it works from each diagnostic's own fields, so
    a missing end position is visible as end_line/end_col being None (and a
    ty diagnostic with no end line is matched on its start line instead of
    being capped at MEDIUM confidence).
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
        1. is_available() — version() returns non-None
        2. run(paths, cwd) — invoke the checker subprocess
        3. parse(raw) — parse raw output into normalized diagnostics

    Adapters hold only configuration (the subprocess timeout) and a memoized
    version string. They can be reused across runs.
    """

    #: Seconds to wait for the checker subprocess in run(). None = no limit.
    timeout: float | None = None

    #: Seconds to wait for a `--version` probe before treating the checker as
    #: unavailable. Generous because Pyright's node startup can be slow on a
    #: loaded machine.
    version_probe_timeout: float = 30.0

    #: Exit codes that mean "the checker ran to completion" (0 = clean,
    #: 1 = diagnostics found for every supported checker). Any other code with
    #: no parsed diagnostics is treated as a failed run, not as "no issues".
    ok_returncodes: frozenset[int] = frozenset({0, 1})

    _version_cache: str | None = None
    _version_probed: bool = False

    def __init__(self, timeout: float | None = None) -> None:
        self.timeout = timeout

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
    def detect_version(self) -> str | None:
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

    @property
    def executable(self) -> str:
        """
        The checker's executable, resolved through shutil.which.

        subprocess.run(["pyright", ...]) on Windows only finds .exe files, but
        npm installs Pyright as pyright.cmd. shutil.which honours PATHEXT and
        returns the .cmd shim. Falls back to the bare name so a missing tool
        still surfaces as FileNotFoundError, i.e. "not installed".
        """
        return shutil.which(self.name) or self.name

    def version(self) -> str | None:
        """
        detect_version(), memoized per instance.

        The runner calls is_available() and run() calls version(); without
        memoization every checker would be probed twice per rety run.
        """
        if not self._version_probed:
            self._version_cache = self.detect_version()
            self._version_probed = True
        return self._version_cache

    def is_available(self) -> bool:
        """Return True if this checker is installed and detectable."""
        return self.version() is not None

    def _run_subprocess(
        self,
        cmd: list[str],
        cwd: str,
        *,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """
        Shared subprocess runner used by concrete adapters.

        Captures stdout and stderr as UTF-8 text. Does not raise on non-zero
        returncode — type checkers exit non-zero when diagnostics are found,
        which is expected and normal. Raises subprocess.TimeoutExpired if the
        checker exceeds `timeout` (default: self.timeout); the runner turns
        that into a CheckerResult.error.

        The checker runs in its own process group so that a timeout (or
        kill_running_checkers() on Ctrl+C) kills the whole process tree.
        subprocess.run(timeout=...) kills only the direct child: Pyright runs
        as a wrapper (pyright.cmd or the PyPI shim) around `node`, and on
        Windows run() then blocks in communicate() until the orphaned node
        process exits and releases the pipes.
        """
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Checkers write UTF-8. Without an explicit encoding, text=True
            # decodes with the locale code page (cp1252 on Windows), which
            # turns Pyright's non-breaking spaces into "Â " mojibake.
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            **_new_process_group_kwargs(),
        )
        with _running_lock:
            _running.add(proc)
        try:
            stdout, stderr = proc.communicate(
                timeout=timeout if timeout is not None else self.timeout
            )
        except subprocess.TimeoutExpired as exc:
            _kill_tree(proc)
            exc.stdout, exc.stderr = proc.communicate()
            raise
        except BaseException:
            _kill_tree(proc)
            raise
        finally:
            with _running_lock:
                _running.discard(proc)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


# ---------------------------------------------------------------------------
# Process-tree management
# ---------------------------------------------------------------------------

_running: set[subprocess.Popen[str]] = set()
_running_lock = threading.Lock()


def _new_process_group_kwargs() -> dict[str, Any]:
    """Popen arguments that put the child in a new process group / session."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill proc and every process it started. Never raises."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=30,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()  # fallback if the tree kill was unavailable
    except OSError:
        pass


def kill_running_checkers() -> None:
    """
    Kill every checker subprocess still running. Called on Ctrl+C: because
    checkers run in their own process group, the terminal's interrupt does
    not reach them.
    """
    with _running_lock:
        procs = list(_running)
    for proc in procs:
        _kill_tree(proc)
