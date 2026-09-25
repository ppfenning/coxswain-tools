"""Where the Iceberg lake lives: catalog and warehouse URLs from the provider profile, else files in the runs dir."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_tools.store_url import read_provider_profile

_PASSWORD = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://[^/:@]*):[^/]*@")


class LakeUnavailable(RuntimeError):
    """The optional `lake` extra is not installed."""


@dataclass(frozen=True)
class LakeConfig:
    catalog_uri: str
    warehouse: str


def _non_empty(value: object, default: str) -> str:
    """A non-empty string passes through unchanged; anything else gives `default`."""
    return value if isinstance(value, str) and value else default


def resolve_lake(profile: Mapping[str, Any], runs_dir: str | Path) -> LakeConfig:
    """`lake_catalog_url` and `lake_url` from the profile; a Postgres or s3:// value is not rewritten."""
    return LakeConfig(
        catalog_uri=_non_empty(profile.get("lake_catalog_url"), f"sqlite:///{Path(runs_dir) / 'lake-catalog.db'}"),
        warehouse=_non_empty(profile.get("lake_url"), f"file://{Path(runs_dir) / 'lake'}"),
    )


def redact(url: str) -> str:
    """The URL with any password replaced by `***`; a URL without one is returned as it came."""
    return _PASSWORD.sub(r"\1:***@", url)


def resolve_lake_at(provider_profile: Path | str, runs_dir: str | Path) -> LakeConfig:
    """Edge. The lake config for the provider profile at `provider_profile`; opens no connection."""
    return resolve_lake(read_provider_profile(provider_profile), runs_dir)


def load_catalog(config: LakeConfig):
    """Edge. The SqlCatalog `coxswain` for `config`; pyiceberg is imported here, not at module load."""
    try:
        from pyiceberg.catalog.sql import SqlCatalog
    except ImportError as err:
        raise LakeUnavailable("the Iceberg lake needs the optional extra `lake`: pip install 'coxswain-tools[lake]'") from err
    return SqlCatalog("coxswain", uri=config.catalog_uri, warehouse=config.warehouse)
