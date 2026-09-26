"""Doctor checks for the Iceberg lake: the catalog, the warehouse and each table's snapshots and mark.

Doctor only reports. It creates nothing and repairs nothing; the fix for a missing namespace or table is `cox lake sync`.
A table needs the mark exactly when the sync writes one, so the marked tables are the sync's HISTORY.
Module level imports no pyiceberg: the lake extra is optional and `run_checks` says so when it is absent."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agent_tools.lake_config import LakeConfig, LakeUnavailable, load_catalog, redact

__all__ = ["Check", "run_checks", "sqlite_catalog_path", "verdict"]

OK, WARN, FAIL = "ok", "warn", "fail"
_SYNC_HINT = "run cox lake sync"
_NEEDS_EXTRA = "the Iceberg lake needs the optional extra `lake`: pip install 'coxswain-tools[lake]'"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def verdict(checks: Sequence[Check]) -> str:
    """The worst status among `checks`: fail beats warn beats ok. No checks is ok."""
    statuses = {c.status for c in checks}
    return FAIL if FAIL in statuses else WARN if WARN in statuses else OK


def _fail(name: str, err: BaseException, config: LakeConfig) -> Check:
    """A fail check whose detail carries no password; the config URLs are redacted wherever the error quotes them."""
    text = str(err).replace(config.catalog_uri, redact(config.catalog_uri)).replace(config.warehouse, redact(config.warehouse))
    return Check(name, FAIL, f"{type(err).__name__}: {redact(text)}")


def _table_checks(name: str, snapshots: int | None, mark: str | None, needs_mark: bool) -> list[Check]:
    """`snapshots` is the count behind the current snapshot, 0 when there is none; None means the table is missing."""
    if snapshots is None:
        return [Check(f"table {name}", WARN, f"missing; {_SYNC_HINT}")]
    snapshot = Check(f"table {name}", OK, f"snapshots: {snapshots}") if snapshots else Check(f"table {name}", WARN, "no snapshot")
    if not needs_mark:
        return [snapshot]
    return [snapshot, Check(f"table {name} mark", OK, f"mark {mark}") if mark else Check(f"table {name} mark", WARN, "no mark")]


def _local_path(warehouse: str) -> Path | None:
    """The directory a `file://` or plain-path warehouse names; None for a remote scheme."""
    parsed = urlparse(warehouse)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    return Path(warehouse) if not parsed.scheme else None


def _warehouse_check(config: LakeConfig, probe: Any) -> Check:
    """Edge. A local warehouse must exist; any other opens `probe`'s metadata file through the table's FileIO."""
    shown = redact(config.warehouse)
    local = _local_path(config.warehouse)
    if local is not None:
        return Check("warehouse", OK, shown) if local.exists() else Check("warehouse", FAIL, f"{shown} does not exist")
    if probe is None:
        return Check("warehouse", WARN, f"{shown} has no table to open a metadata file from")
    try:
        probe.io.new_input(probe.metadata_location).open().close()
    except Exception as err:  # any FileIO failure is the finding
        return _fail("warehouse", err, config)
    return Check("warehouse", OK, f"{shown} metadata file opens")


def _load_tables(catalog: Any, names: Sequence[str], namespace: str, config: LakeConfig) -> tuple[dict[str, Any], list[Check]]:
    """Edge. Each table by name, None when missing; a load that fails otherwise gives a fail check and no entry."""
    from pyiceberg.exceptions import NoSuchTableError

    loaded: dict[str, Any] = {}
    failures: list[Check] = []
    for name in names:
        try:
            loaded[name] = catalog.load_table(f"{namespace}.{name}")
        except NoSuchTableError:
            loaded[name] = None
        except Exception as err:  # a corrupt or unreadable table is a finding, not a crash
            failures.append(_fail(f"table {name}", err, config))
    return loaded, failures


def sqlite_catalog_path(uri: str) -> Path | None:
    """The file a `sqlite:///<path>` URI names; None for any other scheme or an in-memory database."""
    prefix = "sqlite:///"
    path = uri[len(prefix) :].split("?", 1)[0] if uri.startswith(prefix) else ""
    return Path(path) if path and path != ":memory:" else None


def _object_store_check(config: LakeConfig, env: Mapping[str, str]) -> list[Check]:
    """One row for the object_store block: the endpoint and whether each named env var is set. No block gives no row.

    Only names and set/unset reach the detail; no value is read into it."""
    block = config.object_store
    if not block:
        return []
    endpoint = block.get("endpoint")
    names = [n for key in ("access_key_env", "secret_key_env") if isinstance(n := block.get(key), str) and n]
    states = [f"{n} {'set' if env.get(n) else 'unset'}" for n in names]
    shown = f"endpoint {redact(endpoint)}" if isinstance(endpoint, str) and endpoint else "no endpoint"
    unset = any(not env.get(n) for n in names)
    return [Check("object store", WARN if unset else OK, "; ".join([shown, *states]))]


def run_checks(config: LakeConfig, env: Mapping[str, str] = os.environ) -> list[Check]:
    """Edge. The object store row, then every lake check for `config`."""
    return [*_object_store_check(config, env), *_lake_checks(config, env)]


def _lake_checks(config: LakeConfig, env: Mapping[str, str]) -> list[Check]:
    """Edge. Every lake check for `config`; it returns early where nothing further is reachable."""
    try:
        # Imported before the catalog-file guard: a missing extra must fail, not read as "no catalog yet".
        from pyiceberg.catalog.sql import SqlCatalog  # noqa: F401

        from agent_tools.lake_sync import HISTORY
        from agent_tools.lake_tables import HWM_PROPERTY, NAMESPACE, TABLES
    except ImportError:
        return [Check("lake extra", FAIL, _NEEDS_EXTRA)]
    file = sqlite_catalog_path(config.catalog_uri)
    if file is not None and not file.exists():
        return [Check("catalog", WARN, f"no catalog yet; {_SYNC_HINT}")]
    try:
        catalog = load_catalog(config, env)
        namespaces = catalog.list_namespaces()
    except LakeUnavailable as err:
        return [Check("lake extra", FAIL, str(err))]
    except Exception as err:  # any failure to reach the catalog is the finding
        return [_fail("catalog", err, config)]
    checks = [Check("catalog", OK, f"answers at {redact(config.catalog_uri)}")]
    if (NAMESPACE,) not in namespaces:
        return [*checks, Check(f"namespace {NAMESPACE}", WARN, f"not found; {_SYNC_HINT}")]
    loaded, failures = _load_tables(catalog, list(TABLES), NAMESPACE, config)
    probe = next((t for t in loaded.values() if t is not None), None)
    table_checks = [
        c
        for name, table in loaded.items()
        for c in _table_checks(
            name,
            None if table is None else len(table.metadata.snapshots) if table.current_snapshot() else 0,
            None if table is None else table.properties.get(HWM_PROPERTY),
            name in HISTORY,
        )
    ]
    return [*checks, Check(f"namespace {NAMESPACE}", OK, "exists"), _warehouse_check(config, probe), *table_checks, *failures]
