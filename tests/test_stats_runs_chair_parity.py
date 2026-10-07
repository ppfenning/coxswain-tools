"""`run_store.run_spans` returns the same rows from the DuckDB snapshot as from the store fallback."""

import json
import logging
import sqlite3
import sys
import time

import pytest

from agent_tools import analytics_snapshot, run_store

SINCE = "2026-09-25T03:00:00+00:00"
RUNS = (
    ("old", "2026-09-25T01:00:00+00:00", "2026-09-25T02:00:00+00:00"),
    ("recent", "2026-09-25T03:00:00+00:00", "2026-09-25T05:00:00+00:00"),
    ("live", "2026-09-25T04:00:00+00:00", None),
    ("killed", "2026-09-25T04:10:00+00:00", None),
    ("quiet", "2026-09-25T04:30:00+00:00", None),
    ("early", "2026-09-25T01:30:00+00:00", None),
)
CALLS = (("killed", "2026-09-25T04:20:00+00:00"), ("early", "2026-09-25T02:00:00+00:00"))
EXPECTED = [
    ("recent", "2026-09-25T03:00:00+00:00", "2026-09-25T05:00:00+00:00"),
    ("live", "2026-09-25T04:00:00+00:00", None),
    ("killed", "2026-09-25T04:10:00+00:00", "2026-09-25T04:20:00+00:00"),
    ("quiet", "2026-09-25T04:30:00+00:00", "2026-09-25T04:30:00+00:00"),
]


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    """The fixture store: a SQLite `cox.db` with `runs` and `node_calls`, no routing profile, only `live` alive."""
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(tmp_path / "absent-profile.yaml"))
    monkeypatch.setattr(analytics_snapshot, "_warned", False)
    monkeypatch.setattr(run_store, "_run_ended", lambda runs_dir, run_id, ended_at: ended_at is not None or run_id != "live")
    conn = sqlite3.connect(tmp_path / "cox.db")
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, launched_at TEXT, ended_at TEXT)")
    conn.execute("CREATE TABLE node_calls (run_id TEXT, ts TEXT)")
    conn.executemany("INSERT INTO runs VALUES (?, ?, ?)", RUNS)
    conn.executemany("INSERT INTO node_calls VALUES (?, ?)", CALLS)
    conn.commit()
    conn.close()
    return tmp_path


def _write_snapshot(runs_dir) -> None:
    """A fresh snapshot of the same rows, so `refresh_snapshot` copies nothing and needs no Postgres."""
    duckdb = pytest.importorskip("duckdb")
    paths = analytics_snapshot.snapshot_paths(runs_dir)
    paths["meta"].parent.mkdir(parents=True)
    con = duckdb.connect()
    con.execute("CREATE TABLE runs (run_id VARCHAR, launched_at VARCHAR, ended_at VARCHAR)")
    con.execute("CREATE TABLE node_calls (run_id VARCHAR, ts VARCHAR)")
    con.execute("CREATE TABLE chair_actions (kind VARCHAR, ts VARCHAR)")
    con.execute("CREATE TABLE task_records (run_id VARCHAR)")
    con.executemany("INSERT INTO runs VALUES (?, ?, ?)", RUNS)
    con.executemany("INSERT INTO node_calls VALUES (?, ?)", CALLS)
    for table in analytics_snapshot.TABLES:
        con.execute(f"COPY {table} TO '{paths[table]}' (FORMAT PARQUET)")
    con.close()
    paths["meta"].write_text(json.dumps({"built_at": time.time()}), encoding="utf-8")


def test_run_spans_fallback_without_duckdb_gives_the_store_rows(runs_dir, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "duckdb", None)
    with caplog.at_level(logging.WARNING, logger="agent_tools.analytics_snapshot"):
        assert run_store.run_spans(runs_dir, SINCE) == EXPECTED
    assert "using the Postgres fallback" in caplog.text


def test_run_spans_snapshot_equals_fallback(runs_dir, monkeypatch, caplog):
    _write_snapshot(runs_dir)
    with caplog.at_level(logging.WARNING, logger="agent_tools.analytics_snapshot"):
        from_snapshot = run_store.run_spans(runs_dir, SINCE)
    assert "using the Postgres fallback" not in caplog.text
    monkeypatch.setitem(sys.modules, "duckdb", None)
    from_fallback = run_store.run_spans(runs_dir, SINCE)
    assert from_snapshot == from_fallback == EXPECTED
