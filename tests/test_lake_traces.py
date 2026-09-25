import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("pyarrow")
pytest.importorskip("sqlalchemy")

import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.catalog.sql import SqlCatalog

from agent_tools.lake_tables import NAMESPACE, ensure_tables
from agent_tools.lake_traces import new_files, register_traces, trace_files

_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()), ("call_id", pa.string()), ("seq", pa.int32()), ("day", pa.string()),
        ("type", pa.string()), ("subtype", pa.string()), ("tool", pa.string()), ("event", pa.string()),
    ]
)


@pytest.fixture
def catalog(tmp_path):
    (tmp_path / "wh").mkdir()
    cat = SqlCatalog("test", uri=f"sqlite:///{tmp_path}/cat.db", warehouse=f"file://{tmp_path}/wh")
    ensure_tables(cat)
    return cat


@pytest.fixture
def root(tmp_path):
    return tmp_path / "traces"


def _write(path, run_id):
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "run_id": [run_id], "call_id": ["c1"], "seq": [1], "day": ["2026-09-25"],
        "type": ["assistant"], "subtype": [None], "tool": ["Read"], "event": ["{}"],
    }
    pq.write_table(pa.table(row, schema=_SCHEMA), path)
    return str(path)


def _table(catalog):
    return catalog.load_table(f"{NAMESPACE}.traces")


def test_trace_files_keeps_the_layout_and_sorts():
    paths = ["/r/2026/09/25/b.parquet", "/r/2026/09/24/a.parquet", "/r/2026/9/25/c.parquet"]
    assert trace_files(paths, "/r/") == ["/r/2026/09/24/a.parquet", "/r/2026/09/25/b.parquet"]


@pytest.mark.parametrize(
    "path",
    ["/r/notes.parquet", "/r/2026/09/a.parquet", "/r/2026/09/25/a.parquet.tmp", "/r/20xx/09/25/a.parquet",
     "/r/2026/09/25/x/a.parquet", "/other/2026/09/25/a.parquet"],
)
def test_trace_files_drops_a_path_off_the_layout(path):
    assert trace_files([path], "/r") == []


def test_new_files_drops_the_registered_and_keeps_order():
    assert new_files(["c", "a", "b"], ["a"]) == ["c", "b"]


def test_the_first_call_registers_both_files_in_place(catalog, root, tmp_path):
    written = [_write(root / "2026/09/25/r1.parquet", "r1"), _write(root / "2026/09/25/r2.parquet", "r2")]
    assert register_traces(catalog, str(root)) == (2, 2, 0)
    table = _table(catalog)
    assert sorted(t.file.file_path.removeprefix("file://") for t in table.scan().plan_files()) == sorted(written)
    assert sorted(table.scan().to_arrow().column("run_id").to_pylist()) == ["r1", "r2"]
    assert not list((tmp_path / "wh").rglob("*.parquet"))


def test_a_second_call_registers_none(catalog, root):
    _write(root / "2026/09/25/r1.parquet", "r1")
    register_traces(catalog, str(root))
    assert register_traces(catalog, str(root)) == (1, 0, 1)
    assert _table(catalog).scan().to_arrow().num_rows == 1


def test_a_file_added_later_registers_only_itself(catalog, root):
    _write(root / "2026/09/25/r1.parquet", "r1")
    register_traces(catalog, str(root))
    _write(root / "2026/09/26/r2.parquet", "r2")
    assert register_traces(catalog, str(root)) == (2, 1, 1)
    assert sorted(_table(catalog).scan().to_arrow().column("run_id").to_pylist()) == ["r1", "r2"]


def test_a_file_off_the_layout_is_ignored(catalog, root):
    _write(root / "2026/09/25/r1.parquet", "r1")
    _write(root / "notes.parquet", "stray")
    assert register_traces(catalog, str(root)) == (1, 1, 0)
    assert _table(catalog).scan().to_arrow().column("run_id").to_pylist() == ["r1"]


def test_a_dry_run_registers_nothing(catalog, root):
    _write(root / "2026/09/25/r1.parquet", "r1")
    assert register_traces(catalog, str(root), dry_run=True) == (1, 0, 0)
    assert _table(catalog).current_snapshot() is None


def test_a_missing_root_returns_zero(catalog, tmp_path):
    assert register_traces(catalog, str(tmp_path / "absent")) == (0, 0, 0)
