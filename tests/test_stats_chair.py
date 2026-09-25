from __future__ import annotations

import json

import pytest

from agent_tools.stats_chair import chair_cost

_PRICES = {"claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.4}}


def _assistant(model: str, inp: int, write: int, read: int, out: int) -> str:
    usage = {"input_tokens": inp, "cache_creation_input_tokens": write, "cache_read_input_tokens": read, "output_tokens": out}
    return json.dumps({"type": "assistant", "sessionId": "s", "timestamp": "2026-09-24T14:30:29.810Z", "message": {"model": model, "usage": usage}})


# line 1: (2*4 + 347*20 + 26870*5 + 27975*0.4) / 1e6 = (8 + 6940 + 134350 + 11190) / 1e6 = 0.152488
# line 2: (10*4 + 1000*20 + 0*5 + 100000*0.4) / 1e6 = (40 + 20000 + 0 + 40000) / 1e6 = 0.06004
_TWO_LINES = [_assistant("claude-opus-5-5", 2, 26870, 27975, 347), _assistant("claude-opus-5-5", 10, 0, 100000, 1000)]


def test_chair_cost_over_a_two_line_transcript_equals_the_hand_computed_value():
    assert chair_cost(_TWO_LINES, _PRICES) == pytest.approx(0.212528)


def test_lines_that_are_not_priced_assistant_usage_add_nothing():
    noise = ["", "not json", "[1]", json.dumps({"type": "user", "message": {"content": "hi"}}), json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5"}}), _assistant("unpriced-model", 5, 5, 5, 5)]
    assert chair_cost([*_TWO_LINES, *noise], _PRICES) == pytest.approx(0.212528)


def test_a_missing_token_field_counts_as_zero():
    line = json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": {"output_tokens": 1000000}}})
    assert chair_cost([line], _PRICES) == pytest.approx(20.0)
