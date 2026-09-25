import json

from agent_tools.stats_examples import (
    build_examples,
    format_lines,
    handoff_example,
    parse_record,
    review_charter_example,
    since_ok,
)

_HANDOFF = {"plan": {"steps": ["a"]}, "change_facts": {"module_count": 1}, "handoff": {"complete": True}}
_REVIEW = {"ticket": "t-1", "build": {"patch": "diff --git a b"}, "review": {"verdict": "revise"}}


def _state_text(example):
    return "\n".join(f"{name}: {text}" for name, text in example["state"].items())


def test_handoff_true_is_yes_and_the_state_is_python_str_in_order():
    example = handoff_example(_HANDOFF)
    assert example == {"state": {"plan": "{'steps': ['a']}", "change_facts": "{'module_count': 1}"}, "label": "yes"}
    assert list(example["state"]) == ["plan", "change_facts"]
    assert _state_text(example) == "plan: {'steps': ['a']}\nchange_facts: {'module_count': 1}"


def test_handoff_false_is_no():
    assert handoff_example({**_HANDOFF, "handoff": {"complete": False}})["label"] == "no"


def test_handoff_skips_a_missing_plan_a_string_complete_and_an_empty_plan():
    assert handoff_example({k: v for k, v in _HANDOFF.items() if k != "plan"}) is None
    assert handoff_example({**_HANDOFF, "handoff": {"complete": "true"}}) is None
    assert handoff_example({**_HANDOFF, "handoff": None}) is None
    assert handoff_example({**_HANDOFF, "plan": ""}) is None
    assert handoff_example({**_HANDOFF, "plan": None}) is None


def test_review_charter_takes_the_patch_and_the_ticket_as_plan_in_order():
    example = review_charter_example(_REVIEW)
    assert example == {"state": {"patch": "diff --git a b", "plan": "t-1"}, "label": "revise"}
    assert list(example["state"]) == ["patch", "plan"]


def test_review_charter_labels_all_three_verdicts_and_ignores_arbitration():
    for verdict in ("approve", "revise", "reject"):
        assert review_charter_example({**_REVIEW, "review": {"verdict": verdict}, "arbitration": {"verdict": "reject"}})["label"] == verdict


def test_review_charter_skips_a_bad_verdict_a_non_string_patch_and_an_empty_ticket():
    assert review_charter_example({**_REVIEW, "review": {"verdict": "maybe"}}) is None
    assert review_charter_example({**_REVIEW, "review": {"verdict": None}}) is None
    assert review_charter_example({**_REVIEW, "build": {"patch": 3}}) is None
    assert review_charter_example({**_REVIEW, "build": {}}) is None
    assert review_charter_example({**_REVIEW, "ticket": ""}) is None


def test_since_ok_keeps_the_day_itself_and_later_and_drops_earlier_or_undated():
    assert since_ok({"date": "2026-09-24"}, "2026-09-24")
    assert since_ok({"date": "2026-09-25T01:00:00Z"}, "2026-09-24")
    assert not since_ok({"date": "2026-09-23T23:59:59Z"}, "2026-09-24")
    assert not since_ok({}, "2026-09-24")
    assert since_ok({}, None)


def test_build_examples_drops_unparsed_and_unusable_records():
    records = [_HANDOFF, None, {"handoff": {"complete": True}}, {**_HANDOFF, "date": "2026-01-01"}, {**_HANDOFF, "date": "2026-09-24"}]
    assert len(build_examples(records, "handoff", None)) == 3
    assert len(build_examples(records, "handoff", "2026-09-01")) == 1


def test_parse_record_accepts_only_a_json_object():
    assert parse_record('{"a": 1}') == {"a": 1}
    assert parse_record("[1]") is None
    assert parse_record("{oops") is None


def test_format_lines_round_trips_one_object_per_line():
    text = format_lines([handoff_example(_HANDOFF), review_charter_example(_REVIEW)])
    lines = text.splitlines()
    assert [set(json.loads(line)) for line in lines] == [{"state", "label"}] * 2
    assert format_lines([]) == ""
