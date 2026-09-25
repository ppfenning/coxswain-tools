from agent_tools.stats_gates import gate_rows, verdict_line


def _call(role, cost, task_id="t1", run_id="r1"):
    return {"run_id": run_id, "role": role, "cost_usd": cost, "task_id": task_id}


def _task(task_id, run_id="r1", outcome="landed", **facts):
    return {"run_id": run_id, "task_id": task_id, "outcome": outcome, **facts}


def _row(role, tasks, calls):
    return next(r for r in gate_rows(tasks, calls) if r["role"] == role)


REBUILT = {"fix_loop_attempts": 2, "fix_loop_stopped": 0}


def test_calls_and_cost_count_every_call_including_those_without_a_task():
    row = _row("handoff", [], [_call("handoff", 0.5), _call("handoff", 0.25, task_id=None)])
    assert (row["calls"], row["cost"]) == (2, 0.75)


def test_cost_per_task_divides_by_distinct_call_task_ids_even_without_a_task_row_and_skips_none():
    tasks = [_task("t1"), _task("t2")]
    calls = [
        _call("handoff", 1.0, "t1"),
        _call("handoff", 1.0, "t1"),
        _call("handoff", 1.0, "t2"),
        _call("handoff", 3.0, "t9"),
        _call("handoff", 3.0, None),
    ]
    assert _row("handoff", tasks, calls)["cost_per_task"] == 3.0


def test_verdict_mix_counts_each_verdict_over_joined_tasks():
    tasks = [_task("t1", charter_verdict="approve"), _task("t2", charter_verdict="revise"), _task("t3", charter_verdict="approve")]
    calls = [_call("review_charter", 1, t) for t in ("t1", "t2", "t3")]
    assert _row("review_charter", tasks, calls)["verdict_mix"] == {"approve": 2, "revise": 1}


def test_changed_is_an_objecting_verdict_followed_by_a_rebuild_or_a_stop():
    tasks = [
        _task("t1", charter_verdict="revise", fix_loop_attempts=2, fix_loop_stopped=0),
        _task("t2", charter_verdict="revise", fix_loop_attempts=1, fix_loop_stopped=1),
        _task("t3", charter_verdict="revise", fix_loop_attempts=1, fix_loop_stopped=0),
        _task("t4", charter_verdict="approve"),
    ]
    calls = [_call("review_charter", 1, t["task_id"]) for t in tasks]
    row = _row("review_charter", tasks, calls)
    assert (row["changed"], row["changed_rate"]) == (2, 0.5)


def test_handoff_and_plan_roles_need_a_rebuild_too():
    tasks = [
        _task("t1", handoff_verdict="no", plan_gate_verdict="fail", fix_loop_attempts=1, fix_loop_stopped=0),
        _task("t2", handoff_verdict="no", plan_gate_verdict="fail", **REBUILT),
    ]
    calls = [_call(role, 1, t) for role in ("handoff", "plan_attack") for t in ("t1", "t2")]
    rows = {r["role"]: r for r in gate_rows(tasks, calls)}
    assert (rows["handoff"]["changed"], rows["plan_attack"]["changed"]) == (1, 1)


def test_a_reject_verdict_followed_by_a_rebuild_counts_as_changed_for_every_review_role():
    tasks = [
        _task("t1", charter_verdict="reject", adversary_verdict="reject", arbiter_verdict="reject", **REBUILT),
        _task("t2", charter_verdict="approve", adversary_verdict="approve", arbiter_verdict="approve"),
    ]
    calls = [_call(role, 1, t) for role in ("review_charter", "review_adversary", "arbitrate") for t in ("t1", "t2")]
    rows = gate_rows(tasks, calls)
    assert [(r["changed"], r["changed_rate"]) for r in rows] == [(1, 0.5)] * 3


def test_a_silent_objection_is_excluded_and_the_other_tasks_still_rate():
    tasks = [_task("t1", charter_verdict="revise")] + [
        _task(f"a{n}", charter_verdict="approve") for n in range(30)
    ]
    calls = [_call("review_charter", 1, t["task_id"]) for t in tasks]
    row = _row("review_charter", tasks, calls)
    assert (row["verdict_tasks"], row["excluded"], row["changed"], row["changed_rate"]) == (31, 1, 0, 0.0)
    assert "cheaper tier" in verdict_line(row)


def test_changed_rate_is_over_the_tasks_with_a_known_outcome():
    tasks = [
        _task("t1", charter_verdict="revise"),
        _task("t2", charter_verdict="revise", **REBUILT),
        _task("t3", charter_verdict="approve"),
    ]
    calls = [_call("review_charter", 1, t["task_id"]) for t in tasks]
    row = _row("review_charter", tasks, calls)
    assert (row["verdict_mix"], row["excluded"], row["changed"], row["changed_rate"]) == ({"revise": 2, "approve": 1}, 1, 1, 0.5)


