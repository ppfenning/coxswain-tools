"""Clean up after a harness run: its worktrees and scratch branches, never its phase branches.

Pure planning, then one edge that runs git. Dry-run is the default; nothing is
removed unless asked. A phase branch (`epic/<initiative>/<phase>`) is what the
next run stacks on and is never touched; the task branches
(`agents/<run-id>/<task>`) and the scratch branches (`<phase>--<task>`) belong
to a finished run and go.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

__all__ = ["apply_cleanup", "git_branches", "git_worktrees", "plan_cleanup"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout


def git_worktrees(repo: Path) -> list[str]:
    return [line.split(" ", 1)[1] for line in _git(repo, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]


def git_branches(repo: Path) -> list[str]:
    return [line.lstrip("*+ ").strip() for line in _git(repo, "branch", "--list").splitlines() if line.strip()]


def _task_of(branch: str) -> str | None:
    """The task name behind a doomed branch: third segment of `agents/<run>/<task>`,
    or the text after the last `--` of `epic/<init>/<phase>--<task>`."""
    if branch.startswith("agents/") and branch.count("/") >= 2:
        return branch.split("/", 2)[2]
    if branch.startswith("epic/") and "--" in branch:
        return branch.rsplit("--", 1)[1]
    return None


def _on_main(repo: Path, branch: str, default_branch: str) -> bool:
    return _git(repo, "cherry", default_branch, branch).strip() == ""


def plan_cleanup(*, run_id: str, worktrees: Sequence[str], branches: Sequence[str], worktree_root: str) -> dict[str, Any]:
    """Pure: what a run left behind. Phase branches are listed as kept, explicitly."""
    root = str(Path(worktree_root).expanduser()) + "/" + run_id
    doomed_worktrees = [w for w in worktrees if w.startswith(root + "/") or w == root]
    doomed_branches = [b for b in branches if b.startswith(f"agents/{run_id}/") or ("--" in b and b.startswith("epic/"))]
    kept = [b for b in branches if b.startswith("epic/") and "--" not in b]
    return {"run_id": run_id, "worktrees": doomed_worktrees, "branches": doomed_branches, "kept_phase_branches": kept, "root": root}


def apply_cleanup(repo: Path | str, plan: dict[str, Any], *, dry_run: bool = True,
                   landed: Sequence[str] = (), force: bool = False, default_branch: str = "main") -> list[str]:
    """The edge. Returns what was (or would be) done, one line each. A branch
    whose task is neither `landed` nor already on `default_branch` is kept,
    printed with the command to land it, unless `force`."""
    repo = Path(repo)
    lines = []
    for w in plan["worktrees"]:
        lines.append(f"{'would remove' if dry_run else 'removed'} worktree {w}")
        if not dry_run:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", w], capture_output=True)
    if not dry_run:
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)
    for b in plan["branches"]:
        task = _task_of(b)
        on_main = task in landed or _on_main(repo, b, default_branch)
        if not on_main and not force:
            lines.append(f"kept {b}: approved, not on main — cox runs land {plan['run_id']} --repo {repo} --task {task} --apply")
            continue
        suffix = " (forced)" if force and not on_main else ""
        lines.append(f"{'would delete' if dry_run else 'deleted'} branch {b}{suffix}")
        if not dry_run:
            subprocess.run(["git", "-C", str(repo), "branch", "-D", b], capture_output=True)
    root = Path(plan["root"])
    if root.exists():
        lines.append(f"{'would remove' if dry_run else 'removed'} directory {root}")
        if not dry_run:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
    for b in plan["kept_phase_branches"]:
        lines.append(f"kept phase branch {b}")
    return lines
