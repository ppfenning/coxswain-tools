from datetime import UTC, datetime, timedelta

from agent_tools.chair_land_backoff import backoff_for
from agent_tools.chair_revert_exec import (
    UNSETTLED,
    RerunRecord,
    RevertFailure,
    RevertResult,
    attempt_revert,
    gate_reverts,
    perform_revert,
    record_failure,
    record_success,
    reran_commits,
    rerun_reds,
    revert_due,
)
from agent_tools.land import pr_footer

URL = "https://example.test/pull/9"


class FakeGit:
    def __init__(self, calls: list, conflicts: list[str]) -> None:
        self.calls, self.conflicts = calls, conflicts

    def fetch_main(self, repo: str) -> None:
        self.calls.append(("fetch", repo))

    def revert_onto_branch(self, repo: str, branch: str, commit: str) -> list[str]:
        self.calls.append(("revert", repo, branch, commit))
        return self.conflicts

    def push(self, repo: str, branch: str) -> None:
        self.calls.append(("push", repo, branch))


class FakeForge:
    def __init__(self, calls: list, failing: list[str]) -> None:
        self.calls, self.failing = calls, failing

    def open_pr(self, repo: str, branch: str, title: str, body: str) -> str:
        self.calls.append(("open", repo, branch, title, body))
        return URL

    def wait_checks(self, pr: str) -> list[str]:
        self.calls.append(("wait", pr))
        return self.failing

    def merge(self, pr: str) -> None:
        self.calls.append(("merge", pr))


def run(conflicts: list[str], failing: list[str]) -> tuple[RevertResult, list]:
    calls: list = []
    result = perform_revert("r", 7, "abc123", "tests red", FakeGit(calls, conflicts), FakeForge(calls, failing))
    return result, calls


def test_clean_revert_runs_in_order_and_merges() -> None:
    result, calls = run([], [])
    assert [c[0] for c in calls] == ["fetch", "revert", "push", "open", "wait", "merge"]
    assert result == RevertResult("merged", URL, "")
    assert calls[1] == ("revert", "r", "revert/7", "abc123")
    assert calls[2] == ("push", "r", "revert/7")


def test_conflict_stops_before_push_and_pr() -> None:
    result, calls = run(["a.py", "b.py"], [])
    assert [c[0] for c in calls] == ["fetch", "revert"]
    assert result.status == "conflict"
    assert result.pr == ""
    assert "a.py" in result.detail and "b.py" in result.detail


def test_failing_checks_leave_pr_unmerged() -> None:
    result, calls = run([], ["lint"])
    assert [c[0] for c in calls] == ["fetch", "revert", "push", "open", "wait"]
    assert result.status == "checks_failed"
    assert result.pr == URL
    assert "lint" in result.detail


def test_title_names_the_pr_number() -> None:
    _, calls = run([], [])
    assert calls[3][3] == "Revert #7: main red after land"


def test_body_carries_reason_and_ends_with_footer() -> None:
    _, calls = run([], [])
    body = calls[3][4]
    assert "tests red" in body
    assert body.endswith(pr_footer(None))


