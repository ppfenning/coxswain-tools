import json
import sqlite3

from agent_tools import run_store
from agent_tools.cli import main


def test_a_task_record_maps_to_the_gate_row_fields():
    record = {
        "review": {"verdict": "revise"},
        "adversary": {"verdict": "approve"},
        "arbitration": {"verdict": "reject"},
        "handoff": {"complete": False},
        "plan_gate": {"ran": True},
        "fix_loop": {"attempts": 2, "stopped": None},
        "landed": True,
    }
    assert run_store._task_verdict_row(record, "r1", "t1") == {
        "run_id": "r1",
        "task_id": "t1",
        "charter_verdict": "revise",
        "adversary_verdict": "approve",
        "arbiter_verdict": "reject",
        "handoff_verdict": "no",
        "plan_gate_verdict": "pass",
        "fix_loop_attempts": 2,
        "fix_loop_stopped": 0,
        "outcome": "landed",
    }


def test_the_store_row_states_fix_loop_and_plan_gate_as_the_stats_db_ingest_does():
    def row(record):
        return run_store._task_verdict_row(record, "r1", "t1")

    assert row({"fix_loop": {"attempts": 1, "stopped": False}})["fix_loop_stopped"] == 0
    assert row({"fix_loop": {"attempts": 1, "stopped": "budget"}})["fix_loop_stopped"] == 1
    assert row({"fix_loop": [{}, {}]})["fix_loop_attempts"] == 2
    assert row({"plan_gate": {"verdict": "revise", "ran": True}})["plan_gate_verdict"] == "revise"
    assert row({"plan_gate": "pass"})["plan_gate_verdict"] == "pass"


def test_one_task_id_in_two_phases_of_a_run_states_no_verdict():
    first = run_store._task_verdict_row({"review": {"verdict": "approve"}}, "r1", "t1")
    second = run_store._task_verdict_row({"review": {"verdict": "revise"}}, "r1", "t1")
    (row,) = run_store._one_per_key([first, second])
    assert (row["run_id"], row["task_id"], row["charter_verdict"]) == ("r1", "t1", None)


def _store(runs_dir):
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute("CREATE TABLE task_records (run_id TEXT, phase_id TEXT, task_id TEXT, record_json TEXT)")
    conn.execute("CREATE TABLE node_calls (run_id TEXT, task_id TEXT, role TEXT, cost_usd REAL, ts TEXT)")
    # Both runs retry task t1: node_calls.task_id is bare, so only (run_id, task_id) tells their verdicts apart.
    records = {
        "r_old": {"review": {"verdict": "approve"}, "fix_loop": {"attempts": 1, "stopped": None}, "landed": True},
        "r_new": {"review": {"verdict": "revise"}, "fix_loop": {"attempts": 2, "stopped": None}, "landed": True},
    }
    for run_id, ts, cost in (("r_old", "2026-09-01T10:05:00Z", 0.25), ("r_new", "2026-09-20T10:05:00Z", 0.5)):
        conn.execute("INSERT INTO task_records VALUES (?, 'p1', 't1', ?)", (run_id, json.dumps(records[run_id])))
        conn.execute("INSERT INTO node_calls VALUES (?, 't1', 'review_charter', ?, ?)", (run_id, cost, ts))
    conn.commit()
    conn.close()


def test_stats_gates_store_reads_task_records_and_node_calls(tmp_path, capsys):
    _store(tmp_path)
    assert main(["stats", "gates", "--store", "--runs-dir", str(tmp_path), "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)["rows"]
    assert (row["role"], row["calls"], row["cost"]) == ("review_charter", 2, 0.75)
    assert row["verdict_mix"] == {"approve": 1, "revise": 1}
    assert (row["changed"], row["caught"]) == (1, 1)


def test_stats_gates_store_since_keeps_a_task_whose_run_called_after_it(tmp_path, capsys):
    _store(tmp_path)
    assert main(["stats", "gates", "--store", "--runs-dir", str(tmp_path), "--since", "2026-09-10", "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)["rows"]
    assert (row["calls"], row["cost"], row["verdict_mix"]) == (1, 0.5, {"revise": 1})
