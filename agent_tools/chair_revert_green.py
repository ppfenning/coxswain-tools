"""Reader: did main's CI go green at or after a commit.

Takes the same injected runner as chair_revert_ports. Nothing here spawns a process.
"""

from __future__ import annotations

import json

from agent_tools.chair_revert_ports import Runner

__all__ = ["main_green_since"]

_RUN_LIMIT = "50"


def _successful_shas(output: str) -> list[str]:
    """headSha of each completed, successful run in `gh run list` JSON output; [] when it is not a JSON list."""
    try:
        runs = json.loads(output)
    except ValueError:
        return []
    if not isinstance(runs, list):
        return []
    return [
        run["headSha"]
        for run in runs
        if isinstance(run, dict)
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and isinstance(run.get("headSha"), str)
    ]


def main_green_since(runner: Runner, repo: str, slug: str, commit: str) -> bool:
    """True when a successful main run sits on `commit` or on a descendant of it. Any failure reads as False."""
    try:
        code, output = runner(
            [
                "gh", "run", "list", "--repo", slug, "--branch", "main",
                "--json", "headSha,status,conclusion", "--limit", _RUN_LIMIT,
            ]
        )  # fmt: skip
        if code != 0:
            return False
        return any(
            sha == commit or runner(["git", "-C", repo, "merge-base", "--is-ancestor", commit, sha])[0] == 0
            for sha in _successful_shas(output)
        )
    except Exception:
        return False
