"""Land a partial phase's approved tasks one by one, each task land wrapped in a backup and restore of its siblings.

A task land deletes every `agents/<run>/*` branch of its run, not just its own, so the chair used to back the
other tasks' branches up by hand before each land and recreate them after. The core here builds the ordered step
list from the partial_land action and reads git's branch listing; `perform_partial_land` is the edge, running git
and the task land through injected callables, so tests drive the core with literals and the edge with fakes.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, TypedDict

from agent_tools.chair_types import Action

Run = Callable[[list[str]], tuple[int, str]]  # argv -> (exit code, output); the edge's only door to git
Land = Callable[[Action], Mapping[str, Any]]  # a `land` action -> its result, with at least `status` and `reason`

BACKUP_PREFIX = "refs/backup/partial-land"


class LandedCommit(TypedDict):
    """One task land of the sequence that reached main: its task, its origin/main commit, and its own output, which
    names its PR. The watch recorder records one red-main watch per entry."""

    task_id: str
    commit: str
    reason: str


class Step(TypedDict):
    kind: Literal["backup", "land", "restore"]
    task_id: str
    run: str


def agents_prefix(run: str) -> str:
    return f"agents/{run}/"


def backup_ref(run: str, task: str) -> str:
    """Where a sibling's branch tip is kept while another task of its run lands."""
    return f"{BACKUP_PREFIX}/{run}/{task}"


def land_action(action: Action, task_id: str) -> Action:
    """The task land one approved task of the partial_land takes: the run that holds its record, the action's repo."""
    land: Action = {
        "kind": "land",
        "initiative": action.get("initiative", ""),
        "phase": action.get("phase", ""),
        "task_id": task_id,
        "repo": action.get("repo", ""),
        "run": action.get("task_runs", {}).get(task_id, ""),
    }
    return {**land, "epoch": action["epoch"]} if "epoch" in action else land


def steps(action: Action) -> list[Step]:
    """Backup, land, restore for each approved task in the action's order; a task whose run is unknown ends the list."""
    runs = action.get("task_runs", {})
    planned: list[Step] = []
    for task_id in action.get("task_ids", []):
        run = runs.get(task_id, "")
        if not run:
            break
        planned += [
            {"kind": "backup", "task_id": task_id, "run": run},
            {"kind": "land", "task_id": task_id, "run": run},
            {"kind": "restore", "task_id": task_id, "run": run},
        ]
    return planned


def parse_branches(listing: str) -> dict[str, str]:
    """`<branch> <sha>` lines (git for-each-ref's `%(refname:short) %(objectname)`) as branch -> sha."""
    pairs = (line.split() for line in listing.splitlines())
    return {fields[0]: fields[1] for fields in pairs if len(fields) == 2}


def siblings(run: str, task: str, branches: Mapping[str, str]) -> dict[str, str]:
    """The other tasks' `agents/<run>/*` branches, as task -> sha; the landing task's own branch is not one."""
    prefix = agents_prefix(run)
    return {
        name[len(prefix) :]: sha
        for name, sha in branches.items()
        if name.startswith(prefix) and name[len(prefix) :] != task and "/" not in name[len(prefix) :]
    }


def to_restore(backed: Mapping[str, str], branches: Mapping[str, str], run: str) -> dict[str, str]:
    """The backed-up siblings whose branch the land removed, as task -> sha; a branch still there is left alone."""
    prefix = agents_prefix(run)
    return {task: sha for task, sha in backed.items() if prefix + task not in branches}


def _listing(git: Run, repo: str, run: str) -> tuple[dict[str, str] | None, str]:
    code, out = git(
        [
            "git",
            "-C",
            repo,
            "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            f"refs/heads/{agents_prefix(run)}",
        ]
    )
    return (parse_branches(out), "") if code == 0 else (None, f"git for-each-ref in {repo}: {out.strip()}")


def _backup(git: Run, repo: str, run: str, task: str) -> tuple[dict[str, str], str]:
    """(sibling task -> sha, failure line or ""); each sibling's tip is written under `backup_ref` first."""
    branches, error = _listing(git, repo, run)
    if branches is None:
        return {}, error
    backed = siblings(run, task, branches)
    for sibling, sha in backed.items():
        code, out = git(["git", "-C", repo, "update-ref", backup_ref(run, sibling), sha])
        if code != 0:
            return backed, f"backing up agents/{run}/{sibling} in {repo}: {out.strip()}"
    return backed, ""


