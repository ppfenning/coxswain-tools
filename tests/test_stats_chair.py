from __future__ import annotations

import json

import pytest

from agent_tools.stats_chair import (
    catalog_prices,
    chair_cost,
    chair_report,
    frontmatter_item,
    hand_finished,
    lines_since,
    quarantine_cost_by_cause,
    render_chair,
    session_ids,
    set_attempt_cause,
)

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


def _call(task_id, run, cost):
    return {"role": "build", "task_id": task_id, "cost_usd": cost, "model": "haiku", "tier": "cheap", "run": run}


def test_quarantine_cost_charges_each_run_to_the_cause_on_that_runs_attempt():
    items = [
        {"id": "a", "attempts": [{"run": "r1", "cause": "harness"}, {"run": "r2", "cause": "code"}]},
        {"id": "b", "attempts": [{"run": "r2", "cause": "ticket"}]},
        {"id": "c", "attempts": [{"run": "r2"}]},
    ]
    calls = [
        _call("a", "r1", 0.5), _call("a", "r2", 0.25), _call("b", "r2", 1.0), _call("c", "r2", 4.0),
        _call(None, "r2", 8.0), _call("zzz", "r2", 16.0), _call("a", "r9", 32.0),
    ]
    assert quarantine_cost_by_cause(items, calls) == {"ticket": 1.0, "code": 0.25, "harness": 0.5, "unknown": 0.0}


def test_quarantine_cost_is_zero_for_every_cause_with_no_items():
    assert quarantine_cost_by_cause([], [_call(None, "r1", 1.0)]) == {"ticket": 0.0, "code": 0.0, "harness": 0.0, "unknown": 0.0}


# The attempt shape real work items carry: one flow mapping per run.
_R1 = "  - {run: gate-1, phase: build, reason: 'fix loop: 3 rounds', ts: '2026-09-05T12:20:45+00:00'}\n"
_R2 = "  - {run: gate-2, phase: build, reason: budget}\n"
_ITEM = f"---\nid: t1\nattempts:\n{_R1}{_R2}lint: []\n---\nbody\n"


def test_set_attempt_cause_rewrites_the_named_runs_flow_entry_and_nothing_else():
    assert set_attempt_cause(_ITEM, "gate-1", "code", "bad: test") == _ITEM.replace(
        _R1, "  - {run: gate-1, phase: build, reason: 'fix loop: 3 rounds', ts: '2026-09-05T12:20:45+00:00', cause: code, note: 'bad: test'}\n"
    )


def test_set_attempt_cause_keeps_a_block_entry_in_block_style_at_its_own_indent():
    text = "---\nattempts:\n- run: r1\n  kind: quarantine\n- run: r1\n  kind: quarantine\n  tags:\n  - a\nlint: []\n---\n"
    assert set_attempt_cause(text, "r1", "ticket", None) == (
        "---\nattempts:\n- run: r1\n  kind: quarantine\n- run: r1\n  kind: quarantine\n  tags:\n  - a\n  cause: ticket\nlint: []\n---\n"
    )


def test_set_attempt_cause_keeps_an_existing_note_when_none_is_given():
    text = "---\nattempts:\n  - {run: r1, note: keep}\n---\n"
    assert set_attempt_cause(text, "r1", "ticket", None) == "---\nattempts:\n  - {run: r1, note: keep, cause: ticket}\n---\n"


@pytest.mark.parametrize("text", [
    _ITEM.replace("gate-", "other-"),
    "---\nattempts: []\n---\n",
    "---\nattempts: [gate-1]\n---\n",
    "---\nattempts:\n  - gate-1\n---\n",
    "---\nid: t1\n---\n",
    "no frontmatter\n",
])
def test_set_attempt_cause_is_none_without_a_mapping_attempt_for_the_run(text):
    assert set_attempt_cause(text, "gate-1", "code", None) is None


def test_hand_finished_is_a_landed_task_with_no_merge_row_at_exit_zero():
    rows = [
        {"run": "r1", "task": "a", "steps_reached": ["pr", "merge"], "exit": 0},
        {"run": "r1", "task": "b", "steps_reached": ["pr"], "exit": 1},
        {"run": "r1", "task": "c", "steps_reached": ["pr", "merge"], "exit": 1},
    ]
    assert hand_finished([("r1", "a"), ("r1", "b"), ("r1", "c"), ("r1", "d")], rows) == [("r1", "b"), ("r1", "c"), ("r1", "d")]


def test_chair_report_composes_every_fact():
    landed = [("r1", "a"), ("r1", "b"), ("r1", "c"), ("r1", "d")]
    rows = [{"run": "r1", "task": t, "steps_reached": ["merge"], "exit": 0} for t in "abc"]
    calls = [{"task_id": None, "cost_usd": 1.0, "run": "r1"}, {"task_id": "a", "cost_usd": 0.5, "run": "r1"}]
    items = [{"id": "a", "attempts": [{"run": "r1", "cause": "code"}]}]
    assert chair_report("2026-09-24", landed, calls, rows, 2.0, items) == {
        "window": {"since": "2026-09-24"},
        "harness_prs": 4,
        "harness_usd": 1.5,
        "chair_usd": 2.0,
        "chair_usd_per_pr": 0.5,
        "hand_finished": {"n": 1, "pct": 25.0},
        "quarantine_usd": {"ticket": 0.0, "code": 0.5, "harness": 0.0, "unknown": 0.0},
    }


def test_chair_report_with_no_prs_or_no_prices_reports_none():
    report = chair_report(None, [], [], [], None, [])
    assert (report["chair_usd_per_pr"], report["hand_finished"]["pct"], report["chair_usd"]) == (None, None, None)


def test_render_chair_has_the_harness_pr_row_and_one_quarantine_row_per_cause():
    lines = render_chair(chair_report(None, [("r1", "a")], [], [], 1.0, [])).splitlines()
    assert lines[1].startswith("harness PRs")
    assert [line.split()[2] for line in lines if line.startswith("quarantine $")] == ["ticket", "code", "harness", "unknown"]


def test_catalog_prices_reads_models_with_a_price_and_drops_the_rest():
    catalog = {"models": {"m": {"price": {"input": 1.0}}, "no-price": {"tier": "cheap"}}}
    assert catalog_prices(catalog) == {"m": {"input": 1.0}}
    assert catalog_prices(None) == {}


def test_lines_since_keeps_lines_dated_on_or_after_the_date():
    new = _assistant("m", 1, 0, 0, 1)
    old = new.replace("2026-09-24", "2026-09-01")
    assert lines_since([old, new, "junk"], "2026-09-24") == [new]
    assert lines_since([old, new], None) == [old, new]


def test_session_ids_merges_chair_json_history_and_extra_without_repeats():
    record = {"claude_session": "s1", "history": ["s0", {"claude_session": "s1"}]}
    assert session_ids(record, ["s2", "s0"]) == ["s1", "s0", "s2"]
    assert session_ids(None, []) == []


def test_frontmatter_item_reads_block_attempts_and_defaults_the_id_to_the_stem():
    text = "---\nstate: done\nattempts:\n  - run: r1\n    cause: code\n---\nbody\n"
    assert frontmatter_item(text, "t01") == {"state": "done", "attempts": [{"run": "r1", "cause": "code"}], "id": "t01"}
    assert frontmatter_item("no frontmatter", "t02") == {"id": "t02"}
