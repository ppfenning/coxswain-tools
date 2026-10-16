import argparse
import json
import socket
import sqlite3
from datetime import UTC, datetime

import pytest

from agent_tools import cli
from agent_tools.chair_holder import HolderLease, HolderVerdict, chair_holder_verdict, format_holder_verdict, tick_age_s

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)
TICK = datetime(2026, 10, 14, 11, 59, 30, tzinfo=UTC)
FUTURE = datetime(2026, 10, 14, 13, 0, 0, tzinfo=UTC)
PAST = datetime(2026, 10, 14, 11, 0, 0, tzinfo=UTC)
LOOP = HolderLease("chair-run", 4242, "alpha", FUTURE, True)


def test_live_same_host():
    v = chair_holder_verdict(LOOP, "alpha", NOW, TICK, True, 120.0)
    assert v == HolderVerdict("live_same_host", "chair-run", 4242, "alpha", 30.0, "")


def test_live_other_host():
    v = chair_holder_verdict(LOOP, "beta", NOW, TICK, None, 120.0)
    assert v == HolderVerdict("live_other_host", "chair-run", 4242, "alpha", 30.0, "")


def test_other_host_stale_tick():
    v = chair_holder_verdict(LOOP, "beta", NOW, TICK, None, 10.0)
    assert (v.kind, v.reason) == ("none", "stale tick")


def test_expired():
    lease = HolderLease("chair-run", 4242, "alpha", PAST, True)
    v = chair_holder_verdict(lease, "alpha", NOW, TICK, True, 120.0)
    assert (v.kind, v.reason) == ("none", "expired")


def test_dead_pid():
    v = chair_holder_verdict(LOOP, "alpha", NOW, TICK, False, 120.0)
    assert (v.kind, v.reason) == ("none", "dead")


def test_session_style_holder():
    lease = HolderLease("claude-session", 77, "alpha", FUTURE, False)
    v = chair_holder_verdict(lease, "alpha", NOW, TICK, True, 120.0)
    assert (v.kind, v.reason) == ("none", "not a loop")


def test_tick_age_s():
    assert tick_age_s(NOW, TICK) == 30.0
    assert tick_age_s(NOW, None) is None


def test_format_is_one_line():
    line = format_holder_verdict(HolderVerdict("live_other_host", "chair-run", 4242, "alpha", 30.0))
    assert line == "chair loop chair-run pid 4242 on alpha, last tick 30s ago"
    assert "\n" not in line


# -- cox chair attach: the edge, driven with a fake lease reader ------------------------

HERE = socket.gethostname()


class FakeLeases:
    """Serves one lease row and records every write-shaped call; `attach` must make none."""

    def __init__(self, row):
        self.row = row
        self.reads = []
        self.writes = []

    def read(self, runs_dir):
        self.reads.append(runs_dir)
        return self.row

    def take(self, *args):
        self.writes.append(("take", args))

    def release(self, *args):
        self.writes.append(("release", args))


def _row(host=HERE, pid=4242, expires_at=FUTURE, tick=TICK):
    status = None if tick is None else json.dumps({"tick_at": tick.isoformat(), "status": "ok", "current_action": None})
    return {"holder": f"chair-run@{host}:{pid}", "expires_at": expires_at.isoformat(), "epoch": 3, "status": status}


def _attach(tmp_path, row, pid_alive=True):
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path}\n")
    fake = FakeLeases(row)
    rc = cli._chair_attach(argparse.Namespace(profile=str(profile)), fake.read, lambda pid: pid_alive, lambda: NOW)
    return rc, fake


def test_attach_live_same_host_loop_exits_0_with_the_holder_line(tmp_path, capsys):
    rc, fake = _attach(tmp_path, _row())
    assert rc == 0
    assert capsys.readouterr().out == f"chair loop chair-run pid 4242 on {HERE}, last tick 30s ago\n"
    assert fake.reads == [tmp_path / "runs"]
    assert fake.writes == []


def test_attach_live_other_host_loop_exits_0(tmp_path, capsys):
    rc, fake = _attach(tmp_path, _row(host="elsewhere"))
    assert rc == 0
    assert capsys.readouterr().out == "chair loop chair-run pid 4242 on elsewhere, last tick 30s ago\n"
    assert fake.writes == []


