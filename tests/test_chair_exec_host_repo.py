"""A clear or carry names the repo by this machine's path locally and by its home-relative path on a lane host."""

import json
import shlex
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_tools import chair_exec
from agent_tools.chair_exec import Deps, perform
from agent_tools.remote_argv import ssh_argv

HOME = "/home/user"
REPO = "/home/user/repos/app"
HOST_REPO = "repos/app"
SSH = "dev@h1"
BRANCH = "epic/alpha/build"


def _deps(calls: list[list[str]], reply: Callable[[list[str]], tuple[int, str]] = lambda command: (0, "")) -> Deps:
    """A recording `run`. `reply` sees the command: an ssh argv's remote command split back into words."""

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return reply(shlex.split(argv[-1]) if argv[0] == "ssh" else argv)

    return Deps(
        run=run,
        delete_branches=lambda repo, pattern: ([], ""),
        acquire_lease=lambda holder, host: "",
        record=lambda action: None,
        run_id=lambda action: "run-1",
        repo_for=lambda action: REPO,
        ssh_for=lambda host: SSH if host == "h1" else "",
        home=HOME,
    )


def _clear(**extra: object) -> dict:
    return {"kind": "clear_branches", "initiative": "alpha", "epoch": 1, "repo": REPO, **extra}


def _remote(calls: list[list[str]]) -> list[list[str]]:
    return [shlex.split(argv[-1]) for argv in calls if argv[0] == "ssh"]


def test_a_clear_with_a_host_lists_worktrees_by_the_local_path_here_and_the_home_relative_path_there() -> None:
    calls: list[list[str]] = []
    results = perform([_clear(host="h1")], _deps(calls), lambda: 1, False)
    assert ["git", "-C", REPO, "worktree", "list", "--porcelain"] in calls
    assert ssh_argv(SSH, ["git", "-C", HOST_REPO, "worktree", "list", "--porcelain"]) in calls
    assert not any(REPO in word for command in _remote(calls) for word in command)
    assert results[0]["status"] == "done"


def test_a_failed_host_worktree_list_names_the_repo_as_the_action_holds_it() -> None:
    calls: list[list[str]] = []
    deps = _deps(calls, lambda command: (1, "no such directory") if HOST_REPO in command else (0, ""))
    results = perform([_clear(host="h1")], deps, lambda: 1, False)
    assert results[0]["status"] == "failed"
    assert results[0]["reason"].startswith(f"on h1: git worktree list in {REPO}:")


def test_a_clear_with_no_host_runs_no_ssh_argv_and_uses_only_the_local_path() -> None:
    calls: list[list[str]] = []
    results = perform([_clear()], _deps(calls), lambda: 1, False)
    assert results[0]["status"] == "done"
    assert calls == [["git", "-C", REPO, "worktree", "list", "--porcelain"]]


def test_a_carry_pushes_a_chair_only_branch_to_the_host_by_its_home_relative_path() -> None:
    calls: list[list[str]] = []

    def reply(command: list[str]) -> tuple[int, str]:
        if "rev-parse" in command and HOST_REPO in command:  # only the host's verify names the home-relative path
            return 1, ""
        return (0, "/tmp/cox-carry-x\n") if command[0] == "mktemp" else (0, "")

    results = perform([_clear(host="h1", carry=["build"])], _deps(calls, reply), lambda: 1, False)
    push = ["git", "-C", REPO, "push", f"{SSH}:{HOST_REPO}", f"refs/heads/{BRANCH}:refs/heads/{BRANCH}"]
    assert push in calls
    assert ["git", "-C", HOST_REPO, "worktree", "add", "/tmp/cox-carry-x", BRANCH] in _remote(calls)
    assert results[0]["status"] == "done"


def test_fetch_branch_fetches_from_the_home_relative_host_location(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"name": "h1", "ssh": SSH, "versions_json": json.dumps({"workspace_dir": "/home/user/work"})}
    argvs: list[list[str]] = []
    monkeypatch.setattr(chair_exec, "_host_row", lambda runs_dir, host: row)
    monkeypatch.setattr(chair_exec, "run_argv", lambda argv, *a, **k: argvs.append(argv) or (0, ""))
    edge = chair_exec._GitEdge(Path("runs"), Path("/home/user/work"), "/home/user/work/app", HOME)
    edge.fetch_branch("h1", "agents/r1/t1")
    assert argvs == [
        ["git", "-C", "/home/user/work/app", "fetch", f"{SSH}:work/app", "+refs/heads/agents/r1/t1:refs/heads/agents/r1/t1"]
    ]
