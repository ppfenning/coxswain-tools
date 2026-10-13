from agent_tools.chair_plan_land import backoff_held, plan_lands


def _facts(run: str = "i-1", **extra) -> dict:
    task = {
        "id": "a", "initiative": "i", "repo": "r", "phase": "p", "phase_done": True, "needs": [], "run": run,
        "needs_fetch": True,
    }
    return {"approved": [task], "initiatives": [{"id": "i", "started": True, "ready_tasks": [], "landed": set()}], **extra}


def _conflict(run: str = "i-1") -> dict:
    return {"initiative": "i", "phase": "p", "run": run, "paths": ["a.py"]}


def _kinds(facts: dict) -> list[str]:
    return [a["kind"] for a in plan_lands(facts)]


def test_a_standing_conflict_plans_no_fetch_and_no_land():
    assert _kinds(_facts(land_conflicts=[_conflict()])) == []


def test_a_different_run_id_plans_the_fetch_and_the_land():
    assert _kinds(_facts(run="i-2", land_conflicts=[_conflict(run="i-1")])) == ["fetch", "land_phase"]


def test_an_absent_facts_key_plans_as_before():
    assert _kinds(_facts()) == ["fetch", "land_phase"]


def test_a_standing_conflict_holds_its_initiative_from_launches():
    assert backoff_held(_facts(land_conflicts=[_conflict()])) == frozenset({"i"})


def test_a_standing_conflict_plans_no_partial_land_for_its_run():
    task = {**_facts()["approved"][0], "phase_done": False}
    phase_tasks = [{"initiative": "i", "phase": "p", "tasks": [("a", "approved"), ("b", "quarantined")]}]
    facts = {**_facts(), "approved": [task], "phase_tasks": phase_tasks}
    assert (_kinds(facts), _kinds({**facts, "land_conflicts": [_conflict()]})) == (["fetch", "partial_land"], [])
