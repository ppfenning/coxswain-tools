from agent_tools.home_model import (
    Facts,
    Quit,
    Refuse,
    Setup,
    State,
    Talk,
    backlog_pane,
    frame,
    leader_pane,
    runs_pane,
    step,
    window_pane,
)
from agent_tools.runs_top import Row, render

_ROW = Row(run="r1", alive=True, phase="build", node="build-in-worktree", attempt=1, turns=3, tokens=1500, verdict="", status="running", ceiling="")

_WINDOW = {"tier": "sonnet", "effort_ceiling": "high", "spent_usd": 12.5, "time_to_reset": "2h15m"}
_BACKLOG = {"queued": 4, "decomposed": 2, "landed": 9, "ready": {"tools-home": 3}}
_RUNS_PANE = tuple(render([_ROW], 80))


def _facts(**over) -> Facts:
    base = {"leader": None, "leader_liveness": "none", "runs_rows": (_ROW,), "backlog": _BACKLOG, "window": _WINDOW, "now": 0.0}
    return Facts(**{**base, **over})


def _live_leader_facts(**over) -> Facts:
    base = {"leader": {"session": "s1", "heartbeat_at": "1970-01-01T00:00:10+00:00"}, "leader_liveness": "live", "now": 70.0}
    return _facts(**{**base, **over})


def test_leader_pane_marks_attention_when_leader_is_stale_and_a_run_is_alive():
    lines = leader_pane(_facts(leader={"session": "s1"}, leader_liveness="stale"), 80)
    assert lines[0].startswith("!")


def test_leader_pane_is_plain_with_no_heartbeat_when_the_leader_carries_none():
    lines = leader_pane(_facts(leader={"session": "s1"}, leader_liveness="live"), 80)
    assert lines == ("LEADER", "holder: s1  status: live  heartbeat: n/a")


def test_leader_pane_shows_heartbeat_age_from_facts_now():
    lines = leader_pane(_live_leader_facts(), 80)
    assert lines == ("LEADER", "holder: s1  status: live  heartbeat: 60s ago")


def test_runs_pane_returns_runs_top_render_unchanged():
    assert runs_pane(_facts(), 80) == _RUNS_PANE


def test_backlog_pane_shows_counts_and_ready_per_initiative():
    assert backlog_pane(_facts(), 80) == (
        "BACKLOG",
        "queued 4  decomposed 2  landed 9",
        "ready: tools-home=3",
    )


def test_window_pane_shows_the_pacing_verdict():
    assert window_pane(_facts(), 80) == (
        "WINDOW",
        "tier sonnet effort high",
        "spent $12.50  reset in 2h15m",
    )


def test_window_pane_cuts_a_too_long_reason_with_an_ellipsis():
    lines = window_pane(_facts(window={**_WINDOW, "reason": "y" * 100}), 80)
    assert lines == (
        "WINDOW",
        "tier sonnet effort high",
        "spent $12.50  reset in 2h15m",
        "y" * 79 + "…",
    )


def test_step_t_returns_talk_and_only_talk():
    state = State(plugin_dir="/plugins/coxswain", leader_liveness="none", other_holder=None)
    _, effect = step(state, "t")
    assert effect == Talk("/plugins/coxswain")


def test_step_s_returns_setup_and_only_setup():
    state = State(plugin_dir="/plugins/coxswain", leader_liveness="none", other_holder=None)
    _, effect = step(state, "s")
    assert effect == Setup()


def test_step_q_returns_quit_and_only_quit():
    state = State(plugin_dir="/plugins/coxswain", leader_liveness="none", other_holder=None)
    _, effect = step(state, "q")
    assert effect == Quit()


def test_step_t_refuses_when_another_session_holds_a_live_leader():
    state = State(plugin_dir="/plugins/coxswain", leader_liveness="live", other_holder="s2")
    _, effect = step(state, "t")
    assert effect == Refuse("s2")


def test_frame_at_80_stacks_the_four_panes_with_the_runs_header_intact():
    facts = _live_leader_facts()
    state = State(plugin_dir="/p", leader_liveness="none", other_holder=None)
    assert frame(facts, state, 80) == (
        "LEADER",
        "holder: s1  status: live  heartbeat: 60s ago",
        "BACKLOG",
        "queued 4  decomposed 2  landed 9",
        "ready: tools-home=3",
        "WINDOW",
        "tier sonnet effort high",
        "spent $12.50  reset in 2h15m",
        *_RUNS_PANE,
    )


def test_frame_at_200_puts_the_three_top_panes_on_one_row():
    facts = _live_leader_facts()
    state = State(plugin_dir="/p", leader_liveness="none", other_holder=None)
    assert frame(facts, state, 200) == (
        "LEADER                                                             BACKLOG                                                            WINDOW                                                            ",
        "holder: s1  status: live  heartbeat: 60s ago                       queued 4  decomposed 2  landed 9                                   tier sonnet effort high                                           ",
        "                                                                   ready: tools-home=3                                                spent $12.50  reset in 2h15m                                      ",
        *_RUNS_PANE,
    )


def test_frame_at_160_fits_the_side_by_side_row_exactly():
    facts = _live_leader_facts()
    state = State(plugin_dir="/p", leader_liveness="none", other_holder=None)
    assert frame(facts, state, 160) == (
        "LEADER                                                BACKLOG                                               WINDOW                                              ",
        "holder: s1  status: live  heartbeat: 60s ago          queued 4  decomposed 2  landed 9                      tier sonnet effort high                             ",
        "                                                      ready: tools-home=3                                   spent $12.50  reset in 2h15m                        ",
        *_RUNS_PANE,
    )


def test_frame_at_161_fits_the_side_by_side_row_exactly():
    facts = _live_leader_facts()
    state = State(plugin_dir="/p", leader_liveness="none", other_holder=None)
    assert frame(facts, state, 161) == (
        "LEADER                                                BACKLOG                                               WINDOW                                               ",
        "holder: s1  status: live  heartbeat: 60s ago          queued 4  decomposed 2  landed 9                      tier sonnet effort high                              ",
        "                                                      ready: tools-home=3                                   spent $12.50  reset in 2h15m                         ",
        *_RUNS_PANE,
    )
