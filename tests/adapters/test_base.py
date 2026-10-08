"""Tests for shared adapter helpers (rety/adapters/base.py)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rety.adapters.base import resolve_path


def test_relative_path_resolves_against_invocation_cwd(tmp_path: Path) -> None:
    assert resolve_path("src/a.py", str(tmp_path)) == str((tmp_path / "src" / "a.py").resolve())


def test_absolute_path_ignores_cwd(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    assert resolve_path(str(target), "/somewhere/else") == str(target.resolve())


def test_missing_cwd_falls_back_to_process_cwd() -> None:
    assert resolve_path("a.py", None) == str(Path("a.py").resolve())


@pytest.mark.skipif(os.name != "nt", reason="drive letters exist only on Windows")
def test_windows_drive_case_and_separators_are_normalized(tmp_path: Path) -> None:
    """Pyright reports 'd:\\x.py'; mypy/ty report 'D:\\x.py'. Both must match."""
    target = str((tmp_path / "a.py").resolve())
    lower_drive = target[0].lower() + target[1:]

    assert resolve_path(lower_drive, None) == target
    assert resolve_path(target.replace("\\", "/"), None) == target


@pytest.mark.skipif(os.name == "nt", reason="non-Windows behaviour")
def test_windows_path_is_kept_verbatim_on_posix() -> None:
    assert resolve_path(r"C:\proj\a.py", "/tmp") == r"C:\proj\a.py"


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


def test_timeout_kills_grandchild_that_holds_the_pipes(tmp_path: Path) -> None:
    """
    Pyright runs as a wrapper around `node`. A timeout must kill the whole
    tree: otherwise Windows blocks until node exits, and POSIX leaks it.
    """
    import subprocess
    import sys
    import time

    from rety.adapters.mypy import MypyAdapter

    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
        "time.sleep(60)\n"
    )

    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        MypyAdapter()._run_subprocess([sys.executable, "-c", code], cwd=".", timeout=2)
    assert time.monotonic() - start < 20

    if os.name != "nt":
        grandchild = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("grandchild process survived the timeout")


def test_kill_running_checkers_stops_a_running_subprocess() -> None:
    import sys
    import threading
    import time

    from rety.adapters import base
    from rety.adapters.mypy import MypyAdapter

    errors: list[BaseException] = []

    def run() -> None:
        try:
            MypyAdapter()._run_subprocess(
                [sys.executable, "-c", "import time; time.sleep(60)"], cwd="."
            )
        except BaseException as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    thread = threading.Thread(target=run)
    start = time.monotonic()
    thread.start()
    while not base._running and time.monotonic() - start < 10:
        time.sleep(0.05)

    base.kill_running_checkers()
    thread.join(timeout=20)

    assert not thread.is_alive()
    assert time.monotonic() - start < 20
    assert not errors