def test_objections_that_are_all_silent_on_the_fix_loop_report_no_outcome_and_are_not_flagged():
    tasks = [_task(f"t{n}", charter_verdict="revise") for n in range(30)]
    calls = [_call("review_charter", 1, t["task_id"]) for t in tasks]
    row = _row("review_charter", tasks, calls)
    assert (row["changed"], row["changed_rate"], row["caught"], row["cost_per_changed"]) == (None, None, None, None)
    assert "no outcome data" in verdict_line(row)


def test_an_unknown_plan_gate_verdict_yields_no_changed_fact():
    row = _row("plan_attack", [_task("t1", plan_gate_verdict="proceed", **REBUILT)], [_call("plan_attack", 1, "t1")])
    assert (row["verdict_mix"], row["changed"], row["changed_rate"]) == ({"proceed": 1}, None, None)


def test_plan_attack_and_plan_competition_share_one_outcome_and_one_verdict_line():
    tasks = [_task("t1", plan_gate_verdict="fail", **REBUILT), _task("t2", plan_gate_verdict="pass")]
    calls = [_call("plan_attack", 1.0, "t1"), _call("plan_attack", 1.0, "t2"), _call("plan_competition", 4.0, "t1")]
    rows = {r["role"]: r for r in gate_rows(tasks, calls)}
    attack, competition = rows["plan_attack"], rows["plan_competition"]
    assert (attack["changed"], competition["changed"]) == (1, 1)
    assert (attack["cost_per_changed"], competition["cost_per_changed"]) == (2.0, 4.0)
    assert verdict_line(attack).split(": ")[1] == verdict_line(competition).split(": ")[1]


def test_caught_counts_changed_tasks_that_landed():
    tasks = [
        _task("t1", handoff_verdict="no", outcome="landed", **REBUILT),
        _task("t2", handoff_verdict="no", outcome="quarantined", **REBUILT),
        _task("t3", handoff_verdict="yes", outcome="landed"),
    ]
    calls = [_call("handoff", 1, t["task_id"]) for t in tasks]
    row = _row("handoff", tasks, calls)
    assert (row["changed"], row["caught"]) == (2, 1)


def test_agreed_counts_tasks_both_reviewers_approved_and_only_on_review_roles():
    tasks = [
        _task("t1", charter_verdict="approve", adversary_verdict="approve", handoff_verdict="yes"),
        _task("t2", charter_verdict="approve", adversary_verdict="revise", handoff_verdict="yes"),
    ]
    calls = [_call(role, 1, t) for role in ("review_charter", "review_adversary", "handoff") for t in ("t1", "t2")]
    rows = {r["role"]: r for r in gate_rows(tasks, calls)}
    assert (rows["review_charter"]["agreed"], rows["review_adversary"]["agreed"]) == (1, 1)
    assert rows["handoff"]["agreed"] is None


def test_cost_per_changed_divides_cost_by_changed():
    tasks = [_task("t1", handoff_verdict="no", **REBUILT), _task("t2", handoff_verdict="yes")]
    calls = [_call("handoff", 3.0, "t1"), _call("handoff", 1.0, "t2")]
    assert _row("handoff", tasks, calls)["cost_per_changed"] == 4.0


def test_cost_per_changed_is_none_when_nothing_changed():
    tasks = [_task("t1", handoff_verdict="yes")]
    row = _row("handoff", tasks, [_call("handoff", 3.0, "t1")])
    assert (row["changed"], row["cost_per_changed"]) == (0, None)


def test_a_role_without_verdict_facts_reports_none_never_zero():
    row = _row("validate_chunk", [_task("t1", **REBUILT)], [_call("validate_chunk", 2.0, "t1")])
    assert (row["calls"], row["cost"], row["cost_per_task"]) == (1, 2.0, 2.0)
    none_fields = ("verdict_mix", "changed", "changed_rate", "caught", "agreed", "cost_per_changed")
    assert [row[f] for f in none_fields] == [None] * 6


def test_rows_follow_role_order_and_omit_roles_without_calls():
    calls = [_call("plan_competition", 1), _call("arbitrate", 1), _call("handoff", 1)]
    assert [r["role"] for r in gate_rows([], calls)] == ["handoff", "arbitrate", "plan_competition"]


def test_verdict_line_flags_a_rate_under_five_percent():
    line = verdict_line({"role": "review_charter", "changed_rate": 0.04})
    assert "review_charter" in line and "cheaper tier" in line and "small tasks" in line


def test_verdict_line_does_not_flag_a_rate_of_exactly_five_percent():
    assert verdict_line({"role": "review_charter", "changed_rate": 0.05}) == "review_charter: the step is earning its cost."


def test_verdict_line_says_the_record_has_no_outcome_data_when_the_rate_is_none():
    assert "no outcome data" in verdict_line({"role": "validate_chunk", "changed_rate": None})
