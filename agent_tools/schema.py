"""Pure core for schema-version comparison, no subprocess and no clock.
`cartridges_schema`/`graphs_schema` are a deliberate bend: they read a
checkout's `__init__.py` off disk, because the alternative — an import — is
exactly what fails outside that checkout's own venv, which is the bug this
module exists to fix. `cell` and `status` stay pure."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

__all__ = ["TOOLS_SCHEMA", "cartridges_schema", "cell", "graphs_schema", "status"]

TOOLS_SCHEMA = "1.0"
_NAMES = ("cartridges", "graphs", "tools")


def cell(version: str | None) -> str:
    return version if version is not None else "?"


def _read_constant(path: Path | str, name: str) -> str | None:
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    match = re.search(rf'{name}\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def _fallback_import(module: str, attr: str) -> str | None:
    try:
        return getattr(__import__(module), attr)
    except (ImportError, AttributeError):
        return None


def _nearest_checkout(seed: str | Path) -> Path | None:
    """The nearest ancestor of `seed` (`seed` itself included) that holds
    `core/__init__.py`, or `None` when no ancestor does."""
    if not seed:
        return None
    start = Path(seed)
    for ancestor in (start, *start.parents):
        if (ancestor / "core" / "__init__.py").is_file():
            return ancestor
    return None


def cartridges_schema(provider_profile: str | Path, skills_roots: Sequence[str | Path] = ()) -> str | None:
    """Never reads the profile's own `cartridges_dir`: that field names a
    workspace data directory, not the cartridges checkout. The checkout is
    instead the nearest ancestor of `provider_profile`, then of each
    `skills_roots` entry, that holds `core/__init__.py`."""
    for seed in (provider_profile, *skills_roots):
        checkout = _nearest_checkout(seed)
        if checkout is not None:
            version = _read_constant(checkout / "core" / "__init__.py", "SCHEMA_VERSION")
            if version is not None:
                return version
    return _fallback_import("core", "SCHEMA_VERSION")


def graphs_schema(harness_dir: str | Path) -> str | None:
    path = Path(harness_dir) / "harness" / "__init__.py" if harness_dir else None
    return (path and _read_constant(path, "CORE_SCHEMA")) or _fallback_import("harness", "CORE_SCHEMA")


def _major(version: str) -> str:
    return version.split(".", 1)[0]


def status(versions: Mapping[str, str | None]) -> tuple[str, str]:
    known = [(name, versions[name]) for name in _NAMES if versions.get(name) is not None]
    majors_seen = list(dict.fromkeys(_major(version) for _, version in known))
    if len(majors_seen) <= 1:
        return "ok", ", ".join(f"{name} {version}" for name, version in known)
    counts = {m: sum(1 for _, v in known if _major(v) == m) for m in majors_seen}
    common = max(majors_seen, key=lambda m: counts[m])
    warned = [(name, version) for name, version in known if _major(version) != common]
    return "WARN", ", ".join(f"{name} {version}" for name, version in warned)
