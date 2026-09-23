"""Tests for shared adapter helpers (rety/adapters/base.py)."""

from __future__ import annotations

from pathlib import Path

from rety.adapters.base import resolve_path


def test_relative_path_resolves_against_invocation_cwd(tmp_path: Path) -> None:
    assert resolve_path("src/a.py", str(tmp_path)) == str((tmp_path / "src" / "a.py").resolve())


def test_absolute_path_ignores_cwd(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    assert resolve_path(str(target), "/somewhere/else") == str(target.resolve())


def test_missing_cwd_falls_back_to_process_cwd() -> None:
    assert resolve_path("a.py", None) == str(Path("a.py").resolve())


def test_executable_resolves_through_shutil_which(monkeypatch) -> None:
    """npm installs pyright as pyright.cmd on Windows; which() finds it via PATHEXT."""
    from rety.adapters import base
    from rety.adapters.pyright import PyrightAdapter

    monkeypatch.setattr(base.shutil, "which", lambda name: rf"C:\npm\{name}.cmd")
    assert PyrightAdapter().executable == r"C:\npm\pyright.cmd"


def test_executable_falls_back_to_bare_name(monkeypatch) -> None:
    from rety.adapters import base
    from rety.adapters.mypy import MypyAdapter

    monkeypatch.setattr(base.shutil, "which", lambda name: None)
    assert MypyAdapter().executable == "mypy"


def test_run_subprocess_decodes_utf8_regardless_of_locale() -> None:
    """Pyright emits UTF-8 with non-breaking spaces; the locale code page must not mangle it."""
    import sys

    from rety.adapters.mypy import MypyAdapter

    code = "import sys; sys.stdout.buffer.write('a\u00a0b \u2192 c'.encode('utf-8'))"
    result = MypyAdapter()._run_subprocess([sys.executable, "-c", code], cwd=".")

    assert result.stdout == "a b → c"
