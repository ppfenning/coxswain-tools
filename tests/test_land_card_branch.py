from agent_tools import land

REPO = "wire"
BRANCH = "card/x/wire"


def _phase(**extra):
    return {"run": "epic-x-5", "phase": "seams", "initiative": "x", **extra}


def _task(task, status="approved"):
    return {"task": task, "title": f"Title {task}", "repo": REPO, "status": status, "review": {"verdict": "approve"}}


def _plan(record, items, records):
    return land.land_plan(record, {}, "main", items=items, task_records=records, repo=REPO)


def _kinds(steps):
    return [s["kind"] for s in steps]


def test_three_approved_tasks_give_three_apply_steps_and_no_pr():
    items = [{"id": t, "status": "approved"} for t in "abc"]
    steps = _plan(_phase(card_branch=BRANCH), items, [_task(t) for t in "abc"])
    assert _kinds(steps) == ["apply_task"] * 3 + ["checks", "push"] + ["mark_done"] * 3
    assert {"squash_phase", "pr_create", "wait_checks", "merge"}.isdisjoint(_kinds(steps))
    assert [s["task"] for s in steps if s["kind"] == "apply_task"] == ["a", "b", "c"]
    assert steps[4] == {"kind": "push", "branch": BRANCH}
    assert {s["branch"] for s in steps if s["kind"] == "apply_task"} == {BRANCH}
    assert steps[3] == {"kind": "checks", "checks": land.checks_argv({}), "worktree_of": BRANCH}


def test_apply_steps_follow_the_records_branch_when_it_differs_from_the_derived_one():
    items = [{"id": "a", "status": "approved"}]
    steps = _plan(_phase(card_branch="card/other/wire"), items, [_task("a")])
    assert [(s["kind"], s["branch"]) for s in steps if s["kind"] in ("apply_task", "push")] == [
        ("apply_task", "card/other/wire"),
        ("push", "card/other/wire"),
    ]


def test_a_quarantined_task_is_absent_and_the_approved_ones_still_land():
    items = [{"id": "a", "status": "approved"}, {"id": "b", "status": "approved"}, {"id": "c", "status": "dropped"}]
    steps = _plan(_phase(card_branch=BRANCH), items, [_task("a"), _task("b"), _task("c", "quarantined")])
    assert [s["task"] for s in steps if s["kind"] == "apply_task"] == ["a", "b"]
    assert [s["task"] for s in steps if s["kind"] == "mark_done"] == ["a", "b"]


def test_a_record_without_card_branch_keeps_todays_plan():
    items = [{"id": "a", "status": "approved"}]
    records = [{**_task("a"), "arbitration": {"verdict": "approve"}}]
    plain = _plan(_phase(), items, records)
    assert plain == _plan(_phase(card_branch=""), items, records)
    assert {"squash_phase", "pr_create"} <= set(_kinds(plain))
    assert "apply_task" not in _kinds(plain)


def test_card_mode_still_refuses_first_when_a_task_is_not_landable():
    items = [{"id": "a", "status": "approved"}]
    steps = _plan(_phase(card_branch=BRANCH), items, [{**_task("a"), "review": {"verdict": "revise"}}])
    assert _kinds(steps) == ["refuse"]


def test_an_empty_repo_refuses_instead_of_planning_nothing():
    items = [{"id": "a", "status": "approved"}]
    steps = land.land_plan(_phase(card_branch=BRANCH), {}, "main", items=items, task_records=[_task("a")])
    assert _kinds(steps) == ["refuse"]


def test_approved_records_of_another_repo_refuse():
    items = [{"id": "a", "status": "approved"}]
    steps = _plan(_phase(card_branch=BRANCH), items, [{**_task("a"), "repo": "elsewhere"}])
    assert _kinds(steps) == ["refuse"]
    assert "a" in steps[0]["reason"]


def test_an_approved_item_whose_record_is_quarantined_is_noted_not_applied():
    items = [{"id": "a", "status": "approved"}, {"id": "b", "status": "approved"}]
    steps = _plan(_phase(card_branch=BRANCH), items, [_task("a"), _task("b", "quarantined")])
    assert [s["task"] for s in steps if s["kind"] == "apply_task"] == ["a"]
    assert [s["task"] for s in steps if s["kind"] == "mark_done"] == ["a"]
    assert steps[-1]["kind"] == "note" and steps[-1]["unmerged"] == ["b"]
