from agent_tools.events import Event, from_log, from_trace_names, from_usage, merge, poll


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


def test_leader_taken_wording():
    events = from_log("run1", ["run1 started", "leader taken: cos1 (pid 4242) on host1"])
    assert Event("run1", "leader_taken", 1, {"session": "cos1", "pid": 4242, "host": "host1"}) in events


def test_leader_released_wording():
    events = from_log("run1", ["run1 started", "leader released: cos1"])
    assert Event("run1", "leader_released", 1, {"session": "cos1"}) in events


def test_leader_stale_wording():
    events = from_log("run1", ["run1 started", "leader stale: cos1 (pid 4242) on host1"])
    assert Event("run1", "leader_stale", 1, {"session": "cos1", "pid": 4242, "host": "host1"}) in events


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


def _state(log_lines_seen=0, trace_names_seen=frozenset(), emitted_exit=False, emitted_cost=False):
    return {
        "log_lines_seen": log_lines_seen,
        "trace_names_seen": trace_names_seen,
        "emitted_exit": emitted_exit,
        "emitted_cost": emitted_cost,
    }


def test_merge_keeps_each_streams_own_order_and_places_usage_last():
    log = [Event("run1", "verdict", 3, {}), Event("run1", "verdict", 1, {})]
    trace = [Event("run1", "node_started", 1000, {})]
    usage = [Event("run1", "run_exited_cost", 10**9, {})]
    assert merge(log, trace, usage) == [log[0], log[1], trace[0], usage[0]]


def test_a_retry_keeps_the_logs_own_order():
    log_events = from_log(
        "run1",
        [
            "run1 started",
            "  build -> revise",
            "some fix line",
        ],
    )
    events = merge(log_events, from_trace_names("run1", ["build-2.jsonl"]))
    kinds = [e.kind for e in events]
    assert kinds == ["run_started", "verdict", "node_started"]


def test_poll_twice_with_growing_inputs_emits_each_event_once():
    state0 = _state()
    events1, state1 = poll(
        "run1", state0, ["run1 started", "review_charter verdict: approve"], ["build-1.jsonl"], None
    )
    assert [e.kind for e in events1] == ["run_started", "verdict", "node_started"]

    events2, state2 = poll(
        "run1",
        state1,
        ["quarantined task: t1 — bad", "epic run1: 1 phase(s) complete, 1 task(s) landed, 1 task(s) quarantined"],
        ["build-1.jsonl", "build-2.jsonl"],
        None,
    )
    assert [e.kind for e in events2] == ["task_quarantined", "run_exited", "node_started"]
    assert state2["emitted_exit"] is True
    assert state2["log_lines_seen"] == 4
    assert state2["trace_names_seen"] == frozenset({"build-1.jsonl", "build-2.jsonl"})


def test_usage_arriving_after_the_exit_line_still_emits_the_cost_event():
    state0 = _state()
    _, state1 = poll(
        "run1",
        state0,
        ["run1 started", "epic run1: 1 phase(s) complete, 0 task(s) landed, 0 task(s) quarantined"],
        [],
        None,
    )
    assert state1["emitted_exit"] is True
    assert state1["emitted_cost"] is False

    usage = {"summary": {"cost_usd": 4.5, "turns": 3}}
    events2, state2 = poll("run1", state1, [], [], usage)
    assert [e.kind for e in events2] == ["run_exited_cost"]
    assert state2["emitted_cost"] is True


def test_an_empty_poll_returns_no_events_and_the_same_state():
    state0 = _state(log_lines_seen=2, trace_names_seen=frozenset({"build-1.jsonl"}), emitted_exit=True)
    events, state1 = poll("run1", state0, [], [], None)
    assert events == []
    assert state1 == state0
