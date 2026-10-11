"""Revert a landed PR that turned main red: the imperative edge.

It decides nothing beyond the order of steps and reaches git and the forge only
through the two ports below. It takes plain values, not chair types.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from agent_tools.chair_land_backoff import aligned, backoff_for
from agent_tools.land import pr_footer

if TYPE_CHECKING:
    from agent_tools.chair_types import Action

__all__ = [
    "CHECKS_FAILED",
    "CONFLICT",
    "ERRORED",
    "MERGED",
    "ForgePort",
    "GitPort",
    "RevertFailure",
    "RevertResult",
    "attempt_revert",
    "gate_reverts",
    "perform_revert",
    "record_failure",
    "record_success",
    "revert_body",
    "revert_due",
]

MERGED = "merged"
CONFLICT = "conflict"
CHECKS_FAILED = "checks_failed"
ERRORED = "errored"


class GitPort(Protocol):
    def fetch_main(self, repo: str) -> None: ...

    def revert_onto_branch(self, repo: str, branch: str, commit: str) -> list[str]:
        """Branch from origin/main and `git revert --no-edit` the commit; return the conflicting files, having aborted the revert if any."""
        ...

    def push(self, repo: str, branch: str) -> None: ...


class ForgePort(Protocol):
    def open_pr(self, repo: str, branch: str, title: str, body: str) -> str:
        """Return the PR url."""
        ...

    def wait_checks(self, pr: str) -> list[str]:
        """Return the names of failing checks; empty means green."""
        ...

    def merge(self, pr: str) -> None: ...


@dataclass(frozen=True)
class RevertResult:
    status: str
    pr: str = ""  # empty when no PR was opened
    detail: str = ""


def revert_body(reason: str, footer: str) -> str:
    return f"{reason}\n\n{footer}"


def perform_revert(repo: str, pr: int, commit: str, reason: str, git: GitPort, forge: ForgePort) -> RevertResult:
    branch = f"revert/{pr}"
    git.fetch_main(repo)
    conflicts = git.revert_onto_branch(repo, branch, commit)
    if conflicts:
        return RevertResult(CONFLICT, "", "revert conflicts in: " + ", ".join(conflicts))
    git.push(repo, branch)
    url = forge.open_pr(repo, branch, f"Revert #{pr}: main red after land", revert_body(reason, pr_footer(None)))
    failing = forge.wait_checks(url)
    if failing:
        return RevertResult(CHECKS_FAILED, url, "revert checks failed: " + ", ".join(failing))
    forge.merge(url)
    return RevertResult(MERGED, url, "")


@dataclass(frozen=True)
class RevertFailure:
    commit: str
    attempts: int  # consecutive failures for this commit
    last_failed_at: str  # ISO UTC


def record_failure(failures: Sequence[RevertFailure], commit: str, now: datetime) -> tuple[list[RevertFailure], bool]:
    """The rows with one more failure for the commit, and True only when this is the first of its streak."""
    attempts = 1 + sum(f.attempts for f in failures if f.commit == commit)
    kept = [f for f in failures if f.commit != commit]
    return [*kept, RevertFailure(commit, attempts, now.isoformat())], attempts == 1


def record_success(failures: Sequence[RevertFailure], commit: str) -> list[RevertFailure]:
    return [f for f in failures if f.commit != commit]


def revert_due(failures: Sequence[RevertFailure], commit: str, now: datetime) -> bool:
    """True when the commit has no failure row or its backoff, shared with the stuck-land schedule, has elapsed."""
    return all(now >= aligned(f.last_failed_at, now) + backoff_for(f.attempts) for f in failures if f.commit == commit)


def gate_reverts(actions: Sequence[Action], failures: Sequence[RevertFailure], now: datetime) -> list[Action]:
    """Drop each revert_land whose commit is still backing off; every other action passes through."""
    return [a for a in actions if a.get("kind") != "revert_land" or revert_due(failures, a.get("commit", ""), now)]


def attempt_revert(
    repo: str,
    pr: int,
    commit: str,
    reason: str,
    git: GitPort,
    forge: ForgePort,
    failures: Sequence[RevertFailure],
    now: datetime,
) -> tuple[RevertResult, list[RevertFailure], bool]:
    """Edge. Any raise or non-merged status is a failure; the bool asks for a needs_chair on the first of a streak."""
    try:
        result = perform_revert(repo, pr, commit, reason, git, forge)
    except Exception as exc:  # every port failure, timeouts included, backs off
        result = RevertResult(ERRORED, "", f"{type(exc).__name__}: {exc}")
    if result.status == MERGED:
        return result, record_success(failures, commit), False
    updated, first = record_failure(failures, commit, now)
    return result, updated, first
