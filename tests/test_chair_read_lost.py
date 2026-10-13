import os
import sqlite3
from pathlib import Path

from agent_tools import run_store
from agent_tools.chair_read_lost import ghost_runs_of, lost_runs_of, read_lost_runs, stale_hosts

NOW = "2026-09-27T12:00:00Z"


def _host(name: str, beat_at: str | None) -> dict:
    return {"name": name, "beat_at": beat_at}


def test_a_host_beaten_eleven_minutes_ago_is_stale():
    assert stale_hosts([_host("h", "2026-09-27T11:49:00Z")], NOW) == {"h"}


def test_a_host_beaten_five_minutes_ago_is_not_stale():
    assert stale_hosts([_host("h", "2026-09-27T11:55:00Z")], NOW) == set()


def test_a_host_with_a_blank_beat_at_is_stale():
    assert stale_hosts([_host("h", "")], NOW) == {"h"}


def test_a_lane_on_a_stale_host_with_no_exit_record_is_lost():
    lane = run_store.Lane("i-2", "h", "2026-09-27T10:00:00Z", "2026-09-27T11:00:00Z")
    assert lost_runs_of([lane], {"h"}, {}, NOW) == {"i": "i-2"}


def test_the_same_lane_is_not_lost_once_its_run_is_exited():
    lane = run_store.Lane("i-2", "h", "2026-09-27T10:00:00Z", "2026-09-27T11:00:00Z")
    assert lost_runs_of([lane], {"h"}, {"i-2": True}, NOW) == {}


def test_a_lane_on_a_stale_host_that_renewed_its_lease_two_minutes_ago_is_not_lost():
    lane = run_store.Lane("i-2", "h", "2026-09-27T10:00:00Z", "2026-09-27T11:58:00Z")
    assert lost_runs_of([lane], {"h"}, {}, NOW) == {}


def test_edge_keys_exits_by_run_and_reports_only_the_unexited_lane_on_the_stale_host(tmp_path):
    conn = sqlite3.connect(tmp_path / "cox.db")
    conn.execute("CREATE TABLE hosts (name TEXT, beat_at TEXT)")
    conn.executemany("INSERT INTO hosts VALUES (?, ?)", [("dead", "2026-09-27T11:49:00Z"), ("fresh", "2026-09-27T11:59:00Z")])
    conn.execute("CREATE TABLE runs (run_id TEXT, launched_at TEXT, ended_at TEXT, status TEXT, host TEXT)")
    conn.executemany(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
        [
            ("i-2", "2026-09-27T10:00:00Z", "2026-09-27T11:30:00Z", "ok", "dead"),
            ("j-1", "2026-09-27T10:00:00Z", None, None, "dead"),
            ("k-1", "2026-09-27T10:00:00Z", None, None, "fresh"),
        ],
    )
    conn.execute("CREATE TABLE leases (name TEXT, holder TEXT, epoch INTEGER, heartbeat_at TEXT, expires_at TEXT)")
    conn.executemany(
        "INSERT INTO leases VALUES (?, ?, 1, '2026-09-27T11:40:00Z', '2026-09-27T12:02:00Z')",
        [("runs:i", "i-2"), ("runs:j", "j-1"), ("runs:k", "k-1")],
    )
    conn.commit()
    conn.close()
    assert read_lost_runs(tmp_path, NOW) == {"j": "j-1"}


def _row(run_id: str, ended_at: str | None, expires_at: str | None, launched_at: str = "2026-09-27T10:00:00Z") -> dict:
    return {"run_id": run_id, "launched_at": launched_at, "ended_at": ended_at, "status": None, "expires_at": expires_at}


def test_an_expired_lease_with_no_ended_at_and_no_live_pid_is_a_ghost_whatever_traces_sit_locally():
    assert ghost_runs_of([_row("g-1", None, "2026-09-27T11:00:00Z")], set(), set(), NOW) == {"g": "g-1"}


def test_a_run_with_a_live_lease_is_not_a_ghost():
    assert ghost_runs_of([_row("g-1", None, "2026-09-27T12:02:00Z")], set(), set(), NOW) == {}


def test_a_run_with_a_live_pid_is_not_a_ghost():
    assert ghost_runs_of([_row("g-1", None, "2026-09-27T11:00:00Z")], {"g-1"}, set(), NOW) == {}


def test_a_run_with_an_ended_at_is_not_a_ghost():
    assert ghost_runs_of([_row("g-1", "2026-09-27T10:30:00Z", "2026-09-27T11:00:00Z")], set(), set(), NOW) == {}


def test_a_remote_unfetched_run_is_not_a_ghost():
    assert ghost_runs_of([_row("g-1", None, "2026-09-27T11:00:00Z")], set(), {"g-1"}, NOW) == {}


def test_an_older_unfetched_run_does_not_hide_a_newer_ghost():
    older = _row("g-1", None, "2026-09-27T11:00:00Z", "2026-09-27T08:00:00Z")
    newer = _row("g-2", None, "2026-09-27T11:00:00Z")
    assert ghost_runs_of([older, newer], set(), {"g-1"}, NOW) == {"g": "g-2"}


def test_a_lease_expiring_exactly_now_is_expired():
    assert ghost_runs_of([_row("g-1", None, NOW)], set(), set(), NOW) == {"g": "g-1"}


def test_a_blank_or_unparsable_lease_is_not_a_ghost():
    rows = [_row("a-1", None, None), _row("b-1", None, ""), _row("c-1", None, "soon")]
    assert ghost_runs_of(rows, set(), set(), NOW) == {}


