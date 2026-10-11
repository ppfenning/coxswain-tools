from __future__ import annotations

import json

import pytest

from agent_tools.chair_revert_exec import CHECKS_FAILED, CONFLICT, MERGED, perform_revert
from agent_tools.chair_revert_ports import (
    GhForgePort,
    GitRevertPort,
    backup_ref,
    find_open_revert_pr,
    main_ci,
    main_ci_run,
    parent_count,
    rerun_failed,
)

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


def test_main_ci_run_returns_the_id_it_judged() -> None:
    def judged(status: str, conclusion: str) -> tuple[str, str, int | None]:
        return main_ci_run(Recorder(_listing(status, conclusion)), "o/r", "abc")

    assert judged("in_progress", "") == ("pending", "", 55)
    assert judged("completed", "success") == ("green", "", 55)
    assert judged("completed", "cancelled") == ("pending", "run concluded cancelled", 55)
    red = Recorder({**_listing("completed", "failure"), ("gh", "run", "view"): (0, "boom")})
    assert main_ci_run(red, "o/r", "abc") == ("red", "boom", 55)
    assert main_ci_run(Recorder({("gh",): (0, "[]")}), "o/r", "abc") == ("pending", "", None)


class Flaky(SharedCheckout):
    """Main CI that lists each scripted run in turn: red first, then whatever the rerun produced."""

    def __init__(
        self, listings: list[dict[str, object]], script: dict[tuple[str, ...], tuple[int, str]] | None = None
    ) -> None:
        super().__init__({("gh", "run", "view"): (0, "flaky test failed"), **(script or {})})
        self.listings = listings

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        if argv[:3] == ["gh", "run", "list"]:
            self.calls.append(argv)
            return 0, json.dumps([self.listings.pop(0)])
        return super().__call__(argv)


RED_RUN = {"databaseId": 55, "status": "completed", "conclusion": "failure"}
GREEN_RUN = {"databaseId": 55, "status": "completed", "conclusion": "success"}
RERUN = ["gh", "run", "rerun", "55", "--repo", "o/r", "--failed"]
WATCH = ["gh", "run", "watch", "55", "--repo", "o/r"]


def test_flaky_main_goes_green_on_rerun_and_leaves_the_shared_checkout_alone() -> None:
    run = Flaky([RED_RUN, GREEN_RUN])
    state, _, run_id = main_ci_run(run, "o/r", "abc")
    assert (state, run_id) == ("red", 55)
    assert rerun_failed(run, "o/r", 55, "abc") == ("green", "", 55)
    gh = [c[1:3] for c in run.calls if c[0] == "gh" and c[2] != "view"]
    assert gh == [["run", "list"], ["run", "rerun"], ["run", "watch"], ["run", "list"]]
    assert RERUN in run.calls and WATCH in run.calls
    _assert_shared_checkout_untouched(run.calls)
    assert run.head == "main"


def test_rerun_that_stays_red_reports_red() -> None:
    run = Flaky([RED_RUN])
    assert rerun_failed(run, "o/r", 55, "abc") == ("red", "flaky test failed", 55)
    assert run.head == "main"


def test_rerun_wait_that_expires_raises_without_rereading_ci() -> None:
    run = Flaky([RED_RUN], {("gh", "run", "watch"): (124, "gh run watch timed out after 1800s")})
    with pytest.raises(RuntimeError, match="timed out after 1800s"):
        rerun_failed(run, "o/r", 55, "abc")
    assert [c for c in run.calls if c[:3] == ["gh", "run", "list"]] == []
    assert run.head == "main"


def test_rerun_refused_raises_without_waiting() -> None:
    run = Flaky([RED_RUN], {("gh", "run", "rerun"): (1, "run is still in progress")})
    with pytest.raises(RuntimeError, match="still in progress"):
        rerun_failed(run, "o/r", 55, "abc")
    assert WATCH not in run.calls


