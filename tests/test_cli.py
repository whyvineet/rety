"""
CLI tests (rety/cli.py) using click's CliRunner and fake adapters.
No real type checker is invoked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from rety import cli
from tests.test_runner import FakeAdapter


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeAdapter]:
    """Replace the adapter registry with three fakes; 'pyrefly' is not installed."""
    instances = {
        "mypy": FakeAdapter("mypy"),
        "pyright": FakeAdapter("pyright"),
        "pyrefly": FakeAdapter("pyrefly", available=False),
    }
    registry = {name: (lambda inst=inst, **_kw: inst) for name, inst in instances.items()}
    monkeypatch.setattr(cli, "ALL_ADAPTERS", registry)
    return instances


def test_skipped_checkers_are_reported_on_stderr(
    fake_registry: dict[str, FakeAdapter], tmp_path: Path
) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(
        cli.main, ["check", "--checker", "mypy,pyright,pyrefly", str(target)]
    )

    assert result.exit_code == 0, result.output
    assert "Skipped 1 checker(s)" in result.stderr
    assert "pyrefly" in result.stderr
    assert "pip install pyrefly" in result.stderr
    assert "--require-all" in result.stderr


def test_broken_checker_is_reported_as_broken_not_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ok = FakeAdapter("mypy")
    broken = FakeAdapter("pyright", available=False, on_path=True)
    monkeypatch.setattr(
        cli, "ALL_ADAPTERS", {"mypy": lambda **_kw: ok, "pyright": lambda **_kw: broken}
    )
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(cli.main, ["check", "--checker", "mypy,pyright", str(target)])

    assert "'--version' failed" in result.stderr
    assert "pip install pyright" not in result.stderr


def test_nothing_skipped_prints_no_notice(
    fake_registry: dict[str, FakeAdapter], tmp_path: Path
) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(cli.main, ["check", "--checker", "mypy,pyright", str(target)])

    assert result.exit_code == 0, result.output
    assert "Skipped" not in result.stderr


def test_require_all_fails_when_checker_missing(
    fake_registry: dict[str, FakeAdapter], tmp_path: Path
) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(
        cli.main, ["check", "--require-all", "--checker", "mypy,pyrefly", str(target)]
    )

    assert result.exit_code == 2
    assert "pyrefly" in result.stderr
    assert "not installed" in result.stderr


def test_json_output_lists_only_checkers_that_ran(
    fake_registry: dict[str, FakeAdapter], tmp_path: Path
) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(
        cli.main, ["check", "--format", "json", "--checker", "mypy,pyright,pyrefly", str(target)]
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["checkers_run"] == ["mypy", "pyright"]
    assert report["total_diagnostics"] == {"mypy": 1, "pyright": 1}


def test_json_report_records_how_it_was_produced(
    fake_registry: dict[str, FakeAdapter], tmp_path: Path
) -> None:
    from rety import __version__

    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(
        cli.main,
        ["check", "--format", "json", "-t", "2", "--checker", "mypy,pyright", str(target)],
    )

    report = json.loads(result.stdout)
    assert report["rety_version"] == __version__
    assert report["paths"] == [str(target)]
    assert report["cwd"]
    assert report["line_tolerance"] == 2
    assert report["checker_returncodes"] == {"mypy": 1, "pyright": 1}
    assert report["checker_durations_ms"] == {"mypy": 1.0, "pyright": 1.0}


def test_unknown_checker_name_is_rejected(
    fake_registry: dict[str, FakeAdapter], tmp_path: Path
) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(cli.main, ["check", "--checker", "mypy,nope", str(target)])

    assert result.exit_code == 2
    assert "Unknown checker(s): nope" in result.stderr


def test_output_requires_json_format(fake_registry: dict[str, FakeAdapter], tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(cli.main, ["check", "--output", "r.json", str(target)])

    assert result.exit_code == 2
    assert "--output is only valid with --format json" in result.stderr


def test_duplicate_checker_names_run_once(
    fake_registry: dict[str, FakeAdapter], tmp_path: Path
) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(
        cli.main, ["check", "--format", "json", "--checker", "mypy,mypy,pyright,MYPY", str(target)]
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["checkers_run"] == ["mypy", "pyright"]
    assert report["total_diagnostics"] == {"mypy": 1, "pyright": 1}


def test_failed_checker_is_not_counted_and_exits_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ok = FakeAdapter("mypy")
    broken = FakeAdapter("pyright", returncode=3, stdout="", stderr="bad config")
    monkeypatch.setattr(
        cli, "ALL_ADAPTERS", {"mypy": lambda **_kw: ok, "pyright": lambda **_kw: broken}
    )
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(
        cli.main, ["check", "--format", "json", "--checker", "mypy,pyright", str(target)]
    )

    assert result.exit_code == 3
    assert "pyright failed" in result.stderr
    report = json.loads(result.stdout)
    assert report["checkers_run"] == ["mypy"]
    assert "pyright" in report["checker_errors"]
    assert "bad config" in report["checker_errors"]["pyright"]


def test_timed_out_checker_message_mentions_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    slow = FakeAdapter("mypy", timeout_after=5)
    monkeypatch.setattr(cli, "ALL_ADAPTERS", {"mypy": lambda **_kw: slow})
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(cli.main, ["check", "--checker", "mypy", str(target)])

    assert result.exit_code == 3
    assert "timed out after 5 seconds" in result.stderr
    assert "failed: mypy" in result.stdout


# ---------------------------------------------------------------------------
# --fail-on exit codes
# ---------------------------------------------------------------------------


def _invoke_fail_on(level: str, target: Path, checker: str = "mypy") -> int:
    args = ["check", "--fail-on", level, "--checker", checker, str(target)]
    return CliRunner().invoke(cli.main, args).exit_code


def test_fail_on_none_exits_0_even_with_errors(
    fake_registry: dict[str, FakeAdapter], tmp_path: Path
) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")
    assert _invoke_fail_on("none", target) == 0


def test_fail_on_error_exits_1_when_errors_found(
    fake_registry: dict[str, FakeAdapter], tmp_path: Path
) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")
    assert _invoke_fail_on("error", target) == 1


def test_fail_on_any_exits_1_when_anything_found(
    fake_registry: dict[str, FakeAdapter], tmp_path: Path
) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")
    assert _invoke_fail_on("any", target) == 1


def test_fail_on_error_ignores_warnings_but_any_does_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from rety.schema import Severity

    warner = FakeAdapter("mypy", severity=Severity.warning)
    monkeypatch.setattr(cli, "ALL_ADAPTERS", {"mypy": lambda **_kw: warner})
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    assert _invoke_fail_on("error", target) == 0
    assert _invoke_fail_on("any", target) == 1


def test_parse_warnings_go_to_stderr_and_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_runner import WarningAdapter

    adapter = WarningAdapter("ty")
    monkeypatch.setattr(cli, "ALL_ADAPTERS", {"ty": lambda **_kw: adapter})
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(cli.main, ["check", "-f", "json", "-c", "ty", str(target)])

    assert result.exit_code == 0
    assert "Warning: ty adapter: 1 line(s) did not match" in result.stderr
    assert json.loads(result.stdout)["checker_warnings"] == {
        "ty": ["ty adapter: 1 line(s) did not match"]
    }


def test_python_option_is_made_absolute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def make(**kwargs: object) -> FakeAdapter:
        seen.update(kwargs)
        return FakeAdapter("mypy")

    monkeypatch.setattr(cli, "ALL_ADAPTERS", {"mypy": make})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "x.py").write_text("x = 1\n")

    CliRunner().invoke(cli.main, ["check", "-c", "mypy", "--python", "python.exe", "x.py"])

    assert seen["python"] == str((tmp_path / "python.exe").resolve())
