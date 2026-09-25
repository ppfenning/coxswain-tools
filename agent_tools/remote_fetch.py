"""Edge: bring an ended remote run's records and branches back to the chair. `run_cmd` takes an argv and returns its exit code.

Arguments
- `host`: the `LaneHost`; its `workspace_dir` holds `runs/<run>/` and `runs/<run>.log` on the host.
- `chair_runs_dir`: the chair's runs directory; the chair workspace is its parent.
- `repo_paths`: reads repo paths out of the fetched run directory.
- `locate`: turns a host path into an rsync or git location; the default is `<ssh>:<path>`.

Where the repos come from. `cox runs land` does not read a repo from a record; it
takes `--repo` on the command line. The only record field that names a task's
repo is the optional `repo` key of `tasks/<phase>/<task>.json`, which
`runs_stranded._remedy` reads before falling back to the work item. `task_repos`
reads that key, so there is no new schema. A caller that knows the repo the way
land does passes `lambda _: [repo]` instead. The key is optional, so a run whose
records name no repo is a `FetchError`, never an empty success.

Repo path rule. A recorded path may be a host path or a chair path, because the
lane writes the path it saw. A path under the host `workspace_dir` maps to the
same relative path under the chair workspace. The chair path then maps back by
swapping the chair workspace prefix for the host `workspace_dir`. A path outside
both workspaces is the same path on both machines. Matching is on a path
component, so `/ws-other` is not under `/ws`.

Each repo is fetched with `git -C <chair repo> fetch <host repo> <refspec>`. Then
`git -C <chair repo> ls-remote --exit-code . refs/heads/agents/<run>/*` must find
a ref, so a fetch that brought no branch is a `FetchError`. A lane whose lease is
held or that has no `ended_at` is refused before anything is copied."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_tools.lane_hosts import LaneHost
from agent_tools.remote_argv import git_fetch_argv, rsync_pull_argv

__all__ = [
    "FetchError", "chair_repo_path", "fetch_run", "host_repo_path", "pull_argvs", "refuse_unended", "task_repos",
]


@dataclass(frozen=True)
class FetchError:
    step: str
    message: str


def refuse_unended(lease_released: bool, ended_at: str | None) -> str | None:
    if lease_released and ended_at:
        return None
    return "the lane has not ended: its lease is held or it has no ended_at"


def _swap(from_prefix: str, to_prefix: str, path: str) -> str:
    old = from_prefix.rstrip("/")
    if path == old or path.startswith(old + "/"):
        return to_prefix.rstrip("/") + path[len(old):]
    return path


def host_repo_path(chair_workspace: str, host_workspace: str, chair_repo: str) -> str:
    return _swap(chair_workspace, host_workspace, chair_repo)


def chair_repo_path(chair_workspace: str, host_workspace: str, recorded: str) -> str:
    return _swap(host_workspace, chair_workspace, recorded)


def pull_argvs(run_location: str, log_location: str, chair_runs_dir: str, run: str) -> list[list[str]]:
    """The run directory lands at `<chair_runs_dir>/<run>/`, the log beside it."""
    runs = chair_runs_dir.rstrip("/")
    return [
        rsync_pull_argv(run_location.rstrip("/") + "/", f"{runs}/{run}/"),
        rsync_pull_argv(log_location, runs + "/"),
    ]


def _repo_of(path: Path) -> str | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    repo = record.get("repo") if isinstance(record, dict) else None
    return repo if isinstance(repo, str) and repo else None


def task_repos(run_dir: Path) -> list[str]:
    """Distinct `repo` values of the task records, in path order."""
    found = (_repo_of(p) for p in sorted(run_dir.glob("tasks/*/*.json")))
    return list(dict.fromkeys(r for r in found if r is not None))


def fetch_run(
    host: LaneHost,
    run: str,
    chair_runs_dir: Path,
    repo_paths: Callable[[Path], Sequence[str]],
    run_cmd: Callable[[list[str]], int],
    locate: Callable[[str], str] | None = None,
    *,
    lease_released: bool,
    ended_at: str | None,
) -> tuple[str, ...] | FetchError:
    """The chair repos whose `agents/<run>/*` branches now exist, or the step that failed. Nothing is raised."""
    refusal = refuse_unended(lease_released, ended_at)
    if refusal is not None:
        return FetchError("refuse", refusal)
    place = locate if locate is not None else (lambda path: f"{host.ssh}:{path}")
    host_ws = host.workspace_dir.rstrip("/")
    chair_runs_dir.mkdir(parents=True, exist_ok=True)
    for argv in pull_argvs(place(f"{host_ws}/runs/{run}"), place(f"{host_ws}/runs/{run}.log"), str(chair_runs_dir), run):
        code = run_cmd(argv)
        if code != 0:
            return FetchError("rsync", f"{' '.join(argv)} exited {code}")
    chair_ws = str(chair_runs_dir.parent)
    repos = tuple(dict.fromkeys(chair_repo_path(chair_ws, host_ws, r) for r in repo_paths(chair_runs_dir / run)))
    if not repos:
        return FetchError("repos", f"no task record under {chair_runs_dir / run}/tasks names a repo")
    for repo in repos:
        # git_fetch_argv has no repo selector, so `-C` goes in after its leading "git".
        fetch = git_fetch_argv(place(host_repo_path(chair_ws, host_ws, repo)), run)
        code = run_cmd(["git", "-C", repo, *fetch[1:]])
        if code != 0:
            return FetchError("git", f"fetching {run} into {repo} exited {code}")
        found = run_cmd(["git", "-C", repo, "ls-remote", "--exit-code", ".", f"refs/heads/agents/{run}/*"])
        if found != 0:
            return FetchError("verify", f"no refs/heads/agents/{run}/* in {repo} after the fetch")
    return repos
