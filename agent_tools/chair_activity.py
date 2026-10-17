"""Pure assembly of the dash feed's `chair_agents` and `spend_split` sections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from agent_tools.chair_agents import agent_rows
from agent_tools.chair_spend_nodes import cost_per_done_task, group_spend_nodes
from agent_tools.chair_spend_split import spend_split
from agent_tools.stats_chair import chair_cost, lines_since

_WINDOWS = ("five_hour", "weekly")


def _window_inputs(
    window: Mapping,
    session_lines: Sequence[str],
    agents: Sequence[Mapping],
    prices: Mapping[str, Mapping[str, float]],
) -> dict:
    """`lines_since` compares dates, so the ISO `start` is cut to its YYYY-MM-DD day."""
    day = str(window["start"])[:10]
    return {
        "chair_session_usd": float(chair_cost(lines_since(session_lines, day), prices)),
        "chair_agents_usd": float(sum(chair_cost(lines_since(a["lines"], day), prices) for a in agents)),
        "cox_seats_usd": window["seat_usd"],
        "meter_fraction": window["meter_fraction"],
        "resets_at": window["resets_at"],
    }


def _node_fields(window_out: Mapping, rows: Sequence[Mapping], done: int) -> dict:
    """`spend_split` has a fixed key set, so the node fields are added to its window after it returns."""
    return {
        **window_out,
        "by_node": group_spend_nodes(rows),
        "cost_per_landed_task": round(
            cost_per_done_task(window_out["cox_seats_usd"], done), 4
        ),  # 96.9 / 6 is 16.150000000000002
    }


def gather_activity(
    session_lines: Sequence[str],
    agents: Sequence[Mapping],
    prices: Mapping[str, Mapping[str, float]],
    now: datetime,
    windows: Mapping[str, Mapping],
    node_rows: Mapping[str, Sequence[Mapping]] | None = None,
    done_counts: Mapping[str, int] | None = None,
) -> dict:
    """`node_rows` and `done_counts` are keyed by window name; the caller has already cut them to that window."""
    split = spend_split({name: _window_inputs(windows[name], session_lines, agents, prices) for name in _WINDOWS})
    return {
        "chair_agents": agent_rows(list(agents), dict(prices), now),
        "spend_split": {
            name: _node_fields(split[name], (node_rows or {}).get(name, ()), (done_counts or {}).get(name, 0))
            for name in _WINDOWS
        },
    }
