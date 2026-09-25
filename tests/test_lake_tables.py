import pytest

pytest.importorskip("pyiceberg")
pytest.importorskip("sqlalchemy")

from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.transforms import DayTransform

from agent_tools.lake_tables import HWM_PROPERTY, NAMESPACE, TABLES, ensure_tables


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


@pytest.mark.parametrize(
    ("name", "partition_column"),
    [("runs", "launched_at"), ("phases", "ts"), ("node_calls", "ts"), ("ledger", "ts")],
)
def test_a_partitioned_table_has_one_day_partition_on_its_timestamp(catalog, name, partition_column):
    ensure_tables(catalog)
    table = catalog.load_table(f"{NAMESPACE}.{name}")
    (field,) = table.spec().fields
    assert isinstance(field.transform, DayTransform)
    assert table.schema().find_field(field.source_id).name == partition_column
    assert str(table.schema().find_field(field.source_id).field_type) == "timestamptz"


def test_the_runs_mark_is_ended_at_though_it_is_partitioned_by_launched_at():
    assert TABLES["runs"].hwm_column == "ended_at"
    assert TABLES["runs"].spec.fields[0].name == "launched_at_day"


def test_gate_decisions_is_unpartitioned_with_no_high_water_column(catalog):
    ensure_tables(catalog)
    assert catalog.load_table(f"{NAMESPACE}.gate_decisions").spec().fields == ()
    assert TABLES["gate_decisions"].hwm_column is None


def test_gate_decisions_and_ledger_carry_the_store_columns():
    assert [f.name for f in TABLES["gate_decisions"].schema.fields] == [
        "run_id", "phase_id", "seq", "kind", "target", "decision", "risk", "outcome",
        "applied", "edited", "epoch", "detail_json",
    ]
    assert [f.name for f in TABLES["ledger"].schema.fields] == [
        "row_hash", "run_id", "ts", "principal", "kind", "risk", "outcome",
        "cartridge_sha", "provider_profile", "schema_tag", "epoch", "row_json",
    ]


def test_the_traces_seq_is_int32():
    assert str(TABLES["traces"].schema.find_field("seq").field_type) == "int"


def test_the_traces_table_is_unpartitioned(catalog):
    ensure_tables(catalog)
    assert catalog.load_table(f"{NAMESPACE}.traces").spec().fields == ()
    assert TABLES["traces"].hwm_column is None


def test_the_high_water_property_key_is_namespaced():
    assert HWM_PROPERTY == "coxswain.hwm"
