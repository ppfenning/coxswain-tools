"""Pure spend grouping over node_calls rows already filtered to one window."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _num(row: Mapping[str, Any], key: str) -> float:
    return row.get(key) or 0


def _group(key: tuple[str, str, str], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    role, tier, model = key
    return {
        "role": role,
        "tier": tier,
        "model": model,
        "calls": len(rows),
        "turns": int(sum(_num(r, "turns") for r in rows)),
        "tokens": int(sum(_num(r, "input_total") + _num(r, "output_tokens") for r in rows)),
        "cost_usd": float(sum(_num(r, "cost_usd") for r in rows)),
    }


def group_spend_nodes(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Sum by (role, tier, model_alias); order by cost descending, then role, tier, model ascending."""
    keys = {(str(r.get("role")), str(r.get("tier")), str(r.get("model_alias"))) for r in rows}
    groups = [
        _group(k, [r for r in rows if (str(r.get("role")), str(r.get("tier")), str(r.get("model_alias"))) == k])
        for k in keys
    ]
    return sorted(groups, key=lambda g: (-g["cost_usd"], g["role"], g["tier"], g["model"]))


def cost_per_done_task(total_cost_usd: float, done_count: int) -> float:
    """Window cost over tasks that reached done; 0.0 when none did."""
    return total_cost_usd / done_count if done_count > 0 else 0.0