def test_only_the_newest_run_of_an_initiative_can_be_its_ghost():
    older = _row("g-1", None, "2026-09-27T11:00:00Z", "2026-09-27T09:00:00Z")
    newer = _row("g-2", "2026-09-27T10:30:00Z", "2026-09-27T11:00:00Z")
    assert ghost_runs_of([older, newer], set(), set(), NOW) == {}


def test_lost_runs_of_returns_a_ghost_beside_the_lane_rule():
    lane = run_store.Lane("i-2", "h", "2026-09-27T10:00:00Z", "2026-09-27T11:00:00Z")
    rows = [_row("g-1", None, "2026-09-27T11:00:00Z")]
    assert lost_runs_of([lane], {"h"}, {}, NOW, rows=rows) == {"i": "i-2", "g": "g-1"}


def test_a_run_the_old_rule_reports_is_reported_once_and_unchanged_when_it_is_also_a_ghost():
    lane = run_store.Lane("i-2", "h", "2026-09-27T10:00:00Z", "2026-09-27T11:00:00Z")
    old_rule = lost_runs_of([lane], {"h"}, {}, NOW)
    rows = [_row("i-2", None, "2026-09-27T11:30:00Z"), _row("g-1", None, "2026-09-27T11:00:00Z")]
    found = lost_runs_of([lane], {"h"}, {}, NOW, rows=rows)
    assert list(found.values()).count("i-2") == 1
    assert {k: v for k, v in found.items() if k in old_rule} == old_rule == {"i": "i-2"}


def test_a_lane_run_wins_over_a_different_ghost_run_of_the_same_initiative():
    lane = run_store.Lane("i-2", "h", "2026-09-27T10:00:00Z", "2026-09-27T11:00:00Z")
    rows = [_row("i-3", None, "2026-09-27T11:30:00Z", "2026-09-27T11:00:00Z")]
    assert lost_runs_of([lane], {"h"}, {}, NOW, rows=rows) == {"i": "i-2"}


def test_lost_runs_of_without_rows_is_the_lane_rule_alone():
    lane = run_store.Lane("i-2", "h", "2026-09-27T10:00:00Z", "2026-09-27T11:00:00Z")
    assert lost_runs_of([lane], {"h"}, {}, NOW) == {"i": "i-2"}


def _ghost_store(runs_dir: Path, holder: str | None) -> None:
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute("CREATE TABLE runs (run_id TEXT, launched_at TEXT, ended_at TEXT, status TEXT)")
    conn.execute("INSERT INTO runs VALUES ('g-1', '2026-09-27T10:00:00Z', NULL, NULL)")
    conn.execute("CREATE TABLE leases (name TEXT, holder TEXT, epoch INTEGER, heartbeat_at TEXT, expires_at TEXT)")
    if holder is not None:
        conn.execute("INSERT INTO leases VALUES ('runs:g', ?, 1, '2026-09-27T10:30:00Z', '2026-09-27T11:00:00Z')", (holder,))
    conn.commit()
    conn.close()


def test_edge_reports_an_expired_lease_run_with_no_ended_at_and_an_empty_local_log(tmp_path):
    _ghost_store(tmp_path, "g-1")
    (tmp_path / "g-1.remote.json").write_text("{}")
    (tmp_path / "g-1.log").write_text("")
    assert read_lost_runs(tmp_path, NOW) == {"g": "g-1"}


def test_edge_does_not_report_a_remote_run_with_no_local_trace_and_no_fetch(tmp_path):
    _ghost_store(tmp_path, "g-1")
    (tmp_path / "g-1.remote.json").write_text("{}")
    assert read_lost_runs(tmp_path, NOW) == {}


def test_edge_reports_a_ghost_whose_local_run_dir_holds_traces(tmp_path):
    _ghost_store(tmp_path, "g-1")
    (tmp_path / "g-1.remote.json").write_text("{}")
    (tmp_path / "g-1").mkdir()
    (tmp_path / "g-1" / "trace.jsonl").write_text("{}\n")
    assert read_lost_runs(tmp_path, NOW) == {"g": "g-1"}


def test_edge_an_older_unfetched_remote_run_does_not_hide_a_newer_ghost(tmp_path):
    _ghost_store(tmp_path, "g-2")
    conn = sqlite3.connect(tmp_path / "cox.db")
    conn.execute("INSERT INTO runs VALUES ('g-2', '2026-09-27T10:10:00Z', NULL, NULL)")
    conn.commit()
    conn.close()
    (tmp_path / "g-1.remote.json").write_text("{}")
    (tmp_path / "g-2.log").write_text("")
    assert read_lost_runs(tmp_path, NOW) == {"g": "g-2"}


def test_edge_does_not_report_a_live_pid_whose_lease_expired(tmp_path):
    _ghost_store(tmp_path, "g-1")
    (tmp_path / "g-1.pid").write_text(str(os.getpid()))
    assert read_lost_runs(tmp_path, NOW) == {}


def test_edge_reports_an_expired_lease_run_whose_pid_is_dead(tmp_path):
    _ghost_store(tmp_path, "g-1")
    (tmp_path / "g-1.pid").write_text("999999999")
    assert read_lost_runs(tmp_path, NOW) == {"g": "g-1"}


def test_edge_does_not_credit_an_expired_lease_held_by_another_run(tmp_path):
    _ghost_store(tmp_path, "g-0")
    assert read_lost_runs(tmp_path, NOW) == {}


def test_edge_does_not_report_a_run_with_no_lease_row(tmp_path):
    _ghost_store(tmp_path, None)
    assert read_lost_runs(tmp_path, NOW) == {}
