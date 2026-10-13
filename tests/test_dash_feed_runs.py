from datetime import UTC, datetime
from pathlib import Path

from agent_tools import console_screen, dash_feed

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _lane(run: str) -> console_screen.LaneRow:
    return console_screen.LaneRow(run, None, "", "p3", "build", 1, 4, 0.5, 0, 3)


def _record(run: str, hour: int, minute: int, status: str) -> dict:
    return {
        "run": run, "machine": "host-b", "phase": "p2", "node": "review", "attempt": 1, "turns": 5, "cost": 0.25,
        "verdict": "approve", "status": status, "ended_at": f"2026-10-04T{hour:02d}:{minute:02d}:00Z",
    }


def test_runs_are_the_live_lanes_then_the_newest_ten_finished_runs():
    records = [
        *(_record(f"done-{m:02d}", 11, m, "landed" if m % 2 else "quarantined") for m in range(1, 13)),
        _record("live-1", 11, 59, "exited"),
        _record("old-1", 4, 0, "landed"),
    ]

    runs = dash_feed._runs_v1([_lane("live-1"), _lane("live-2")], "host-b", records, NOW)

    assert [r["run"] for r in runs] == ["live-1", "live-2", *(f"done-{m:02d}" for m in range(12, 2, -1))]
    assert [r["status"] for r in runs[:2]] == ["running", "running"]
    assert [r["status"] for r in runs[2:6]] == ["quarantined", "landed", "quarantined", "landed"]
    assert len(runs) == 12
    assert all(set(r) == set(runs[0]) for r in runs)


def test_every_run_row_carries_its_end_and_keeps_its_status():
    records = [_record("done-1", 11, 1, "landed"), _record("done-2", 11, 2, "quarantined"), _record("done-3", 11, 3, "exited")]
    ends = {"done-1": "landed", "done-2": {"kind": "quarantined", "cause": "tests failed"}}

    runs = dash_feed._runs_v1([_lane("live-1"), _lane("live-2")], "host-b", records, NOW, ends=ends)

    assert [(r["run"], r["end"]) for r in runs] == [
        ("live-1", "running"), ("live-2", "running"), ("done-3", "idle"),
        ("done-2", {"kind": "quarantined", "cause": "tests failed"}), ("done-1", "landed"),
    ]
    assert [r["status"] for r in runs] == ["running", "running", "exited", "quarantined", "landed"]


def test_a_lane_is_running_even_when_the_ends_mapping_names_it():
    runs = dash_feed._runs_v1([_lane("live-1")], "host-b", [], NOW, ends={"live-1": "landed"})

    assert runs[0]["end"] == "running"


def test_ends_are_run_end_over_each_runs_task_records_and_log_tail():
    schema_line = "error: database is at schema version 7, newest known migration is 6"
    rows = {
        "runs": [
            {"run_id": "r-1", "status": "ok", "record_json": "{}"},
            {"run_id": "r-2", "status": "error", "record_json": "{}"},
        ],
        "task_records": [{"run_id": "r-1", "phase_id": "p1", "task_id": "t1", "record_json": '{"landed": true}'}],
        "node_calls": [],
    }

    assert dash_feed._ends_v1(rows, {"r-2": schema_line}) == {
        "r-1": "landed", "r-2": {"kind": "died", "cause": "schema_version"},
    }
    assert dash_feed._ends_v1({}, {}) == {}


def test_a_store_that_cannot_be_read_gives_no_ends_and_idle_finished_rows(monkeypatch):
    def broken(rows, tails):
        raise OSError("store unreachable")

    monkeypatch.setattr(dash_feed, "_ends_v1", broken)
    ends = dash_feed._ends_edge(Path("/nonexistent"), {"runs": [{"run_id": "done-1"}]})
    runs = dash_feed._runs_v1([], "host-b", [_record("done-1", 11, 1, "landed")], NOW, ends=ends)

    assert (ends, runs[0]["end"], runs[0]["status"]) == ({}, "idle", "landed")
