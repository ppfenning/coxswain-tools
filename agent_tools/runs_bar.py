"""Pure core for `cox runs bar`: a one-line Waybar custom-module JSON body
built from the same per-run rows `runs_top.render` draws.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = ["attention", "bar"]

_ATTENTION_STATUSES = ("quarantined", "budget")
_NO_RUNS = "no runs in flight"


def attention(rows: Sequence[Mapping]) -> bool:
    """True when a row's status is quarantined or budget-stopped since its last exit."""
    return any(r["status"] in _ATTENTION_STATUSES for r in rows)


def bar(rows: Sequence[Mapping], attention: bool) -> dict:
    live = [r for r in rows if r["alive"]]
    n = len(live)
    cost = sum(r["cost_usd"] for r in live)
    tooltip = "\n".join(f"{r['phase']} · {r['node']}" for r in live) if live else _NO_RUNS
    cls = "attention" if attention else ("running" if n else "idle")
    return {"text": f"{n} runs · ${cost:.2f}", "tooltip": tooltip, "class": cls}