def _prs(*heads: str) -> dict[tuple[str, ...], tuple[int, str]]:
    prs = [
        {"number": 9 + n, "url": f"https://github.com/o/r/pull/{9 + n}", "headRefName": h} for n, h in enumerate(heads)
    ]
    return {("gh", "pr", "list"): (0, json.dumps(prs))}


def test_find_open_revert_pr_matches_the_head_branch_exactly() -> None:
    run = Recorder(_prs("revert/70", "revert/7"))
    assert find_open_revert_pr(run, "o/r", 7) == "https://github.com/o/r/pull/10"
    assert run.calls == [
        [
            "gh",
            "pr",
            "list",
            "--repo",
            "o/r",
            "--state",
            "open",
            "--head",
            "revert/7",
            "--json",
            "number,url,headRefName",
        ]
    ]


def test_find_open_revert_pr_is_none_without_a_matching_head() -> None:
    assert find_open_revert_pr(Recorder(_prs("revert/70", "feature")), "o/r", 7) is None
    assert find_open_revert_pr(Recorder({("gh",): (0, "[]")}), "o/r", 7) is None


def test_find_open_revert_pr_failure_raises_instead_of_reading_as_none() -> None:
    with pytest.raises(RuntimeError, match="HTTP 502"):
        find_open_revert_pr(Recorder({("gh",): (1, "HTTP 502")}), "o/r", 7)


TIP_A = "a" * 40
TIP_B = "b" * 40
REF_A1 = "refs/backup/revert/7/aaaaaaaaaaaa-1"
REF_A2 = "refs/backup/revert/7/aaaaaaaaaaaa-2"
REF_B1 = "refs/backup/revert/7/bbbbbbbbbbbb-1"
LS_REMOTE = (*G, "ls-remote")
UPDATE_A1 = [*G, "update-ref", REF_A1, TIP_A]
UPDATE_B1 = [*G, "update-ref", REF_B1, TIP_B]
REMOTE_DROP = [*G, "push", "origin", "--delete", "revert/7"]
PR_FLOW = {("gh", "pr", "create"): (0, CREATED), ("gh", "pr", "checks"): (0, GREEN)}


class Refs(SharedCheckout):
    """SharedCheckout that keeps the refs `update-ref` writes, so a later rev-parse of one finds it as git would."""

    def __init__(self, refs: dict[str, str], script: dict[tuple[str, ...], tuple[int, str]] | None = None) -> None:
        super().__init__(script)
        self.refs = dict(refs)

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        if argv[:3] == G and argv[3] == "rev-parse" and argv[-1] in self.refs:
            self.calls.append(argv)
            return 0, self.refs[argv[-1]] + "\n"
        answer = super().__call__(argv)
        if argv[:3] == G and argv[3] == "update-ref" and answer[0] == 0:
            self.refs[argv[4]] = argv[5]
        return answer


def _redo(run: Refs, pr_open: bool = False) -> tuple[list[str], str]:
    port = _git(run)
    backups = port.clear_stale_branch("/r", "revert/7", pr_open)
    return backups, perform_revert("/r", 7, "abc", "main red", port, GhForgePort(run)).status


def test_backup_ref_names_differ_by_attempt() -> None:
    assert backup_ref("revert/7", TIP_A, 1) != backup_ref("revert/7", TIP_A, 2)


def test_existing_local_branch_is_backed_up_before_it_is_deleted_and_recreated() -> None:
    run = Refs({"refs/heads/revert/7": TIP_A}, PR_FLOW)
    backups, status = _redo(run)
    assert (backups, status) == ([REF_A1], MERGED)
    assert run.calls.index(UPDATE_A1) < run.calls.index(BRANCH_DROP) < run.calls.index(WORKTREE_ADD)
    assert REMOTE_DROP not in run.calls
    assert run.refs[REF_A1] == TIP_A
    _assert_clean(run)