def test_attach_dead_holder_exits_non_zero_with_the_reason(tmp_path, capsys):
    rc, fake = _attach(tmp_path, _row(), pid_alive=False)
    assert rc == 1
    assert capsys.readouterr().out == f"no live chair loop: dead (chair-run pid 4242 on {HERE}, last tick 30s ago)\n"
    assert fake.writes == []


def test_attach_expired_lease_exits_non_zero_with_the_reason(tmp_path, capsys):
    rc, fake = _attach(tmp_path, _row(expires_at=PAST))
    assert rc == 1
    assert capsys.readouterr().out.startswith("no live chair loop: expired (")
    assert fake.writes == []


def test_attach_holder_without_a_tick_is_not_a_loop(tmp_path, capsys):
    rc, _ = _attach(tmp_path, _row(tick=None))
    assert rc == 1
    assert capsys.readouterr().out.startswith("no live chair loop: not a loop (")


def test_attach_no_lease_row_exits_non_zero(tmp_path, capsys):
    rc, fake = _attach(tmp_path, None)
    assert (rc, capsys.readouterr().out) == (1, "no live chair loop: no lease\n")
    assert fake.writes == []


def test_attach_unreachable_store_prints_one_line_and_exits_non_zero(tmp_path, capsys):
    def unreachable(runs_dir):
        raise RuntimeError("psycopg is not installed\nand the store is Postgres")

    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path}\n")
    rc = cli._chair_attach(argparse.Namespace(profile=str(profile)), unreachable, lambda pid: True, lambda: NOW)
    out = capsys.readouterr().out
    assert rc == 1
    assert out == "no live chair loop: store unreadable (RuntimeError: psycopg is not installed and the store is Postgres)\n"


def test_attach_row_without_a_status_column_is_judged_by_pid_not_called_not_a_loop(tmp_path, capsys):
    row = {k: v for k, v in _row().items() if k != "status"}
    rc, _ = _attach(tmp_path, row)
    assert rc == 0
    assert capsys.readouterr().out.startswith("chair loop chair-run")


def _sqlite_store(tmp_path, columns, values):
    runs = tmp_path / "runs"
    runs.mkdir()
    conn = sqlite3.connect(runs / "cox.db")
    conn.execute(f"CREATE TABLE leases (name TEXT PRIMARY KEY, holder TEXT, epoch INTEGER, expires_at TEXT{columns})")
    conn.execute(f"INSERT INTO leases VALUES ('chair', ?, 3, ?{', ?' * (len(values) - 2)})", values)
    conn.commit()
    conn.close()
    return runs / "cox.db"


def _attach_real_store(tmp_path, monkeypatch, columns, values):
    """The default reader against a real SQLite store; only the clock and the pid probe are injected."""
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path}\n")
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(profile))
    db = _sqlite_store(tmp_path, columns, values)
    before = db.read_bytes()
    rc = cli._chair_attach(argparse.Namespace(profile=str(profile)), None, lambda pid: True, lambda: NOW)
    return rc, before == db.read_bytes()


def test_attach_real_store_reads_the_lease_and_leaves_the_store_bytes_unchanged(tmp_path, monkeypatch, capsys):
    status = json.dumps({"tick_at": TICK.isoformat(), "status": "ok", "current_action": None})
    rc, unchanged = _attach_real_store(
        tmp_path, monkeypatch, ", status TEXT", (f"chair-run@{HERE}:4242", FUTURE.isoformat(), status)
    )
    assert (rc, unchanged) == (0, True)
    assert capsys.readouterr().out == f"chair loop chair-run pid 4242 on {HERE}, last tick 30s ago\n"


def test_attach_real_store_without_a_status_column_is_not_called_not_a_loop(tmp_path, monkeypatch, capsys):
    rc, unchanged = _attach_real_store(tmp_path, monkeypatch, "", (f"chair-run@{HERE}:4242", FUTURE.isoformat()))
    assert (rc, unchanged) == (0, True)
    assert capsys.readouterr().out.startswith("chair loop chair-run pid 4242")


def test_attach_is_registered_under_chair(capsys):
    with pytest.raises(SystemExit):
        cli.main(["chair", "attach", "--help"])
    assert "never takes, steals, releases or writes the lease" in capsys.readouterr().out
