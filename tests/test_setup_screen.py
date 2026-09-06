import argparse
import sys

import pytest

from agent_tools import cartridge_screen, cli, setup_screen, setup_tui
from agent_tools.setup_screen import key_name, loop, main, resolved_argv, run_action


def test_resolved_argv_falls_back_to_the_venv_binary_only_when_off_path_and_present():
    argv = ["cartridge", "init", "acme"]
    venv = "/root/agent-cartridges/.venv/bin/cartridge"
    assert resolved_argv(argv, cartridge_on_path=False, venv_cartridge_exists=True,
                          venv_cartridge=venv) == [venv, "init", "acme"]
    assert resolved_argv(argv, cartridge_on_path=True, venv_cartridge_exists=True,
                          venv_cartridge=venv) == argv
    assert resolved_argv(argv, cartridge_on_path=False, venv_cartridge_exists=False,
                          venv_cartridge=venv) == argv
    assert resolved_argv(["other"], cartridge_on_path=False, venv_cartridge_exists=True,
                          venv_cartridge=venv) == ["other"]


def test_key_name_maps_curses_codes_to_the_models_key_strings():
    curses = pytest.importorskip("curses")
    assert key_name(curses.KEY_UP) == "UP"
    assert key_name(curses.KEY_DOWN) == "DOWN"
    assert key_name(10) == "ENTER"
    assert key_name(13) == "ENTER"
    assert key_name(9) == "TAB"
    assert key_name(curses.KEY_BACKSPACE) == "BACKSPACE"
    assert key_name(127) == "BACKSPACE"
    assert key_name(8) == "BACKSPACE"
    assert key_name(ord("q")) == "q"
    assert key_name(-1) == ""


def test_run_action_runs_the_argv_through_a_real_subprocess():
    lines, rc = run_action({"argv": [sys.executable, "-c", "print('hi')"]})
    assert lines == ["hi"]
    assert rc == 0


