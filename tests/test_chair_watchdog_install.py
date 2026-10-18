from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest

from agent_tools import chair_service, cli

COX = "/usr/local/bin/cox"


@pytest.fixture
def home(monkeypatch, tmp_path: Path) -> Path:
    """A HOME with a profile, `cox` on PATH, and every way to run the check or a tool made to fail."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    (tmp_path / "profile.yaml").write_text(f"workspace_dir: {tmp_path}/ws\n")
    monkeypatch.setattr(cli.shutil, "which", lambda _name: COX)

    def refuse(*_args, **_kwargs):
        raise AssertionError("install and uninstall must run no check and no tool")

    monkeypatch.setattr(cli.subprocess, "run", refuse)
    monkeypatch.setattr(cli.subprocess, "Popen", refuse)
    monkeypatch.setattr(cli.chair_watchdog, "run_watchdog", refuse)
    return tmp_path


def _watchdog(home: Path, *flags: str) -> int:
    return cli.main(["chair", "watchdog", "--profile", str(home / "profile.yaml"), *flags])


def test_watchdog_paths_are_the_platforms_own() -> None:
    home = Path("/h")
    assert cli._watchdog_paths("darwin", home) == [Path("/h/Library/LaunchAgents/coxswain-chair-watchdog.plist")]
    assert cli._watchdog_paths("linux", home) == [
        Path("/h/.config/systemd/user/coxswain-chair-watchdog.service"),
        Path("/h/.config/systemd/user/coxswain-chair-watchdog.timer"),
    ]


def test_darwin_install_writes_the_plist_prints_the_launchctl_line_then_uninstall_removes_it(
    home: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    plist = home / "Library" / "LaunchAgents" / chair_service.WATCHDOG_PLIST_NAME
    assert _watchdog(home, "--install", "--interval-minutes", "7") == 0
    assert capsys.readouterr().out.splitlines() == [str(plist), f"launchctl bootstrap gui/$(id -u) {plist}"]
    parsed = plistlib.loads(plist.read_bytes())
    assert parsed["Label"] == "coxswain-chair-watchdog"
    assert parsed["ProgramArguments"] == [COX, "chair", "watchdog", "--profile", str(home / "profile.yaml")]
    assert parsed["StartInterval"] == 420
    assert _watchdog(home, "--uninstall") == 0
    assert capsys.readouterr().out.splitlines() == [f"removed {plist}"]
    assert not plist.exists()


def test_linux_install_writes_service_and_timer_prints_the_systemctl_lines_then_uninstall_removes_both(
    home: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    garage = home / ".config" / "agent-tools" / "garage.env"
    garage.parent.mkdir(parents=True)
    garage.write_text("TOKEN=x\n")
    unit_dir = home / ".config" / "systemd" / "user"
    service, timer = unit_dir / chair_service.WATCHDOG_SERVICE_NAME, unit_dir / chair_service.WATCHDOG_TIMER_NAME
    assert _watchdog(home, "--install") == 0
    assert capsys.readouterr().out.splitlines() == [
        str(service),
        str(timer),
        "systemctl --user daemon-reload",
        "systemctl --user enable --now coxswain-chair-watchdog.timer",
    ]
    service_lines = service.read_text().splitlines()
    assert f"ExecStart={COX} chair watchdog --profile {home / 'profile.yaml'}" in service_lines
    assert f"EnvironmentFile=-{garage}" in service_lines
    assert "OnUnitActiveSec=5min" in timer.read_text().splitlines()
    assert _watchdog(home, "--uninstall") == 0
    assert capsys.readouterr().out.splitlines() == [f"removed {service}", f"removed {timer}"]
    assert not service.exists() and not timer.exists()


@pytest.mark.parametrize("minutes", ["0", "-3"])
def test_interval_below_one_is_refused_with_exit_2_and_writes_nothing(home: Path, monkeypatch, capsys, minutes: str) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert _watchdog(home, "--install", "--interval-minutes", minutes) == 2
    assert capsys.readouterr().out == "refusing: --interval-minutes must be at least 1\n"
    assert not (home / ".config" / "systemd").exists()


def test_install_and_uninstall_together_are_a_parser_error(home: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        _watchdog(home, "--install", "--uninstall")
    assert exc.value.code == 2


def test_uninstall_with_nothing_installed_says_so_and_exits_0(home: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert _watchdog(home, "--uninstall") == 0
    assert capsys.readouterr().out == "no watchdog timer installed\n"


def test_install_without_a_profile_refuses_with_exit_2(home: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    missing = home / "nope.yaml"
    assert cli.main(["chair", "watchdog", "--install", "--profile", str(missing)]) == 2
    assert capsys.readouterr().out == f"routing: no profile at {missing}\n"