def test_existing_remote_branch_is_fetched_backed_up_then_deleted() -> None:
    listing = f"{TIP_B}\trefs/heads/revert/7\n{TIP_A}\trefs/heads/x/revert/7\n"
    run = Refs({}, {**PR_FLOW, LS_REMOTE: (0, listing)})
    backups, status = _redo(run)
    assert (backups, status) == ([REF_B1], MERGED)
    fetch = [*G, "fetch", "origin", "revert/7"]
    assert run.calls.index(fetch) < run.calls.index(UPDATE_B1) < run.calls.index(REMOTE_DROP)
    assert run.calls.index(REMOTE_DROP) < run.calls.index(WORKTREE_ADD)
    assert BRANCH_DROP not in run.calls
    _assert_clean(run)


def test_local_and_remote_at_one_tip_get_distinct_backups_both_written_before_any_delete() -> None:
    run = Refs({"refs/heads/revert/7": TIP_A}, {**PR_FLOW, LS_REMOTE: (0, f"{TIP_A}\trefs/heads/revert/7\n")})
    backups, _ = _redo(run)
    assert backups == [REF_A1, REF_A2]
    updates = [run.calls.index([*G, "update-ref", ref, TIP_A]) for ref in backups]
    assert max(updates) < min(run.calls.index(BRANCH_DROP), run.calls.index(REMOTE_DROP))
    _assert_clean(run)


def test_existing_open_pr_leaves_the_branch_alone() -> None:
    run = Refs({"refs/heads/revert/7": TIP_A}, {**_prs("revert/7"), LS_REMOTE: (0, f"{TIP_A}\trefs/heads/revert/7\n")})
    pr_open = find_open_revert_pr(run, "o/r", 7) is not None
    assert (pr_open, _git(run).clear_stale_branch("/r", "revert/7", pr_open)) == (True, [])
    assert run.calls == [
        [
            "gh",
            "pr",
            "list",
            "--repo",
            "o/r",
            "--state",
            "open",
            "--head",
            "revert/7",
            "--json",
            "number,url,headRefName",
        ]
    ]
    assert run.refs == {"refs/heads/revert/7": TIP_A}
    _assert_shared_checkout_untouched(run.calls)
    assert run.head == "main"


def test_no_stale_branch_writes_nothing_and_deletes_nothing() -> None:
    run = Refs({}, PR_FLOW)
    assert _redo(run) == ([], MERGED)
    writes = [c for c in run.calls if c[:4] in ([*G, "update-ref"], [*G, "branch"]) or c == REMOTE_DROP]
    assert writes == []


def test_push_failure_after_cleanup_keeps_the_backup_and_the_shared_checkout() -> None:
    run = Refs({"refs/heads/revert/7": TIP_A}, {("git", "-C", W, "push"): (1, "rejected")})
    with pytest.raises(RuntimeError, match="rejected"):
        _redo(run)
    assert run.calls.index(UPDATE_A1) < run.calls.index(BRANCH_DROP) < run.calls.index(WORKTREE_ADD)
    assert run.refs[REF_A1] == TIP_A
    _assert_clean(run)


def test_failed_backup_write_stops_before_any_delete() -> None:
    run = Refs({"refs/heads/revert/7": TIP_A}, {(*G, "update-ref"): (1, "cannot lock ref")})
    with pytest.raises(RuntimeError, match="cannot lock ref"):
        _git(run).clear_stale_branch("/r", "revert/7", False)
    assert BRANCH_DROP not in run.calls
    assert run.head == "main"


def test_unreadable_remote_listing_raises_instead_of_reading_as_no_branch() -> None:
    run = Refs({"refs/heads/revert/7": TIP_A}, {LS_REMOTE: (128, "could not read from remote")})
    with pytest.raises(RuntimeError, match="could not read from remote"):
        _git(run).clear_stale_branch("/r", "revert/7", False)
    assert BRANCH_DROP not in run.calls
