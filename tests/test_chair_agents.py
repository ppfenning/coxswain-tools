from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_tools.chair_agents import agent_rows

_PRICES = {"claude-sonnet-5-5": {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.4}}
_NOW = datetime(2026, 10, 16, 18, 0, 0, tzinfo=UTC)
_FIXTURE = Path(__file__).parent / "fixtures" / "dash_activity_v1.json"


def _user(ts: str) -> str:
    return json.dumps({"type": "user", "timestamp": ts, "message": {"content": "go"}})


def _assistant(ts: str, message_id: str, stop_reason: str | None, out: int = 10) -> str:
    usage = {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": out}
    message = {"id": message_id, "model": "claude-sonnet-5-5", "stop_reason": stop_reason, "usage": usage}
    return json.dumps({"type": "assistant", "timestamp": ts, "message": message})


def _agent(agent_id: str, lines: list[str], description: str = "work") -> dict:
    return {"agent_id": agent_id, "meta": {"description": description}, "lines": lines}


def test_a_done_agent_reports_its_label_times_model_tokens_and_cost():
    lines = [_user("2026-10-16T17:26:41.250Z"), _assistant("2026-10-16T17:32:26.900Z", "m1", "end_turn")]
    [row] = agent_rows([_agent("a1", lines, "Land the phase\nsecond line")], _PRICES, _NOW)
    assert row == {
        "label": "Land the phase",
        "agent_id": "a1",
        "model": "claude-sonnet-5-5",
        "state": "done",
        "started_at": "2026-10-16T17:26:41Z",
        "finished_at": "2026-10-16T17:32:26Z",
        "tokens": 16,
        "cost_usd": 0.0002,
    }


def test_the_label_is_cut_to_120_characters():
    lines = [_user("2026-10-16T17:00:00Z"), _assistant("2026-10-16T17:01:00Z", "m1", "end_turn")]
    [row] = agent_rows([_agent("a1", lines, "x" * 200)], _PRICES, _NOW)
    assert row["label"] == "x" * 120


def test_an_agent_two_minutes_old_without_end_turn_is_running_with_no_finish():
    lines = [_user("2026-10-16T17:57:00Z"), _assistant("2026-10-16T17:58:00Z", "m1", "tool_use")]
    [row] = agent_rows([_agent("a1", lines)], _PRICES, _NOW)
    assert (row["state"], row["finished_at"], row["started_at"]) == ("running", None, "2026-10-16T17:57:00Z")


def test_an_agent_an_hour_stale_without_end_turn_is_failed_and_keeps_its_finish():
    lines = [_user("2026-10-16T16:50:00Z"), _assistant("2026-10-16T17:00:00Z", "m1", "tool_use")]
    [row] = agent_rows([_agent("a1", lines)], _PRICES, _NOW)
    assert (row["state"], row["finished_at"]) == ("failed", "2026-10-16T17:00:00Z")


def test_usage_repeated_across_lines_of_one_message_is_counted_once():
    lines = [
        _assistant("2026-10-16T17:00:00Z", "m1", None),
        _assistant("2026-10-16T17:00:01Z", "m1", "end_turn"),
    ]
    [row] = agent_rows([_agent("a1", lines)], _PRICES, _NOW)
    assert row["tokens"] == 16


def test_tokens_and_cost_both_take_the_first_usage_block_of_a_repeated_message():
    lines = [
        _assistant("2026-10-16T17:00:00Z", "m1", None, out=1_000_000),
        _assistant("2026-10-16T17:00:01Z", "m1", "end_turn", out=2_000_000),
    ]
    [row] = agent_rows([_agent("a1", lines)], _PRICES, _NOW)
    assert (row["tokens"], row["cost_usd"]) == (1_000_006, 20.0)


def _bare(ts: str, out: int, **top: str) -> str:
    usage = {"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": out}
    message = {"model": "claude-sonnet-5-5", "stop_reason": "end_turn", "usage": usage}
    return json.dumps({"type": "assistant", "timestamp": ts, **top, "message": message})


def test_without_a_message_id_the_request_id_then_the_line_itself_is_the_dedupe_key():
    ts = "2026-10-16T17:00:00Z"
    same_request = [_bare(ts, 10, requestId="r1"), _bare(ts, 20, requestId="r1")]
    same_line = [_bare(ts, 10), _bare(ts, 10)]
    different_lines = [_bare(ts, 10), _bare(ts, 20)]
    tokens = [
        agent_rows([_agent("a", lines)], _PRICES, _NOW)[0]["tokens"]
        for lines in (same_request, same_line, different_lines)
    ]
    assert tokens == [16, 16, 42]


def test_a_line_without_usage_does_not_hide_the_usage_of_its_message():
    no_usage = json.dumps(
        {
            "type": "assistant",
            "timestamp": "2026-10-16T17:00:00Z",
            "message": {"id": "m1", "model": "claude-sonnet-5-5"},
        }
    )
    lines = [no_usage, _assistant("2026-10-16T17:00:01Z", "m1", "end_turn")]
    [row] = agent_rows([_agent("a1", lines)], _PRICES, _NOW)
    assert row["tokens"] == 16


def test_a_transcript_with_no_assistant_has_no_model_no_tokens_and_a_state_from_its_age():
    fresh = _agent("fresh", [_user("2026-10-16T17:58:00Z")])
    stale = _agent("stale", [_user("2026-10-16T16:00:00Z")])
    rows = {row["agent_id"]: row for row in agent_rows([fresh, stale], _PRICES, _NOW)}
    assert (rows["fresh"]["state"], rows["fresh"]["model"], rows["fresh"]["tokens"], rows["fresh"]["cost_usd"]) == (
        "running",
        None,
        0,
        0.0,
    )
    assert rows["stale"]["state"] == "failed"


def test_non_json_lines_and_unparsable_timestamps_are_skipped_without_losing_other_agents():
    noisy = _agent(
        "noisy", ["not json", "[1]", _user("garbage"), _user("2026-10-16T17:57:00Z"), _assistant("", "m1", "end_turn")]
    )
    hopeless = _agent("hopeless", [_user(""), "not json"])
    good = _agent("good", [_user("2026-10-16T17:00:00Z"), _assistant("2026-10-16T17:01:00Z", "m2", "end_turn")])
    rows = agent_rows([noisy, hopeless, good], _PRICES, _NOW)
    assert [(r["agent_id"], r["started_at"], r["finished_at"]) for r in rows] == [
        ("noisy", "2026-10-16T17:57:00Z", "2026-10-16T17:57:00Z"),
        ("good", "2026-10-16T17:00:00Z", "2026-10-16T17:01:00Z"),
    ]


def test_rows_are_newest_started_first_and_cut_to_the_limit():
    def done(agent_id: str, start: str) -> dict:
        return _agent(agent_id, [_user(start), _assistant(start, "m", "end_turn")])

    agents = [
        done("old", "2026-10-16T10:00:00Z"),
        done("new", "2026-10-16T12:00:00Z"),
        done("mid", "2026-10-16T11:00:00Z"),
    ]
    assert [r["agent_id"] for r in agent_rows(agents, _PRICES, _NOW)] == ["new", "mid", "old"]
    assert [r["agent_id"] for r in agent_rows(agents, _PRICES, _NOW, limit=2)] == ["new", "mid"]


def test_the_row_keys_equal_the_fixtures_first_chair_agent():
    fixture_keys = set(json.loads(_FIXTURE.read_text())["chair_agents"][0])
    lines = [_user("2026-10-16T17:00:00Z"), _assistant("2026-10-16T17:01:00Z", "m1", "end_turn")]
    [row] = agent_rows([_agent("a1", lines)], _PRICES, _NOW)
    assert set(row) == fixture_keys
