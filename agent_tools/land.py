"""Land an approved run: pick the branch it lives on, cherry-pick it onto a
fresh PR branch, open the PR, wait for it to go green, merge, and clean up.

`land_plan` and `pr_body` are pure: given the task record (the json at
`runs/<run>/tasks/<phase>/<task>.json`) and, for each candidate branch, the
commit subjects the edge found ahead of the default branch, they return the
ordered steps or a single `refuse` step naming why. `branches` already
excludes merge commits — the edge gathers it with `git log --no-merges`,
because a merge commit is a fact git holds (parent count), not something a
commit subject reliably spells out. The record is expected to carry `run`,
`task`, `phase`, and `initiative` (the edge fills the first three in from the
file's own path when the record itself is silent on them) plus `proposals`,
`review`, `arbitration`, `change_facts`, and `build`; a record silent on
`initiative` still lands cleanly off the scratch branch, and only fails to
resolve a phase branch, which becomes the ordinary "no branch is exactly one
commit ahead" refuse rather than a crash. `cli.py` is the edge: it gathers
the record and the branches with `git log`, then walks the plan through
`subprocess` and `gh`.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "checks_argv",
    "land_plan",
    "pr_body",
    "wait_decision",
]


def _proposal(record: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((p for p in record.get("proposals", []) if p.get("kind") == kind), None)


def _verdict(record: dict[str, Any], section: str) -> str | None:
    return (record.get(section) or {}).get("verdict")


def checks_argv(repo_facts: dict[str, Any]) -> list[str]:
    """The checks launch argv, cheapest and most specific first. The executor
    runs it with `cwd` already at the repo root, so a venv path is relative,
    not absolute. `uv run` next when the repo pins its dependencies with a
    lockfile; a bare `pytest -q` only when neither fact holds, and only PATH
    can say whether that one exists."""
    if repo_facts.get("venv_python"):
        return [".venv/bin/python", "-m", "pytest", "-q"]
    if repo_facts.get("uv_lock"):
        return ["uv", "run", "pytest", "-q"]
    return ["pytest", "-q"]


def land_plan(record: dict[str, Any], branches: dict[str, list[str]], default_branch: str,
              repo_facts: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The ordered steps to land `record`, or a one-step `refuse`."""
    if _proposal(record, "draft_pr_create") is None:
        return [{"kind": "refuse", "reason": "no draft_pr_create proposal in record"}]
    arbitration = _verdict(record, "arbitration")
    if arbitration != "approve":
        return [{"kind": "refuse", "reason": f"arbitration verdict is {arbitration!r}, not 'approve'"}]

    run, task, phase = record.get("run"), record.get("task"), record.get("phase")
    initiative = record.get("initiative")
    scratch_branch = f"agents/{run}/{task}"
    phase_branch = f"epic/{initiative}/{phase}" if initiative else None
    scratch_subjects = branches.get(scratch_branch, [])
    phase_subjects = branches.get(phase_branch, []) if phase_branch else []

    if len(scratch_subjects) == 1:
        chosen, subject = scratch_branch, scratch_subjects[0]
    elif len(phase_subjects) == 1:
        chosen, subject = phase_branch, phase_subjects[0]
    else:
        found = {b: len(subs) for b, subs in branches.items()}
        return [{"kind": "refuse", "reason": f"no branch is exactly one commit ahead of {default_branch}", "found": found}]

    pr_branch = f"pr/{task}"
    draft = _proposal(record, "draft_pr_create")
    return [
        {"kind": "pick_branch", "branch": chosen, "commit_subject": subject},
        {"kind": "cherry_pick", "branch": chosen, "commit_subject": subject, "onto": pr_branch, "from": default_branch},
        {"kind": "checks", "argv": checks_argv(repo_facts or {})},
        {"kind": "push", "branch": pr_branch},
        {"kind": "pr_create", "title": draft.get("title", subject), "body": pr_body(record)},
        {"kind": "wait_checks"},
        {"kind": "merge", "squash": True, "delete_branch": True},
        {"kind": "clean", "run": run},
        {"kind": "mark_done", "task": task},
    ]


_NO_CHECKS = "no checks reported"


def wait_decision(returncode: int, output: str, elapsed_s: float, timeout_s: float) -> str:
    """`green`, `failed`, `retry` or `timeout`: a branch whose checks have not registered yet is retried until `timeout_s`."""
    if returncode == 0:
        return "green"
    if _NO_CHECKS in output.lower():
        return "retry" if elapsed_s < timeout_s else "timeout"
    return "failed"


def pr_body(record: dict[str, Any]) -> str:
    """The PR description: verdicts, fix-loop attempts, checks, and cost if present."""
    lines = [f"Run: {record.get('run')}", f"Task: {record.get('task')}"]
    review, arbitration = _verdict(record, "review"), _verdict(record, "arbitration")
    if review:
        lines.append(f"Review verdict: {review}")
    if arbitration:
        lines.append(f"Arbitration verdict: {arbitration}")
    facts = record.get("change_facts") or {}
    if facts.get("fix_loop_attempts") is not None:
        lines.append(f"Fix-loop attempts: {facts['fix_loop_attempts']}")
    if facts.get("checks"):
        lines.append(f"Checks: {facts['checks']}")
    summary = (record.get("build") or {}).get("summary")
    if summary:
        lines += ["", summary]
    cost = record.get("cost_usd", (record.get("usage") or {}).get("cost_usd"))
    try:
        cost = None if cost is None else float(cost)
    except (TypeError, ValueError):
        cost = None
    if cost is not None:
        lines.append(f"Cost: ${cost:.2f}")
    return "\n".join(lines)
