from agent_tools.chair_plan_land import plan_lands
from agent_tools.chair_plan_partial import classify_phase, partial_land_action
from agent_tools.chair_read_approved import phase_task_rows


def _facts(tasks: list[tuple[str, str]], approved: list[dict] | None = None) -> dict:
    return {
        "approved": approved or [],
        "initiatives": [{"id": "i", "started": True, "ready_tasks": [], "landed": set()}],
        "phase_tasks": [{"initiative": "i", "phase": "p", "tasks": tasks}],
    }


def _approved(id: str, done: bool) -> dict:
    return {
        "id": id, "initiative": "i", "repo": "r", "phase": "p", "phase_done": done, "needs": [], "run": "i-1",
        "needs_fetch": False,
    }


def test_approved_beside_quarantined_gives_a_partial_land_naming_the_approved_task():
    assert partial_land_action("i", "p", [("a", "approved"), ("b", "quarantined")]) == {
        "kind": "partial_land", "initiative": "i", "phase": "p", "task_ids": ["a"], "quarantined": 1,
    }


def test_approved_ids_keep_the_phase_order_and_done_and_dropped_siblings_do_not_block():
    tasks = [("c", "approved"), ("d", "done"), ("a", "approved"), ("e", "dropped"), ("q", "quarantined")]
    assert partial_land_action("i", "p", tasks) == {
        "kind": "partial_land", "initiative": "i", "phase": "p", "task_ids": ["c", "a"], "quarantined": 1,
    }


def test_approved_beside_a_running_task_gives_no_land_action():
    assert plan_lands(_facts([("a", "approved"), ("b", "running")], [_approved("a", False)])) == []


def test_a_quarantined_task_does_not_excuse_a_running_one():
    assert partial_land_action("i", "p", [("a", "approved"), ("b", "quarantined"), ("c", "running")]) is None


def test_a_pending_sibling_blocks_and_an_in_review_sibling_blocks():
    assert classify_phase([("a", "approved"), ("b", "quarantined"), ("c", "ready")]) == "wait"
    assert classify_phase([("a", "approved"), ("b", "quarantined"), ("c", "review")]) == "wait"


def test_an_all_approved_phase_still_gives_the_whole_phase_land():
    facts = _facts([("a", "approved"), ("b", "approved")], [_approved("a", True), _approved("b", True)])
    assert [a["kind"] for a in plan_lands(facts)] == ["land_phase"]


def test_a_quarantined_only_phase_gives_no_partial_land():
    assert partial_land_action("i", "p", [("a", "quarantined"), ("b", "quarantined")]) is None
    assert plan_lands(_facts([("a", "quarantined"), ("b", "done")])) == []


def test_plan_lands_emits_the_partial_land_for_a_quarantined_sibling():
    assert plan_lands(_facts([("a", "approved"), ("b", "quarantined")], [_approved("a", False)])) == [
        {
            "kind": "partial_land", "initiative": "i", "phase": "p", "task_ids": ["a"], "quarantined": 1,
            "repo": "r", "run": "i-1", "task_runs": {"a": "i-1"},
        }
    ]


def test_the_edge_lists_every_task_state_of_a_phase_with_approved_work():
    items = [
        {"id": "a", "initiative": "i", "phase": "p", "state": "approved"},
        {"id": "b", "initiative": "i", "phase": "p", "state": "quarantined"},
        {"id": "c", "initiative": "i", "phase": "q", "state": "ready"},
    ]
    assert phase_task_rows(items) == [{"initiative": "i", "phase": "p", "tasks": [("a", "approved"), ("b", "quarantined")]}]
