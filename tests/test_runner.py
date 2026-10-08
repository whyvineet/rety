"""
Tests for the concurrent checker runner (rety/runner.py) using fake adapters.
No real type checker is invoked.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from rety.adapters.base import AdapterCapabilities, CheckerAdapter
from rety.runner import CheckerFailedError, CheckerUnavailableError, run_checkers
from rety.schema import NormalizedDiagnostic, RawInvocation, Severity


class FakeAdapter(CheckerAdapter):
    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        delay: float = 0.0,
        fail: bool = False,
        timeout_after: float | None = None,
        timeout: float | None = None,
        severity: Severity = Severity.error,
        returncode: int = 1,
        stdout: str | None = None,
        stderr: str = "",
    ) -> None:
        super().__init__(timeout=timeout)
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._name = name
        self._available = available
        self._delay = delay
        self._fail = fail
        self._timeout_after = timeout_after
        self._severity = severity
        self.version_calls = 0
        self.seen_cwd: str | None = None
        self.seen_paths: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities()

    def detect_version(self) -> str | None:
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
            checker=self._name,
            returncode=self._returncode,
            stdout=f"{self._name}-out" if self._stdout is None else self._stdout,
            stderr=self._stderr,
            duration_ms=1.0,
            version="1.0",
        )

    def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        if not raw.stdout:
            return []
        return [
            NormalizedDiagnostic(
                checker=self._name,
                file="/x.py",
                start_line=1,
                severity=self._severity,
                message=raw.stdout,
                raw=raw.stdout,
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
        paths=["src"],
        cwd="/tmp",
    )
    assert [r.checker_name for r in results] == ["a", "c"]


def test_require_all_raises_for_missing_adapter() -> None:
    with pytest.raises(CheckerUnavailableError) as excinfo:
        run_checkers(
            [FakeAdapter("a"), FakeAdapter("missing", available=False)],
            paths=["src"],
            cwd="/tmp",
            require_all=True,
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


def test_fatal_exit_without_diagnostics_is_a_failure() -> None:
    """Pyright exit 3 (config error) must not look like 'no issues found'."""
    bad = FakeAdapter("pyright", returncode=3, stdout="", stderr="Config file is invalid")
    (r,) = run_checkers([bad], paths=["src"], cwd="/tmp")

    assert isinstance(r.error, CheckerFailedError)
    assert r.error.returncode == 3
    assert "Config file is invalid" in str(r.error)


def test_fatal_exit_with_diagnostics_is_not_a_failure() -> None:
    """mypy exits 2 on syntax errors but still reports them; that is a real result."""
    (r,) = run_checkers([FakeAdapter("mypy", returncode=2)], paths=["src"], cwd="/tmp")
    assert r.succeeded
    assert len(r.diagnostics) == 1


def test_clean_exit_without_diagnostics_is_not_a_failure() -> None:
    (r,) = run_checkers([FakeAdapter("ty", returncode=0, stdout="")], paths=["src"], cwd="/tmp")
    assert r.succeeded
    assert r.diagnostics == []


def test_timeout_is_reported_as_error_not_crash() -> None:
    results = run_checkers([FakeAdapter("slow", timeout_after=5)], paths=["src"], cwd="/tmp")
    (r,) = results
    assert not r.succeeded
    assert "timed out" in str(r.error)
    assert r.diagnostics == []


class WarningAdapter(FakeAdapter):
    def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
        import warnings

        warnings.warn("ty adapter: 1 line(s) did not match", RuntimeWarning, stacklevel=2)
        return super().parse(raw)


def test_parse_warnings_are_recorded_on_the_result_every_time() -> None:
    """Python shows a warning once per location by default; rety must not lose repeats."""
    for _ in range(2):
        (r,) = run_checkers([WarningAdapter("ty")], paths=["src"], cwd="/tmp")
        assert r.succeeded
        assert r.warnings == ["ty adapter: 1 line(s) did not match"]


def test_parse_exception_is_captured_with_the_real_invocation() -> None:
    class Exploding(FakeAdapter):
        def parse(self, raw: RawInvocation) -> list[NormalizedDiagnostic]:
            raise ValueError("bad output")

    (r,) = run_checkers([Exploding("x")], paths=["src"], cwd="/tmp")
    assert isinstance(r.error, ValueError)
    assert r.invocation.stdout == "x-out"
