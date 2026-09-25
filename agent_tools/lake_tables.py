"""The Iceberg lake tables: their columns, their day partitions and each one's high-water column.

Columns for runs, phases and node_calls follow the store DDL in tests/test_run_store_backends.py.
Columns for ledger and gate_decisions are copied from the coxswain-graphs store DDL. gate_decisions has no
timestamp, so it is unpartitioned and has no high-water column.
Timestamps are ISO UTC text in the store. The lake column is timestamptz and the sync converts.
The traces `seq` is int32 and `day` is a string, as graphs writes them.
This module touches no store. `ensure_tables` takes a catalog and nothing else."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from pyiceberg.catalog import Catalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import DayTransform
from pyiceberg.types import (
    BooleanType,
    DoubleType,
    IcebergType,
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

__all__ = ["HWM_PROPERTY", "NAMESPACE", "TABLES", "TableDef", "ensure_tables"]

NAMESPACE = "coxswain"
HWM_PROPERTY = "coxswain.hwm"
_PARTITION_FIELD_ID = 1000

_TEXT, _INT, _LONG, _DOUBLE, _BOOL, _TS = (
    StringType(), IntegerType(), LongType(), DoubleType(), BooleanType(), TimestamptzType(),
)
_Columns = Sequence[tuple[str, IcebergType]]


@dataclass(frozen=True)
class TableDef:
    schema: Schema
    spec: PartitionSpec
    hwm_column: str | None


def _schema(columns: _Columns) -> Schema:
    """Every column optional; a field id is the column's 1-based position."""
    return Schema(*(NestedField(i, name, kind, required=False) for i, (name, kind) in enumerate(columns, start=1)))


def _day_partitioned(columns: _Columns, partition_column: str, hwm_column: str) -> TableDef:
    """Partition by day of `partition_column`; `hwm_column` is the column the table's mark follows."""
    schema = _schema(columns)
    field = PartitionField(
        source_id=schema.find_field(partition_column).field_id,
        field_id=_PARTITION_FIELD_ID,
        transform=DayTransform(),
        name=f"{partition_column}_day",
    )
    return TableDef(schema, PartitionSpec(field), hwm_column)


_NODE_CALLS: _Columns = [
    ("call_id", _TEXT), ("run_id", _TEXT), ("seq", _LONG), ("task_id", _TEXT), ("role", _TEXT), ("tier", _TEXT),
    ("model_alias", _TEXT), ("cost_usd", _DOUBLE), ("ceiling_usd", _DOUBLE), ("ceiling_source", _TEXT),
    ("turns", _LONG), ("duration_ms", _LONG), ("input_tokens", _LONG), ("cache_read_tokens", _LONG),
    ("cache_creation_tokens", _LONG), ("input_total", _LONG), ("output_tokens", _LONG), ("ok", _BOOL),
    ("ts", _TS), ("decision_json", _TEXT), ("detail_json", _TEXT),
]
_LEDGER: _Columns = [
    ("row_hash", _TEXT), ("run_id", _TEXT), ("ts", _TS), ("principal", _TEXT), ("kind", _TEXT), ("risk", _TEXT),
    ("outcome", _TEXT), ("cartridge_sha", _TEXT), ("provider_profile", _TEXT), ("schema_tag", _TEXT),
    ("epoch", _LONG), ("row_json", _TEXT),
]
_GATE_DECISIONS: _Columns = [
    ("run_id", _TEXT), ("phase_id", _TEXT), ("seq", _LONG), ("kind", _TEXT), ("target", _TEXT), ("decision", _TEXT),
    ("risk", _TEXT), ("outcome", _TEXT), ("applied", _BOOL), ("edited", _BOOL), ("epoch", _LONG),
    ("detail_json", _TEXT),
]
_TRACES: _Columns = [
    ("run_id", _TEXT), ("call_id", _TEXT), ("seq", _INT), ("day", _TEXT),
    ("type", _TEXT), ("subtype", _TEXT), ("tool", _TEXT), ("event", _TEXT),
]

TABLES: Mapping[str, TableDef] = MappingProxyType(
    {
        "runs": _day_partitioned(
            [("run_id", _TEXT), ("launched_at", _TS), ("ended_at", _TS)], "launched_at", "ended_at"
        ),
        "phases": _day_partitioned(
            [("run_id", _TEXT), ("phase_id", _TEXT), ("ts", _TS), ("record_json", _TEXT)], "ts", "ts"
        ),
        "node_calls": _day_partitioned(_NODE_CALLS, "ts", "ts"),
        # No timestamp in the store, so nothing to partition by and no column of its own to mark.
        "gate_decisions": TableDef(_schema(_GATE_DECISIONS), PartitionSpec(), None),
        "ledger": _day_partitioned(_LEDGER, "ts", "ts"),
        # Unpartitioned so add_files can register the existing graphs-parquet-traces files as they are.
        "traces": TableDef(_schema(_TRACES), PartitionSpec(), None),
    }
)


def ensure_tables(catalog: Catalog) -> None:
    """Edge. Create the namespace and any missing table; an existing one is left untouched."""
    catalog.create_namespace_if_not_exists(NAMESPACE)
    for name, table in TABLES.items():
        catalog.create_table_if_not_exists(f"{NAMESPACE}.{name}", schema=table.schema, partition_spec=table.spec)
