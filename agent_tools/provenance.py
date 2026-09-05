"""Attribute each editable cartridge key to the layer that last set it.

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
    return node[part] if isinstance(node, dict) and part in node else _MISSING


def _get(value: dict, path: str):
    return functools.reduce(_step, path.split("."), value)


def _label(raw: str) -> str:
    return "edited" if raw.endswith("/cartridge.d/edited.yaml") else raw


def _editable_paths(final: dict) -> list[str]:
    fixed = [path for path in _FIXED_PATHS if _get(final, path) is not _MISSING]
    cast = (final.get("cast") or {}) if isinstance(final, dict) else {}
    skills = (final.get("skills") or {}) if isinstance(final, dict) else {}
    return fixed + [f"cast.{key}" for key in cast] + [f"skills.{key}" for key in skills]


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
