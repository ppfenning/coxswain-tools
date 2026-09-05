"""Rows for the cartridge editor: what the probe resolved, and whether it can
be written back, decided by which layer's provenance owns it.

Pure: takes the probe dict the doctor's real-cartridge probe already
produces, returns plain data. No filesystem, no clock.
"""

from __future__ import annotations

import functools
import itertools
from dataclasses import dataclass
from typing import Any

from agent_tools.provenance import _FIXED_PATHS

_MISSING = object()

_CHOICE_KEYS = {"policy.review_tier", "policy.plan_competition.min_tier"}


@dataclass(frozen=True)
class Row:
    key: str
    value: Any
    layer: str
    editable: bool
    kind: str


def _step(node, part):
    if isinstance(node, dict) and part in node:
        return node[part]
    if part == "crew" and isinstance(node, dict) and "cast" in node:
        return node["cast"]
    return _MISSING


def _get(value: dict, path: str) -> Any:
    return functools.reduce(_step, path.split("."), value)


def _crew(resolved: dict) -> dict:
    if not isinstance(resolved, dict):
        return {}
    return resolved["crew"] or {} if "crew" in resolved else (resolved.get("cast") or {})


def _section(key: str) -> str:
    return key.split(".", 1)[0]


def _kind(key: str) -> str:
    if key.endswith(".enabled"):
        return "toggle"
    if key in _CHOICE_KEYS:
        return "choice"
    return "text"


def _seat_field_paths(resolved: dict, seat: str, value) -> list[str]:
    if not isinstance(value, dict):
        return [f"crew.{seat}"]
    return [
        f"crew.{seat}.{field}"
        for field in ("enabled", "skills")
        if _get(resolved, f"crew.{seat}.{field}") is not _MISSING
    ]


def _keys(resolved: dict) -> list[str]:
    seats = [path for seat, value in _crew(resolved).items() for path in _seat_field_paths(resolved, seat, value)]
    skills_map = resolved.get("skills") if isinstance(resolved, dict) else None
    skills = [
        f"skills.{key}"
        for key in (skills_map if isinstance(skills_map, dict) else {})
        if _get(resolved, f"skills.{key}") is not _MISSING
    ]
    fixed = [key for key in _FIXED_PATHS if _get(resolved, key) is not _MISSING]
    return seats + skills + fixed


def rows(probe: dict, team: str) -> list[Row]:
    resolved = probe.get("resolved") or {}
    provenance = probe.get("provenance") or {}
    broken = "provenance_error" in probe
    return sorted(
        (
            Row(
                key=key,
                value=_get(resolved, key),
                layer="unknown" if broken else provenance.get(key, "unknown"),
                editable=(not broken) and provenance.get(key) in (team, "edited"),
                kind=_kind(key),
            )
            for key in _keys(resolved)
        ),
        key=lambda row: (_section(row.key), row.key),
    )


def sections(items: list[Row]) -> dict[str, list[Row]]:
    """Group rows by their leading key segment. `rows()` already sorts by
    section then key; re-sort defensively so a caller need not."""
    ordered = sorted(items, key=lambda row: _section(row.key))
    return {
        section: list(group)
        for section, group in itertools.groupby(ordered, key=lambda row: _section(row.key))
    }
