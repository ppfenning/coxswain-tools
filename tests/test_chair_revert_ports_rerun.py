"""rerun_infra: the rerun cap is GitHub's own run `attempt`, so the port keeps no memory between ticks."""

from __future__ import annotations

import json

import pytest

from agent_tools.chair_revert_ports import rerun_infra

LIST = [
    "gh", "run", "list", "--repo", "o/r", "--branch", "main", "--commit", "abc",
    "--json", "databaseId,status,conclusion,headSha,attempt",
]  # fmt: skip
RERUN = ["gh", "run", "rerun", "7", "--repo", "o/r", "--failed"]


def _runner(rows: list[dict], list_code: int = 0, rerun_code: int = 0):
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        if argv[:3] == ["gh", "run", "list"]:
            return list_code, json.dumps(rows)
        return rerun_code, "rerun requested"

    return run, calls


def _row(run_id: int = 7, conclusion: str = "failure", attempt: int = 1, sha: str = "abc") -> dict:
    return {"databaseId": run_id, "status": "completed", "conclusion": conclusion, "headSha": sha, "attempt": attempt}


def test_a_failed_first_attempt_is_rerun_with_the_failed_flag() -> None:
    run, calls = _runner([_row()])
    assert rerun_infra(run, "o/r", "abc") == ("rerun", "reran the failed jobs of run 7 for abc (attempt 1)")
    assert calls == [LIST, RERUN]


def test_no_run_for_the_commit_is_pending_and_reruns_nothing() -> None:
    run, calls = _runner([])
    assert rerun_infra(run, "o/r", "abc") == ("pending", "")
    assert calls == [LIST]


def test_a_row_for_another_commit_is_never_judged() -> None:
    run, calls = _runner([_row(run_id=9, sha="other")])
    assert rerun_infra(run, "o/r", "abc") == ("pending", "")
    assert calls == [LIST]


def test_a_run_not_failed_is_pending_and_reruns_nothing() -> None:
    run, calls = _runner([_row(conclusion="")])
    assert rerun_infra(run, "o/r", "abc") == ("pending", "")
    assert calls == [LIST]


def test_only_the_newest_run_for_the_commit_is_judged() -> None:
    run, calls = _runner([_row(run_id=5, conclusion="failure"), _row(run_id=7, conclusion="success")])
    assert rerun_infra(run, "o/r", "abc") == ("pending", "")
    assert calls == [LIST]


def test_the_third_rerun_is_still_allowed() -> None:
    run, calls = _runner([_row(attempt=3)])
    assert rerun_infra(run, "o/r", "abc")[0] == "rerun"
    assert calls == [LIST, RERUN]


def test_an_attempt_past_the_cap_is_capped_and_reruns_nothing() -> None:
    run, calls = _runner([_row(attempt=4)])
    assert rerun_infra(run, "o/r", "abc") == ("capped", "run 7 for abc still failed after 4 attempts")
    assert calls == [LIST]


def test_a_failed_listing_raises() -> None:
    run, _ = _runner([], list_code=1)
    with pytest.raises(RuntimeError):
        rerun_infra(run, "o/r", "abc")


def test_a_failed_rerun_raises() -> None:
    run, _ = _runner([_row()], rerun_code=1)
    with pytest.raises(RuntimeError):
        rerun_infra(run, "o/r", "abc")


def test_the_port_never_watches_the_run() -> None:
    run, calls = _runner([_row()])
    rerun_infra(run, "o/r", "abc")
    assert not any(argv[:3] == ["gh", "run", "watch"] for argv in calls)
