from agent_tools.stats_query import explain_report, render_capped, roles_report, series_report


def _call(task_id, role="build", model="sonnet", join_confidence="heuristic", failure_class=None):
    return {
        "role": role,
        "model": model,
        "task_id": task_id,
        "join_confidence": join_confidence,
        "failure_class": failure_class,
    }


def _task(task_id, outcome="landed", attempt=None, cost_usd=0.0):
    return {"task_id": task_id, "outcome": outcome, "attempt": attempt, "cost_usd": cost_usd}


def _call_with_run(run_id, task_id, role="build", model="sonnet"):
    return {**_call(task_id, role=role, model=model), "run_id": run_id}


def test_roles_report_excludes_a_null_attempt_task_from_attempts_to_land_but_counts_it():
    calls = [_call("t1"), _call("t2"), _call("t3")]
    tasks = [
        _task("t1", attempt=2, cost_usd=1.0),
        _task("t2", attempt=4, cost_usd=1.0),
        _task("t3", attempt=None, cost_usd=1.0),
    ]
    [row] = roles_report(calls, tasks)
    assert row["attempts_to_land"] == 3.0
    assert row["attempts_unknown"] == 1
    assert row["landed_rate"] == 1.0


def test_roles_report_reports_coverage_below_one_for_a_partial_join():
    calls = [
        _call("t1"),
        _call("t2"),
        _call(None, join_confidence="none"),
    ]
    tasks = [_task("t1", attempt=1, cost_usd=2.0), _task("t2", outcome="quarantined", attempt=1)]
    [row] = roles_report(calls, tasks)
    assert row["n_calls"] == 3
    assert row["n_joined"] == 2
    assert row["coverage"] == round(2 / 3, 4)
    assert row["landed_rate"] == 0.5


def test_roles_report_flags_a_group_spanning_two_regimes():
    calls = [_call_with_run("r1", "t1"), _call_with_run("r2", "t2")]
    tasks = [_task("t1", attempt=1, cost_usd=1.0), _task("t2", attempt=1, cost_usd=1.0)]
    runs = [
        {"run_id": "r1", "cartridge_sha": "abc", "provider_profile": "old-profile"},
        {"run_id": "r2", "cartridge_sha": "abc", "provider_profile": "new-profile"},
    ]
    [row] = roles_report(calls, tasks, runs)
    assert row["regimes"] == [
        {"cartridge_sha": "abc", "provider_profile": "new-profile"},
        {"cartridge_sha": "abc", "provider_profile": "old-profile"},
    ]


def test_roles_report_provider_profile_filter_drops_calls_from_the_other_regime():
    calls = [_call_with_run("r1", "t1"), _call_with_run("r2", "t2")]
    tasks = [_task("t1", attempt=1, cost_usd=1.0), _task("t2", attempt=1, cost_usd=1.0)]
    runs = [
        {"run_id": "r1", "cartridge_sha": "abc", "provider_profile": "old-profile"},
        {"run_id": "r2", "cartridge_sha": "abc", "provider_profile": "new-profile"},
    ]
    [row] = roles_report(calls, tasks, runs, provider_profile="new-profile")
    assert row["n_calls"] == 1
    assert row["regimes"] == [{"cartridge_sha": "abc", "provider_profile": "new-profile"}]


def test_roles_report_cartridge_sha_filter_drops_calls_from_the_other_regime():
    calls = [_call_with_run("r1", "t1"), _call_with_run("r2", "t2")]
    tasks = [_task("t1", attempt=1, cost_usd=1.0), _task("t2", attempt=1, cost_usd=1.0)]
    runs = [
        {"run_id": "r1", "cartridge_sha": "sha-old", "provider_profile": "p1"},
        {"run_id": "r2", "cartridge_sha": "sha-new", "provider_profile": "p1"},
    ]
    [row] = roles_report(calls, tasks, runs, cartridge_sha="sha-new")
    assert row["n_calls"] == 1
    assert row["regimes"] == [{"cartridge_sha": "sha-new", "provider_profile": "p1"}]


