"""Plan one PR per repository from the card branch. Pure, no I/O."""

from agent_tools.card_branch import card_branch


def card_pr_body(card: str, repo_name: str, task_ids: list[str]) -> str:
    """First line names the card, then one `- <task id>` line per task, in order."""
    return "\n".join([f"card {card}: {repo_name}", *[f"- {t}" for t in task_ids]])


def card_pr_steps(card: str, repos: dict[str, dict]) -> list[dict]:
    """One pr_create step per repository; a repository with no task ids yields none."""
    return [
        {
            "kind": "pr_create",
            "repo": name,
            "title": f"card {card}: {name}",
            "body": card_pr_body(card, name, info["task_ids"]),
            "head": card_branch(card, name),
            "base": info["default_branch"],
        }
        for name, info in repos.items()
        if info["task_ids"]
    ]


def card_pr_refs(card: str, repos: list[str]) -> list[dict]:
    """The shape the caller records on the card so every PR is referenced from it."""
    return [{"repo": name, "head": card_branch(card, name)} for name in repos]
