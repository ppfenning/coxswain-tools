import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")

from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.transforms import DayTransform

from agent_tools.lake_tables import HWM_PROPERTY, NAMESPACE, TABLES, ensure_tables

HISTORY = ("runs", "phases", "node_calls", "gate_decisions", "ledger")


@pytest.fixture
def catalog(tmp_path):
    (tmp_path / "wh").mkdir()
    return SqlCatalog("test", uri=f"sqlite:///{tmp_path}/cat.db", warehouse=f"file://{tmp_path}/wh")


def _state(catalog):
    tables = (catalog.load_table(f"{NAMESPACE}.{name}") for name in TABLES)
    return {t.name(): (t.metadata_location, t.current_snapshot(), t.schema()) for t in tables}


def test_ensure_tables_creates_the_six_tables(catalog):
    ensure_tables(catalog)
    assert sorted(name for _, name in catalog.list_tables(NAMESPACE)) == sorted(TABLES)
    assert len(TABLES) == 6


def test_a_second_call_changes_no_snapshot_or_schema(catalog):
    ensure_tables(catalog)
    before = _state(catalog)
    ensure_tables(catalog)
    assert _state(catalog) == before


@pytest.mark.parametrize("name", HISTORY)
def test_a_history_table_has_one_day_partition_on_its_high_water_column(catalog, name):
    ensure_tables(catalog)
    table = catalog.load_table(f"{NAMESPACE}.{name}")
    (field,) = table.spec().fields
    assert isinstance(field.transform, DayTransform)
    assert table.schema().find_field(field.source_id).name == TABLES[name].hwm_column
    assert str(table.schema().find_field(field.source_id).field_type) == "timestamptz"


def test_the_traces_table_is_unpartitioned(catalog):
    ensure_tables(catalog)
    assert catalog.load_table(f"{NAMESPACE}.traces").spec().fields == ()
    assert TABLES["traces"].hwm_column is None


def test_the_high_water_property_key_is_namespaced():
    assert HWM_PROPERTY == "coxswain.hwm"
