"""Where the Iceberg lake lives: catalog and warehouse URLs from the provider profile, else files in the runs dir."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_tools.store_url import read_provider_profile

_PASSWORD = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://[^/:@]*):[^/]*@")
_LITERAL_SECRETS = frozenset({"access_key", "secret_key", "secret"})
_PLAIN_PROPERTIES = (("endpoint", "s3.endpoint"), ("region", "s3.region"))
_ENV_PROPERTIES = (("access_key_env", "s3.access-key-id"), ("secret_key_env", "s3.secret-access-key"))


class LakeUnavailable(RuntimeError):
    """The optional `lake` extra is not installed."""


@dataclass(frozen=True)
class LakeConfig:
    catalog_uri: str
    warehouse: str
    object_store: Mapping[str, Any] | None = None


def _non_empty(value: object, default: str) -> str:
    """A non-empty string passes through unchanged; anything else gives `default`."""
    return value if isinstance(value, str) and value else default


def resolve_lake(profile: Mapping[str, Any], runs_dir: str | Path) -> LakeConfig:
    """`lake_catalog_url` and `lake_url` from the profile; a Postgres or s3:// value is not rewritten."""
    return LakeConfig(
        catalog_uri=_non_empty(profile.get("lake_catalog_url"), f"sqlite:///{Path(runs_dir) / 'lake-catalog.db'}"),
        warehouse=_non_empty(profile.get("lake_url"), f"file://{Path(runs_dir) / 'lake'}"),
        object_store=block if isinstance(block := profile.get("object_store"), Mapping) else None,
    )


def _from_env(name: str, env: Mapping[str, str]) -> str:
    """The value of env var `name`; an unset or empty one raises a `ValueError` that names it."""
    value = env.get(name)
    if not value:
        raise ValueError(f"object_store names env var {name}, which is not set")
    return value


def iceberg_properties(object_store: Mapping[str, Any] | None, env: Mapping[str, str]) -> dict[str, str]:
    """pyiceberg S3 properties for the profile's `object_store` block; `{}` without one. A literal secret is refused."""
    if not object_store:
        return {}
    literal = sorted(_LITERAL_SECRETS.intersection(object_store))
    if literal:
        raise ValueError(
            f"object_store holds a literal secret ({', '.join(literal)}); name an env var in access_key_env or secret_key_env"
        )
    plain = {prop: str(object_store[key]) for key, prop in _PLAIN_PROPERTIES if object_store.get(key)}
    secrets = {prop: _from_env(object_store[key], env) for key, prop in _ENV_PROPERTIES if object_store.get(key)}
    addressing = {"s3.force-virtual-addressing": "false"} if object_store.get("path_style", True) else {}
    return {**plain, **secrets, **addressing}


def redact(url: str) -> str:
    """The URL with any password replaced by `***`; a URL without one is returned as it came."""
    return _PASSWORD.sub(r"\1:***@", url)


def resolve_lake_at(provider_profile: Path | str, runs_dir: str | Path) -> LakeConfig:
    """Edge. The lake config for the provider profile at `provider_profile`; opens no connection."""
    return resolve_lake(read_provider_profile(provider_profile), runs_dir)


def load_catalog(config: LakeConfig, env: Mapping[str, str] = os.environ):
    """Edge. The SqlCatalog `coxswain` for `config`; pyiceberg is imported here, not at module load.

    An `s3://` warehouse also gets the object_store properties, with credentials read from `env`."""
    try:
        from pyiceberg.catalog.sql import SqlCatalog
    except ImportError as err:
        raise LakeUnavailable("the Iceberg lake needs the optional extra `lake`: pip install 'coxswain-tools[lake]'") from err
    extra = iceberg_properties(config.object_store, env) if config.warehouse.startswith("s3://") else {}
    return SqlCatalog("coxswain", uri=config.catalog_uri, warehouse=config.warehouse, **extra)