def test_roles_report_cost_per_landed_counts_only_priced_landed_tasks():
    calls = [_call("t1"), _call("t2")]
    tasks = [_task("t1", attempt=1, cost_usd=2.0), _task("t2", attempt=1, cost_usd=None)]
    [row] = roles_report(calls, tasks)
    assert row["cost_per_landed"] == 2.0
    assert row["cost_unknown"] == 1


def test_roles_report_cost_per_landed_is_none_when_no_landed_task_carries_a_cost():
    calls = [_call("t1")]
    tasks = [_task("t1", attempt=1, cost_usd=None)]
    [row] = roles_report(calls, tasks)
    assert row["cost_per_landed"] is None
    assert row["cost_unknown"] == 1


def test_series_report_cartridge_sha_filter_keeps_only_matching_runs():
    runs = [
        {"run_id": "run-a", "cartridge_sha": "abc", "provider_profile": "p1"},
        {"run_id": "run-b", "cartridge_sha": "def", "provider_profile": "p1"},
    ]
    rows = series_report(runs, [], cartridge_sha="def")
    assert [r["run_id"] for r in rows] == ["run-b"]


def test_explain_report_keeps_unclassified_calls_out_of_the_unknown_bucket():
    calls = [
        _call("t1", failure_class="ok"),
        _call("t2", failure_class="ok"),
        _call("t3", failure_class="budget_stop"),
        _call("t4", failure_class="unknown"),
        _call("t5", failure_class=None),
    ]
    report = explain_report(calls, [], "build")
    assert report["by_failure_class"] == {"ok": 2, "budget_stop": 1, "unknown": 1}
    assert report["unclassified"] == 1
    assert report["coverage"] == 0.8


def test_series_report_reports_none_cost_per_landed_when_a_run_lands_nothing():
    runs = [
        {"run_id": "run-a", "cartridge_sha": "abc", "provider_profile": "p1"},
        {"run_id": "run-b", "cartridge_sha": "def", "provider_profile": "p1"},
    ]
    tasks = [
        {"run_id": "run-a", "outcome": "landed", "cost_usd": 1.0},
        {"run_id": "run-a", "outcome": "landed", "cost_usd": 2.0},
        {"run_id": "run-a", "outcome": "quarantined", "cost_usd": 0.5},
        {"run_id": "run-b", "outcome": "quarantined", "cost_usd": 0.5},
    ]
    rows = series_report(runs, tasks)
    by_id = {r["run_id"]: r for r in rows}
    assert by_id["run-a"]["cost_per_landed"] == round(3.5 / 2, 2)
    assert by_id["run-b"]["cost_per_landed"] is None
    assert by_id["run-b"]["tasks_landed"] == 0


def test_series_report_cost_per_landed_is_none_when_the_landed_task_carries_no_cost():
    runs = [{"run_id": "run-a", "cartridge_sha": "abc", "provider_profile": "p1"}]
    tasks = [{"run_id": "run-a", "outcome": "landed", "cost_usd": None}]
    [row] = series_report(runs, tasks)
    assert row["tasks_landed"] == 1
    assert row["cost_per_landed"] is None


def test_series_report_reports_zero_coverage_for_a_run_with_no_task_rows():
    runs = [{"run_id": "run-z", "cartridge_sha": "zzz", "provider_profile": "p1"}]
    rows = series_report(runs, [])
    [row] = rows
    assert row["coverage"] == 0.0
    assert row["cost_usd"] == 0.0
    assert row["cost_per_landed"] is None


def test_render_capped_drops_trailing_rows_and_marks_the_drop():
    rows = [{"role": "build", "model": "sonnet", "n": i} for i in range(50)]
    result = render_capped(rows, cap_tokens=50)
    assert "more rows" in result
    assert len(result) // 4 <= 50


def test_render_capped_renders_every_row_verbatim_under_the_cap():
    rows = [{"role": "build", "n": 1}, {"role": "review", "n": 2}]
    result = render_capped(rows, cap_tokens=300)
    assert "role=build" in result
    assert "role=review" in result
    assert "more rows" not in result


def test_render_capped_prints_a_dash_not_0_0_for_an_uncomputed_value():
    result = render_capped([{"cost_per_landed": None}], cap_tokens=300)
    assert "cost_per_landed=-" in result
    assert "0.0" not in result
