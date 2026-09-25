import sqlite3

from agent_tools import stats_derive, stats_schema
from agent_tools.stats_query import (
    bounds_report,
    coverage_report,
    explain_report,
    gates_inputs,
    render_capped,
    render_gates,
    roles_report,
    series_report,
    spend_mix_report,
)


def _call(task_id, role="build", model="sonnet", join_confidence="heuristic", failure_class=None):
    return {
        "role": role,
        "model": model,
        "task_id": task_id,
        "join_confidence": join_confidence,
        "failure_class": failure_class,
    }


def _task(task_id, outcome="landed", attempt=None, cost_usd=0.0, outcome_kind=None):
    return {"task_id": task_id, "outcome": outcome, "attempt": attempt, "cost_usd": cost_usd, "outcome_kind": outcome_kind}


def _call_with_run(run_id, task_id, role="build", model="sonnet"):
    return {**_call(task_id, role=role, model=model), "run_id": run_id}


def _bounds_call(role, model, cost_usd, failure_class=None):
    return {"role": role, "model": model, "cost_usd": cost_usd, "failure_class": failure_class}


def test_bounds_report_groups_by_role_and_model():
    calls = [_bounds_call("build", "sonnet", 1.0), _bounds_call("build", "haiku", 1.0), _bounds_call("plan", "sonnet", 1.0)]
    rows = bounds_report(calls, lambda role, model: None)
    assert {(r["role"], r["model"]) for r in rows} == {("build", "sonnet"), ("build", "haiku"), ("plan", "sonnet")}


def test_bounds_report_censors_a_group_whose_max_sits_within_five_percent_of_the_ceiling():
    calls = [_bounds_call("build", "sonnet", 0.96)] * 20
    [row] = bounds_report(calls, lambda role, model: 1.0)
    assert row["ceiling"] == 1.0
    assert row["censored"] is True


def test_bounds_report_censors_a_group_with_a_budget_stopped_call_even_far_under_the_ceiling():
    calls = [_bounds_call("build", "sonnet", 0.1)] * 19 + [_bounds_call("build", "sonnet", 0.1, failure_class="budget_stop")]
    [row] = bounds_report(calls, lambda role, model: 100.0)
    assert row["censored"] is True


def test_bounds_report_is_not_censored_when_max_is_low_and_no_call_budget_stopped():
    calls = [_bounds_call("build", "sonnet", 0.1)] * 20
    [row] = bounds_report(calls, lambda role, model: 100.0)
    assert row["censored"] is False


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


def test_roles_report_splits_a_challenger_call_from_a_standard_call_in_the_same_role_and_model():
    calls = [
        {**_call("t1"), "challenger": 1},
        _call("t2"),
    ]
    tasks = [_task("t1", outcome="quarantined"), _task("t2", outcome="landed")]
    rows = roles_report(calls, tasks)
    by_challenger = {row["challenger"]: row for row in rows}
    assert set(by_challenger) == {True, False}
    assert by_challenger[True]["n_calls"] == 1
    assert by_challenger[True]["landed_rate"] == 0.0
    assert by_challenger[False]["n_calls"] == 1
    assert by_challenger[False]["landed_rate"] == 1.0


def test_roles_report_counts_unverified_and_infra_apart_from_refused():
    calls = [_call("t1"), _call("t2"), _call("t3")]
    tasks = [
        _task("t1", outcome="quarantined", outcome_kind="refused"),
        _task("t2", outcome="quarantined", outcome_kind="unverified"),
        _task("t3", outcome="quarantined", outcome_kind="infra"),
    ]
    [row] = roles_report(calls, tasks)
    assert row["refused_tasks"] == 1
    assert row["unverified_tasks"] == 1
    assert row["infra_tasks"] == 1


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


def _insert_call(conn, run_id, seq, role, model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens):
    conn.execute(
        "INSERT INTO calls (run_id, seq, role, model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, seq, role, model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens),
    )


def test_spend_mix_report_groups_calls_by_model_against_a_literal_stats_db(tmp_path):
    conn = stats_schema.connect(tmp_path / "stats.db")
    _insert_call(conn, "r1", 1, "build", "sonnet", 100, 50, 0, 0)
    _insert_call(conn, "r1", 2, "plan", "sonnet", 200, 10, 5, 5)
    _insert_call(conn, "r1", 3, "build", "haiku", 40, 20, 0, 0)
    conn.commit()
    conn.row_factory = sqlite3.Row
    calls = [dict(row) for row in conn.execute("SELECT * FROM calls").fetchall()]
    conn.close()

    report = spend_mix_report(calls)

    assert {row["model"] for row in report} == {"sonnet", "haiku"}
    sonnet_rows = [c for c in calls if c["model"] == "sonnet"]
    [sonnet_report] = [row for row in report if row["model"] == "sonnet"]
    assert sonnet_report == stats_derive.spend_mix_for_model(sonnet_rows, "sonnet")


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


