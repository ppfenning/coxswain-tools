from agent_tools import land

REPO = "wire"
BRANCH = "card/x/wire"


def _phase(**extra):
    return {"run": "epic-x-5", "phase": "seams", "initiative": "x", "card_branch": BRANCH, **extra}


def _task(task, status="approved", repo=REPO):
    return {"task": task, "title": f"Title {task}", "repo": repo, "status": status, "review": {"verdict": "approve"}}


def _plan(record, ids, records=None, repo=REPO):
    items = [{"id": t, "status": "approved"} for t in ids]
    return land.land_plan(record, {}, "main", items=items, task_records=records or [_task(t) for t in ids], repo=repo)


def _kinds(steps):
    return [s["kind"] for s in steps]


def test_last_phase_ends_with_one_pr_create_naming_the_card_and_tasks():
    steps = _plan(_phase(last_phase=True), "ab")
    prs = [s for s in steps if s["kind"] == "pr_create"]
    assert prs == [{
        "kind": "pr_create", "repo": REPO, "title": "card x: wire",
        "body": "card x: wire\n- a\n- b", "head": BRANCH, "base": "main",
    }]


def test_the_pr_create_is_the_final_step_with_no_wait_or_record_after_it():
    steps = _plan(_phase(last_phase=True), "ab")
    assert steps[-1]["kind"] == "pr_create"
    assert {"wait_checks", "record", "merge"}.isdisjoint(_kinds(steps))


def test_a_non_last_phase_appends_none():
    assert "pr_create" not in _kinds(_plan(_phase(), "ab"))
    assert "pr_create" not in _kinds(_plan(_phase(last_phase=False), "ab"))


def test_a_repo_with_every_task_quarantined_appends_none():
    items = [{"id": "a", "status": "approved"}]
    steps = land.land_plan(_phase(last_phase=True), {}, "main", items=items,
                           task_records=[_task("a", "quarantined")], repo=REPO)
    assert "pr_create" not in _kinds(steps)


def test_three_approved_tasks_give_three_commits_and_one_pr_create():
    steps = _plan(_phase(last_phase=True), "abc")
    assert _kinds(steps).count("apply_task") == 3
    assert _kinds(steps).count("pr_create") == 1
    assert steps[-1]["body"].splitlines()[1:] == ["- a", "- b", "- c"]


def test_each_repo_plan_lists_only_its_own_tasks():
    records = [_task("a"), _task("b", repo="other")]
    wire = _plan(_phase(last_phase=True), "ab", records)
    other = _plan(_phase(last_phase=True, card_branch="card/x/other"), "ab", records, repo="other")
    assert (wire[-1]["head"], wire[-1]["body"].splitlines()[1:]) == ("card/x/wire", ["- a"])
    assert (other[-1]["head"], other[-1]["body"].splitlines()[1:]) == ("card/x/other", ["- b"])
