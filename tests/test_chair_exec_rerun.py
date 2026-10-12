"""The rerun_main action: an infra main is rerun through the injected port, never reverted."""

from agent_tools.chair_exec import Deps, perform

RERUN = {"kind": "rerun_main", "initiative": "init", "phase": "p1", "repo": "o/r", "commit": "abc123", "epoch": 0}


def _run(port=None):
    calls, recorded, quarantined, resolved = [], [], [], []

    def rerun(repo, commit):
        calls.append((repo, commit))
        if isinstance(port, Exception):
            raise port
        return port

    deps = Deps(
        run=lambda argv: (0, ""),
        delete_branches=lambda repo, pattern: ([], ""),
        acquire_lease=lambda holder, host: "",
        record=recorded.append,
        run_id=lambda a: "run-1",
        repo_for=lambda a: "r",
        quarantine_phase=lambda *args: quarantined.append(args),
        resolve_land=lambda *args: resolved.append(args),
        rerun_main=rerun if port is not None else None,
    )
    results = perform([RERUN], deps, lambda: 0, False)
    return results, calls, recorded, quarantined, resolved


def test_a_rerun_is_done_and_calls_the_port_once_with_repo_and_commit():
    results, calls, recorded, quarantined, resolved = _run(("rerun", "reran run 7"))
    assert calls == [("o/r", "abc123")]
    assert [(r["status"], r["reason"]) for r in results] == [("done", "reran run 7")]
    assert [(r["kind"], r["status"]) for r in recorded] == [("rerun_main", "done")]
    assert (quarantined, resolved) == ([], [])


def test_a_pending_run_is_done_with_nothing_raised():
    results, _, recorded, _, _ = _run(("pending", ""))
    assert [r["status"] for r in results] == ["done"]
    assert "needs_chair" not in results[0]
    assert len(recorded) == 1


def test_a_capped_run_fails_and_raises_one_main_infra_needs_chair_naming_the_commit():
    results, _, recorded, quarantined, resolved = _run(("capped", "run 7 for abc123 still failed after 4 attempts"))
    assert results[0]["status"] == "failed"
    needs = results[0]["needs_chair"]
    assert (needs["kind"], needs["cause"], needs["initiative"], needs["phase"]) == (
        "needs_chair",
        "main_infra",
        "init",
        "p1",
    )
    assert "abc123" in needs["reason"]
    assert [(r["kind"], r["status"]) for r in recorded] == [("rerun_main", "failed"), ("needs_chair", "recorded")]
    assert (quarantined, resolved) == ([], [])


def test_a_raising_port_is_a_failed_result_and_no_needs_chair():
    results, _, _, quarantined, resolved = _run(OSError("network down"))
    assert results[0]["status"] == "failed"
    assert "OSError: network down" in results[0]["reason"]
    assert "needs_chair" not in results[0]
    assert (quarantined, resolved) == ([], [])


def test_a_missing_port_is_refused():
    results, calls, _, _, _ = _run(None)
    assert [r["status"] for r in results] == ["refused"]
    assert calls == []
