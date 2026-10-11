from datetime import UTC, datetime, timedelta

from agent_tools.chair_land_backoff import backoff_for
from agent_tools.chair_revert_exec import (
    RevertFailure,
    RevertResult,
    attempt_revert,
    gate_reverts,
    perform_revert,
    record_failure,
    record_success,
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


def test_each_raising_step_is_a_failure_with_one_needs_chair() -> None:
    for step in ("revert", "push", "open_pr", "wait"):
        result, rows, ask = attempt_revert("r", 7, "abc", "red", RaisingGit([], step), RaisingForge([], step), [], T0)
        assert result.status == "errored"
        assert rows == [RevertFailure("abc", 1, T0.isoformat())]
        assert ask is True
        _, rows, ask = attempt_revert("r", 7, "abc", "red", RaisingGit([], step), RaisingForge([], step), rows, T0)
        assert (rows[0].attempts, ask) == (2, False)


def test_non_merged_status_fails_and_merge_clears() -> None:
    _, rows, ask = attempt_revert("r", 7, "abc", "red", FakeGit([], ["a.py"]), FakeForge([], []), [], T0)
    assert (rows[0].attempts, ask) == (1, True)
    result, rows, ask = attempt_revert("r", 7, "abc", "red", FakeGit([], []), FakeForge([], []), rows, T0)
    assert (result.status, rows, ask) == ("merged", [], False)
