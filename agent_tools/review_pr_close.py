"""Pure core: pick a landed task's review PRs and word the close comment."""

from collections.abc import Mapping, Sequence


def review_branch(initiative: str, task: str) -> str:
    return f"review/{initiative}/{task}"


def review_prs_for_task(
    initiative: str, task: str, prs: Sequence[Mapping]
) -> list[Mapping]:
    """PRs whose head is exactly the review branch; no prefix or suffix match."""
    branch = review_branch(initiative, task)
    return [pr for pr in prs if pr.get("head") == branch]


def close_comment(sha: str, task: str) -> str:
    return f"Landed {task} as {sha}."