def test_run_action_falls_back_to_the_venv_binary_when_cartridge_is_not_on_path(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_tools.setup_screen.shutil.which", lambda name: None)
    marker = tmp_path / "marker"
    venv_bin = tmp_path / "agent-cartridges" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    script = venv_bin / "cartridge"
    script.write_text(f'#!/bin/sh\ntouch {marker}\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | 0o111)

    lines, rc = run_action({"argv": ["cartridge", "init", "acme"]}, root=str(tmp_path))

    assert rc == 0
    assert marker.exists()


def test_run_action_uses_cartridge_as_is_when_it_is_on_path(tmp_path, monkeypatch):
    # A venv binary also exists here, so this only passes if PATH wins over it.
    monkeypatch.setattr("agent_tools.setup_screen.shutil.which", lambda name: "/usr/bin/cartridge")
    venv_bin = tmp_path / "agent-cartridges" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    script = venv_bin / "cartridge"
    script.write_text('#!/bin/sh\ntouch marker\n', encoding="utf-8")
    script.chmod(script.stat().st_mode | 0o111)
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        raise OSError("not actually run")

    monkeypatch.setattr("agent_tools.setup_screen.subprocess.run", fake_run)

    lines, rc = run_action({"argv": ["cartridge", "init", "acme"]}, root=str(tmp_path))

    assert calls == [["cartridge", "init", "acme"]]
    assert rc == 127


class _FakeStdscr:
    def __init__(self, keys, size=(24, 80)):
        self._keys = list(keys)
        self._size = size
        self.addnstr_calls = []

    def getmaxyx(self):
        return self._size

    def clear(self):
        pass

    def refresh(self):
        pass

    def addnstr(self, y, x, s, n):
        self.addnstr_calls.append((y, x, s, n))

    def getch(self):
        return self._keys.pop(0)


def test_down_down_enter_runs_install_shows_output_and_never_exceeds_the_width():
    curses = pytest.importorskip("curses")
    runs = []
    roots = []

    def fake_runner(action, *, root=""):
        runs.append(action["argv"])
        roots.append(root)
        return ["ok minimal"], 0

    stdscr = _FakeStdscr([curses.KEY_DOWN, curses.KEY_DOWN, 10, ord("q")])
    loop(stdscr, setup_tui.initial("R", "T", "W"), runner=fake_runner)

    assert runs == [["agent-tools", "setup", "install", "--root", "R", "--team", "T", "--workspace", "W"]]
    assert roots == ["R"]
    assert any("ok minimal" in call[2] for call in stdscr.addnstr_calls)
    assert all(len(call[2]) <= call[3] for call in stdscr.addnstr_calls)


def test_a_narrow_terminal_forces_render_to_truncate_and_addnstr_never_overflows():
    stdscr = _FakeStdscr([ord("q")], size=(5, 10))
    loop(stdscr, setup_tui.initial("root", "team", "workspace"))
    assert stdscr.addnstr_calls
    assert all(len(call[2]) <= call[3] for call in stdscr.addnstr_calls)
    assert any(len(call[2]) == 10 for call in stdscr.addnstr_calls)


def test_main_returns_0_regardless_of_what_curses_wrapper_or_loop_return(monkeypatch):
    curses = pytest.importorskip("curses")
    monkeypatch.setattr(curses, "wrapper", lambda fn: None)
    assert main("r", "t", "w") == 0


def test_main_enters_cartridge_screen_on_a_mode_action_and_resumes_the_menu(monkeypatch):
    curses = pytest.importorskip("curses")
    calls = []

    def fake_loop(stdscr, screen):
        calls.append("loop")
        if calls.count("loop") == 1:
            return screen, {"kind": "mode", "mode": "editor"}
        return {**screen, "quit": True}, None

    monkeypatch.setattr(setup_screen, "loop", fake_loop)
    monkeypatch.setattr(cartridge_screen, "main", lambda fields: calls.append(fields) or 0)
    monkeypatch.setattr(curses, "wrapper", lambda fn: fn(None))

    assert setup_screen.main("r", "t", "w") == 0
    assert calls == ["loop", {"root": "r", "team": "t", "workspace": "w"}, "loop"]


def test_q_ends_the_loop_without_running_anything():
    runs = []
    stdscr = _FakeStdscr([ord("q")])
    loop(stdscr, setup_tui.initial("", "", ""),
         runner=lambda action, **kw: runs.append(action) or ([], 0))
    assert runs == []


class _NonTty:
    def isatty(self):
        return False


def test_setup_with_no_subcommand_on_a_non_tty_stdin_exits_2_before_reading_the_profile(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", _NonTty())
    monkeypatch.setattr(cli, "_setup_fields", lambda a: (_ for _ in ()).throw(AssertionError("should not be called")))
    rc = cli.main(["setup"])
    assert rc == 2
    assert "setup: needs a terminal; use setup doctor / setup install / cartridge init directly" in capsys.readouterr().out


def test_setup_fields_prefills_from_a_resolved_profile_and_is_empty_otherwise(tmp_path):
    profile = tmp_path / "profile.yaml"
    profile.write_text("team: acme\nharness_dir: /repo/agent-graphs\nworkspace_dir: /work/space\n", encoding="utf-8")
    assert cli._setup_fields(argparse.Namespace(profile=str(profile))) == ("/repo", "acme", "/work/space")
    assert cli._setup_fields(argparse.Namespace(profile=str(tmp_path / "absent.yaml"))) == ("", "", "")


def test_setup_doctor_still_dispatches_to_doctor(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_setup_doctor", lambda a: calls.append(a) or 0)
    assert cli.main(["setup", "doctor"]) == 0
    assert len(calls) == 1


def test_run_action_with_a_missing_binary_reports_a_line_instead_of_raising():
    """`cartridge` is installed warn-only, so its absence is expected, not a crash."""
    from agent_tools.setup_screen import run_action
    lines, rc = run_action({"action": "run", "argv": ["definitely-not-a-binary-xyz", "init", "acme"]})
    assert rc == 127
    assert lines and "definitely-not-a-binary-xyz" in lines[0]