T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def test_wait_sequence_is_15_30_60_120_120_minutes() -> None:
    assert [backoff_for(n) // timedelta(minutes=1) for n in (1, 2, 3, 4, 5)] == [15, 30, 60, 120, 120]


def test_no_revert_land_before_the_wait_elapses() -> None:
    red = [{"kind": "revert_land", "commit": "abc"}, {"kind": "needs_chair", "commit": "abc"}]
    for attempts, minutes in ((1, 15), (2, 30), (3, 60), (4, 120), (5, 120)):
        rows = [RevertFailure("abc", attempts, T0.isoformat())]
        before = T0 + timedelta(minutes=minutes) - timedelta(seconds=1)
        at = T0 + timedelta(minutes=minutes)
        assert not revert_due(rows, "abc", before)
        assert [a["kind"] for a in gate_reverts(red, rows, before)] == ["needs_chair"]
        assert revert_due(rows, "abc", at)
        assert [a["kind"] for a in gate_reverts(red, rows, at)] == ["revert_land", "needs_chair"]


def test_gate_leaves_other_commits_alone() -> None:
    rows = [RevertFailure("abc", 1, T0.isoformat())]
    other = [{"kind": "revert_land", "commit": "def"}]
    assert gate_reverts(other, rows, T0) == other


def test_five_failure_streak_raises_one_needs_chair() -> None:
    rows: list[RevertFailure] = []
    firsts = []
    for n in range(5):
        rows, first = record_failure(rows, "abc", T0 + timedelta(hours=n))
        firsts.append(first)
    assert firsts == [True, False, False, False, False]
    assert rows == [RevertFailure("abc", 5, (T0 + timedelta(hours=4)).isoformat())]


def test_success_clears_the_streak() -> None:
    rows, _ = record_failure([], "abc", T0)
    rows, first = record_failure(rows, "abc", T0)
    assert (rows[0].attempts, first) == (2, False)
    assert record_success(rows, "abc") == []
    rows, first = record_failure(record_success(rows, "abc"), "abc", T0)
    assert (rows[0].attempts, first) == (1, True)


class RaisingGit(FakeGit):
    def __init__(self, calls: list, step: str) -> None:
        super().__init__(calls, [])
        self.step = step

    def revert_onto_branch(self, repo: str, branch: str, commit: str) -> list[str]:
        if self.step == "revert":
            raise RuntimeError("revert boom")
        return super().revert_onto_branch(repo, branch, commit)

    def push(self, repo: str, branch: str) -> None:
        if self.step == "push":
            raise TimeoutError("push timed out")
        super().push(repo, branch)


class RaisingForge(FakeForge):
    def __init__(self, calls: list, step: str) -> None:
        super().__init__(calls, [])
        self.step = step

    def open_pr(self, repo: str, branch: str, title: str, body: str) -> str:
        if self.step == "open_pr":
            raise RuntimeError("open boom")
        return super().open_pr(repo, branch, title, body)

    def wait_checks(self, pr: str) -> list[str]:
        if self.step == "wait":
            raise TimeoutError("wait timed out")
        return super().wait_checks(pr)


def no_pr(repo: str, pr: int) -> None:
    return None


def no_stale(repo: str, branch: str, pr_open: bool) -> list[str]:
    return []


def test_each_raising_step_is_a_failure_with_one_needs_chair() -> None:
    for step in ("revert", "push", "open_pr", "wait"):
        git, forge = RaisingGit([], step), RaisingForge([], step)
        result, rows, ask = attempt_revert(
            "r", 7, "abc", "red", git, forge, [], T0, find_open_pr=no_pr, clear_stale=no_stale
        )
        assert result.status == "errored"
        assert rows == [RevertFailure("abc", 1, T0.isoformat())]
        assert ask is True
        _, rows, ask = attempt_revert(
            "r", 7, "abc", "red", git, forge, rows, T0, find_open_pr=no_pr, clear_stale=no_stale
        )
        assert (rows[0].attempts, ask) == (2, False)


def test_non_merged_status_fails_and_merge_clears() -> None:
    git, forge = FakeGit([], ["a.py"]), FakeForge([], [])
    _, rows, ask = attempt_revert("r", 7, "abc", "red", git, forge, [], T0, find_open_pr=no_pr, clear_stale=no_stale)
    assert (rows[0].attempts, ask) == (1, True)
    git = FakeGit([], [])
    result, rows, ask = attempt_revert(
        "r", 7, "abc", "red", git, forge, rows, T0, find_open_pr=no_pr, clear_stale=no_stale
    )
    assert (result.status, rows, ask) == ("merged", [], False)


RED = {
    "kind": "revert_land",
    "initiative": "i",
    "phase": "p",
    "repo": "r",
    "pr": 7,
    "commit": "abc",
    "reason": "old log",
}


class FakeRerun:
    def __init__(self, result: tuple[str, str, int | None] | Exception) -> None:
        self.result, self.calls = result, []

    def __call__(self, *args: object) -> tuple[str, str, int | None]:
        self.calls.append(args)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_flaky_red_that_reruns_green_plans_no_revert_and_records_one_rerun() -> None:
    rerun, read = FakeRerun(("green", "", 5)), FakeRerun(("red", "", 5))
    out = rerun_reds([RED, {"kind": "needs_chair"}], {"abc": 5}, rerun, read, set(), [], T0)
    assert [a["kind"] for a in out.actions] == ["needs_chair"]
    assert (rerun.calls, read.calls) == ([("r", 5, "abc")], [])
    assert out.recorded == [RerunRecord("abc", 5, "green", T0.isoformat())]


def test_rerun_that_fails_again_keeps_the_revert_land_with_the_new_log() -> None:
    out = rerun_reds([RED], {"abc": 5}, FakeRerun(("red", "new log", 5)), FakeRerun(("green", "", 5)), set(), [], T0)
    assert out.actions == [{**RED, "reason": "new log"}]
    assert len(out.recorded) == 1 and out.failures == [] and out.asks == []


def test_second_tick_after_a_rerun_reads_ci_and_does_not_rerun_again() -> None:
    first = rerun_reds([RED], {"abc": 5}, FakeRerun(("red", "log", 5)), FakeRerun(("red", "", 5)), set(), [], T0)
    rerun, read = FakeRerun(("green", "", 5)), FakeRerun(("red", "still red", 5))
    second = rerun_reds([RED], {"abc": 5}, rerun, read, reran_commits(first.recorded), [], T0)
    assert (rerun.calls, read.calls) == ([], [("r", "abc")])
    assert second.actions == [{**RED, "reason": "still red"}] and second.recorded == []


def test_red_without_a_run_id_passes_through_unrerun() -> None:
    rerun = FakeRerun(("green", "", 5))
    assert rerun_reds([RED], {}, rerun, rerun, set(), [], T0).actions == [RED]
    assert rerun.calls == []


def test_rerun_that_fails_the_wait_is_recorded_backs_off_and_is_never_reissued() -> None:
    out = rerun_reds(
        [RED], {"abc": 5}, FakeRerun(TimeoutError("watch timed out")), FakeRerun(("red", "", 5)), set(), [], T0
    )
    assert out.actions == [] and out.recorded == [RerunRecord("abc", 5, UNSETTLED, T0.isoformat())]
    assert out.failures == [RevertFailure("abc", 1, T0.isoformat())] and out.asks == ["abc"]
    later = T0 + timedelta(minutes=15)
    rerun, read = FakeRerun(("green", "", 5)), FakeRerun(TimeoutError("x"))
    again = rerun_reds([RED], {"abc": 5}, rerun, read, reran_commits(out.recorded), out.failures, later)
    assert (rerun.calls, read.calls) == ([], [("r", "abc")])
    assert (again.failures[0].attempts, again.asks, again.recorded) == (2, [], [])


def test_rerun_left_pending_is_a_recorded_failed_attempt() -> None:
    out = rerun_reds([RED], {"abc": 5}, FakeRerun(("pending", "", 5)), FakeRerun(("red", "", 5)), set(), [], T0)
    assert out.actions == [] and out.failures[0].attempts == 1 and out.recorded[0].state == UNSETTLED


def test_a_commit_backing_off_is_neither_rerun_nor_read_nor_reverted() -> None:
    rows = [RevertFailure("abc", 1, T0.isoformat())]
    rerun, read = FakeRerun(("red", "", 5)), FakeRerun(("red", "", 5))
    out = rerun_reds([RED], {"abc": 5}, rerun, read, set(), rows, T0 + timedelta(minutes=14))
    assert (out.actions, rerun.calls, read.calls, out.failures) == ([], [], [], rows)


def test_green_rerun_clears_the_commits_failure_streak() -> None:
    rows = [RevertFailure("abc", 2, (T0 - timedelta(days=1)).isoformat())]
    out = rerun_reds([RED], {"abc": 5}, FakeRerun(("green", "", 5)), FakeRerun(("red", "", 5)), set(), rows, T0)
    assert out.failures == []


def test_open_revert_pr_is_reused_and_no_second_pr_is_made() -> None:
    calls: list = []
    looked: list = []

    def find(repo: str, pr: int) -> str | None:
        looked.append((repo, pr))
        return URL

    def clear(repo: str, branch: str, pr_open: bool) -> list[str]:
        calls.append(("clear", repo, branch, pr_open))
        return []

    result = perform_revert("r", 7, "abc", "red", FakeGit(calls, []), FakeForge(calls, []), find, clear)
    assert looked == [("r", 7)]
    assert [c[0] for c in calls] == ["wait", "merge"]
    assert result == RevertResult("merged", URL, "")


def test_no_open_pr_clears_the_stale_branch_before_the_revert() -> None:
    calls: list = []

    def clear(repo: str, branch: str, pr_open: bool) -> list[str]:
        calls.append(("clear", repo, branch, pr_open))
        return []

    perform_revert("r", 7, "abc", "red", FakeGit(calls, []), FakeForge(calls, []), lambda repo, pr: None, clear)
    assert [c[0] for c in calls] == ["fetch", "clear", "revert", "push", "open", "wait", "merge"]
    assert calls[1] == ("clear", "r", "revert/7", False)


def test_reused_pr_with_failing_checks_backs_off_through_attempt_revert() -> None:
    forge = FakeForge([], ["lint"])
    git = FakeGit([], [])
    result, rows, ask = attempt_revert(
        "r", 7, "abc", "red", git, forge, [], T0, find_open_pr=lambda r, p: URL, clear_stale=no_stale
    )
    assert git.calls == []
    assert (result.status, result.pr, ask) == ("checks_failed", URL, True)
    assert rows == [RevertFailure("abc", 1, T0.isoformat())]
