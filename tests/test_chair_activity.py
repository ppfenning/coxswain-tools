from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

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
        "by_node", "cost_per_landed_task",
    }  # fmt: skip
    assert all(set(window) == window_keys for window in out["spend_split"].values())


def _row(
    role: str, tier: str, model_alias: str, cost_usd: float, turns: int, input_total: int, output_tokens: int
) -> dict:
    return {
        "role": role,
        "tier": tier,
        "model_alias": model_alias,
        "cost_usd": cost_usd,
        "turns": turns,
        "input_total": input_total,
        "output_tokens": output_tokens,
    }


def test_three_rows_across_two_roles_and_two_models_group_by_node_in_cost_order():
    rows = [
        _row("build", "standard", "claude-sonnet-5-5", 1.0, 4, 100, 50),
        _row("build", "standard", "claude-sonnet-5-5", 0.5, 2, 10, 5),
        _row("review", "premium", "claude-opus-5", 3.0, 1, 200, 20),
    ]
    out = gather_activity(_SESSION, [_AGENT], _PRICES, _NOW, _WINDOWS, node_rows={"five_hour": rows})
    assert out["spend_split"]["five_hour"]["by_node"] == [
        {"role": "review", "tier": "premium", "model": "claude-opus-5", "calls": 1, "turns": 1, "tokens": 220, "cost_usd": 3.0},
        {"role": "build", "tier": "standard", "model": "claude-sonnet-5-5", "calls": 2, "turns": 6, "tokens": 165, "cost_usd": 1.5},
    ]  # fmt: skip
    assert out["spend_split"]["weekly"]["by_node"] == []


def test_an_empty_window_gives_an_empty_by_node_and_a_zero_cost_per_landed_task():
    out = gather_activity(_SESSION, [_AGENT], _PRICES, _NOW, _WINDOWS)
    assert all(w["by_node"] == [] and w["cost_per_landed_task"] == 0.0 for w in out["spend_split"].values())


def test_two_done_tasks_divide_the_window_seat_cost():
    out = gather_activity(_SESSION, [_AGENT], _PRICES, _NOW, _WINDOWS, done_counts={"five_hour": 2})
    assert out["spend_split"]["five_hour"]["cost_per_landed_task"] == 1.5
    assert out["spend_split"]["weekly"]["cost_per_landed_task"] == 0.0


def _full_usage(ts: str, message_id: str, stop_reason: str, parts: tuple[int, int, int, int]) -> str:
    usage = dict(
        zip(("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"), parts)
    )
    message = {"id": message_id, "model": "claude-sonnet-5-5", "stop_reason": stop_reason, "usage": usage}
    return json.dumps({"type": "assistant", "timestamp": ts, "message": message})


def _user(ts: str) -> str:
    return json.dumps({"type": "user", "timestamp": ts, "message": {"content": "go"}})


def _node_group(role: str, tier: str, model: str, calls: int, turns: int, tokens: int, cost: float) -> list[dict]:
    """`calls` rows whose first carries the group's totals, so the sums are exact."""
    return [
        _row(role, tier, model, cost, turns, tokens, 0),
        *(_row(role, tier, model, 0.0, 0, 0, 0) for _ in range(calls - 1)),
    ]


def test_the_output_built_from_the_fixture_inputs_equals_the_fixture():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "dash_activity_v1.json").read_text())
    sonnet, opus = "claude-sonnet-5-5", "claude-opus-5"
    # The two shown agents, plus two whose timestamps do not parse: `agent_rows` drops them
    # while `lines_since` still reads their date, which is what lifts the window agent totals.
    agents = [
        {"agent_id": "a33d15b19c2151ccf", "meta": {"description": "Land the chair attach phase"}, "lines": [
            _user("2026-10-16T17:26:41Z"),
            _full_usage("2026-10-16T17:32:26Z", "a1", "end_turn", (50_000, 5_000, 11_697, 128_782)),
        ]},
        {"agent_id": "b7e1c0d94f2a6c315", "meta": {"description": "Rebuild the intake tickets"}, "lines": [
            _user("2026-10-16T18:40:02Z"),
            _full_usage("2026-10-16T18:43:00Z", "b1", "tool_use", (10_000, 5_000, 8_260, 96_744)),
        ]},
        {"agent_id": "hidden-day", "meta": {}, "lines": [_assistant("2026-10-16T25:00:00Z", "h1", 28_500)]},
        {"agent_id": "hidden-week", "meta": {}, "lines": [_assistant("2026-10-12T25:00:00Z", "h2", 860_000)]},
    ]  # fmt: skip
    session = [_assistant("2026-10-16T17:30:00Z", "s1", 155_000), _assistant("2026-10-12T09:00:00Z", "s2", 1_905_000)]
    windows = {
        "five_hour": {"start": "2026-10-16T17:00:00Z", "seat_usd": 2.0, "meter_fraction": 0.12, "resets_at": "2026-10-16T22:00:00Z"},
        "weekly": {"start": "2026-10-11T08:00:00Z", "seat_usd": 96.9, "meter_fraction": 0.57, "resets_at": "2026-10-18T08:00:00Z"},
    }  # fmt: skip
    node_rows = {
        "five_hour": [
            *_node_group("build", "standard", sonnet, 14, 96, 1_482_000, 1.1),
            *_node_group("review", "premium", opus, 3, 21, 318_000, 0.5),
            *_node_group("build", "premium", opus, 2, 17, 244_000, 0.4),
        ],
        "weekly": [
            *_node_group("build", "standard", sonnet, 212, 1480, 21_640_000, 41.3),
            *_node_group("review", "premium", opus, 58, 402, 6_120_000, 28.6),
            *_node_group("plan", "premium", opus, 37, 214, 3_890_000, 19.4),
            *_node_group("review", "standard", sonnet, 61, 305, 4_210_000, 7.6),
        ],
    }  # fmt: skip
    now = datetime(2026, 10, 16, 18, 45, 0, tzinfo=UTC)
    out = gather_activity(session, agents, _PRICES, now, windows, node_rows, {"five_hour": 2, "weekly": 6})
    assert out["spend_split"] == fixture["spend_split"]
    # The fixture lists the agents oldest first; `agent_rows` returns the newest first.
    assert out["chair_agents"] == list(reversed(fixture["chair_agents"]))


def test_the_inputs_are_not_mutated():
    args = (list(_SESSION), [copy.deepcopy(_AGENT)], copy.deepcopy(_PRICES), _NOW, copy.deepcopy(_WINDOWS))
    before = copy.deepcopy(args)
    gather_activity(*args)
    assert args == before
