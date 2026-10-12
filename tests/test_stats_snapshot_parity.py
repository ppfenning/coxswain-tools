"""The stats gates and efficiency readers return the same rows from the analytics snapshot as from the store."""

import json
import sqlite3
import time

import pytest

from agent_tools import analytics_snapshot, run_store

# DOUBLE, not REAL: DuckDB's REAL is a float32, and the Postgres store uses DOUBLE PRECISION.
DDL = {
    "node_calls": "CREATE TABLE node_calls (call_id TEXT, run_id TEXT, task_id TEXT, role TEXT, cost_usd DOUBLE, "
    "turns INTEGER, cache_read_tokens INTEGER, input_total INTEGER, ts TEXT)",
    "chair_actions": "CREATE TABLE chair_actions (ts TEXT, action_json TEXT)",
    "runs": "CREATE TABLE runs (run_id TEXT, launched_at TEXT, ended_at TEXT)",
    "task_records": "CREATE TABLE task_records (run_id TEXT, phase_id TEXT, task_id TEXT, record_json TEXT)",
}

# Both runs retry t1. r_new runs t3 in two phases, so it states no verdict.
CALLS = (
    ("c1", "r_old", "t1", "build", 0.25, 3, 10, 40, "2026-09-01T10:05:00Z"),
    ("c2", "r_old", "t1", "review_charter", 0.125, 1, 0, 20, "2026-09-01T10:25:00Z"),
    ("c3", "r_new", "t1", "build", 0.5, 2, 5, 30, "2026-09-20T10:05:00Z"),
    ("c4", "r_new", "t1", "review_charter", 0.25, 1, 0, 10, "2026-09-20T11:05:00Z"),
    ("c5", "r_new", "t2", "build", 1.0, 4, 8, 50, "2026-09-21T09:00:00Z"),
    ("c6", "r_new", None, "scope", 0.0625, 1, 0, 5, "2026-09-21T09:30:00Z"),
)
RECORDS = (
    ("r_old", "p1", "t1", {"review": {"verdict": "approve"}, "fix_loop": {"attempts": 1, "stopped": None}, "landed": True}),
    ("r_new", "p1", "t1", {"review": {"verdict": "revise"}, "fix_loop": {"attempts": 2, "stopped": None}, "landed": True}),
    ("r_new", "p1", "t3", {"review": {"verdict": "approve"}, "landed": True}),
    ("r_new", "p2", "t3", {"review": {"verdict": "revise"}, "landed": False}),
)
ROWS = {
    "node_calls": CALLS,
    "chair_actions": (),
    "runs": (("r_old", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z"), ("r_new", "2026-09-20T10:00:00Z", None)),
    "task_records": tuple((run, phase, task, json.dumps(record)) for run, phase, task, record in RECORDS),
}

READERS = {
    "task_verdict_rows": lambda d: run_store.task_verdict_rows(d, None),
    "task_verdict_rows_since": lambda d: run_store.task_verdict_rows(d, "2026-09-10"),
    "gate_call_rows": lambda d: run_store.gate_call_rows(d, None),
    "gate_call_rows_since": lambda d: run_store.gate_call_rows(d, "2026-09-10"),
    "efficiency_rows": lambda d: run_store.efficiency_rows(d, "2026-09-10"),
}


def _by_key(rows):
    """Rows in a fixed order: the snapshot gives no ORDER BY promise, and neither does the store."""
    return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str))


def _normal(result):
    if isinstance(result, dict):
        return {name: _by_key(rows) for name, rows in result.items()}
    return _by_key(result)


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    profile = tmp_path / "profile.yaml"
    profile.write_text("{}\n")
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(profile))
    conn = sqlite3.connect(tmp_path / "cox.db")
    for table, ddl in DDL.items():
        conn.execute(ddl)
        marks = ", ".join("?" * len(ROWS[table][0])) if ROWS[table] else ""
        if ROWS[table]:
            conn.executemany(f"INSERT INTO {table} VALUES ({marks})", ROWS[table])
    conn.commit()
    conn.close()
    return tmp_path


def _write_snapshot(runs_dir, built_at):
    duckdb = pytest.importorskip("duckdb")
    paths = analytics_snapshot.snapshot_paths(runs_dir)
    paths["meta"].parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        for table in analytics_snapshot.TABLES:
            con.execute(DDL[table])
            if ROWS[table]:
                marks = ", ".join("?" * len(ROWS[table][0]))
                con.executemany(f"INSERT INTO {table} VALUES ({marks})", [list(row) for row in ROWS[table]])
            con.execute(f"COPY {table} TO '{paths[table]}' (FORMAT PARQUET)")
    finally:
        con.close()
    paths["meta"].write_text(json.dumps({"built_at": built_at}), encoding="utf-8")


def test_a_sqlite_store_never_asks_the_snapshot(runs_dir, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the snapshot was queried on a SQLite store")

    monkeypatch.setattr(analytics_snapshot, "query", refuse)
    assert len(run_store.gate_call_rows(runs_dir, None)) == len(CALLS)
    assert len(run_store.efficiency_rows(runs_dir, "2026-09-10")["tasks"]) == 2


def test_the_fixture_gives_the_readers_something_to_compare(runs_dir):
    assert {r["task_id"] for r in run_store.task_verdict_rows(runs_dir, None)} == {"t1", "t3"}
    assert run_store.efficiency_rows(runs_dir, "2026-09-10")["calls"]
    assert run_store.gate_call_rows(runs_dir, "2026-09-10")


@pytest.mark.parametrize("name", sorted(READERS))
def test_the_snapshot_gives_the_rows_the_store_gives(name, runs_dir, monkeypatch):
    read = READERS[name]
    expected = _normal(read(runs_dir))
    _write_snapshot(runs_dir, time.time())
    monkeypatch.setattr(run_store, "_store_url", lambda _runs_dir: "postgresql://u@h/db")

    def refuse(*args, **kwargs):
        raise AssertionError("the Postgres fallback ran with a fresh snapshot")

    monkeypatch.setattr(run_store, "_store_tuples", refuse)
    assert _normal(read(runs_dir)) == expected
