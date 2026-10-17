from agent_tools.chair_plan import plan_tick
from agent_tools.chair_types import Facts

REFUSAL = "decompose refused: the intake has no acceptance section"
REQUEUE = ["cox", "route", "requeue", "m"]


def _facts(**overrides) -> Facts:
    base = {
        "lease": {"holder": "a", "host": "h", "epoch": 7, "mine": True, "released": False, "stale": False},
        "limits": {"hard_stop": False, "weekly_fraction": 0.1, "hard_stop_fraction": 0.9, "launch_cap": 5, "go_degraded": False},
        "dispatch": {"max_in_flight": 5, "live_runs": 0, "hosts": []},
        "approved": [],
        "initiatives": [],
        "quarantines": [],
        "intake": [],
        "work_store_ready": True,
        "sources_configured": True,
        "remote_unfetched": {},
        "run_exited": {},
        "lost_runs": {},
        "last_housekeeping_at": None,
        "housekeeping_hours": 24.0,
        "stale_days": 7,
        "stale_candidates": [],
        "stall_candidates": [],
        "home": {},
    }
    return {**base, **overrides}  # type: ignore[return-value]


def _entry(run="m-1", **extra):
    return {"initiative": "m", "run": run, **extra}


def _empty(actions):
    return [a for a in actions if a.get("cause") == "empty_decompose"]


def test_an_empty_decompose_entry_becomes_one_needs_chair_with_the_refusal_and_a_requeue_remedy():
    facts = _facts(empty_decompose=[_entry(refusal=REFUSAL)])
    assert plan_tick(facts) == [
        {
            "kind": "needs_chair",
            "initiative": "m",
            "run": "m-1",
            "cause": "empty_decompose",
            "reason": REFUSAL,
            "remedy": "requeue",
            "command": REQUEUE,
            "epoch": 7,
        },
    ]


def test_an_entry_with_no_log_line_says_the_refusal_line_was_not_found():
    [action] = _empty(plan_tick(_facts(empty_decompose=[_entry()])))
    assert action["reason"] == "refusal line not found in the decompose log for m-1"


def test_a_reported_initiative_and_run_raises_nothing():
    facts = _facts(empty_decompose=[_entry(refusal=REFUSAL)], empty_decompose_reported=["m|m-1"])
    assert _empty(plan_tick(facts)) == []


def test_a_newer_run_for_the_same_initiative_raises_again():
    facts = _facts(empty_decompose=[_entry("m-2", refusal=REFUSAL)], empty_decompose_reported=["m|m-1"])
    assert [a["run"] for a in _empty(plan_tick(facts))] == ["m-2"]


def test_no_relaunch_or_requeue_action_is_emitted():
    kinds = {a["kind"] for a in plan_tick(_facts(empty_decompose=[_entry(refusal=REFUSAL)]))}
    assert kinds == {"needs_chair"}


def test_an_empty_decompose_needs_chair_item_passes_through_at_the_hard_stop():
    facts = _facts(
        limits={"hard_stop": True, "weekly_fraction": 0.95, "hard_stop_fraction": 0.9, "launch_cap": 5, "go_degraded": False},
        empty_decompose=[_entry(refusal=REFUSAL)],
    )
    [action] = plan_tick(facts)
    assert (action["kind"], action["cause"], action["command"]) == ("needs_chair", "empty_decompose", REQUEUE)