def test_coverage_report_reports_full_coverage_when_every_field_is_present():
    runs = [{"run_id": "r1", "provider_profile": "default", "host": "box1", "cartridge_sha": "abc"}]
    calls = [{"run_id": "r1", "task_id": "r1:p1:t1", "failure_class": "ok"}]
    tasks = [{"run_id": "r1", "task_id": "r1:p1:t1", "outcome": "landed", "outcome_kind": "refused"}]
    report = coverage_report(calls, tasks, runs)
    assert len(report) == 7
    assert all(row["known"] == row["total"] and row["fraction"] == 1.0 for row in report)


def test_coverage_report_fractions_reflect_a_gap_in_outcome_kind_and_call_source():
    runs = [{"run_id": "r1", "provider_profile": "default", "host": "box1", "cartridge_sha": "abc"}]
    calls = [
        {"run_id": "r1", "task_id": "r1:p1:t1", "failure_class": "ok"},
        {"run_id": "r1", "task_id": None, "failure_class": "ok"},
    ]
    tasks = [
        {"run_id": "r1", "task_id": "r1:p1:t1", "outcome": "landed", "outcome_kind": "refused"},
        {"run_id": "r1", "task_id": "r1:p1:t2", "outcome": "landed", "outcome_kind": None},
    ]
    report = {row["question"]: row for row in coverage_report(calls, tasks, runs)}
    assert report["calls_with_a_task"] == {"question": "calls_with_a_task", "known": 1, "total": 2, "fraction": 0.5}
    assert report["tasks_with_an_outcome_kind"] == {
        "question": "tasks_with_an_outcome_kind", "known": 1, "total": 2, "fraction": 0.5,
    }
    assert report["runs_with_provider_profile"]["fraction"] == 1.0
    assert report["tasks_with_a_known_outcome"]["fraction"] == 1.0


def _gates_conn():
    conn = stats_schema.connect(":memory:")
    for run_id, started in (("r_old", "2026-09-01T10:00:00Z"), ("r_new", "2026-09-20T10:00:00Z"), ("r_undated", None)):
        conn.execute("INSERT INTO runs (run_id, started_at) VALUES (?, ?)", (run_id, started))
        conn.execute("INSERT INTO tasks (run_id, task_id) VALUES (?, 't')", (run_id,))
        conn.execute("INSERT INTO calls (run_id, seq, role, task_id) VALUES (?, 1, 'handoff', 't')", (run_id,))
    return conn


def test_gates_inputs_without_since_returns_every_task_and_call_as_dicts():
    tasks, calls = gates_inputs(_gates_conn(), None)
    assert sorted(t["run_id"] for t in tasks) == ["r_new", "r_old", "r_undated"]
    assert sorted(c["run_id"] for c in calls) == ["r_new", "r_old", "r_undated"]
    assert calls[0]["role"] == "handoff" and "charter_verdict" in tasks[0]


def test_gates_inputs_since_keeps_rows_whose_run_started_on_or_after_the_date():
    tasks, calls = gates_inputs(_gates_conn(), "2026-09-20")
    assert [t["run_id"] for t in tasks] == ["r_new"]
    assert [c["run_id"] for c in calls] == ["r_new"]
    assert sorted(t["run_id"] for t in gates_inputs(_gates_conn(), "2026-09-01")[0]) == ["r_new", "r_old"]


def test_render_gates_pads_columns_and_prints_none_as_a_dash():
    row = {
        "role": "handoff", "calls": 2, "cost": 0.5, "cost_per_task": 0.25, "verdict_mix": {"yes": 1, "no": 1},
        "changed": None, "caught": None, "agreed": None, "cost_per_changed": None,
    }
    head, line, blank, verdict = render_gates([row], ["handoff: x."]).split("\n")
    assert line == "handoff | 2     | 0.5000 | 0.2500    | no:1 yes:1  | -       | -      | -      | -"
    assert head.startswith("role    | calls | cost   | cost/task | verdict mix | changed")
    assert (blank, verdict) == ("", "handoff: x.")
