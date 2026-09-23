"""
rety concurrent checker runner.

Working directory (cwd) contract — IMPORTANT:
    All checker subprocesses are run with cwd = the directory from which the
    user invoked `rety` (i.e., os.getcwd() captured at CLI entry time, then
    passed explicitly through to here). This ensures each checker's upward-
    walking native config discovery finds the same config it would find if
    invoked directly from the same shell:

      - mypy:     reads mypy.ini / pyproject.toml [tool.mypy] / setup.cfg
      - Pyright:  walks upward for pyrightconfig.json / pyproject.toml [tool.pyright]
      - Pyrefly:  reads pyrefly.toml / pyproject.toml [tool.pyrefly]
      - ty:       reads ty.toml / pyproject.toml [tool.ty]

    Using the target file's directory as cwd (a tempting alternative) would
    silently select the wrong config when the user's project root and the
    target file's directory differ, poisoning the comparison.

    Never use os.getcwd() inside this module — always accept cwd as a parameter.

Concurrency model:
    Uses concurrent.futures.ThreadPoolExecutor. Subprocess.run is blocking,
    so thread-pool parallelism is appropriate. asyncio.subprocess would also
    work but introduces event-loop re-entry complexity when rety is used as
    a library. ThreadPoolExecutor with len(adapters) workers gives the same
    wall-clock benefit: all checkers run concurrently.

    Concurrency is meaningful here because mypy is significantly slower than
    Pyrefly/ty (pure-Python interpreter vs. Rust). Running them concurrently
    means the wall-clock time is ~max(checker_times), not ~sum(checker_times).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from rety.adapters.base import CheckerAdapter
from rety.schema import NormalizedDiagnostic, RawInvocation


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CheckerUnavailableError(Exception):
    """
    Raised when a required checker is not installed or not on PATH.

    Provides a concrete install hint so the user knows exactly what to do.
    """

    _INSTALL_HINTS: dict[str, str] = {
        "mypy": "pip install mypy  OR  uv tool install mypy",
        "pyright": "npm install -g pyright  OR  pip install pyright",
        "pyrefly": "pip install pyrefly  OR  uv tool install pyrefly",
        "ty": "pip install ty  OR  uv tool install ty",
    }

    def __init__(self, checker_name: str) -> None:
        hint = self._INSTALL_HINTS.get(checker_name, f"Install {checker_name}")
        super().__init__(
            f"Checker '{checker_name}' is not installed or not found on PATH.\n"
            f"To install: {hint}"
        )
        self.checker_name = checker_name


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class CheckerResult:
    """
    The result of running and parsing a single checker.

    Attributes:
        adapter:      The adapter that produced this result.
        invocation:   Raw subprocess invocation details (stdout, stderr, timing).
        diagnostics:  Normalized diagnostics parsed from the invocation output.
        error:        Set if the adapter encountered an unexpected error during
                      run() or parse(). Does NOT include non-zero returncode from
                      the checker (that's normal when diagnostics are found).
    """

    adapter: CheckerAdapter
    invocation: RawInvocation
    diagnostics: list[NormalizedDiagnostic]
    error: Optional[Exception] = field(default=None)

    @property
    def checker_name(self) -> str:
        return self.adapter.name

    @property
    def succeeded(self) -> bool:
        """True if no unexpected error occurred (non-zero returncode is OK)."""
        return self.error is None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_checkers(
    adapters: list[CheckerAdapter],
    paths: list[str],
    cwd: Optional[str] = None,
    require_all: bool = False,
) -> list[CheckerResult]:
    """
    Run the given checker adapters concurrently and return their results.

    Args:
        adapters:    Adapter instances to run. Callers should pre-filter to
                     the checkers they want; all provided adapters are attempted.
        paths:       File or directory paths to analyze. Passed as-is to each
                     checker's CLI. Relative paths are resolved by the checker
                     relative to cwd.
        cwd:         Working directory for subprocess invocations. MUST be the
                     directory where `rety` was invoked (os.getcwd() at CLI entry
                     time), not a target file's directory. Defaults to os.getcwd()
                     if not provided, but callers should always pass this explicitly
                     to avoid implicit dependency on the process cwd changing.
        require_all: If True, raise CheckerUnavailableError for any adapter that
                     is not installed. If False (default), unavailable adapters
                     are silently skipped — only available ones appear in results.

    Returns:
        List of CheckerResult, one per available adapter that was run, in the
        same order as `adapters`. Execution is concurrent, but the result
        order never depends on which checker finished first, so headers and
        JSON reports are stable from run to run.

    Raises:
        CheckerUnavailableError: If require_all=True and any adapter is unavailable.
    """
    effective_cwd = cwd if cwd is not None else os.getcwd()

    # Partition into available / unavailable before spawning threads
    available: list[CheckerAdapter] = []
    for adapter in adapters:
        if adapter.is_available():
            available.append(adapter)
        elif require_all:
            raise CheckerUnavailableError(adapter.name)
        # else: silently skip — caller can see which adapters are absent from results

    if not available:
        return []

    with ThreadPoolExecutor(
        max_workers=len(available),
        thread_name_prefix="rety-checker",
    ) as executor:
        futures = [
            executor.submit(_run_one, adapter, paths, effective_cwd)
            for adapter in available
        ]
        # Collect in submission order, not completion order.
        return [future.result() for future in futures]


def _run_one(
    adapter: CheckerAdapter,
    paths: list[str],
    cwd: str,
) -> CheckerResult:
    """
    Run a single adapter synchronously (called from a worker thread).

    Catches all exceptions from run() and parse() and returns them as
    CheckerResult.error rather than propagating, so one failing adapter
    doesn't abort the others.
    """
    try:
        invocation = adapter.run(paths, cwd)
        diagnostics = adapter.parse(invocation)
        return CheckerResult(
            adapter=adapter,
            invocation=invocation,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        # Construct a minimal RawInvocation to satisfy the dataclass contract
        dummy = RawInvocation(
            checker=adapter.name,
            returncode=-1,
            stdout="",
            stderr=str(exc),
            duration_ms=0.0,
            version=None,
        )
        return CheckerResult(
            adapter=adapter,
            invocation=dummy,
            diagnostics=[],
            error=exc,
        )
