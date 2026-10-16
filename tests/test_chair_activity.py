from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

from agent_tools.chair_activity import gather_activity
from agent_tools.chair_agents import agent_rows

_PRICES = {"claude-sonnet-5-5": {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.4}}
_NOW = datetime(2026, 10, 17, 3, 0, 0, tzinfo=UTC)
_WINDOWS = {
    "five_hour": {
        "start": "2026-10-16T22:00:00Z",
        "seat_usd": 3.0,
        "meter_fraction": 0.25,
        "resets_at": "2026-10-17T03:00:00Z",
    },
    "weekly": {
        "start": "2026-10-17T00:00:00Z",
        "seat_usd": 10.0,
        "meter_fraction": 0.5,
        "resets_at": "2026-10-18T08:00:00Z",
    },
}


def _assistant(ts: str, message_id: str, out: int) -> str:
    message = {
        "id": message_id,
        "model": "claude-sonnet-5-5",
        "stop_reason": "end_turn",
        "usage": {"output_tokens": out},
    }
    return json.dumps({"type": "assistant", "timestamp": ts, "message": message})


# 100000 output tokens at $20 per million is $2.0; 50000 is $1.0.
_SESSION = [_assistant("2026-10-17T01:00:00Z", "s1", 100_000)]
_AGENT = {
    "agent_id": "a1",
    "meta": {"description": "Land it"},
    "lines": [_assistant("2026-10-16T23:00:00Z", "g1", 50_000)],
}


def test_the_agent_line_is_in_the_five_hour_window_and_only_the_session_line_in_the_weekly():
    out = gather_activity(_SESSION, [_AGENT], _PRICES, _NOW, _WINDOWS)
    assert out["chair_agents"] == agent_rows([_AGENT], _PRICES, _NOW)
    five, weekly = out["spend_split"]["five_hour"], out["spend_split"]["weekly"]
    assert (five["chair_session_usd"], five["chair_agents_usd"], five["cox_seats_usd"], five["total_usd"]) == (
        2.0,
        1.0,
        3.0,
        6.0,
    )
    assert (five["chair_session_share"], five["chair_agents_share"], five["cox_seats_share"]) == (0.3333, 0.1667, 0.5)
    assert (weekly["chair_session_usd"], weekly["chair_agents_usd"], weekly["cox_seats_usd"], weekly["total_usd"]) == (
        2.0,
        0.0,
        10.0,
        12.0,
    )
    assert (weekly["chair_session_share"], weekly["chair_agents_share"], weekly["cox_seats_share"]) == (
        0.1667,
        0.0,
        0.8333,
    )
    assert (five["meter_fraction"], five["resets_at"]) == (0.25, "2026-10-17T03:00:00Z")


def test_an_empty_session_and_no_agents_leave_the_seat_dollars_alone():
    out = gather_activity([], [], _PRICES, _NOW, _WINDOWS)
    assert out["chair_agents"] == []
    five = out["spend_split"]["five_hour"]
    assert (five["chair_session_usd"], five["chair_agents_usd"], five["cox_seats_usd"], five["cox_seats_share"]) == (
        0.0,
        0.0,
        3.0,
        1.0,
    )


def test_empty_prices_zero_both_chair_figures_and_keep_the_seat_figure():
    out = gather_activity(_SESSION, [_AGENT], {}, _NOW, _WINDOWS)
    weekly = out["spend_split"]["weekly"]
    assert (weekly["chair_session_usd"], weekly["chair_agents_usd"], weekly["cox_seats_usd"], weekly["total_usd"]) == (
        0.0,
        0.0,
        10.0,
        10.0,
    )


def test_the_top_level_and_window_keys_match_the_fixture():
    out = gather_activity(_SESSION, [_AGENT], _PRICES, _NOW, _WINDOWS)
    assert set(out) == {"chair_agents", "spend_split"}
    assert set(out["spend_split"]) == {"five_hour", "weekly"}
    window_keys = {
        "chair_session_usd", "chair_agents_usd", "cox_seats_usd", "total_usd",
        "chair_session_share", "chair_agents_share", "cox_seats_share", "meter_fraction", "resets_at",
    }  # fmt: skip
    assert all(set(window) == window_keys for window in out["spend_split"].values())


def test_the_inputs_are_not_mutated():
    args = (list(_SESSION), [copy.deepcopy(_AGENT)], copy.deepcopy(_PRICES), _NOW, copy.deepcopy(_WINDOWS))
    before = copy.deepcopy(args)
    gather_activity(*args)
    assert args == before
