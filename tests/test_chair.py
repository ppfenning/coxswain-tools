"""beat_loop: a fake clock and a fake pid-liveness check drive its while-alive
loop with no real wait and no real process ever needing to die."""

from __future__ import annotations

import datetime
import socket

from agent_tools import chair


class _FakeClock:
    def __init__(self) -> None:
        self.ticks = 0

    def sleep(self, seconds: float) -> None:
        self.ticks += 1


def test_two_iterations_then_the_pid_dies_beats_twice_and_returns(tmp_path, monkeypatch):
    calls = []

    def fake_beat(*args, **kwargs):
        calls.append(args)
        return {"heartbeat_at": "x"}, ""

    monkeypatch.setattr(chair, "beat", fake_beat)
    outcomes = iter([True, True, False])

    def fake_alive(pid: int, sig: int) -> None:
        if not next(outcomes):
            raise ProcessLookupError()

    clock = _FakeClock()
    rc = chair.beat_loop("chair-test", 4321, runs_dir=tmp_path, interval=0, clock=clock, alive=fake_alive)

    assert rc == 0
    assert len(calls) == 2
    assert clock.ticks == 2


def test_one_tick_against_a_real_record_renews_heartbeat_at_on_disk(tmp_path):
    session, pid, host = "chair-test", 4321, socket.gethostname()
    taken_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).isoformat()
    chair.write(tmp_path, {"session": session, "pid": pid, "host": host, "taken_at": taken_at, "heartbeat_at": taken_at, "runs": []})
    outcomes = iter([True, False])

    def fake_alive(pid: int, sig: int) -> None:
        if not next(outcomes):
            raise ProcessLookupError()

    rc = chair.beat_loop(session, pid, runs_dir=tmp_path, interval=0, clock=_FakeClock(), alive=fake_alive)

    assert rc == 0
    assert chair.read(tmp_path)["heartbeat_at"] != taken_at


def test_a_refusal_from_beat_is_printed_and_stops_the_loop_at_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(chair, "beat", lambda *a, **k: (None, "chair: not held"))

    rc = chair.beat_loop("chair-test", 4321, runs_dir=tmp_path, interval=0, clock=_FakeClock(), alive=lambda pid, sig: None)

    assert rc == 2
    assert "chair: not held" in capsys.readouterr().out


def test_a_permission_error_from_alive_is_treated_as_still_alive_not_gone(tmp_path, monkeypatch):
    calls = []

    def fake_beat(*args, **kwargs):
        calls.append(args)
        return {"heartbeat_at": "x"}, ""

    monkeypatch.setattr(chair, "beat", fake_beat)
    outcomes = iter([PermissionError(), True, False])

    def fake_alive(pid: int, sig: int) -> None:
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        if not outcome:
            raise ProcessLookupError()

    rc = chair.beat_loop("chair-test", 4321, runs_dir=tmp_path, interval=0, clock=_FakeClock(), alive=fake_alive)

    assert rc == 0
    assert len(calls) == 2
