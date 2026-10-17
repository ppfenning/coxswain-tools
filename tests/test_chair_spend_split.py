import json
from pathlib import Path

from agent_tools.chair_spend_split import spend_split

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dash_activity_v1.json").read_text())["spend_split"]
# Temporary: the producer task that emits these keys removes this exclusion.
NEW_KEYS = {"by_node", "cost_per_landed_task"}

ZERO = {
    "chair_session_usd": 0.0,
    "chair_agents_usd": 0.0,
    "cox_seats_usd": 0.0,
    "total_usd": 0.0,
    "chair_session_share": 0.0,
    "chair_agents_share": 0.0,
    "cox_seats_share": 0.0,
    "meter_fraction": 0.0,
    "resets_at": "",
}


def _window(session, agents, seats, meter, resets):
    return {
        "chair_session_usd": session,
        "chair_agents_usd": agents,
        "cox_seats_usd": seats,
        "meter_fraction": meter,
        "resets_at": resets,
    }


def test_five_hour_matches_fixture():
    out = spend_split({"five_hour": _window(3.1, 1.2, 2.0, 0.12, "2026-10-16T22:00:00Z")})
    assert out["five_hour"] == {k: v for k, v in FIXTURE["five_hour"].items() if k not in NEW_KEYS}


def test_weekly_matches_fixture():
    out = spend_split({"weekly": _window(41.2, 18.4, 96.9, 0.57, "2026-10-18T08:00:00Z")})
    assert out["weekly"] == {k: v for k, v in FIXTURE["weekly"].items() if k not in NEW_KEYS}


def test_zero_total_gives_zero_shares():
    out = spend_split({"five_hour": _window(0, 0, 0, 0.3, "x")})
    assert out["five_hour"] == {**ZERO, "meter_fraction": 0.3, "resets_at": "x"}


def test_none_values_and_missing_window_give_all_zero():
    out = spend_split({"five_hour": _window(None, None, None, None, None)})
    assert out == {"five_hour": ZERO, "weekly": ZERO}


def test_output_is_json_serialisable():
    out = spend_split({"weekly": _window(41.2, 18.4, 96.9, 0.57, "r")})
    assert json.loads(json.dumps(out)) == out
