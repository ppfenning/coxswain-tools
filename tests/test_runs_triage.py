from agent_tools.runs_triage import (
    Attempt,
    SurfaceStatus,
    TriageError,
    render_json,
    render_text,
    surface_status,
    tail_lines,
    triage_task,
)

OUTPUT_25 = "\n".join(f"out {i}" for i in range(1, 26))

FULL_RECORD = {
    "initiative": "init-a",
    "phase": "p1",
    "state": "quarantined",
    "surfaces": ["agent_tools/a.py", "agent_tools/b.py", "agent_tools/c.py (new)"],
    "review": {"verdict": "revise", "objection": "no test for the empty row"},
    "failed_check": {"command": "pytest -q", "output": OUTPUT_25},
}

ATTEMPTS = [
    {"seq": 2, "kind": "build", "cause": "review_revise", "reason": "adversary objected"},
    {"seq": 1, "kind": "build", "cause": "check_failed", "reason": "pytest failed"},
]

REFUSAL = {"time": "2026-10-16T10:00:00Z", "head": "abc123", "reason": "main moved"}


class StubStore:
    def __init__(self, records, attempts=(), run_id=None, refusals=None):
        self.records = records
        self._attempts = attempts
        self.run_id = run_id
        self.refusals = refusals or {}

    def task_record(self, task_id):
        return self.records.get(task_id)

    def attempts(self, task_id):
        return list(self._attempts)

    def newest_run_id(self, task_id):
        return self.run_id

    def land_refusal(self, phase):
        return self.refusals.get(phase)


class StubGit:
    def __init__(self, on_main):
        self.on_main = set(on_main)

    def path_exists_on_main(self, path):
        return path in self.on_main


def full_triage():
    store = StubStore({"t-7": FULL_RECORD}, ATTEMPTS, "run-9", {"p1": REFUSAL})
    return triage_task("t-7", store, StubGit({"agent_tools/a.py"}))


def test_full_text():
    assert render_text(full_triage()) == (
        "task t-7  initiative=init-a  phase=p1  state=quarantined  newest_run=run-9\n"
        "attempts:\n"
        "  1 build cause=check_failed reason=pytest failed\n"
        "  2 build cause=review_revise reason=adversary objected\n"
        "review: revise\n"
        "  objection: no test for the empty row\n"
        "failed check: pytest -q\n"
        "  out 6\n  out 7\n  out 8\n  out 9\n  out 10\n  out 11\n  out 12\n  out 13\n"
        "  out 14\n  out 15\n  out 16\n  out 17\n  out 18\n  out 19\n  out 20\n  out 21\n"
        "  out 22\n  out 23\n  out 24\n  out 25\n"
        "land refusal: time=2026-10-16T10:00:00Z head=abc123\n"
        "  reason: main moved\n"
        "surfaces:\n"
        "  present agent_tools/a.py\n"
        "  missing agent_tools/b.py\n"
        "  new agent_tools/c.py"
    )


def test_full_json():
    assert render_json(full_triage()) == (
        '{"task_id": "t-7", "initiative": "init-a", "phase": "p1", "state": "quarantined", '
        '"newest_run_id": "run-9", "attempts": ['
        '{"seq": 1, "kind": "build", "cause": "check_failed", "reason": "pytest failed"}, '
        '{"seq": 2, "kind": "build", "cause": "review_revise", "reason": "adversary objected"}], '
        '"review": {"verdict": "revise", "objection": "no test for the empty row"}, '
        '"failed_check": {"command": "pytest -q", "output_tail": '
        '"out 6\\nout 7\\nout 8\\nout 9\\nout 10\\nout 11\\nout 12\\nout 13\\nout 14\\nout 15\\n'
        'out 16\\nout 17\\nout 18\\nout 19\\nout 20\\nout 21\\nout 22\\nout 23\\nout 24\\nout 25"}, '
        '"land_refusal": {"time": "2026-10-16T10:00:00Z", "head": "abc123", "reason": "main moved"}, '
        '"surfaces": [{"path": "agent_tools/a.py", "status": "present"}, '
        '{"path": "agent_tools/b.py", "status": "missing"}, '
        '{"path": "agent_tools/c.py", "status": "new"}]}'
    )


def test_unknown_task_is_a_value():
    result = triage_task("nope", StubStore({}), StubGit(()))
    assert result == TriageError("nope", "unknown task id: nope")


def empty_triage():
    record = {"initiative": "init-b", "phase": "p2", "state": "planned", "surfaces": []}
    return triage_task("t-1", StubStore({"t-1": record}), StubGit(()))


def test_empty_blocks_text():
    assert render_text(empty_triage()) == (
        "task t-1  initiative=init-b  phase=p2  state=planned  newest_run=-\n"
        "attempts: none recorded\n"
        "review: none recorded\n"
        "failed check: none recorded\n"
        "land refusal: none recorded\n"
        "surfaces: none listed"
    )


def test_empty_blocks_json():
    assert render_json(empty_triage()) == (
        '{"task_id": "t-1", "initiative": "init-b", "phase": "p2", "state": "planned", '
        '"newest_run_id": null, "attempts": [], "review": null, "failed_check": null, '
        '"land_refusal": null, "surfaces": []}'
    )


def test_tail_keeps_last_twenty_lines():
    assert tail_lines(OUTPUT_25).splitlines() == [f"out {i}" for i in range(6, 26)]


def test_tail_keeps_short_output_whole():
    assert tail_lines("a\nb\nc") == "a\nb\nc"


def test_surface_status_rules():
    assert surface_status("x.py (new)", False) == SurfaceStatus("x.py", "new")
    assert surface_status("x.py (new)", True) == SurfaceStatus("x.py", "present")
    assert surface_status("x.py", True) == SurfaceStatus("x.py", "present")
    assert surface_status("x.py", False) == SurfaceStatus("x.py", "missing")


def test_attempts_sorted_by_seq():
    assert full_triage().attempts == (
        Attempt(1, "build", "check_failed", "pytest failed"),
        Attempt(2, "build", "review_revise", "adversary objected"),
    )
