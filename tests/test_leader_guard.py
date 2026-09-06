from __future__ import annotations

import datetime
import json
import os

from agent_tools import cli, leader

_NOW = datetime.datetime(2026, 9, 6, 5, 0, tzinfo=datetime.UTC)


def _record(session: str, pid: int) -> dict:
    return {"session": session, "pid": pid, "host": "h", "taken_at": _NOW.isoformat(), "heartbeat_at": _NOW.isoformat()}


def test_a_live_foreign_lock_refuses_by_name():
    line = leader.guard(_record("loop-a", 1), "loop-b", "live")
    assert line == "refusing: the landing loop is held by loop-a (live); pass --force to override"


def test_the_holders_own_lock_and_a_stale_foreign_lock_pass():
    assert leader.guard(_record("loop-a", 1), "loop-a", "live") is None
    assert leader.guard(_record("loop-a", 1), "loop-b", "stale") is None
    assert leader.guard(None, "loop-b", "none") is None


def test_the_edge_refuses_with_exit_2_and_force_overrides(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(leader, "pid_alive", lambda pid: True)
    monkeypatch.setattr(cli, "_leader_identity", lambda: (os.getpid(), "h"))
    record = _record("loop-a", os.getpid())
    record["heartbeat_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    (tmp_path / "leader.json").write_text(json.dumps(record))
    assert cli._leader_guard_or_refuse(tmp_path, "loop-b", False) == 2
    assert "held by loop-a" in capsys.readouterr().out
    assert cli._leader_guard_or_refuse(tmp_path, "loop-b", True) is None
    assert capsys.readouterr().out.startswith("override:")
    assert cli._leader_guard_or_refuse(tmp_path, "loop-a", False) is None
