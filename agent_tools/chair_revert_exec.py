"""Revert a landed PR that turned main red: the imperative edge.

It decides nothing beyond the order of steps and reaches git and the forge only
through the two ports below. It takes plain values, not chair types.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
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
    "UNSETTLED",
    "ForgePort",
    "GitPort",
    "RerunOutcome",
    "RerunRecord",
    "RevertFailure",
    "RevertResult",
    "attempt_revert",
    "gate_reverts",
    "perform_revert",
    "record_failure",
    "record_success",
    "reran_commits",
    "rerun_reds",
    "revert_body",
    "revert_due",
]

MERGED = "merged"
CONFLICT = "conflict"
CHECKS_FAILED = "checks_failed"
ERRORED = "errored"
UNSETTLED = "unsettled"

# (repo, run id, commit) to the main CI (state, failing log tail, run id) read again once the rerun settled.
Rerun = Callable[[str, int, str], tuple[str, str, int | None]]
# (repo, commit) to the main CI (state, failing log tail, run id), read without rerunning.
ReadCi = Callable[[str, str], tuple[str, str, int | None]]
# (repo, pr number) to the url of the open revert PR for that pr, or None.
FindOpenPr = Callable[[str, int], str | None]
# (repo, branch, pr_open) to the backup refs written for a stale local or remote branch.
ClearStale = Callable[[str, str, bool], list[str]]


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


def perform_revert(
    repo: str,
    pr: int,
    commit: str,
    reason: str,
    git: GitPort,
    forge: ForgePort,
    find_open_pr: FindOpenPr | None = None,
    clear_stale: ClearStale | None = None,
) -> RevertResult:
    """With `find_open_pr`, an open revert PR is reused and no branch or PR is made; `clear_stale` backs up and clears a stale branch first."""
    # Not the live path yet: chair_exec._revert_land passes neither port, so it still reverts onto a fresh revert/<pr>
    # and fails on a stale one. `attempt_revert` requires both; route _revert_land through it to go live.
    branch = f"revert/{pr}"
    url = find_open_pr(repo, pr) if find_open_pr is not None else None
    if not url:
        git.fetch_main(repo)
        if clear_stale is not None:
            clear_stale(repo, branch, False)
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
    *,
    find_open_pr: FindOpenPr,
    clear_stale: ClearStale,
) -> tuple[RevertResult, list[RevertFailure], bool]:
    """Edge. Any raise or non-merged status is a failure; the bool asks for a needs_chair on the first of a streak."""
    try:
        result = perform_revert(repo, pr, commit, reason, git, forge, find_open_pr, clear_stale)
    except Exception as exc:  # every port failure, timeouts included, backs off
        result = RevertResult(ERRORED, "", f"{type(exc).__name__}: {exc}")
    if result.status == MERGED:
        return result, record_success(failures, commit), False
    updated, first = record_failure(failures, commit, now)
    return result, updated, first


@dataclass(frozen=True)
class RerunRecord:
    """One rerun issued for a red main commit, kept beside the RevertFailure rows so the commit is never rerun twice."""

    commit: str
    run: int
    state: str  # green, red, or UNSETTLED when the wait raised or ended pending
    at: str  # ISO UTC


def reran_commits(records: Sequence[RerunRecord]) -> set[str]:
    return {r.commit for r in records}


@dataclass(frozen=True)
class RerunOutcome:
    actions: list[Action]  # the input actions, less each revert_land cleared, backing off or unsettled
    recorded: list[RerunRecord]  # one row per rerun issued this tick, whatever its wait did
    failures: list[RevertFailure]
    asks: list[str]  # commits whose unsettled read is the first of a streak, for a needs_chair


def _settle(read: Callable[..., tuple[str, str, int | None]], *args: object) -> tuple[str, str]:
    """A green or red read as it is; a raise or any other state is UNSETTLED, with the detail."""
    try:
        state, output, _ = read(*args)
    except Exception as exc:  # every port failure, a wait timeout included
        return UNSETTLED, f"{type(exc).__name__}: {exc}"
    return (state, output) if state in ("green", "red") else (UNSETTLED, f"main ci {state}: {output}")


def rerun_reds(
    actions: Sequence[Action],
    run_ids: Mapping[str, int],
    rerun: Rerun,
    read_ci: ReadCi,
    reran: Collection[str],
    failures: Sequence[RevertFailure],
    now: datetime,
) -> RerunOutcome:
    """Edge. Rerun each red main's failed run once before its revert_land is planned.

    A commit still backing off is dropped untouched, so callers need not run `gate_reverts` first. The rerun is recorded
    as soon as it is issued, so a commit in `reran` is only read again with `read_ci`, never rerun. Green drops the
    revert_land, red keeps it with the new log, and unsettled is a failed attempt. A commit with no run id and no rerun,
    a failed smoke, passes through.
    """
    kept: list[Action] = []
    recorded: list[RerunRecord] = []
    asks: list[str] = []
    rows = list(failures)
    for a in actions:
        commit, repo = a.get("commit", ""), a.get("repo", "")
        run_id = run_ids.get(commit)
        fresh = commit not in reran and commit not in reran_commits(recorded)
        if a.get("kind") != "revert_land" or (fresh and run_id is None):
            kept.append(a)
            continue
        if not revert_due(rows, commit, now):
            continue
        if fresh and run_id is not None:
            state, output = _settle(rerun, repo, run_id, commit)
            recorded.append(RerunRecord(commit, run_id, state, now.isoformat()))
        else:
            state, output = _settle(read_ci, repo, commit)
        if state == "green":
            rows = record_success(rows, commit)
        elif state == "red":
            kept.append({**a, "reason": output or a.get("reason", "")})
        else:
            rows, first = record_failure(rows, commit, now)
            asks.extend([commit] if first else [])
    return RerunOutcome(kept, recorded, rows, asks)
