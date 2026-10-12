from __future__ import annotations

import json

from agent_tools.chair_revert_green import main_green_since

GH = [
    "gh", "run", "list", "--repo", "o/r", "--branch", "main",
    "--json", "headSha,status,conclusion", "--limit", "50",
]  # fmt: skip


def _ancestor(commit: str, sha: str) -> list[str]:
    return ["git", "-C", "/r", "merge-base", "--is-ancestor", commit, sha]


def _run(sha: str, status: str = "completed", conclusion: str | None = "success") -> dict:
    return {"headSha": sha, "status": status, "conclusion": conclusion}


class Fake:
    def __init__(self, replies: dict[tuple[str, ...], tuple[int, str]]) -> None:
        self.replies = replies
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        return self.replies.get(tuple(argv), (1, ""))


def _listed(*runs: dict) -> tuple[int, str]:
    return 0, json.dumps(list(runs))


def test_success_on_same_commit() -> None:
    runner = Fake({tuple(GH): _listed(_run("abc"))})
    assert main_green_since(runner, "/r", "o/r", "abc") is True
    assert runner.calls == [GH]


def test_success_on_descendant() -> None:
    runner = Fake(
        {
            tuple(GH): _listed(_run("older"), _run("newer")),
            tuple(_ancestor("abc", "older")): (1, ""),
            tuple(_ancestor("abc", "newer")): (0, ""),
        }
    )
    assert main_green_since(runner, "/r", "o/r", "abc") is True
    assert runner.calls == [GH, _ancestor("abc", "older"), _ancestor("abc", "newer")]


def test_failure_only_list() -> None:
    runner = Fake(
        {tuple(GH): _listed(_run("abc", conclusion="failure"), _run("def", status="in_progress", conclusion=None))}
    )
    assert main_green_since(runner, "/r", "o/r", "abc") is False
    assert runner.calls == [GH]


def test_gh_exit_nonzero() -> None:
    runner = Fake({tuple(GH): (1, json.dumps([_run("abc")]))})
    assert main_green_since(runner, "/r", "o/r", "abc") is False
    assert runner.calls == [GH]


def test_malformed_output() -> None:
    for output in ("not json", json.dumps({"headSha": "abc"}), ""):
        runner = Fake({tuple(GH): (0, output)})
        assert main_green_since(runner, "/r", "o/r", "abc") is False


def test_runner_raising_reads_as_false() -> None:
    def boom(argv: list[str]) -> tuple[int, str]:
        raise OSError("gh missing")

    assert main_green_since(boom, "/r", "o/r", "abc") is False
