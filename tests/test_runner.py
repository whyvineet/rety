"""
Tests for the concurrent checker runner (rety/runner.py) using fake adapters.
No real type checker is invoked.
"""

from __future__ import annotations

import subprocess
import time
from typing import Optional

import pytest

from rety.adapters.base import AdapterCapabilities, CheckerAdapter
from rety.runner import CheckerUnavailableError, run_checkers
from rety.schema import NormalizedDiagnostic, RawInvocation, Severity


class FakeAdapter(CheckerAdapter):
    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        delay: float = 0.0,
        fail: bool = False,
        timeout_after: Optional[float] = None,
        timeout: Optional[float] = None,
        severity: Severity = Severity.error,
    ) -> None:
        super().__init__(timeout=timeout)
        self._name = name
        self._available = available
        self._delay = delay
        self._fail = fail
        self._timeout_after = timeout_after
        self._severity = severity
        self.version_calls = 0
        self.seen_cwd: Optional[str] = None
        self.seen_paths: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities()

    def detect_version(self) -> Optional[str]:
        self.version_calls += 1
        return "1.0" if self._available else None

    def run(self, paths: list[str], cwd: str) -> RawInvocation:
        self.version()  # real adapters record the version on the invocation
        self.seen_cwd = cwd
        self.seen_paths = list(paths)
        time.sleep(self._delay)
        if self._timeout_after is not None:
            raise subprocess.TimeoutExpired(cmd=[self._name], timeout=self._timeout_after)
        if self._fail:
            raise RuntimeError(f"{self._name} exploded")
        return RawInvocation(
            checker=self._name, returncode=1, stdout=f"{self._name}-out",
            stderr="", duration_ms=1.0, version="1.0",
        )

    def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        return [
            NormalizedDiagnostic(
                checker=self._name, file="/x.py", start_line=1,
                severity=self._severity, message=raw.stdout, raw=raw.stdout,
            )
        ]


def test_results_follow_adapter_order_not_completion_order() -> None:
    slow = FakeAdapter("slow", delay=0.15)
    fast = FakeAdapter("fast")
    results = run_checkers([slow, fast], paths=["src"], cwd="/tmp")

    assert [r.checker_name for r in results] == ["slow", "fast"]


def test_unavailable_adapters_are_skipped_by_default() -> None:
    results = run_checkers(
        [FakeAdapter("a"), FakeAdapter("b", available=False), FakeAdapter("c")],
        paths=["src"], cwd="/tmp",
    )
    assert [r.checker_name for r in results] == ["a", "c"]


def test_require_all_raises_for_missing_adapter() -> None:
    with pytest.raises(CheckerUnavailableError) as excinfo:
        run_checkers(
            [FakeAdapter("a"), FakeAdapter("missing", available=False)],
            paths=["src"], cwd="/tmp", require_all=True,
        )
    assert excinfo.value.checker_name == "missing"
    assert "missing" in str(excinfo.value)


def test_no_available_adapters_returns_empty() -> None:
    assert run_checkers([FakeAdapter("a", available=False)], paths=["src"], cwd="/tmp") == []


def test_adapter_exception_is_captured_not_propagated() -> None:
    results = run_checkers(
        [FakeAdapter("ok"), FakeAdapter("bad", fail=True)], paths=["src"], cwd="/tmp"
    )
    by_name = {r.checker_name: r for r in results}

    assert by_name["ok"].succeeded
    assert len(by_name["ok"].diagnostics) == 1
    assert not by_name["bad"].succeeded
    assert isinstance(by_name["bad"].error, RuntimeError)
    assert by_name["bad"].diagnostics == []
    assert by_name["bad"].invocation.returncode == -1


def test_cwd_and_paths_are_passed_through() -> None:
    adapter = FakeAdapter("a")
    run_checkers([adapter], paths=["pkg", "tests"], cwd="/some/project")

    assert adapter.seen_cwd == "/some/project"
    assert adapter.seen_paths == ["pkg", "tests"]


def test_version_is_probed_once_per_adapter() -> None:
    """is_available() and run() share one memoized --version probe."""
    adapter = FakeAdapter("a")
    run_checkers([adapter], paths=["src"], cwd="/tmp")
    assert adapter.version_calls == 1


def test_timeout_is_reported_as_error_not_crash() -> None:
    results = run_checkers([FakeAdapter("slow", timeout_after=5)], paths=["src"], cwd="/tmp")
    (r,) = results
    assert not r.succeeded
    assert "timed out" in str(r.error)
    assert r.diagnostics == []