def _restore(git: Run, repo: str, run: str, backed: Mapping[str, str]) -> str:
    """Recreate every backed-up sibling the land removed, then drop the backup refs; "" or the failure line.

    A sibling whose recreate fails keeps its backup ref, so nothing the land removed is lost."""
    branches, error = _listing(git, repo, run)
    if branches is None:
        return error
    failures: dict[str, str] = {}
    for sibling, sha in to_restore(backed, branches, run).items():
        code, out = git(["git", "-C", repo, "branch", f"{agents_prefix(run)}{sibling}", sha])
        if code != 0:
            failures[sibling] = (
                f"restoring agents/{run}/{sibling} in {repo} (kept at {backup_ref(run, sibling)}): {out.strip()}"
            )
    for sibling in backed:
        if sibling not in failures:
            git(["git", "-C", repo, "update-ref", "-d", backup_ref(run, sibling)])
    return "; ".join(failures.values())


def landed_commit(task: str, result: Mapping[str, Any]) -> list[LandedCommit]:
    """The one-entry list for a task land that landed with a commit, else the empty list."""
    if result.get("status") != "landed" or not result.get("commit"):
        return []
    return [{"task_id": task, "commit": str(result["commit"]), "reason": str(result.get("reason", ""))}]


def perform_partial_land(action: Action, git: Run, land: Land) -> dict[str, Any]:
    """Edge. Each approved task in turn: back up its siblings, run its task land, restore the siblings even when the
    land failed. A land that does not land stops the sequence; later tasks wait for the next tick.

    A failed backup stops the sequence before that task's land runs: whatever siblings were already backed up are
    restored, the task is not landed, and the result is `failed`; tasks landed earlier in the tick stay landed.

    The result is the partial_land's: `landed` when every planned task landed, else the failing land's status and
    any needs_chair it raised; `recorded` when no task has a known run, `failed` when a backup or restore failed.
    Whatever the status, `landed_commits` lists every task land that reached main this tick, one entry per commit."""
    repo = action.get("repo", "")
    done: list[str] = []
    commits: list[LandedCommit] = []
    backed: dict[str, str] = {}
    last: Mapping[str, Any] = {}
    for step in steps(action):
        task, run = step["task_id"], step["run"]
        if step["kind"] == "backup":
            backed, error = _backup(git, repo, run, task)
            if error:
                restore_error = _restore(git, repo, run, backed)
                return _outcome(action, "failed", done, "; ".join(filter(None, [error, restore_error])), commits=commits)
        elif step["kind"] == "land":
            try:
                last = land(land_action(action, task))
            except Exception as exc:  # the edge: a land that raised did not land, and the restore below still runs
                last = {"status": "failed", "reason": f"task land raised {type(exc).__name__}: {exc}"}
            commits += landed_commit(task, last)
        else:
            restore_error = _restore(git, repo, run, backed)
            if last.get("status") != "landed":
                return {
                    **_outcome(
                        action, last.get("status", "failed"), done, f"task {task}: {last.get('reason', '')}",
                        commits=commits,
                    ),
                    **({"needs_chair": last["needs_chair"]} if "needs_chair" in last else {}),
                }
            done.append(task)
            if restore_error:
                return _outcome(action, "failed", done, restore_error, last, commits)
    if not done:
        return _outcome(action, "recorded", done, "no approved task of the phase has a known run yet")
    return _outcome(action, "landed", done, "", last, commits)


def _outcome(
    action: Action, status: str, done: Sequence[str], reason: str, last: Mapping[str, Any] = {},
    commits: Sequence[LandedCommit] = (),
) -> dict[str, Any]:
    """The partial_land's result; the landed tasks lead the reason, the last land's output and commit ride along, and
    `landed_commits` carries every landed task's commit (the last one alone would leave the others unwatched)."""
    head = f"landed {', '.join(done)}" if done else ""
    text = "\n".join(filter(None, [head, reason, str(last.get("reason", "")) if status == "landed" else ""]))
    commit = {"commit": last["commit"]} if "commit" in last else {}
    landed = {"landed_commits": list(commits)} if commits else {}
    return {"action": action, "status": status, "reason": text, **commit, **landed}
