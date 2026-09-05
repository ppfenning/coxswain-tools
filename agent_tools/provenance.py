"""Attribute each editable cartridge field to the layer that last set it.

Consumes `core.cartridge.layers(...)`'s own walk verbatim. Never re-derives
the `extends` chain, never touches the filesystem: a pure function over the
list the loader already produced.
"""

from __future__ import annotations

import functools

_MISSING = object()

_FIXED_PATHS = (
    "policy.review_tier",
    "policy.plan_competition.min_tier",
    "policy.build_budget_usd_max",
    "landing_areas.checks",
    "context",
)


def _step(node, part):
    if isinstance(node, dict) and part in node:
        return node[part]
    if part == "crew" and isinstance(node, dict) and "cast" in node:
        return node["cast"]
    return _MISSING


def _get(value: dict, path: str):
    return functools.reduce(_step, path.split("."), value)


def _label(raw: str) -> str:
    return "edited" if raw.endswith("/cartridge.d/edited.yaml") else raw


def _crew(final: dict) -> dict:
    if not isinstance(final, dict):
        return {}
    return final["crew"] or {} if "crew" in final else (final.get("cast") or {})


def _seat_field_paths(final: dict, seat: str, value) -> list[str]:
    if not isinstance(value, dict):
        return [f"crew.{seat}"]
    return [
        f"crew.{seat}.{field}"
        for field in ("enabled", "skills")
        if _get(final, f"crew.{seat}.{field}") is not _MISSING
    ]


def _editable_paths(final: dict) -> list[str]:
    fixed = [path for path in _FIXED_PATHS if _get(final, path) is not _MISSING]
    seats = [path for seat, value in _crew(final).items() for path in _seat_field_paths(final, seat, value)]
    skills_map = final.get("skills") if isinstance(final, dict) else None
    skills = [
        f"skills.{key}"
        for key in (skills_map if isinstance(skills_map, dict) else {})
        if _get(final, f"skills.{key}") is not _MISSING
    ]
    return fixed + seats + skills


def _owner(layers: list[tuple[str, dict]], path: str) -> str:
    values = [_get(resolved, path) for _, resolved in layers]
    changed = [label for (label, _), before, after in zip(layers[1:], values, values[1:]) if after != before]
    return _label(changed[-1] if changed else layers[0][0])


def attribute(layers: list[tuple[str, dict]]) -> dict[str, str]:
    """Map each editable key path present in the final layer to the label of
    the last layer whose value at that path differs from the value one layer
    before it — the first label when nothing later changes it."""
    if not layers:
        return {}
    final = layers[-1][1]
    return {path: _owner(layers, path) for path in _editable_paths(final)}
