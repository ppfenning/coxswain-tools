"""Card branch name, task commit message and card-mode rule. Pure, no I/O."""

SHIPPING_INITIATIVE = "builds-on-one-card-branch-and-opens-one-pr-per"


def card_branch(initiative: str, repo_name: str) -> str:
    """One branch per initiative per repository, so the repo name is part of it."""
    return f"card/{initiative}/{repo_name}"


def task_commit_message(task_id: str, title: str) -> str:
    """Title, a blank line, then a `Task: <id>` line the id can be split back out of."""
    return f"{title}\n\nTask: {task_id}"


def card_mode(initiative: str, launched_after_ship: bool) -> bool:
    """The shipping initiative and anything launched before it landed keep the old flow."""
    return launched_after_ship and initiative != SHIPPING_INITIATIVE
