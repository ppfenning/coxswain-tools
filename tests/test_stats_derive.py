import pytest

from agent_tools.events import Event
from agent_tools.stats_derive import (
    LIST_RATES_USD_PER_MTOK,
    attempt_numbers,
    bounds_for_costs,
    extract_failure_class,
    resolve_outcome,
    spend_mix_for_model,
)


def test_bounds_for_costs_reports_strict_moderate_liberal_from_p50_and_p95():
    costs = [float(i) for i in range(1, 21)]
    assert bounds_for_costs(costs) == {
        "n": 20, "p50": 10.0, "p95": 19.0, "max": 20.0,
        "strict": 10.0, "moderate": 19.0, "liberal": 57.0,
    }


def test_bounds_for_costs_is_insufficient_under_twenty_with_no_candidates():
    assert bounds_for_costs([1.0] * 19) == {"n": 19, "insufficient": True}


def test_attempt_numbers_counts_a_role_repeated_across_a_run():
    calls = [{"role": "build"} for _ in range(12)]
    assert attempt_numbers(calls) == list(range(1, 13))


def test_attempt_numbers_resets_per_role_not_per_run():
    calls = [{"role": "plan"}, {"role": "build"}, {"role": "build"}, {"role": "review"}, {"role": "build"}]
    assert attempt_numbers(calls) == [1, 1, 2, 1, 3]


def test_resolve_outcome_reads_the_explicit_landed_field_first():
    record = {"landed": True, "gate_diffs": [{"outcome": "quarantined"}]}
    assert resolve_outcome(record) == ("landed", "landed_field")


def test_resolve_outcome_falls_to_gate_diffs_when_landed_is_false():
    record = {"ticket": "t1", "landed": False, "gate_diffs": [{"target": "t1", "outcome": "quarantined"}]}
    assert resolve_outcome(record) == ("quarantined", "gate_diffs")


def test_resolve_outcome_reads_the_last_gate_diffs_entry_not_the_first():
    record = {
        "ticket": "t1",
        "gate_diffs": [{"target": "t1", "outcome": "quarantined"}, {"target": "t1", "outcome": "landed"}],
    }
    assert resolve_outcome(record) == ("landed", "gate_diffs")


def test_resolve_outcome_never_matches_gate_diffs_when_the_records_own_ticket_is_missing():
    """A gate_diffs entry with no 'target' key must not match a task_record with
    no 'ticket' key by coincidence of both defaulting to None."""
    record = {"gate_diffs": [{"outcome": "landed"}]}
    assert resolve_outcome(record) == ("unknown", "unknown")


def test_resolve_outcome_ignores_a_gate_diffs_entry_targeting_a_different_ticket():
    record = {
        "ticket": "this-ticket",
        "gate_diffs": [{"target": "this-ticket", "outcome": "quarantined"}, {"target": "other-ticket", "outcome": "landed"}],
    }
    assert resolve_outcome(record) == ("quarantined", "gate_diffs")


def test_resolve_outcome_falls_through_when_no_gate_diffs_entry_targets_this_ticket():
    record = {"ticket": "this-ticket", "gate_diffs": [{"target": "other-ticket", "outcome": "landed"}]}
    assert resolve_outcome(record) == ("unknown", "unknown")


def test_resolve_outcome_falls_to_the_log_line_when_gate_diffs_is_empty():
    record = {
        "ticket": "some-ticket",
        "gate_diffs": [],
        "log_events": [Event("run1", "task_quarantined", 5, {"task": "some-ticket", "reason": "budget"})],
    }
    assert resolve_outcome(record) == ("quarantined", "log_line")


def test_resolve_outcome_defaults_to_unknown_with_no_signal_at_all():
    assert resolve_outcome({}) == ("unknown", "unknown")


def test_resolve_outcome_reads_the_work_store_ahead_of_the_log_line():
    record = {
        "ticket": "some-ticket",
        "gate_diffs": [],
        "work_store_done": True,
        "log_events": [Event("run1", "task_quarantined", 5, {"task": "some-ticket", "reason": "budget"})],
    }
    assert resolve_outcome(record) == ("landed", "work_store")


def test_resolve_outcome_still_prefers_the_explicit_landed_field_over_the_work_store():
    record = {"landed": True, "work_store_done": False, "gate_diffs": [{"outcome": "quarantined"}]}
    assert resolve_outcome(record) == ("landed", "landed_field")


def test_resolve_outcome_reads_the_work_store_ahead_of_a_scoped_gate_diffs_entry():
    """A node record's gate_diffs can carry a 'skipped' outcome for this ticket
    while the work store already shows state: done — the work store must win,
    not be shadowed by the diff that ran before the ticket landed."""
    record = {
        "ticket": "t1",
        "gate_diffs": [{"target": "t1", "outcome": "skipped"}],
        "work_store_done": True,
    }
    assert resolve_outcome(record) == ("landed", "work_store")


def test_a_run_level_budget_stop_never_sets_either_co_resident_tasks_outcome():
    """The deliverable: agent_tools/events.py:51 writes a budget_stop Event
    with an empty detail dict, naming no task. A run with two tickets sharing
    that one log line must leave BOTH tasks 'unknown', never 'budget_stop'."""
    budget_stop_log = [Event("run1", "budget_stop", 9, {})]
    task_a = {"ticket": "ticket-a", "log_events": budget_stop_log}
    task_b = {"ticket": "ticket-b", "log_events": budget_stop_log}
    assert resolve_outcome(task_a) == ("unknown", "unknown")
    assert resolve_outcome(task_b) == ("unknown", "unknown")


