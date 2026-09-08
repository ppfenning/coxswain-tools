from agent_tools.events import Event
from agent_tools.stats_derive import attempt_numbers, extract_failure_class, resolve_outcome


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
    assert extract_failure_class([], "fix loop stopped: budget\n") == "budget_stop"


def test_extract_failure_class_reads_tool_error_from_a_tool_result_block():
    trace = [{"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}}]
    assert extract_failure_class(trace, "") == "tool_error"


def test_extract_failure_class_reads_empty_patch_from_a_success_with_no_edit_tool_call():
    trace = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
        {"type": "result", "is_error": False, "subtype": "success"},
    ]
    assert extract_failure_class(trace, "") == "empty_patch"


def test_extract_failure_class_reads_refused_from_an_assistant_stop_reason():
    trace = [{"type": "assistant", "message": {"stop_reason": "refusal", "content": []}}]
    assert extract_failure_class(trace, "") == "refused"


def test_extract_failure_class_reads_ok_from_a_success_with_an_edit_tool_call():
    trace = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {}}]}},
        {"type": "result", "is_error": False, "subtype": "success"},
    ]
    assert extract_failure_class(trace, "") == "ok"


def test_extract_failure_class_reads_ok_from_a_recovered_tool_error_that_finishes_clean():
    """A failed tool_result mid-trace, corrected by a later edit that lands, is
    `ok`: the terminal result governs, matching agent_tools/records.py:102's
    convention of trusting only the last `type: result` entry."""
    trace = [
        {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {}}]}},
        {"type": "result", "is_error": False, "subtype": "success"},
    ]
    assert extract_failure_class(trace, "") == "ok"


def test_extract_failure_class_defaults_to_unknown_with_no_signal_at_all():
    assert extract_failure_class([], "") == "unknown"
