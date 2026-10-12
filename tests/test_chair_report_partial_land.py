from agent_tools.chair_report import lands_this_tick, needs_chair_items, partial_land_text


def _partial(task_ids, quarantined):
    return {"kind": "partial_land", "initiative": "i", "phase": "p", "task_ids": task_ids, "quarantined": quarantined}


def test_one_approved_and_one_quarantined():
    assert partial_land_text(_partial(["a"], 1)) == "partial-land: i/p 1 approved, 1 quarantined"


def test_three_approved_and_two_quarantined():
    assert partial_land_text(_partial(["a", "b", "c"], 2)) == "partial-land: i/p 3 approved, 2 quarantined"


def test_a_partial_land_action_renders_in_the_needs_slot():
    assert needs_chair_items([_partial(["a"], 1)]) == ["partial-land: i/p 1 approved, 1 quarantined"]


def test_a_genuine_no_run_keeps_its_old_text():
    actions = [{"kind": "needs_chair", "initiative": "i", "cause": "no_run"}]
    assert needs_chair_items(actions) == ["needs chair: i:no_run"]


def test_a_partial_land_comes_after_the_needs_chair_entries():
    actions = [_partial(["a", "b"], 1), {"kind": "needs_chair", "initiative": "j", "cause": "conflict"}]
    assert needs_chair_items(actions) == ["needs chair: j:conflict", "partial-land: i/p 2 approved, 1 quarantined"]


def test_a_landed_partial_land_counts_as_a_land_this_tick():
    results = [{"action": _partial(["a"], 1), "status": "landed", "reason": ""}]
    assert lands_this_tick(results) == 1
