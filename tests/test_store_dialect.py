import os
import sqlite3
import sys

import pytest

from agent_tools.store_dialect import connect_readonly_url, is_postgres, placeholder


def test_placeholder_is_a_question_mark_for_a_path_and_a_sqlite_url():
    assert placeholder("/x/cox.db") == "?"
    assert placeholder("sqlite:///x/cox.db") == "?"


def test_placeholder_is_percent_s_for_postgres():
    assert placeholder("postgresql://h/db") == "%s"


def test_scheme_dispatch_recognises_both_postgres_schemes_and_nothing_else():
    assert is_postgres("postgres://h/db")
    assert is_postgres("postgresql://h/db")
    assert not is_postgres("sqlite:///x/cox.db")
    assert not is_postgres("/x/cox.db")


def test_a_postgres_url_without_psycopg_names_the_extra_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(RuntimeError, match=r"coxswain-tools\[postgres\]"):
        connect_readonly_url("postgresql://h/db")


@pytest.mark.parametrize("prefix", ["", "sqlite:///"])
def test_the_sqlite_open_reads_by_column_name_and_refuses_a_write(tmp_path, prefix):
    path = tmp_path / "cox.db"
    with sqlite3.connect(path) as seed:
        seed.execute("CREATE TABLE t (a INTEGER)")
        seed.execute("INSERT INTO t VALUES (1)")
    conn = connect_readonly_url(f"{prefix}{path}")
    assert conn.execute("SELECT a FROM t").fetchone()["a"] == 1
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO t VALUES (2)")


def test_a_postgres_open_is_autocommit_and_read_only_on_the_server_keeping_the_urls_own_options(monkeypatch):
    import types

    seen = {}
    fake = types.ModuleType("psycopg")
    fake.connect = lambda url, **kwargs: seen.update(url=url, **kwargs) or "conn"
    conninfo = types.ModuleType("psycopg.conninfo")
    conninfo.conninfo_to_dict = lambda _url: {"options": "-c statement_timeout=5000"}
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    monkeypatch.setitem(sys.modules, "psycopg.conninfo", conninfo)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)
    assert connect_readonly_url("postgresql://h/db?options=x") == "conn"
    assert seen["autocommit"] is True
    assert seen["options"] == "-c statement_timeout=5000 -c default_transaction_read_only=on"


@pytest.mark.skipif(not os.environ.get("COX_TEST_POSTGRES_URL"), reason="COX_TEST_POSTGRES_URL is not set")
def test_a_real_postgres_read_holds_no_transaction_and_refuses_a_write():
    import psycopg

    conn = connect_readonly_url(os.environ["COX_TEST_POSTGRES_URL"])
    try:
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TEMP TABLE t (a int)")
    finally:
        conn.close()
