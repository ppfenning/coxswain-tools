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
    "phase_landable",
    "phase_pr_body",
    "pr_body",
    "wait_decision",
]


def _proposal(record: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((p for p in record.get("proposals", []) if p.get("kind") == kind), None)


def _verdict(record: dict[str, Any], section: str) -> str | None:
    return (record.get(section) or {}).get("verdict")


def _approved(record: dict[str, Any]) -> str | None:
    """None when the record's decision is approve, else the reason it is not.
    An arbiter only runs on disagreement, so unanimous approval leaves no
    arbitration verdict at all — the best outcome, not a missing one."""
    arbitration = _verdict(record, "arbitration")
    if arbitration == "approve":
        return None
    if arbitration is not None:
        return f"arbitration verdict is {arbitration!r}, not 'approve'"
    reviews = [v for v in (_verdict(record, "review"), _verdict(record, "adversary")) if v is not None]
    if reviews and all(v == "approve" for v in reviews):
        return None
    return f"no arbitration, and the reviewers were {reviews or 'silent'}"


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


def phase_landable(items: list[dict[str, Any]], records: dict[str, dict[str, Any]]) -> str | None:
    """None when every item in the phase is landable, else the first reason it
    is not: every item must be `done` or `dropped`, and every `done` item's
    task record must be approved (`_approved` returns None)."""
    for item in items:
        status = item.get("status")
        if status not in ("done", "dropped"):
            return f"{item.get('id')} is {status!r}, not done or dropped"
        if status == "dropped":
            continue
        record = records.get(item.get("id"))
        if record is None:
            return f"{item.get('id')} has no task record"
        refusal = _approved(record)
        if refusal is not None:
            return f"{item.get('id')}: {refusal}"
    return None


def phase_pr_body(phase_record: dict[str, Any], task_records: list[dict[str, Any]]) -> str:
    """The phase PR body (§2): the phase's own verdict reasoning, then one
    block per landed ticket, dropped tickets listed last with their reason."""
    verdict = (phase_record.get("phase_verdict") or {}).get("reasoning", "")
    lines = [f"Phase: {phase_record.get('phase')}", verdict]
    dropped = [r for r in task_records if r.get("status") == "dropped"]
    for r in (r for r in task_records if r.get("status") != "dropped"):
        facts = r.get("change_facts") or {}
        lines += [
            "",
            f"- {r.get('task')}: {r.get('title', r.get('task'))}",
            f"  Review: {_verdict(r, 'review')}",
            f"  Adversary: {_verdict(r, 'adversary')}",
            f"  Arbitration: {_verdict(r, 'arbitration') or 'unanimous'}",
            f"  Fix-loop attempts: {facts.get('fix_loop_attempts')}",
            f"  Files touched: {', '.join(facts.get('files_touched', []))}",
        ]
    if dropped:
        lines += ["", "Dropped:"]
        lines += [f"- {r.get('task')}: {r.get('reason', '')}" for r in dropped]
    return "\n".join(lines)


def _phase_plan(phase_record: dict[str, Any], items: list[dict[str, Any]], task_records: list[dict[str, Any]],
                repo_facts: dict[str, Any] | None) -> list[dict[str, Any]]:
    """§1's phase step list, or a one-step `refuse` from `phase_landable`. The
    `checks` step names the phase `branch` instead of running in place; the
    edge builds a throwaway worktree from it rather than switching the
    working repo's own branch out from under whatever else uses it."""
    refusal = phase_landable(items, {r.get("task"): r for r in task_records})
    if refusal is not None:
        return [{"kind": "refuse", "reason": refusal}]
    run, phase, initiative = phase_record.get("run"), phase_record.get("phase"), phase_record.get("initiative")
    phase_branch = f"epic/{initiative}/{phase}"
    landed_tasks = [r.get("task") for r in task_records if r.get("status") != "dropped"]
    return [
        {"kind": "pick_branch", "branch": phase_branch, "commit_subject": f"phase {phase}"},
        {"kind": "checks", "argv": checks_argv(repo_facts or {}), "branch": phase_branch},
        {"kind": "push", "branch": phase_branch},
        {"kind": "pr_create", "title": f"epic {initiative}: {phase}", "body": phase_pr_body(phase_record, task_records)},
        {"kind": "wait_checks"},
        {"kind": "merge", "squash": True, "delete_branch": True},
        {"kind": "clean_phase", "run": run, "phase_branch": phase_branch, "tasks": landed_tasks},
        *[{"kind": "mark_done", "task": t} for t in landed_tasks],
    ]


def land_plan(record: dict[str, Any], branches: dict[str, list[str]], default_branch: str,
              repo_facts: dict[str, Any] | None = None, *, items: list[dict[str, Any]] | None = None,
              task_records: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The ordered steps to land `record`, or a one-step `refuse`. Phase mode
    (`items` given) lands the whole phase off its own branch instead of one
    task's commit."""
    if items is not None:
        return _phase_plan(record, items, task_records or [], repo_facts)
    if _proposal(record, "draft_pr_create") is None:
        return [{"kind": "refuse", "reason": "no draft_pr_create proposal in record"}]
    refusal = _approved(record)
    if refusal is not None:
        return [{"kind": "refuse", "reason": refusal}]

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
        {"kind": "clean", "run": run, "task": task, "branch": scratch_branch},
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
