from datetime import UTC, datetime

import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")
pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog

from agent_tools.lake_query import query, render_table, rows_to_json, tables_named
from agent_tools.lake_tables import NAMESPACE, TABLES, ensure_tables

_T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
_T1 = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _append(catalog, name, rows):
    table = catalog.load_table(f"{NAMESPACE}.{name}")
    table.append(pa.Table.from_pylist(rows, schema=table.schema().as_arrow()))


@pytest.fixture
def catalog(tmp_path):
    (tmp_path / "wh").mkdir()
    cat = SqlCatalog("test", uri=f"sqlite:///{tmp_path}/cat.db", warehouse=f"file://{tmp_path}/wh")
    ensure_tables(cat)
    _append(cat, "runs", [
        {"run_id": "r1", "launched_at": _T0, "ended_at": _T0},
        {"run_id": "r2", "launched_at": _T1, "ended_at": _T1},
        {"run_id": "r3", "launched_at": _T1, "ended_at": None},
    ])
    _append(cat, "ledger", [
        {"row_hash": "h1", "run_id": "r1", "ts": _T0, "kind": "land"},
        {"row_hash": "h2", "run_id": "r2", "ts": _T1, "kind": "review"},
    ])
    return cat


class _Spy:
    """Delegates to a catalog and records which tables were loaded."""

    def __init__(self, catalog):
        self._catalog, self.loaded = catalog, []

    def load_table(self, identifier):
        self.loaded.append(identifier)
        return self._catalog.load_table(identifier)


def test_count_star_over_runs_returns_the_row_count(catalog):
    assert query(catalog, "SELECT count(*) AS n FROM runs") == (["n"], [(3,)])


def test_a_join_across_runs_and_ledger_works(catalog):
    columns, rows = query(
        catalog,
        "SELECT r.run_id, l.kind FROM runs r JOIN ledger l ON l.run_id = r.run_id ORDER BY r.run_id",
    )
    assert (columns, rows) == (["run_id", "kind"], [("r1", "land"), ("r2", "review")])


def test_a_table_the_sql_does_not_name_is_never_loaded(catalog):
    spy = _Spy(catalog)
    query(spy, "SELECT count(*) FROM runs")
    assert spy.loaded == [f"{NAMESPACE}.runs"]


def test_a_named_table_missing_from_the_lake_surfaces_duckdbs_own_error(tmp_path):
    (tmp_path / "wh").mkdir()
    empty = SqlCatalog("empty", uri=f"sqlite:///{tmp_path}/cat.db", warehouse=f"file://{tmp_path}/wh")
    empty.create_namespace(NAMESPACE)
    with pytest.raises(duckdb.CatalogException):
        query(empty, "SELECT * FROM runs")


def test_a_query_that_reads_a_file_with_read_csv_fails(catalog, tmp_path):
    csv = tmp_path / "x.csv"
    csv.write_text("a\n1\n")
    with pytest.raises(duckdb.Error):
        query(catalog, f"SELECT * FROM read_csv('{csv}')")


def test_the_sql_cannot_turn_external_access_back_on(catalog):
    with pytest.raises(duckdb.Error):
        query(catalog, "SET enable_external_access = true; SELECT count(*) FROM runs")


def test_a_timestamptz_column_fetches_and_renders(catalog):
    columns, rows = query(catalog, "SELECT run_id, launched_at FROM runs WHERE run_id = 'r1'")
    assert rows_to_json(columns, rows) == [{"run_id": "r1", "launched_at": "2026-09-01T12:00:00+00:00"}]
    assert render_table(columns, rows).splitlines()[2].startswith("r1      2026-09-01 12:00:00")


def test_tables_named_matches_whole_words_in_table_order():
    sql = "select * from Ledger l join runs r on 1=1 where x = 'myruns' and y = node_calls_x"
    assert tables_named(sql, TABLES) == ("runs", "ledger")


def test_rows_to_json_keys_rows_by_column_and_isoformats_timestamps():
    assert rows_to_json(["a", "t"], [(1, _T0), (None, None)]) == [
        {"a": 1, "t": "2026-09-01T12:00:00+00:00"},
        {"a": None, "t": None},
    ]


def test_render_table_left_aligns_under_a_dashed_rule():
    assert render_table(["id", "name"], [(1, "alpha"), (22, None)]) == (
        "id  name\n--  -----\n1   alpha\n22  NULL"
    )


def test_render_table_of_no_rows_is_the_header_and_rule():
    assert render_table(["a", "bb"], []) == "a  bb\n-  --"
