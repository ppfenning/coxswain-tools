from __future__ import annotations

import datetime
import json
import os

from agent_tools import cli, chair, leader

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


def test_read_finds_chair_json_when_present(tmp_path):
    record = _record("loop-a", os.getpid())
    (tmp_path / "chair.json").write_text(json.dumps(record))
    assert chair.read(tmp_path) == record


def test_read_falls_back_to_leader_json_when_chair_json_is_absent(tmp_path):
    record = _record("loop-a", os.getpid())
    (tmp_path / "leader.json").write_text(json.dumps(record))
    assert chair.read(tmp_path) == record


def test_clear_without_force_refuses_a_live_holders_lock(tmp_path):
    record = _record("loop-a", os.getpid())
    (tmp_path / "chair.json").write_text(json.dumps(record))
    assert chair.clear(tmp_path) is None
    assert (tmp_path / "chair.json").exists()


def test_clear_with_force_removes_a_live_holders_lock_and_reports_it(tmp_path):
    record = _record("loop-a", os.getpid())
    path = tmp_path / "chair.json"
    path.write_text(json.dumps(record))
    assert chair.clear(tmp_path, force=True) == (path, record)
    assert not path.exists()


def test_clear_removes_a_lock_whose_pid_is_not_live_without_force(tmp_path):
    record = _record("loop-a", 2**30)
    path = tmp_path / "chair.json"
    path.write_text(json.dumps(record))
    assert chair.clear(tmp_path) == (path, record)
    assert not path.exists()


def test_write_none_removes_a_legacy_only_lock(tmp_path):
    record = _record("loop-a", os.getpid())
    legacy = tmp_path / "leader.json"
    legacy.write_text(json.dumps(record))
    chair.write(tmp_path, None)
    assert not legacy.exists()
