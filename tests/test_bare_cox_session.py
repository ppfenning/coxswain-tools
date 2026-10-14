"""Bare `cox` opens the Claude session; only `cox home` hands the terminal to towpath."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_tools.chair_holder import HolderLease
from agent_tools.cli import _holder_from_row, main


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
    monkeypatch.setattr("agent_tools.cli._read_holder_inputs", lambda runs_dir: None)
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


def _holder(monkeypatch, *, host: str, is_loop: bool = True, alive: bool | None = True, expires_in_s: float = 3600, tick_age_s: float | None = 42) -> dict:
    """The lease holder is faked at the store edge; every take, steal, release and loop-start call is recorded."""
    now = datetime.now(UTC)
    lease = HolderLease("chair-2026-10-14", 4242, host, now + timedelta(seconds=expires_in_s), is_loop)
    tick = None if tick_age_s is None else now - timedelta(seconds=tick_age_s)
    calls: dict = {"take": [], "lease": [], "spawn": []}
    monkeypatch.setattr("agent_tools.cli._read_holder_inputs", lambda runs_dir: (lease, tick, alive))
    monkeypatch.setattr("socket.gethostname", lambda: "alpha")
    monkeypatch.setattr("agent_tools.cli._route_chair_take", lambda a: calls["take"].append(a) or 1)
    monkeypatch.setattr("agent_tools.cli._spawn", calls["spawn"].append)
    for name in ("acquire_lease", "release_lease", "renew_lease"):
        monkeypatch.setattr(f"agent_tools.chair.{name}", lambda *a, _n=name, **k: calls["lease"].append(_n))
    return calls


def test_a_live_same_host_loop_is_attached_to_and_the_lease_is_left_alone(tmp_path: Path, monkeypatch, capsys):
    calls = _terminal(monkeypatch, tmp_path, {"claude"})
    held = _holder(monkeypatch, host="alpha")
    with pytest.raises(_Execd):
        main([])
    assert calls == [("claude", ["claude"])]
    assert (held["take"], held["lease"], held["spawn"]) == ([], [], [])
    assert capsys.readouterr().out.splitlines() == ["chair loop chair-2026-10-14 pid 4242 on alpha, last tick 42s ago"]


def test_a_live_other_host_loop_is_attached_to_and_the_lease_is_left_alone(tmp_path: Path, monkeypatch, capsys):
    calls = _terminal(monkeypatch, tmp_path, {"claude"})
    held = _holder(monkeypatch, host="beta", alive=None)
    with pytest.raises(_Execd):
        main([])
    assert calls == [("claude", ["claude"])]
    assert (held["take"], held["lease"], held["spawn"]) == ([], [], [])
    assert capsys.readouterr().out.splitlines() == ["chair loop chair-2026-10-14 pid 4242 on beta, last tick 42s ago"]


@pytest.mark.parametrize(
    "holder",
    [
        {"host": "alpha", "alive": False},
        {"host": "alpha", "expires_in_s": -1},
        {"host": "beta", "alive": None, "tick_age_s": 99999},
        {"host": "alpha", "is_loop": False},
    ],
    ids=["dead", "expired", "stale-tick", "session-holder"],
)
def test_a_holder_that_is_not_a_live_loop_takes_the_old_path(tmp_path: Path, monkeypatch, capsys, holder):
    calls = _terminal(monkeypatch, tmp_path, {"claude"})
    held = _holder(monkeypatch, **holder)
    with pytest.raises(_Execd):
        main([])
    assert calls == [("claude", ["claude"])]
    assert len(held["take"]) == 1
    assert "chair loop" not in capsys.readouterr().out


def test_a_lease_row_reads_as_a_loop_only_when_its_status_carries_a_tick():
    row = {"holder": "chair-1@alpha:77", "expires_at": "2026-10-14T12:00:00+00:00", "status": '{"tick_at": "2026-10-14T11:59:00+00:00"}'}
    lease, tick = _holder_from_row(row)
    assert (lease.label, lease.pid, lease.host, lease.is_loop, tick.minute) == ("chair-1", 77, "alpha", True, 59)
    assert _holder_from_row({**row, "status": None})[0].is_loop is False
    assert _holder_from_row({**row, "holder": ""}) is None
    assert _holder_from_row(None) is None


def test_cox_home_with_only_towpath_on_path_execs_towpath(tmp_path: Path, monkeypatch):
    calls = _terminal(monkeypatch, tmp_path, {"towpath"})
    with pytest.raises(_Execd):
        main(["home"])
    assert calls == [("/usr/bin/towpath", ["/usr/bin/towpath"])]
