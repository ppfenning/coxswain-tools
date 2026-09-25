import shutil
import sqlite3

import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("pyarrow")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pyiceberg_core")

from agent_tools.lake_config import LakeConfig, load_catalog
from agent_tools.lake_doctor import Check, _fail, _table_checks, run_checks, verdict
from agent_tools.lake_sync import HISTORY, sync
from agent_tools.lake_tables import TABLES, ensure_tables

E1 = "2026-09-25T05:00:00+00:00"
T = "2026-09-25T04:10:00+00:00"


def ok(name="a"):
    return Check(name, "ok", "")


def warn(name="a"):
    return Check(name, "warn", "")


def fail(name="a"):
    return Check(name, "fail", "")


@pytest.fixture
def config(tmp_path):
    (tmp_path / "wh").mkdir()
    return LakeConfig(f"sqlite:///{tmp_path}/cat.db", f"file://{tmp_path}/wh")


@pytest.fixture
def store_url(tmp_path):
    """One ended run with one row in every history table, columns untyped."""
    path = tmp_path / "store.db"
    conn = sqlite3.connect(path)
    values = {"run_id": "r1", "seq": 1, "ts": T, "launched_at": T, "ended_at": E1}
    for name in HISTORY:
        columns = [f.name for f in TABLES[name].schema.fields]
        conn.execute(f"CREATE TABLE {name} ({', '.join(columns)})")
        conn.execute(f"INSERT INTO {name} VALUES ({', '.join('?' * len(columns))})", [values.get(c) for c in columns])
    conn.commit()
    conn.close()
    return str(path)


def by_name(checks):
    return {c.name: c for c in checks}


def test_verdict_fail_beats_warn_and_ok():
    assert verdict([ok(), warn(), fail()]) == "fail"


def test_verdict_warn_beats_ok():
    assert verdict([ok(), warn(), ok()]) == "warn"


def test_verdict_all_ok_is_ok():
    assert verdict([ok(), ok()]) == "ok"


def test_verdict_of_no_checks_is_ok():
    assert verdict([]) == "ok"


def test_a_missing_table_warns_with_the_sync_hint():
    assert _table_checks("runs", None, None, True) == [Check("table runs", "warn", "missing; run cox lake sync")]


def test_a_table_with_no_snapshot_warns_and_a_table_needing_no_mark_gets_one_check():
    assert _table_checks("traces", 0, None, False) == [Check("table traces", "warn", "no snapshot")]


def test_a_history_table_reports_its_snapshot_count_and_its_mark():
    assert _table_checks("runs", 2, E1, True) == [Check("table runs", "ok", "snapshots: 2"), Check("table runs mark", "ok", f"mark {E1}")]


def test_a_history_table_without_the_mark_warns():
    assert _table_checks("runs", 1, None, True)[1] == Check("table runs mark", "warn", "no mark")


def test_fail_redacts_the_url_an_error_quotes(config):
    cfg = LakeConfig("postgresql://u:hunter2@db:5432/x", config.warehouse)
    check = _fail("catalog", RuntimeError("cannot open postgresql://u:hunter2@db:5432/x"), cfg)
    assert "hunter2" not in check.detail and "u:***@db" in check.detail


def test_an_empty_catalog_warns_on_the_namespace(config):
    checks = by_name(run_checks(config))
    assert checks["catalog"].status == "ok"
    assert checks["namespace coxswain"].status == "warn" and "cox lake sync" in checks["namespace coxswain"].detail
    assert verdict(list(checks.values())) == "warn"


def test_after_ensure_tables_each_table_exists_but_warns_for_no_snapshot(config):
    ensure_tables(load_catalog(config))
    checks = by_name(run_checks(config))
    assert checks["warehouse"].status == "ok"
    assert {n: checks[f"table {n}"] for n in TABLES} == {n: Check(f"table {n}", "warn", "no snapshot") for n in TABLES}
    assert all(checks[f"table {n} mark"].detail == "no mark" for n in HISTORY)
    assert verdict(list(checks.values())) == "warn"


def test_after_a_sync_each_history_table_is_ok_with_its_count_and_mark(config, store_url):
    catalog = load_catalog(config)
    ensure_tables(catalog)
    sync(catalog, store_url)
    checks = by_name(run_checks(config))
    for name in HISTORY:
        assert checks[f"table {name}"] == Check(f"table {name}", "ok", "snapshots: 1")
        assert checks[f"table {name} mark"] == Check(f"table {name} mark", "ok", f"mark {E1}")
    # The sync never writes traces, so it stays without a snapshot.
    assert checks["table traces"].detail == "no snapshot"
    assert "table traces mark" not in checks


def test_an_unreachable_catalog_fails_without_leaking_the_password(tmp_path):
    cfg = LakeConfig("postgresql://u:hunter2@127.0.0.1:1/x", f"file://{tmp_path}")
    checks = run_checks(cfg)
    assert [c.name for c in checks] == ["catalog"] and checks[0].status == "fail"
    assert "hunter2" not in " ".join(f"{c.name} {c.detail}" for c in checks)
    assert verdict(checks) == "fail"


def test_a_missing_local_warehouse_fails(config, tmp_path):
    ensure_tables(load_catalog(config))
    shutil.rmtree(tmp_path / "wh")
    assert by_name(run_checks(config))["warehouse"].status == "fail"
