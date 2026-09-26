import json

from agent_tools.cli import main
from agent_tools.stats_schema import connect


def _insert(conn, table, row):
    cols = ", ".join(row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))


def _call(run_id, task_id, role, cost):
    return {"run_id": run_id, "seq": 1, "role": role, "task_id": task_id, "cost_usd": cost}


def _seed(db):
    """r_old (2026-09-01) holds an approved charter review; r_new (2026-09-20) holds a revised one that forced a rebuild."""
    conn = connect(db)
    for run_id, started in (("r_old", "2026-09-01T10:00:00Z"), ("r_new", "2026-09-20T10:00:00Z")):
        _insert(conn, "runs", {"run_id": run_id, "started_at": started})
    _insert(conn, "tasks", {
        "run_id": "r_old", "task_id": "t0", "outcome": "landed", "charter_verdict": "approve",
        "fix_loop_attempts": 1, "fix_loop_stopped": 0,
    })
    _insert(conn, "tasks", {
        "run_id": "r_new", "task_id": "t1", "outcome": "landed", "charter_verdict": "revise",
        "handoff_verdict": "yes", "fix_loop_attempts": 2, "fix_loop_stopped": 0,
    })
    for call in (
        _call("r_old", "t0", "review_charter", 0.25),
        _call("r_new", "t1", "review_charter", 0.5),
        _call("r_new", "t1", "handoff", 0.125),
        _call("r_new", "t1", "validate_chunk", 0.0625),
        _call("r_new", "t1", "build", 9.0),
    ):
        _insert(conn, "calls", call)
    conn.commit()
    conn.close()


def _cells(line):
    return [c.strip() for c in line.split("|")]


def _table(out):
    return {_cells(line)[0]: _cells(line) for line in out.split("\n\n")[0].splitlines()[1:]}


def test_text_output_has_one_row_per_gate_role_present(tmp_path, capsys):
    _seed(tmp_path / "s.db")
    assert main(["stats", "gates", "--db", str(tmp_path / "s.db")]) == 0
    out = capsys.readouterr().out
    assert list(_table(out)) == ["handoff", "review_charter", "validate_chunk"]
    assert _table(out)["review_charter"][1:4] == ["2", "0.7500", "0.3750"]
    assert "review_charter: not enough data (2 decided tasks, need 50)" in out.split("\n\n")[1]


def test_min_sample_passes_through_to_the_verdict_and_decided_is_in_the_json(tmp_path, capsys):
    _seed(tmp_path / "s.db")
    assert main(["stats", "gates", "--db", str(tmp_path / "s.db"), "--min-sample", "2"]) == 0
    assert "review_charter: the step is earning its cost." in capsys.readouterr().out.split("\n\n")[1]
    assert main(["stats", "gates", "--db", str(tmp_path / "s.db"), "--json"]) == 0
    rows = {r["role"]: r for r in json.loads(capsys.readouterr().out)["rows"]}
    assert (rows["review_charter"]["decided"], rows["validate_chunk"]["decided"]) == (2, 0)


def test_a_value_of_none_prints_a_dash_never_zero(tmp_path, capsys):
    _seed(tmp_path / "s.db")
    main(["stats", "gates", "--db", str(tmp_path / "s.db")])
    row = _table(capsys.readouterr().out)["validate_chunk"]
    assert row[4:] == ["-", "-", "-", "-", "-"]
    assert "0.0 " not in " ".join(row)


def test_json_parses_and_matches_the_text_values(tmp_path, capsys):
    _seed(tmp_path / "s.db")
    main(["stats", "gates", "--db", str(tmp_path / "s.db")])
    text = capsys.readouterr().out
    assert main(["stats", "gates", "--db", str(tmp_path / "s.db"), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    charter = next(r for r in doc["rows"] if r["role"] == "review_charter")
    assert charter["verdict_mix"] == {"approve": 1, "revise": 1}
    assert _table(text)["review_charter"][1:] == [
        str(charter["calls"]), f"{charter['cost']:.4f}", f"{charter['cost_per_task']:.4f}",
        "approve:1 revise:1", str(charter["changed"]), str(charter["caught"]), str(charter["agreed"]),
        f"{charter['cost_per_changed']:.4f}",
    ]
    assert [charter["changed"], charter["caught"], charter["agreed"]] == [1, 1, 0]
    assert doc["verdicts"] == text.split("\n\n")[1].splitlines()
    assert next(r for r in doc["rows"] if r["role"] == "validate_chunk")["changed"] is None


def test_since_excludes_an_older_task(tmp_path, capsys):
    _seed(tmp_path / "s.db")
    assert main(["stats", "gates", "--db", str(tmp_path / "s.db"), "--since", "2026-09-10"]) == 0
    charter = _table(capsys.readouterr().out)["review_charter"]
    assert charter[1:3] == ["1", "0.5000"]
    assert charter[4] == "revise:1"


def test_a_missing_db_prints_the_ingest_hint_on_stderr_and_exits_nonzero(tmp_path, capsys):
    db = tmp_path / "absent" / "s.db"
    assert main(["stats", "gates", "--db", str(db), "--json"]) == 1
    captured = capsys.readouterr()
    assert (captured.out, "cox stats ingest" in captured.err) == ("", True)
    assert not db.parent.exists()


def test_a_db_with_no_gate_rows_prints_the_ingest_hint_and_exits_nonzero(tmp_path, capsys):
    conn = connect(tmp_path / "s.db")
    _insert(conn, "calls", _call("r", "t", "build", 1.0))
    conn.commit()
    conn.close()
    assert main(["stats", "gates", "--db", str(tmp_path / "s.db"), "--since", "2026-01-01"]) == 1
    assert "cox stats ingest" in capsys.readouterr().err


def test_a_since_window_with_no_gate_rows_names_the_window_not_the_ingest(tmp_path, capsys):
    _seed(tmp_path / "s.db")
    assert main(["stats", "gates", "--db", str(tmp_path / "s.db"), "--since", "2026-12-01"]) == 1
    err = capsys.readouterr().err
    assert "on or after 2026-12-01" in err and "ingest" not in err


def test_a_malformed_since_is_a_usage_error_not_a_traceback(tmp_path, capsys):
    _seed(tmp_path / "s.db")
    for since in ("2026-13-99", "20260901", "2026-W36-1"):
        assert main(["stats", "gates", "--db", str(tmp_path / "s.db"), "--since", since]) == 2
        assert "YYYY-MM-DD" in capsys.readouterr().err
