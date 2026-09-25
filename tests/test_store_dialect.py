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
