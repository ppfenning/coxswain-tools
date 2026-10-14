from agent_tools.card_commits import card_commit_steps


def _rec(task: str, status: str = "approved", repo: str = "core") -> dict:
    return {"task": task, "title": f"Title {task}", "status": status, "repo": repo}


def test_three_approved_tasks_give_three_steps():
    steps = card_commit_steps("init-a", "core", [_rec("T-1"), _rec("T-2"), _rec("T-3")])
    assert [s["kind"] for s in steps] == ["apply_task"] * 3
    assert [s["task"] for s in steps] == ["T-1", "T-2", "T-3"]
    assert all(s["branch"] == "card/init-a/core" for s in steps)
    assert all(f"Task: {s['task']}" in s["message"] for s in steps)


def test_quarantined_and_dropped_tasks_leave_no_commit():
    records = [_rec("T-1"), _rec("T-2", "quarantined"), _rec("T-3"), _rec("T-4", "dropped")]
    steps = card_commit_steps("init-a", "core", records)
    assert [s["task"] for s in steps] == ["T-1", "T-3"]
    assert not any("T-2" in s["message"] or "T-4" in s["message"] for s in steps)


def test_record_for_another_repository_gives_no_step():
    assert card_commit_steps("init-a", "core", [_rec("T-1", repo="tools")]) == []
