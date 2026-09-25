import sqlite3
from datetime import UTC, datetime

import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("pyarrow")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pyiceberg_core")

from pyiceberg.catalog.sql import SqlCatalog

from agent_tools.lake_sync import HISTORY, next_hwm, rows_after, sync, to_arrow
from agent_tools.lake_tables import HWM_PROPERTY, NAMESPACE, TABLES, ensure_tables

E1, E2, E3 = "2026-09-25T05:00:00+00:00", "2026-09-25T06:00:00+00:00", "2026-09-25T07:00:00+00:00"
T = "2026-09-25T04:10:00+00:00"

DDL = (
    "CREATE TABLE runs (run_id TEXT PRIMARY KEY, launched_at TEXT, ended_at TEXT)",
    "CREATE TABLE phases (run_id TEXT, phase_id TEXT, ts TEXT, record_json TEXT)",
    "CREATE TABLE node_calls (call_id TEXT PRIMARY KEY, run_id TEXT, seq INTEGER, task_id TEXT, role TEXT, tier TEXT, "
    "model_alias TEXT, cost_usd DOUBLE PRECISION, ceiling_usd DOUBLE PRECISION, ceiling_source TEXT, turns INTEGER, "
    "duration_ms INTEGER, input_tokens INTEGER, cache_read_tokens INTEGER, cache_creation_tokens INTEGER, "
    "input_total INTEGER, output_tokens INTEGER, ok BOOLEAN, ts TEXT, decision_json TEXT, detail_json TEXT)",
    "CREATE TABLE gate_decisions (run_id TEXT, phase_id TEXT, seq INTEGER, kind TEXT, target TEXT, decision TEXT, "
    "risk TEXT, outcome TEXT, applied BOOLEAN, edited BOOLEAN, epoch BIGINT, detail_json TEXT, "
    "PRIMARY KEY (run_id, phase_id, seq))",
    "CREATE TABLE ledger (row_hash TEXT PRIMARY KEY, run_id TEXT, ts TEXT, principal TEXT, kind TEXT, risk TEXT, "
    "outcome TEXT, cartridge_sha TEXT, provider_profile TEXT, schema_tag TEXT, epoch BIGINT, row_json TEXT)",
)
ROWS = (
    ("runs", ("r1", "2026-09-25T04:00:00+00:00", E1)),
    ("runs", ("r2", "2026-09-24T04:00:00+00:00", E2)),
    ("runs", ("live", "2026-09-25T04:30:00+00:00", None)),
    ("phases", ("r1", "scope", T, "{}")),
    ("phases", ("r1", "build", T, "{}")),
    ("phases", ("r2", "scope", T, "{}")),
    ("phases", ("live", "scope", T, "{}")),
    ("gate_decisions", ("r1", "scope", 1, "k", "t", "approve", "low", "ok", 1, 0, 3, "{}")),
    ("gate_decisions", ("live", "scope", 1, "k", "t", "approve", "low", "ok", 1, 0, 3, "{}")),
    ("ledger", ("h1", "r1", T, "p", "k", "low", "ok", "sha", "prof", "v1", 3, "{}")),
    ("ledger", ("h2", "r2", T, "p", "k", "low", "ok", "sha", "prof", "v1", 3, "{}")),
    ("ledger", ("h3", "live", T, "p", "k", "low", "ok", "sha", "prof", "v1", 3, "{}")),
    ("ledger", ("h4", "gone", T, "p", "k", "low", "ok", "sha", "prof", "v1", 3, "{}")),
)
CALLS = (("c1", "r1"), ("c2", "r2"), ("c3", "live"))


def _insert(conn, table, values):
    conn.execute(f"INSERT INTO {table} VALUES ({', '.join('?' * len(values))})", values)


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "store.db"
    conn = sqlite3.connect(path)
    for ddl in DDL:
        conn.execute(ddl)
    for table, values in ROWS:
        _insert(conn, table, values)
    for call_id, run_id in CALLS:
        cols = [f.name for f in TABLES["node_calls"].schema.fields]
        row = {"call_id": call_id, "run_id": run_id, "seq": 1, "ok": 1, "ts": T}
        _insert(conn, "node_calls", tuple(row.get(c) for c in cols))
    conn.commit()
    yield conn, str(path)
    conn.close()


@pytest.fixture
def catalog(tmp_path):
    (tmp_path / "wh").mkdir()
    cat = SqlCatalog("test", uri=f"sqlite:///{tmp_path}/cat.db", warehouse=f"file://{tmp_path}/wh")
    ensure_tables(cat)
    return cat


