"""The commit steps for one card branch: one per approved task. Pure, no I/O."""

from agent_tools.card_branch import card_branch, task_commit_message


def card_commit_steps(initiative: str, repo_name: str, task_records: list[dict]) -> list[dict]:
    """One apply_task step per approved record of this repo, in order; checks stay a land-plan step."""
    branch = card_branch(initiative, repo_name)
    return [
        {
            "kind": "apply_task",
            "branch": branch,
            "task": r["task"],
            "message": task_commit_message(r["task"], r.get("title", r["task"])),
        }
        for r in task_records
        if r.get("repo") == repo_name and r.get("status") == "approved"
    ]
