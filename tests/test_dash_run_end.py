"""run_end names how an ended run ended, from literal store rows."""

import json

from agent_tools.dash_run_end import run_end

SCHEMA_LINE = "error: database is at schema version 7, newest known migration is 6"


def _run(status: str | None, **record: object) -> dict:
    return {"run_id": "r-1", "status": status, "record_json": json.dumps(record)}


def _task(**record: object) -> dict:
    return {"run_id": "r-1", "phase_id": "p1", "task_id": "t1", "record_json": json.dumps(record)}


def test_landed_record() -> None:
    assert run_end(_run("ok"), [_task(landed=True)], "") == "landed"


def test_approved_record() -> None:
    approved = _task(
        review={"verdict": "approve"}, adversary={"verdict": "approve"}, arbitration="arbiter: skipped (both approved)"
    )
    assert run_end(_run("ok"), [approved], "") == "approved"


def test_quarantine_attempt_carries_its_cause() -> None:
    task = _task(attempts=[{"cause": "tests failed"}])
    assert run_end(_run("ok"), [task], "") == {"kind": "quarantined", "cause": "tests failed"}


def test_awaiting_land_is_idle() -> None:
    assert run_end(_run("awaiting_land"), [], "") == "idle"


def test_recorded_waiting_is_idle() -> None:
    assert run_end(_run("ok", outcome={"waiting": ["waiting on land of t0"]}), [], "") == "idle"


def test_stopped() -> None:
    assert run_end(_run("stopped"), [], "") == "stopped"


def test_error_with_schema_refusal_in_tail() -> None:
    assert run_end(_run("error"), [], SCHEMA_LINE) == {"kind": "died", "cause": "schema_version"}


def test_error_with_empty_tail() -> None:
    assert run_end(_run("error"), [], "") == {"kind": "died", "cause": "error"}


def test_lost_with_no_records() -> None:
    assert run_end(_run("lost"), [], "") == {"kind": "died", "cause": "lost"}


def test_lost_with_landed_work_is_landed() -> None:
    assert run_end(_run("lost"), [_task(landed=True)], "") == "landed"


def test_recorded_refused_is_died_with_the_refusal_line() -> None:
    run = _run("ok", outcome={"refused": "refused: no repo facts"})
    assert run_end(run, [], "") == {"kind": "died", "cause": "refused: no repo facts"}
