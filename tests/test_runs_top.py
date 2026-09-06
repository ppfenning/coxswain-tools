from agent_tools.events import Event
from agent_tools.runs_top import Row, column_widths, highlight, render, row


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


def test_turns_sum_across_calls():
    calls = [{"node": "build", "attempt": 1, "turns": 3}, {"node": "build", "attempt": 2, "turns": 2}]
    r = row("r1", True, [], [], calls)
    assert r.turns == 5


def test_row_carries_the_tokens_argument_through_unsummed():
    r = row("r1", True, [], [], [], tokens=1204338)
    assert r.tokens == 1204338


def test_row_with_no_tokens_argument_has_a_none_field():
    r = row("r1", True, [], [], [])
    assert r.tokens is None


def test_render_cuts_every_line_and_sorts_alive_first():
    rows = [
        Row("zzz", False, "build", "review", 1, 5, 1234, "revise", "exited"),
        Row("aaa", True, "build", "build", 2, 3, 500, "", "running"),
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
    rows = [Row("run-1", True, "build", "build", 1, 1, 1, "", "running")]
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
    rows = [Row("run-1", True, "build", "build", 1, 1, 1, "", "running", "standard/high")]
    lines = render(rows, 80)
    assert "CEIL" in lines[0]
    assert "standard/high" in lines[1]


def test_column_widths_on_rows_of_unequal_width():
    rows = [
        Row("r1", True, "build", "review_charter", 1, 3, 500, "", "running"),
        Row("r2", True, "build", "b", 1, 3, 500, "", "running"),
    ]
    widths = column_widths(rows, ("RUN", "PHASE", "NODE"))
    assert widths == (len("RUN"), len("build"), len("review_charter"))


_HEADERS = ("RUN", "PHASE", "NODE", "ATT", "TURNS", "TOKENS", "VERDICT", "STATUS", "CEIL", "BY")


def test_render_aligns_a_long_and_a_short_node_so_att_starts_at_the_same_index():
    long_row = Row("r1", True, "build", "review_charter", 1, 3, 500, "", "running")
    short_row = Row("r2", True, "build", "b", 2, 3, 500, "", "running")
    widths = column_widths([long_row, short_row], _HEADERS)
    start = sum(widths[:3]) + 3
    lines = render([long_row, short_row], 80)
    assert lines[1][start:start + widths[3]] == "1".rjust(widths[3])
    assert lines[2][start:start + widths[3]] == "2".rjust(widths[3])


def test_render_right_aligns_numeric_columns():
    r = Row("r1", True, "build", "b", 1, 22, 3, "", "running")
    widths = column_widths([r], _HEADERS)
    start = sum(widths[:3]) + 3
    lines = render([r], 80)
    assert lines[1][start:start + widths[3]] == "1".rjust(widths[3])
    turns_start = start + widths[3] + 1
    assert lines[1][turns_start:turns_start + widths[4]] == "22".rjust(widths[4])


def test_render_formats_the_tokens_cell_with_a_thousands_separator():
    rows = [Row("r1", True, "build", "b", 1, 1, 1204338, "", "running")]
    lines = render(rows, 80)
    assert "1,204,338" in lines[1]


def test_render_of_a_row_with_no_tokens_leaves_the_cell_blank_not_zero():
    r = Row("r1", True, "build", "b", 1, 1, None, "", "running")
    widths = column_widths([r], _HEADERS)
    start = sum(widths[:5]) + 5
    lines = render([r], 80)
    assert lines[1][start:start + widths[5]] == " " * widths[5]


def test_render_of_no_rows_still_renders_the_header():
    lines = render([], 40)
    assert lines[0].split()[0] == "RUN"
    assert "TOKENS" in lines[0]
