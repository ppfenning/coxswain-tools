from datetime import UTC, datetime

import pytest

from agent_tools.chair_plan import plan_tick, waiting_initiatives
from agent_tools.chair_runnable import Blocking, WaitingInitiative
from agent_tools.chair_types import Facts

_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
_LAUNCHES = {"launch_epic", "relaunch", "clear_branches"}


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
        "run_exited": {"a": True, "b": True},  # a started initiative relaunches only once its newest run has exited
        "schema_deaths": {},
        "lost_runs": {},
        "last_housekeeping_at": None,
        "housekeeping_hours": 24.0,
        "stale_days": 7,
        "stale_candidates": [],
        "stall_candidates": [],
        "home": {},
    }
    return {**base, **overrides}  # type: ignore[return-value]


def _initiative(id: str, phase: str, started: bool, task: str = "t") -> dict:
    return {
        "id": id,
        "repo": f"repo-{id}",
        "started": started,
        "ready_tasks": [{"id": f"{id}-{task}", "phase": phase, "needs": []}],
        "landed": set(),
    }


def _row(id: str, phase: str, state: str) -> dict:
    return {"initiative": id, "phase": phase, "tasks": [(f"{id}-q", state)]}


def _kinds(actions: list[dict], kinds: set[str]) -> list[dict]:
    return [a for a in actions if a["kind"] in kinds]


def _waiting(actions: list[dict]) -> list[dict]:
    return _kinds(actions, {"waiting"})


def test_a_started_initiative_without_a_blocking_row_relaunches_as_a_pair() -> None:
    actions = plan_tick(_facts(initiatives=[_initiative("a", "p2", True)]), _NOW)
    assert [a["kind"] for a in _kinds(actions, _LAUNCHES)] == ["clear_branches", "relaunch"]


@pytest.mark.parametrize("started", [True, False])
def test_a_ready_task_behind_a_quarantined_phase_is_withheld_and_recorded(started: bool) -> None:
    facts = _facts(initiatives=[_initiative("a", "p2", started)], phase_tasks=[_row("a", "p1", "quarantined")])
    actions = plan_tick(facts, _NOW)
    assert _kinds(actions, _LAUNCHES) == []
    assert _waiting(actions) == [
        {"kind": "waiting", "initiative": "a", "phase": "p1", "quarantined": 1, "awaiting_land": 0, "epoch": 7}
    ]


@pytest.mark.parametrize("started", [True, False])
def test_a_ready_task_behind_an_approved_phase_is_withheld(started: bool) -> None:
    facts = _facts(initiatives=[_initiative("a", "p2", started)], phase_tasks=[_row("a", "p1", "approved")])
    actions = plan_tick(facts, _NOW)
    assert _kinds(actions, _LAUNCHES) == []
    assert [(a["quarantined"], a["awaiting_land"]) for a in _waiting(actions)] == [(0, 1)]


def test_a_ready_task_before_the_blocking_phase_launches() -> None:
    facts = _facts(initiatives=[_initiative("a", "p1", False)], phase_tasks=[_row("a", "p2", "quarantined")])
    actions = plan_tick(facts, _NOW)
    assert [a["initiative"] for a in _kinds(actions, {"launch_epic"})] == ["a"]
    assert _waiting(actions) == []


def test_a_ready_task_in_the_blocking_phase_launches() -> None:
    facts = _facts(initiatives=[_initiative("a", "p1", False)], phase_tasks=[_row("a", "p1", "quarantined")])
    actions = plan_tick(facts, _NOW)
    assert [a["initiative"] for a in _kinds(actions, {"launch_epic"})] == ["a"]
    assert _waiting(actions) == []


@pytest.mark.parametrize("started", [True, False])
def test_a_re_readied_task_launches(started: bool) -> None:
    initiative = {**_initiative("a", "p2", started), "ready_tasks": [
        {"id": "a-q", "phase": "p1", "needs": []},
        {"id": "a-t", "phase": "p2", "needs": []},
    ]}
    facts = _facts(initiatives=[initiative], phase_tasks=[_row("a", "p1", "ready")])
    actions = plan_tick(facts, _NOW)
    assert [a["initiative"] for a in _kinds(actions, {"launch_epic", "relaunch"})] == ["a"]
    assert _waiting(actions) == []


def test_a_waiting_initiative_uses_no_launch_slot() -> None:
    facts = _facts(
        limits={"hard_stop": False, "weekly_fraction": 0.1, "hard_stop_fraction": 0.9, "launch_cap": 1, "go_degraded": False},
        initiatives=[_initiative("a", "p2", True), _initiative("b", "p1", False)],
        phase_tasks=[_row("a", "p1", "quarantined")],
    )
    actions = plan_tick(facts, _NOW)
    assert [a["initiative"] for a in _kinds(actions, {"launch_epic", "relaunch"})] == ["b"]
    assert [a["initiative"] for a in _waiting(actions)] == ["a"]


def test_the_hard_stop_records_the_waiting_action_and_launches_nothing() -> None:
    limits = {"hard_stop": True, "weekly_fraction": 0.95, "hard_stop_fraction": 0.9, "launch_cap": 5, "go_degraded": False}
    facts = _facts(limits=limits, initiatives=[_initiative("a", "p2", True)], phase_tasks=[_row("a", "p1", "quarantined")])
    actions = plan_tick(facts, _NOW)
    assert _kinds(actions, _LAUNCHES) == []
    assert [a["initiative"] for a in _waiting(actions)] == ["a"]


def test_an_unchanged_waiting_row_is_not_recorded_again_and_a_changed_one_is() -> None:
    base = {"initiatives": [_initiative("a", "p2", True)], "phase_tasks": [_row("a", "p1", "quarantined")]}
    same = plan_tick(_facts(**base, waiting_reported={"a": "p1|1|0"}), _NOW)
    assert _waiting(same) == []
    assert _kinds(same, _LAUNCHES) == []
    changed = plan_tick(_facts(**base, waiting_reported={"a": "p1|2|0"}), _NOW)
    assert len(_waiting(changed)) == 1
    assert _kinds(changed, _LAUNCHES) == []


def test_waiting_initiatives_names_the_blocked_initiative_only() -> None:
    blocked = _facts(initiatives=[_initiative("a", "p2", True)], phase_tasks=[_row("a", "p1", "quarantined")])
    assert waiting_initiatives(blocked) == [WaitingInitiative("a", Blocking("p1", 1, 0))]
    open_ = _facts(initiatives=[_initiative("a", "p1", True)], phase_tasks=[_row("a", "p2", "quarantined")])
    assert waiting_initiatives(open_) == []
    assert waiting_initiatives(_facts(initiatives=[_initiative("a", "p2", True)])) == []
