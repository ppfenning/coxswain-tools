from datetime import UTC, datetime

from agent_tools.chair_facts import scaled_cap
from agent_tools.chair_plan import plan_tick
from agent_tools.chair_plan_fill import _scaled_lanes, host_free_slots

T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
_LAUNCHES = {"launch_epic", "launch_decompose", "relaunch", "retry"}


def _facts(lane_fraction: float | None, hosts: list[dict], launch_cap: int = 4, **extra) -> dict:
    limits = {"hard_stop": False, "weekly_fraction": 0.0, "hard_stop_fraction": 1.0, "launch_cap": launch_cap, "go_degraded": False}
    return {
        "limits": limits if lane_fraction is None else {**limits, "lane_fraction": lane_fraction},
        "dispatch": {"max_in_flight": 4, "live_runs": 0, "hosts": hosts},
        **extra,
    }


def _free(facts: dict) -> list[int]:
    return [slot[3] for slot in host_free_slots(facts)]  # type: ignore[arg-type]


def _host(name: str = "a", live_runs: int = 0, capacity: int = 4) -> dict:
    return {"name": name, "capacity": capacity, "live_runs": live_runs}


def test_a_quarter_fraction_leaves_one_free_slot_on_a_capacity_four_host():
    assert _free(_facts(0.25, [_host()])) == [1]


def test_a_full_fraction_leaves_all_four_slots():
    assert _free(_facts(1.0, [_host()])) == [4]


def test_a_live_run_uses_up_the_one_scaled_slot():
    assert _free(_facts(0.25, [_host(live_runs=1)])) == [0]


def test_a_zero_fraction_gives_every_host_no_free_slots():
    assert _free(_facts(0.0, [_host("a"), _host("b", capacity=8)])) == [0, 0]


def test_facts_without_a_lane_fraction_give_capacity_minus_live_runs():
    assert _free(_facts(None, [_host(live_runs=1), _host("b", capacity=8)])) == [3, 8]


def test_facts_without_limits_give_capacity_minus_live_runs():
    assert _free({"dispatch": {"max_in_flight": 4, "live_runs": 0, "hosts": [_host(live_runs=1)]}}) == [3]


def test_the_host_lane_rule_matches_the_launch_cap_rule():
    pairs = [(4, 0.25), (4, 1.0), (4, 0.0), (8, 0.3), (1, 0.01), (4, -0.5)]
    assert [_scaled_lanes(c, f) for c, f in pairs] == [scaled_cap(c, f) for c, f in pairs]


def test_a_login_blocked_host_stays_at_zero():
    facts = _facts(1.0, [_host("a"), _host("b")], login_hosts=[{"name": "a", "state": "active", "versions_json": {"login_ok": False}}])
    assert _free(facts) == [0, 4]


def _tick_facts(lane_fraction: float, launch_cap: int) -> dict:
    task = {"id": "a", "initiative": "i", "repo": "r", "phase": "p", "phase_done": True, "needs": [], "run": "i-1", "needs_fetch": True}
    ready = {"id": "j", "started": True, "ready_tasks": [{"id": "t", "needs": []}], "landed": set(), "repo": "r2"}
    return {
        **_facts(lane_fraction, [_host()], launch_cap),
        "approved": [task],
        "initiatives": [{"id": "i", "started": True, "ready_tasks": [], "landed": set(), "repo": "r"}, ready],
        "lease": {"holder": "a", "host": "h", "epoch": 7, "mine": True, "released": False, "stale": False},
        "quarantines": [],
        "intake": [],
        "work_store_ready": True,
        "sources_configured": True,
        "remote_unfetched": {},
        "run_exited": {"i": True},
        "schema_deaths": {},
        "lost_runs": {},
        "last_housekeeping_at": T0.isoformat(),
        "housekeeping_hours": 24.0,
        "stale_days": 7,
        "stale_candidates": [],
        "stall_candidates": [],
        "home": {},
    }


def test_a_zero_fraction_and_cap_still_plans_the_land_and_no_launch():
    kinds = [a["kind"] for a in plan_tick(_tick_facts(0.0, 0), T0)]
    assert "land_phase" in kinds
    assert not _LAUNCHES & set(kinds)
