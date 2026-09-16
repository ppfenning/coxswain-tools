"""Pure core for schema-version comparison. No file reads, no subprocess, no
clock: the CLI edge gathers versions and hands them here."""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["TOOLS_SCHEMA", "cell", "status"]

TOOLS_SCHEMA = "1.0"
_NAMES = ("cartridges", "graphs", "tools")


def cell(version: str | None) -> str:
    return version if version is not None else "?"


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
