import dataclasses

from agent_tools.home_model import (
    Drill,
    Facts,
    Intake,
    Land,
    Quit,
    Refuse,
    Setup,
    State,
    Talk,
    attention_pane,
    backlog_pane,
    frame,
    leader_pane,
    runs_pane,
    step,
    window_pane,
)
from agent_tools.runs_top import Row, render

_ROW = Row(run="r1", alive=True, phase="build", node="build-in-worktree", attempt=1, turns=3, cost_usd=1.5, verdict="", status="running", ceiling="")

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


def test_attention_pane_shows_one_line_per_stop_reason_and_skips_running():
    rows = (
        dataclasses.replace(_ROW, run="r1", status="exited"),
        dataclasses.replace(_ROW, run="r2", status="quarantined"),
        dataclasses.replace(_ROW, run="r3", status="budget"),
        dataclasses.replace(_ROW, run="r4", status="running"),
    )
    assert attention_pane(_facts(runs_rows=rows), 80) == (
        "r1: gate [l]",
        "r2: quarantine [i]",
        "r3: budget stop [i]",
    )


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


def test_step_t_opens_the_conversation_even_while_another_session_holds_a_live_leader():
    state = State(plugin_dir="/plugins/coxswain", leader_liveness="live", other_holder="s2")
    _, effect = step(state, "t")
    assert effect == Talk("/plugins/coxswain")


def test_step_enter_drills_into_the_selected_run():
    state = State(plugin_dir="/p", leader_liveness="none", other_holder=None, selected_run="r1")
    _, effect = step(state, "ENTER")
    assert effect == Drill("r1")


def test_step_l_then_l_lands_dry_run_then_apply_then_resets_on_reselection():
    state = State(plugin_dir="/p", leader_liveness="none", other_holder=None, selected_run="r1", selected_status="exited")
    state, first = step(state, "l")
    assert first == Land("r1", False)
    assert state.land_armed == "r1"

    state, second = step(state, "l")
    assert second == Land("r1", True)
    assert state.land_armed is None

    reselected = State(
        plugin_dir="/p", leader_liveness="none", other_holder=None,
        selected_run="r2", selected_status="exited", land_armed="r1",
    )
    _, third = step(reselected, "l")
    assert third == Land("r2", False)


def test_step_l_is_a_no_op_when_the_selected_run_has_not_exited():
    state = State(plugin_dir="/p", leader_liveness="none", other_holder=None, selected_run="r1", selected_status="running")
    _, effect = step(state, "l")
    assert effect is None


def test_step_l_resets_the_arm_when_selection_changes_without_an_intervening_l_press():
    state = State(plugin_dir="/p", leader_liveness="none", other_holder=None, selected_run="r1", selected_status="exited")
    state, _ = step(state, "l")
    assert state.land_armed == "r1"

    visited = dataclasses.replace(state, selected_run="r2", selected_status="running")
    visited, _ = step(visited, "i")
    assert visited.land_armed is None

    returned = dataclasses.replace(visited, selected_run="r1", selected_status="exited")
    _, effect = step(returned, "l")
    assert effect == Land("r1", False)


def test_step_i_returns_intake():
    state = State(plugin_dir="/p", leader_liveness="none", other_holder=None)
    _, effect = step(state, "i")
    assert effect == Intake()


def test_only_the_landing_key_refuses_under_a_foreign_live_leader():
    state = State(plugin_dir="/p", leader_liveness="live", other_holder="s2", selected_run="r1", selected_status="exited")
    assert step(state, "l")[1] == Refuse("s2")
    assert step(state, "ENTER")[1] == Drill("r1")


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
