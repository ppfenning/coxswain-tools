from agent_tools.events import Event
from agent_tools.runs_top import Row, highlight, render, row


def test_the_last_node_started_and_verdict_win():
    events = [
        Event("r1", "node_started", 0, {"node": "build", "attempt": 1}),
        Event("r1", "node_started", 1, {"node": "review", "attempt": 1}),
        Event("r1", "verdict", 2, {"node": "review", "verdict": "revise"}),
    ]
    r = row("r1", True, ["build"], events, [])
    assert r.node == "review"
    assert r.attempt == 1
    assert r.verdict == "revise"


def test_a_quarantined_event_gives_status_quarantined_and_alert():
    events = [Event("r1", "task_quarantined", 0, {"task": "t1", "reason": "boom"})]
    r = row("r1", True, ["build"], events, [])
    assert r.status == "quarantined"
    assert highlight(r) == "alert"


def test_a_dead_run_with_no_events_is_exited_and_dim():
    r = row("r1", False, [], [], [])
    assert r.status == "exited"
    assert highlight(r) == "dim"


def test_cost_and_turns_sum_across_calls():
    calls = [
        {"node": "build", "attempt": 1, "cost_usd": 0.5, "turns": 3},
        {"node": "build", "attempt": 2, "cost_usd": 0.25, "turns": 2},
    ]
    r = row("r1", True, [], [], calls)
    assert r.cost_usd == 0.75
    assert r.turns == 5


def test_render_cuts_every_line_and_sorts_alive_first():
    rows = [
        Row("zzz", False, "build", "review", 1, 5, 1.2345, "revise", "exited"),
        Row("aaa", True, "build", "build", 2, 3, 0.5, "", "running"),
    ]
    lines = render(rows, 12)
    assert all(len(line) <= 12 for line in lines)
    assert "aaa" in lines[1]
    assert "zzz" in lines[2]


def test_render_of_no_rows_prints_the_no_runs_message():
    lines = render([], 40)
    assert lines[0].startswith("RUN")
    assert lines[1] == "no runs in flight"


def test_a_width_of_10_does_not_raise():
    rows = [Row("run-1", True, "build", "build", 1, 1, 1.0, "", "running")]
    lines = render(rows, 10)
    assert all(len(line) <= 10 for line in lines)


def test_row_with_a_ceiling_carries_the_applied_tier_and_effort():
    ceiling = {"requested": {"tier": "deep", "effort": None}, "applied": {"tier": "standard", "effort": "high"}, "profile": "p.yaml"}
    r = row("r1", True, [], [], [], ceiling)
    assert r.ceiling == "standard/high"


def test_row_with_no_ceiling_has_an_empty_ceiling_label():
    r = row("r1", True, [], [], [])
    assert r.ceiling == ""


def test_render_shows_ceil_column_for_a_row_that_carries_one():
    rows = [Row("run-1", True, "build", "build", 1, 1, 1.0, "", "running", "standard/high")]
    lines = render(rows, 60)
    assert "CEIL" in lines[0]
    assert "standard/high" in lines[1]
