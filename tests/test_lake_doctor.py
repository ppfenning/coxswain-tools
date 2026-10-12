import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("pyarrow")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pyiceberg_core")

from agent_tools.lake_config import LakeConfig, load_catalog
from agent_tools.lake_doctor import (
    Check,
    _dangling_check,
    _fail,
    _load_tables,
    _local_path,
    _object_store_check,
    _pointer_locations,
    _table_checks,
    run_checks,
    sqlite_catalog_path,
    verdict,
)
from agent_tools.lake_sync import HISTORY, sync
from agent_tools.lake_tables import NAMESPACE, TABLES, ensure_tables

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


def test_sqlite_catalog_path_names_the_file_of_a_sqlite_uri_and_nothing_for_other_schemes():
    assert sqlite_catalog_path("sqlite:////tmp/x/cat.db") == Path("/tmp/x/cat.db")
    assert sqlite_catalog_path("postgresql://u:p@db:5432/x") is None


def test_a_missing_sqlite_catalog_warns_and_is_not_created(config, tmp_path):
    assert run_checks(config) == [Check("catalog", "warn", "no catalog yet; run cox lake sync")]
    assert not (tmp_path / "cat.db").exists()


def test_a_missing_extra_with_no_catalog_file_fails_on_the_extra(config, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyiceberg.catalog.sql", None)
    checks = run_checks(config)
    assert [(c.name, c.status) for c in checks] == [("lake extra", "fail")] and "coxswain-tools[lake]" in checks[0].detail
    assert not (tmp_path / "cat.db").exists()


def test_an_empty_catalog_warns_on_the_namespace(config):
    load_catalog(config)
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


def test_the_object_store_row_names_the_endpoint_and_which_env_vars_are_set_and_never_a_value(config):
    block = {"endpoint": "http://minio:9000", "access_key_env": "LAKE_KEY", "secret_key_env": "LAKE_SECRET"}
    cfg = LakeConfig(config.catalog_uri, config.warehouse, block)
    assert _object_store_check(cfg, {"LAKE_KEY": "abc123"}) == [
        Check("object store", "warn", "endpoint http://minio:9000; LAKE_KEY set; LAKE_SECRET unset")
    ]


def test_no_object_store_block_gives_no_row(config):
    assert _object_store_check(config, {}) == []


def test_an_existing_current_file_is_not_dangling(config):
    assert _dangling_check("runs", "/m/2.json", "/m/1.json", lambda _: True, config) is None


def test_a_missing_current_with_an_existing_previous_is_dangling_and_names_the_previous(config):
    check = _dangling_check("runs", "/m/2.json", "/m/1.json", lambda p: p == "/m/1.json", config)
    assert check is not None and check.name == "table runs" and check.status == "fail"
    assert "dangling" in check.detail and "previous metadata at /m/1.json" in check.detail


def test_a_missing_current_with_no_usable_previous_still_fails_by_table_name(config):
    for previous in (None, "/m/1.json"):
        check = _dangling_check("runs", "/m/2.json", previous, lambda _: False, config)
        assert check == Check("table runs", "fail", "metadata pointer is dangling: /m/2.json is missing; no usable previous metadata")


def test_a_dangling_detail_redacts_a_password_in_a_location(config):
    check = _dangling_check("runs", "s3://u:hunter2@b/2.json", "s3://u:hunter2@b/1.json", lambda p: p.endswith("1.json"), config)
    assert check is not None and "hunter2" not in check.detail and "***" in check.detail


class _FailingCatalog:
    """Every load raises `error`, whatever type it is."""

    def __init__(self, error):
        self.error = error

    def load_table(self, identifier):
        raise self.error


def test_a_load_error_of_any_type_is_dangling_when_the_row_points_at_a_missing_file(config, monkeypatch):
    monkeypatch.setattr("agent_tools.lake_doctor._pointer_locations", lambda *_: ("/m/2.json", "/m/1.json"))
    monkeypatch.setattr("agent_tools.lake_doctor._exists", lambda _catalog, location: location == "/m/1.json")
    for error in (FileNotFoundError("gone"), RuntimeError("wrapped"), OSError("wrapped")):
        loaded, failures = _load_tables(_FailingCatalog(error), ["runs"], "ns", config)
        assert loaded == {}
        assert failures == [Check("table runs", "fail", "metadata pointer is dangling: /m/2.json is missing; previous metadata at /m/1.json")]


def test_a_load_error_with_an_existing_current_file_keeps_the_plain_failure(config, monkeypatch):
    monkeypatch.setattr("agent_tools.lake_doctor._pointer_locations", lambda *_: ("/m/2.json", "/m/1.json"))
    monkeypatch.setattr("agent_tools.lake_doctor._exists", lambda _catalog, _location: True)
    _, failures = _load_tables(_FailingCatalog(RuntimeError("corrupt")), ["runs"], "ns", config)
    assert failures == [Check("table runs", "fail", "RuntimeError: corrupt")]


def test_a_load_error_with_an_unreadable_row_keeps_the_load_error_and_names_the_read_error(config, monkeypatch):
    def unreadable(*_):
        raise AttributeError("no column")

    monkeypatch.setattr("agent_tools.lake_doctor._pointer_locations", unreadable)
    _, failures = _load_tables(_FailingCatalog(FileNotFoundError("gone")), ["runs"], "ns", config)
    assert failures == [Check("table runs", "fail", "FileNotFoundError: gone; pointer read failed: AttributeError: no column")]


def test_a_deleted_current_metadata_file_is_reported_as_dangling_for_that_table_only(config, store_url):
    catalog = load_catalog(config)
    ensure_tables(catalog)
    sync(catalog, store_url)
    victim = next(iter(HISTORY))
    # ensure_tables writes the first metadata file and the sync's transaction commits a second, so a previous exists.
    current, previous = _pointer_locations(catalog, NAMESPACE, victim)
    assert current and previous and _local_path(previous).exists()
    _local_path(current).unlink()
    checks = run_checks(config)
    found = by_name(checks)
    assert found[f"table {victim}"].status == "fail"
    assert "dangling" in found[f"table {victim}"].detail and previous in found[f"table {victim}"].detail
    for name in HISTORY:
        if name != victim:
            assert found[f"table {name}"].status == "ok"
            assert found[f"table {name} mark"].status == "ok"
    assert verdict(checks) == "fail"
