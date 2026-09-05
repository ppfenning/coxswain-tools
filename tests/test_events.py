from agent_tools.events import Event, from_log, from_trace_names, from_usage


def test_run_started_is_the_first_line():
    events = from_log("run1", ["run1 started", "some other line"])
    assert events[0] == Event("run1", "run_started", 0, {})


def test_verdict_terse_wording():
    events = from_log("run1", ["run1 started", "review_charter verdict: approve"])
    assert Event("run1", "verdict", 1, {"node": "review_charter", "verdict": "approve"}) in events


def test_verdict_arrow_wording():
    events = from_log("run1", ["run1 started", "  review -> revise"])
    assert Event("run1", "verdict", 1, {"node": "review", "verdict": "revise"}) in events


def test_task_quarantined():
    events = from_log("run1", ["run1 started", "quarantined task: fix_thing — budget exceeded"])
    assert Event("run1", "task_quarantined", 1, {"task": "fix_thing", "reason": "budget exceeded"}) in events


def test_budget_stop_error_wording():
    events = from_log("run1", ["run1 started", "error_max_budget_usd hit at node build"])
    assert Event("run1", "budget_stop", 1, {}) in events


def test_budget_stop_fix_loop_wording():
    events = from_log("run1", ["run1 started", "fix loop stopped: budget"])
    assert Event("run1", "budget_stop", 1, {}) in events


def test_run_exited_summary_line():
    events = from_log(
        "run1",
        ["run1 started", "epic run1: 3 phase(s) complete, 5 task(s) landed, 2 task(s) quarantined"],
    )
    assert Event("run1", "run_exited", 1, {"phases_complete": 3, "quarantined": 2}) in events


def test_node_started_from_trace_name():
    events = from_trace_names("run1", ["build-1.jsonl"])
    assert events == [Event("run1", "node_started", 1000, {"node": "build", "attempt": 1})]


def test_malformed_trace_names_are_ignored():
    events = from_trace_names(
        "run1", ["build.jsonl", "build-abc.jsonl", "build-1-extra.jsonl", "build-2.jsonl"]
    )
    assert events == [Event("run1", "node_started", 2000, {"node": "build", "attempt": 2})]


def test_run_exited_cost_from_usage():
    usage = {"summary": {"cost_usd": 1.23, "turns": 7}}
    assert from_usage("run1", usage) == Event("run1", "run_exited_cost", 10 ** 9, {"cost_usd": 1.23, "turns": 7})


def test_seq_increases_with_line_number():
    events = from_log(
        "run1",
        [
            "run1 started",
            "review_charter verdict: approve",
            "  build -> approve",
            "quarantined task: t1 — bad",
        ],
    )
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
