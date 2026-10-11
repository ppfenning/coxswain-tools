from __future__ import annotations

import json

import pytest

from agent_tools.chair_revert_exec import CHECKS_FAILED, CONFLICT, MERGED, perform_revert
from agent_tools.chair_revert_ports import GhForgePort, GitRevertPort, main_ci, parent_count

G = ["git", "-C", "/r"]
TMP = "/nonexistent-revert-tmp"
W = f"{TMP}/wt"
GW = ["git", "-C", W]
PROBE = [*G, "rev-list", "--parents", "-n", "1", "abc"]
WORKTREE_ADD = [*G, "worktree", "add", "-b", "revert/7", W, "origin/main"]
WORKTREE_REMOVE = [*G, "worktree", "remove", "--force", W]
BRANCH_DROP = [*G, "branch", "-D", "revert/7"]
PR_URL = "https://github.com/o/r/pull/9"
GREEN = "ci\tpass\t1s\thttp://x\n"
CREATED = f"Creating pull request\n{PR_URL}\n"
MOVERS = ("checkout", "switch", "reset")


def _git(run: Recorder) -> GitRevertPort:
    return GitRevertPort(run, mkdtemp=lambda: TMP)


class Recorder:
    """Runner that records argv and answers from a script keyed by an argv prefix."""

    def __init__(self, script: dict[tuple[str, ...], tuple[int, str]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.script = script or {}

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        for prefix, answer in self.script.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return answer
        return 0, ""


def test_parent_count_reads_the_rev_list_line() -> None:
    assert (parent_count("abc p1"), parent_count("abc p1 p2")) == (1, 2)


def test_fetch_main_argv() -> None:
    run = Recorder()
    GitRevertPort(run).fetch_main("/r")
    assert run.calls == [[*G, "fetch", "origin", "main"]]


class SharedCheckout(Recorder):
    """Recorder that also tracks the branch the shared checkout /r reports, as a real checkout would."""

    def __init__(self, script: dict[tuple[str, ...], tuple[int, str]] | None = None) -> None:
        super().__init__({("git", "-C", "/r", "rev-list"): (0, "abc p1\n"), **(script or {})})
        self.head = "main"

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        if argv[:3] == G and argv[3] in MOVERS:
            self.head = argv[-1]
        return super().__call__(argv)


def _assert_shared_checkout_untouched(calls: list[list[str]]) -> None:
    assert [c for c in calls if c[:3] == G and c[3] in MOVERS] == []


def _assert_clean(run: SharedCheckout) -> None:
    _assert_shared_checkout_untouched(run.calls)
    assert run.head == "main"
    assert WORKTREE_REMOVE in run.calls


def _drive(run: SharedCheckout) -> str:
    return perform_revert("/r", 7, "abc", "main red", _git(run), GhForgePort(run)).status


def test_clean_revert_works_in_a_worktree_and_push_removes_it() -> None:
    run = Recorder({("git", "-C", "/r", "rev-list"): (0, "abc p1\n")})
    port = _git(run)
    assert port.revert_onto_branch("/r", "revert/7", "abc") == []
    port.push("/r", "revert/7")
    assert run.calls == [
        PROBE,
        WORKTREE_ADD,
        [*GW, "revert", "--no-edit", "abc"],
        [*GW, "push", "origin", "revert/7"],
        WORKTREE_REMOVE,
    ]


def test_two_parent_commit_adds_m_1() -> None:
    run = Recorder({("git", "-C", "/r", "rev-list"): (0, "abc p1 p2\n")})
    _git(run).revert_onto_branch("/r", "revert/7", "abc")
    assert run.calls[-1] == [*GW, "revert", "--no-edit", "-m", "1", "abc"]


def test_conflict_returns_unmerged_files_and_drops_the_worktree_and_branch() -> None:
    run = Recorder(
        {
            ("git", "-C", "/r", "rev-list"): (0, "abc p1\n"),
            ("git", "-C", W, "revert", "--no-edit"): (1, "CONFLICT"),
            ("git", "-C", W, "diff"): (0, "a.py\nb/c.py\n"),
        }
    )
    assert _git(run).revert_onto_branch("/r", "revert/7", "abc") == ["a.py", "b/c.py"]
    assert run.calls[-4:] == [
        [*GW, "diff", "--name-only", "--diff-filter=U"],
        [*GW, "revert", "--abort"],
        WORKTREE_REMOVE,
        BRANCH_DROP,
    ]
    _assert_shared_checkout_untouched(run.calls)


def test_failed_revert_without_conflicts_raises_and_drops_the_worktree() -> None:
    run = Recorder(
        {("git", "-C", "/r", "rev-list"): (0, "abc p1\n"), ("git", "-C", W, "revert", "--no-edit"): (1, "empty")}
    )
    with pytest.raises(RuntimeError, match="no conflicts"):
        _git(run).revert_onto_branch("/r", "revert/7", "abc")
    assert WORKTREE_REMOVE in run.calls
    assert BRANCH_DROP in run.calls


def test_failed_worktree_add_raises_without_deleting_a_branch() -> None:
    run = Recorder(
        {("git", "-C", "/r", "rev-list"): (0, "abc p1\n"), ("git", "-C", "/r", "worktree", "add"): (128, "exists")}
    )
    with pytest.raises(RuntimeError, match="exists"):
        _git(run).revert_onto_branch("/r", "revert/7", "abc")
    assert BRANCH_DROP not in run.calls


def test_push_failure_raises_and_still_removes_the_worktree() -> None:
    run = Recorder({("git", "-C", "/r", "rev-list"): (0, "abc p1\n"), ("git", "-C", W, "push"): (1, "rejected")})
    port = _git(run)
    port.revert_onto_branch("/r", "revert/7", "abc")
    with pytest.raises(RuntimeError, match="rejected"):
        port.push("/r", "revert/7")
    assert run.calls[-1] == WORKTREE_REMOVE


def test_push_without_a_worktree_pushes_from_the_checkout() -> None:
    run = Recorder({("git",): (1, "rejected")})
    with pytest.raises(RuntimeError, match="rejected"):
        _git(run).push("/r", "revert/7")
    assert run.calls == [[*G, "push", "origin", "revert/7"]]


def test_success_leaves_the_shared_checkout_alone() -> None:
    run = SharedCheckout({("gh", "pr", "create"): (0, CREATED), ("gh", "pr", "checks"): (0, GREEN)})
    assert _drive(run) == MERGED
    _assert_clean(run)


def test_conflict_leaves_the_shared_checkout_alone() -> None:
    run = SharedCheckout({("git", "-C", W, "revert"): (1, "CONFLICT"), ("git", "-C", W, "diff"): (0, "a.py\n")})
    assert _drive(run) == CONFLICT
    _assert_clean(run)
    assert BRANCH_DROP in run.calls


def test_push_failure_leaves_the_shared_checkout_alone() -> None:
    run = SharedCheckout({("git", "-C", W, "push"): (1, "rejected")})
    with pytest.raises(RuntimeError, match="rejected"):
        _drive(run)
    _assert_clean(run)


def test_open_pr_failure_leaves_the_shared_checkout_alone() -> None:
    run = SharedCheckout({("gh", "pr", "create"): (1, "boom")})
    with pytest.raises(RuntimeError, match="boom"):
        _drive(run)
    _assert_clean(run)


def test_wait_raise_leaves_the_shared_checkout_alone() -> None:
    run = SharedCheckout({("gh", "pr", "create"): (0, CREATED), ("gh", "pr", "checks"): (1, "")})
    with pytest.raises(RuntimeError, match="no failing check"):
        _drive(run)
    _assert_clean(run)


def test_wait_timeout_raises_and_leaves_the_shared_checkout_alone() -> None:
    expired = "gh pr checks timed out after 1800s"
    run = SharedCheckout({("gh", "pr", "create"): (0, CREATED), ("gh", "pr", "checks"): (124, expired)})
    with pytest.raises(RuntimeError, match="timed out after 1800s"):
        _drive(run)
    _assert_clean(run)


def test_failing_checks_are_a_checks_failed_result() -> None:
    fail = "t\tfail\t1m\thttp://y\n"
    run = SharedCheckout({("gh", "pr", "create"): (0, CREATED), ("gh", "pr", "checks"): (1, fail)})
    assert _drive(run) == CHECKS_FAILED
    _assert_clean(run)


def test_open_pr_returns_the_url() -> None:
    run = Recorder({("gh",): (0, "Creating pull request\nhttps://github.com/o/r/pull/9\n")})
    url = GhForgePort(run).open_pr("o/r", "revert/7", "Revert #7", "why")
    assert url == "https://github.com/o/r/pull/9"
    assert run.calls == [
        ["gh", "pr", "create", "--repo", "o/r", "--head", "revert/7", "--title", "Revert #7", "--body", "why"]
    ]


def test_wait_checks_returns_only_failing_names() -> None:
    out = "lint\tpass\t10s\thttp://x\ntests\tfail\t1m\thttp://y\nbuild\tpending\t0\thttp://z\n"
    run = Recorder({("gh",): (1, out)})
    assert GhForgePort(run).wait_checks("https://github.com/o/r/pull/9") == ["tests"]
    assert run.calls == [["gh", "pr", "checks", "https://github.com/o/r/pull/9", "--watch"]]


def test_wait_checks_raises_without_evidence() -> None:
    with pytest.raises(RuntimeError, match="no failing check"):
        GhForgePort(Recorder({("gh",): (1, "")})).wait_checks("https://github.com/o/r/pull/9")


def test_merge_squashes() -> None:
    run = Recorder()
    GhForgePort(run).merge("https://github.com/o/r/pull/9")
    assert run.calls == [["gh", "pr", "merge", "https://github.com/o/r/pull/9", "--squash"]]


def _listing(status: str, conclusion: str) -> dict[tuple[str, ...], tuple[int, str]]:
    runs = [{"databaseId": 55, "status": status, "conclusion": conclusion}]
    return {("gh", "run", "list"): (0, json.dumps(runs))}


def test_main_ci_in_progress_is_pending() -> None:
    assert main_ci(Recorder(_listing("in_progress", "")), "o/r", "abc") == ("pending", "")


def test_main_ci_success_is_green() -> None:
    assert main_ci(Recorder(_listing("completed", "success")), "o/r", "abc") == ("green", "")


def test_main_ci_failure_is_red_with_log_tail() -> None:
    log = "\n".join(f"line {n}" for n in range(100))
    run = Recorder({**_listing("completed", "failure"), ("gh", "run", "view"): (0, log)})
    state, output = main_ci(run, "o/r", "abc")
    assert (state, output.splitlines()[0], output.splitlines()[-1]) == ("red", "line 60", "line 99")
    assert run.calls[0] == [
        "gh", "run", "list", "--repo", "o/r", "--branch", "main", "--commit", "abc",
        "--json", "databaseId,status,conclusion", "--limit", "1",
    ]  # fmt: skip
    assert run.calls[1] == ["gh", "run", "view", "55", "--repo", "o/r", "--log-failed"]


def test_main_ci_cancelled_is_not_red() -> None:
    assert main_ci(Recorder(_listing("completed", "cancelled")), "o/r", "abc") == ("pending", "run concluded cancelled")


def test_main_ci_unreadable_log_is_labelled() -> None:
    run = Recorder({**_listing("completed", "failure"), ("gh", "run", "view"): (1, "HTTP 401")})
    assert main_ci(run, "o/r", "abc") == ("red", "failing log unavailable, gh run view exited 1: HTTP 401")


def test_main_ci_no_run_yet_is_pending() -> None:
    assert main_ci(Recorder({("gh",): (0, "[]")}), "o/r", "abc") == ("pending", "")