def _table(catalog, name):
    return catalog.load_table(f"{NAMESPACE}.{name}")


def _counts(catalog):
    return {name: _table(catalog, name).scan().to_arrow().num_rows for name in HISTORY}


def _marks(catalog):
    return {name: _table(catalog, name).properties.get(HWM_PROPERTY) for name in HISTORY}


def _snapshots(catalog):
    return {name: getattr(_table(catalog, name).current_snapshot(), "snapshot_id", None) for name in HISTORY}


def _appended(results):
    return {r.table: r.rows_appended for r in results}


def test_the_first_sync_appends_every_ended_run_and_sets_each_mark(store, catalog):
    _, url = store
    results = sync(catalog, url, dry_run=False)
    expected = {"runs": 2, "phases": 3, "node_calls": 2, "gate_decisions": 1, "ledger": 2}
    assert _appended(results) == expected
    assert {r.table: r.rows_found for r in results} == expected
    assert _counts(catalog) == expected
    assert _marks(catalog) == dict.fromkeys(HISTORY, E2)
    assert {(r.old_mark, r.new_mark) for r in results} == {(None, E2)}


def test_a_second_sync_appends_nothing_and_adds_no_snapshot(store, catalog):
    _, url = store
    sync(catalog, url, dry_run=False)
    before = _snapshots(catalog)
    results = sync(catalog, url, dry_run=False)
    assert _appended(results) == dict.fromkeys(HISTORY, 0)
    assert _snapshots(catalog) == before
    assert _marks(catalog) == dict.fromkeys(HISTORY, E2)


def test_a_run_that_ends_later_is_the_only_one_appended_on_the_third_sync(store, catalog):
    conn, url = store
    sync(catalog, url, dry_run=False)
    conn.execute("UPDATE runs SET ended_at = ? WHERE run_id = 'live'", (E3,))
    conn.commit()
    results = sync(catalog, url, dry_run=False)
    assert _appended(results) == {"runs": 1, "phases": 1, "node_calls": 1, "gate_decisions": 1, "ledger": 1}
    assert _counts(catalog) == {"runs": 3, "phases": 4, "node_calls": 3, "gate_decisions": 2, "ledger": 3}
    assert _marks(catalog) == dict.fromkeys(HISTORY, E3)


def test_a_dry_run_reports_the_counts_and_leaves_tables_and_marks_unchanged(store, catalog):
    _, url = store
    results = sync(catalog, url, dry_run=True)
    assert {r.table: r.rows_found for r in results} == {
        "runs": 2, "phases": 3, "node_calls": 2, "gate_decisions": 1, "ledger": 2,
    }
    assert _appended(results) == dict.fromkeys(HISTORY, 0)
    assert _counts(catalog) == dict.fromkeys(HISTORY, 0)
    assert _marks(catalog) == dict.fromkeys(HISTORY, None)
    assert _snapshots(catalog) == dict.fromkeys(HISTORY, None)


def test_a_store_with_no_ended_run_writes_nothing(tmp_path, catalog):
    path = tmp_path / "empty.db"
    conn = sqlite3.connect(path)
    conn.execute(DDL[0])
    _insert(conn, "runs", ("live", T, None))
    conn.commit()
    conn.close()
    results = sync(catalog, str(path), dry_run=False)
    assert _appended(results) == dict.fromkeys(HISTORY, 0)
    assert _marks(catalog) == dict.fromkeys(HISTORY, None)


def test_rows_after_keeps_only_rows_strictly_greater_than_the_mark():
    rows = [{"t": "a"}, {"t": "b"}, {"t": "c"}]
    assert rows_after(rows, "t", "b") == [{"t": "c"}]
    assert rows_after(rows, "t", None) == rows


def test_next_hwm_is_the_largest_value_or_the_old_mark():
    rows = [{"t": "a"}, {"t": "c"}, {"t": "b"}]
    assert next_hwm(rows, "t", "b") == "c"
    assert next_hwm(rows, "t", "d") == "d"
    assert next_hwm([], "t", None) is None


def test_to_arrow_turns_iso_text_into_utc_timestamps_and_ints_into_bools():
    cols = [f.name for f in TABLES["node_calls"].schema.fields]
    row = dict.fromkeys(cols) | {"call_id": "c1", "ok": 1, "ts": "2026-09-25T04:10:00+02:00"}
    (out,) = to_arrow(TABLES["node_calls"], [row]).to_pylist()
    assert out["ts"] == datetime(2026, 9, 25, 2, 10, tzinfo=UTC)
    assert out["ok"] is True
    assert out["cost_usd"] is None
