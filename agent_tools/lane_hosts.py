"""Pure parse of the optional `lane_hosts` list in a routing profile. Errors are `LaneHostError` values, never raised."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["LaneHost", "LaneHostError", "find_lane_host", "parse_lane_hosts"]

_FIELDS = ("name", "ssh", "workspace_dir")


@dataclass(frozen=True)
class LaneHost:
    name: str
    ssh: str
    workspace_dir: str


@dataclass(frozen=True)
class LaneHostError:
    message: str


def _parse_entry(entry: object, index: int) -> LaneHost | LaneHostError:
    where = f"lane_hosts[{index}]"
    if not isinstance(entry, Mapping):
        return LaneHostError(f"{where} must be a mapping with name, ssh and workspace_dir")
    extra = sorted(str(k) for k in entry if k not in _FIELDS)
    if extra:
        return LaneHostError(
            f"{where} has unknown keys {extra}; keys, passwords and identity files do not belong in the profile"
        )
    bad = [f for f in _FIELDS if not isinstance(entry.get(f), str) or not entry[f].strip()]
    if bad:
        return LaneHostError(f"{where} is missing or has an empty or non-string field: {bad[0]}")
    if not entry["workspace_dir"].startswith("/"):
        return LaneHostError(f"{where} workspace_dir must be an absolute path, got {entry['workspace_dir']!r}")
    return LaneHost(entry["name"], entry["ssh"], entry["workspace_dir"])


def parse_lane_hosts(profile: Mapping) -> tuple[LaneHost, ...] | LaneHostError:
    raw = profile.get("lane_hosts")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return LaneHostError("lane_hosts must be a list of mappings")
    parsed = [_parse_entry(entry, i) for i, entry in enumerate(raw)]
    errors = [p for p in parsed if isinstance(p, LaneHostError)]
    if errors:
        return errors[0]
    hosts = tuple(p for p in parsed if isinstance(p, LaneHost))
    dupes = sorted(n for n, c in Counter(h.name for h in hosts).items() if c > 1)
    if dupes:
        return LaneHostError(f"lane_hosts has a duplicate name: {dupes[0]}")
    return hosts


def find_lane_host(hosts: tuple[LaneHost, ...], name: str) -> LaneHost | None:
    matches = [h for h in hosts if h.name == name]
    return matches[0] if matches else None
