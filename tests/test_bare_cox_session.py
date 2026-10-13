"""Bare `cox` opens the Claude session; only `cox home` hands the terminal to towpath."""

from pathlib import Path

import pytest

from agent_tools.cli import main


class _Execd(Exception):
    pass


def _terminal(monkeypatch, tmp_path: Path, on_path: set[str]) -> list:
    """Both streams are terminals, PATH holds only `on_path`, and every exec is recorded then stopped."""
    calls: list = []

    def _record(*a):
        calls.append(a)
        raise _Execd

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name in on_path else None)
    monkeypatch.setattr("os.execvp", _record)
    monkeypatch.setattr("os.execv", _record)
    monkeypatch.setattr("os.chdir", lambda p: None)
    monkeypatch.setattr("agent_tools.cli._session_launch", lambda *a, **k: (["claude"], str(tmp_path), None))
    monkeypatch.setattr("agent_tools.cli._route_chair_take", lambda a: 1)
    return calls


def test_bare_cox_with_towpath_on_path_opens_the_session_and_never_execs_towpath(tmp_path: Path, monkeypatch):
    calls = _terminal(monkeypatch, tmp_path, {"claude", "towpath"})
    with pytest.raises(_Execd):
        main([])
    assert calls == [("claude", ["claude"])]


def test_bare_cox_with_neither_towpath_nor_coxtop_opens_the_session(tmp_path: Path, monkeypatch):
    calls = _terminal(monkeypatch, tmp_path, {"claude"})
    with pytest.raises(_Execd):
        main([])
    assert calls == [("claude", ["claude"])]


def test_cox_home_with_only_towpath_on_path_execs_towpath(tmp_path: Path, monkeypatch):
    calls = _terminal(monkeypatch, tmp_path, {"towpath"})
    with pytest.raises(_Execd):
        main(["home"])
    assert calls == [("/usr/bin/towpath", ["/usr/bin/towpath"])]
