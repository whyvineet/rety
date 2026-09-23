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


def test_skipped_checkers_are_reported_on_stderr(fake_registry: dict[str, FakeAdapter], tmp_path: Path) -> None:
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


def test_nothing_skipped_prints_no_notice(fake_registry: dict[str, FakeAdapter], tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(cli.main, ["check", "--checker", "mypy,pyright", str(target)])

    assert result.exit_code == 0, result.output
    assert "Skipped" not in result.stderr


def test_require_all_fails_when_checker_missing(fake_registry: dict[str, FakeAdapter], tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(
        cli.main, ["check", "--require-all", "--checker", "mypy,pyrefly", str(target)]
    )

    assert result.exit_code == 1
    assert "pyrefly" in result.stderr
    assert "not installed" in result.stderr


def test_json_output_lists_only_checkers_that_ran(fake_registry: dict[str, FakeAdapter], tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(
        cli.main, ["check", "--format", "json", "--checker", "mypy,pyright,pyrefly", str(target)]
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["checkers_run"] == ["mypy", "pyright"]
    assert report["total_diagnostics"] == {"mypy": 1, "pyright": 1}


def test_unknown_checker_name_is_rejected(fake_registry: dict[str, FakeAdapter], tmp_path: Path) -> None:
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


def test_duplicate_checker_names_run_once(fake_registry: dict[str, FakeAdapter], tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("x = 1\n")

    result = CliRunner().invoke(
        cli.main, ["check", "--format", "json", "--checker", "mypy,mypy,pyright,MYPY", str(target)]
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["checkers_run"] == ["mypy", "pyright"]
    assert report["total_diagnostics"] == {"mypy": 1, "pyright": 1}
