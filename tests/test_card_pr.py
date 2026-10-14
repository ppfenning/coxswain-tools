from agent_tools.card_pr import card_pr_body, card_pr_refs, card_pr_steps

REPOS = {
    "core": {"default_branch": "main", "task_ids": ["T-1", "T-2"]},
    "tools": {"default_branch": "trunk", "task_ids": ["T-3"]},
}


def test_two_repositories_give_two_steps_naming_the_card() -> None:
    steps = card_pr_steps("init-a", REPOS)
    assert [s["repo"] for s in steps] == ["core", "tools"]
    assert all(s["kind"] == "pr_create" for s in steps)
    assert all("init-a" in s["title"] and "init-a" in s["body"] for s in steps)
    assert steps[0]["title"] == "card init-a: core"
    assert steps[0]["head"] == "card/init-a/core"
    assert steps[1]["base"] == "trunk"


def test_body_lists_every_task_id_once_in_order() -> None:
    lines = card_pr_body("init-a", "core", ["T-1", "T-2"]).split("\n")
    assert lines[1:] == ["- T-1", "- T-2"]
    assert "init-a" in lines[0]


def test_repository_with_no_tasks_gives_no_step() -> None:
    repos = {**REPOS, "docs": {"default_branch": "main", "task_ids": []}}
    assert [s["repo"] for s in card_pr_steps("init-a", repos)] == ["core", "tools"]


def test_refs_return_both_repositories() -> None:
    assert card_pr_refs("init-a", ["core", "tools"]) == [
        {"repo": "core", "head": "card/init-a/core"},
        {"repo": "tools", "head": "card/init-a/tools"},
    ]