def test_extract_failure_class_reads_budget_stop_from_the_log_excerpt():
    assert extract_failure_class([], "fix loop stopped: budget\n", "build") == "budget_stop"


def test_extract_failure_class_reads_tool_error_from_a_tool_result_block():
    trace = [{"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}}]
    assert extract_failure_class(trace, "", "build") == "tool_error"


def test_extract_failure_class_reads_empty_patch_from_a_success_with_no_edit_tool_call():
    trace = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
        {"type": "result", "is_error": False, "subtype": "success"},
    ]
    assert extract_failure_class(trace, "", "build") == "empty_patch"


def test_extract_failure_class_reads_refused_from_an_assistant_stop_reason():
    trace = [{"type": "assistant", "message": {"stop_reason": "refusal", "content": []}}]
    assert extract_failure_class(trace, "", "build") == "refused"


def test_extract_failure_class_reads_ok_from_a_success_with_an_edit_tool_call():
    trace = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {}}]}},
        {"type": "result", "is_error": False, "subtype": "success"},
    ]
    assert extract_failure_class(trace, "", "build") == "ok"


def test_extract_failure_class_reads_ok_from_a_recovered_tool_error_that_finishes_clean():
    """A failed tool_result mid-trace, corrected by a later edit that lands, is
    `ok`: the terminal result governs, matching agent_tools/records.py:102's
    convention of trusting only the last `type: result` entry."""
    trace = [
        {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {}}]}},
        {"type": "result", "is_error": False, "subtype": "success"},
    ]
    assert extract_failure_class(trace, "", "build") == "ok"


def test_extract_failure_class_defaults_to_unknown_with_no_signal_at_all():
    assert extract_failure_class([], "", "build") == "unknown"


@pytest.mark.parametrize("role", ["handoff", "review_charter", "plan", None])
def test_extract_failure_class_never_reads_empty_patch_for_a_non_patching_role(role):
    """empty_patch is only meaningful where a patch was expected: only 'build' and
    'style_pass' are granted Write/Edit by the provider profile."""
    trace = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
        {"type": "result", "is_error": False, "subtype": "success"},
    ]
    assert extract_failure_class(trace, "", role) == "ok"


# cost-bounds.md §6 rules 1-7 govern gather/pacing (rule 1-2), bounds/censoring
# (rules 3-5) and the harness/policy level (rules 6-7); none of those name
# spend-mix. Rule 8, "spend-mix cost shares sum to 1.0 per model and use the
# pinned rates", is the only §6 rule this section owns, and
# test_spend_mix_for_model_cost_shares_sum_to_one_at_the_pinned_rates below is
# its one literal test.


def test_list_rates_pin_the_2026_09_published_list_prices():
    assert LIST_RATES_USD_PER_MTOK["sonnet"] == {"input": 3.0, "output": 15.0, "cache_creation": 3.75, "cache_read": 0.30}
    assert LIST_RATES_USD_PER_MTOK["opus"] == {"input": 15.0, "output": 75.0, "cache_creation": 18.75, "cache_read": 1.50}
    assert LIST_RATES_USD_PER_MTOK["haiku"] == {"input": 0.80, "output": 4.0, "cache_creation": 1.0, "cache_read": 0.08}


def test_spend_mix_for_model_sums_token_counts_by_class_across_rows():
    rows = [
        {"role": "build", "input_tokens": 100, "output_tokens": 50, "cache_creation_tokens": 0, "cache_read_tokens": 0},
        {"role": "plan", "input_tokens": 200, "output_tokens": 10, "cache_creation_tokens": 5, "cache_read_tokens": 5},
    ]
    result = spend_mix_for_model(rows, "sonnet")
    assert result["token_counts"] == {"input": 300, "output": 60, "cache_creation": 5, "cache_read": 5}


def test_spend_mix_for_model_cost_shares_sum_to_one_at_the_pinned_rates():
    """cost-bounds.md §6 rule 8."""
    rows = [
        {"role": "build", "input_tokens": 100, "output_tokens": 50, "cache_creation_tokens": 0, "cache_read_tokens": 0},
        {"role": "plan", "input_tokens": 200, "output_tokens": 10, "cache_creation_tokens": 5, "cache_read_tokens": 5},
    ]
    result = spend_mix_for_model(rows, "sonnet")
    assert result["cost_share"] == pytest.approx({
        "input": 0.494438, "output": 0.494438, "cache_creation": 0.010301, "cache_read": 0.000824,
    }, abs=1e-5)
    assert sum(result["cost_share"].values()) == pytest.approx(1.0)


def test_spend_mix_for_model_build_split_excludes_non_build_rows():
    rows = [
        {"role": "build", "input_tokens": 100, "output_tokens": 50, "cache_creation_tokens": 0, "cache_read_tokens": 0},
        {"role": "plan", "input_tokens": 200, "output_tokens": 10, "cache_creation_tokens": 5, "cache_read_tokens": 5},
    ]
    result = spend_mix_for_model(rows, "sonnet")
    assert result["build"]["token_counts"] == {"input": 100, "output": 50, "cache_creation": 0, "cache_read": 0}
    assert result["build"]["cost_share"] == pytest.approx({"input": 0.285714, "output": 0.714286, "cache_creation": 0.0, "cache_read": 0.0}, abs=1e-5)


def test_spend_mix_for_model_with_no_tokens_reports_zero_shares_not_a_raise():
    result = spend_mix_for_model([], "sonnet")
    assert result["token_counts"] == {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
    assert result["cost_share"] == {"input": 0.0, "output": 0.0, "cache_creation": 0.0, "cache_read": 0.0}
    assert result["build"]["cost_share"] == {"input": 0.0, "output": 0.0, "cache_creation": 0.0, "cache_read": 0.0}
