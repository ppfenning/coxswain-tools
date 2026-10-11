from __future__ import annotations

from agent_tools import land

URL = "https://github.com/o/r/actions/runs/7"
NO_STATUS = {"statuses": []}


def _run_row(name: str, conclusion: str = "success") -> dict:
    return {"name": name, "status": "completed", "conclusion": conclusion}


def _suite(conclusion: str) -> dict:
    return {"workflow": "ci.yml", "conclusion": conclusion, "url": URL}


def test_empty_runs_with_a_startup_failure_suite_fail_with_the_workflow_and_url():
    result = land.check_poll_result({"check_runs": []}, NO_STATUS, (), suites=(_suite("startup_failure"),))
    assert result == (1, f"workflow file issue: ci.yml {URL}")


def test_empty_runs_with_a_failure_suite_fail_the_same_way():
    result = land.check_poll_result({"check_runs": []}, NO_STATUS, (), suites=(_suite("failure"),))
    assert result == (1, f"workflow file issue: ci.yml {URL}")


def test_a_startup_failure_suite_beats_a_missing_required_context_before_the_timeout():
    result = land.check_poll_result({"check_runs": [_run_row("lint")]}, NO_STATUS, ("test",), suites=(_suite("startup_failure"),))
    assert result == (1, f"workflow file issue: ci.yml {URL}")


def test_empty_runs_without_suites_still_return_the_no_checks_result():
    assert land.check_poll_result({"check_runs": []}, NO_STATUS, ()) == (1, "no checks reported")
    assert land.check_poll_result({"check_runs": []}, NO_STATUS, (), suites=(_suite("success"),)) == (1, "no checks reported")


def test_a_failed_suite_is_ignored_when_every_check_is_seen():
    assert land.check_poll_result({"check_runs": [_run_row("lint")]}, NO_STATUS, ("lint",), suites=(_suite("failure"),)) == (0, "")


def test_required_contexts_that_never_appear_stay_pending_before_the_timeout():
    runs = {"check_runs": [_run_row("lint")]}
    assert land.check_poll_result(runs, NO_STATUS, ("test", "build"), elapsed_s=179, timeout_s=180) == (
        land.PENDING_RC, "checks pending: test, build")


def test_required_contexts_that_never_appear_fail_after_the_timeout_naming_them():
    runs = {"check_runs": [_run_row("lint")]}
    assert land.check_poll_result(runs, NO_STATUS, ("test", "build"), elapsed_s=180, timeout_s=180) == (
        1, "required checks never appeared within 180s: test, build")


def test_required_contexts_that_appear_are_unaffected_by_elapsed_time():
    runs = {"check_runs": [_run_row("lint"), _run_row("test")]}
    assert land.check_poll_result(runs, NO_STATUS, ("lint", "test"), elapsed_s=9999, timeout_s=180) == (0, "")


def test_wait_checks_fails_a_startup_failure_without_a_rerun(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from agent_tools import forge_github

    calls: list[list[str]] = []

    def fake_run(argv, *, timeout, cwd=None):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(forge_github, "_run", fake_run)
    monkeypatch.setattr(forge_github, "_read_required", lambda repo, ref: ())
    monkeypatch.setattr(forge_github, "_read_checks", lambda repo, ref="HEAD": (True, ({"check_runs": []}, NO_STATUS)))
    monkeypatch.setattr(forge_github, "read_check_suites", lambda repo, sha: (_suite("startup_failure"),))
    ok, detail = forge_github.wait_checks(tmp_path, 180, sleep=lambda s: None)
    assert (ok, detail) == (False, f"workflow file issue: ci.yml {URL}")
    assert not any("rerun" in argv for argv in calls)
