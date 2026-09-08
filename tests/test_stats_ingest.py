import json

import pytest

from agent_tools import stats_ingest
from agent_tools.events import Event
from agent_tools.stats_schema import connect


def _write_run(runs_dir, run_id, *, tasks=(), log_lines=(), usage=None, node=None, launched=None):
    if usage is not None:
        (runs_dir / f"{run_id}.usage.json").write_text(json.dumps(usage))
    if launched is not None:
        (runs_dir / f"{run_id}.launched.json").write_text(json.dumps(launched))
    if node is not None:
        (runs_dir / f"{run_id}:node.json").write_text(json.dumps(node))
    for phase, ticket, record in tasks:
        d = runs_dir / run_id / "tasks" / phase
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{ticket}.json").write_text(json.dumps(record))
    if log_lines:
        (runs_dir / f"{run_id}.log").write_text("\n".join(log_lines) + "\n")


def test_run_row_reads_the_first_nodes_identity_and_sums_human_minutes():
    row = stats_ingest.run_row(
        "r1", {"summary": {}},
        [{"cartridge_sha": "abc", "cartridge_team": "core", "principal": "pat", "human_minutes": 3},
         {"human_minutes": 2}],
        {"launched_by": "session-x"},
    )
    assert row["cartridge_sha"] == "abc"
    assert row["cartridge_team"] == "core"
    assert row["launched_by"] == "session-x"
    assert row["human_minutes"] == 5
    assert row["vendor"] == "claude-code"


def test_call_rows_numbers_attempts_per_role_in_call_order():
    usage = {"calls": [{"role": "build"}, {"role": "build"}, {"role": "review"}]}
    rows = stats_ingest.call_rows("r1", usage)
    assert [r["attempt"] for r in rows] == [1, 2, 1]
    assert [r["seq"] for r in rows] == [0, 1, 2]
    assert all(r["task_id"] is None and r["join_confidence"] is None for r in rows)


def test_task_row_carries_the_records_own_composite_task_id():
    record = {"run_id": "r1:p1:t1", "ticket": "t1"}
    row = stats_ingest.task_row("r1", "p1", "t1", record, gate_diffs=(), log_events=())
    assert row["task_id"] == "r1:p1:t1"
    assert row["outcome"] == "unknown"


def test_task_row_falls_back_to_a_constructed_task_id_when_the_record_names_none():
    row = stats_ingest.task_row("r1", "p1", "t1", {}, gate_diffs=(), log_events=())
    assert row["task_id"] == "r1:p1:t1"


def test_task_row_ignores_a_budget_stop_event_but_still_honors_a_quarantine_event():
    """A budget_stop event is present in log_events (events.py did parse it) and still
    sets no outcome; a task_quarantined event for a DIFFERENT ticket in the same list
    is honored, so the abstention is a deliberate refusal to read that event kind,
    not an absence of one."""
    log_events = [Event("r1", "budget_stop", 0, {}), Event("r1", "task_quarantined", 1, {"task": "t2", "reason": "x"})]
    row_t1 = stats_ingest.task_row("r1", "p1", "t1", {"ticket": "t1"}, gate_diffs=(), log_events=log_events)
    row_t2 = stats_ingest.task_row("r1", "p1", "t2", {"ticket": "t2"}, gate_diffs=(), log_events=log_events)
    assert (row_t1["outcome"], row_t1["outcome_source"]) == ("unknown", "unknown")
    assert (row_t2["outcome"], row_t2["outcome_source"]) == ("quarantined", "log_line")


def test_ingest_is_idempotent(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(
        runs_dir, "run1",
        tasks=[("p1", "t1", {"landed": True})],
        usage={"calls": [{"role": "build", "cost_usd": 1.0}]},
        launched={"launched_by": "s1"},
    )
    db_path = tmp_path / "stats.db"

    first = stats_ingest.ingest(runs_dir, db_path)
    conn = connect(db_path)
    first_tasks = conn.execute("SELECT * FROM tasks").fetchall()
    first_calls = conn.execute("SELECT * FROM calls").fetchall()
    first_runs = conn.execute("SELECT * FROM runs").fetchall()
    conn.close()

    second = stats_ingest.ingest(runs_dir, db_path)
    conn = connect(db_path)
    second_tasks = conn.execute("SELECT * FROM tasks").fetchall()
    second_calls = conn.execute("SELECT * FROM calls").fetchall()
    second_runs = conn.execute("SELECT * FROM runs").fetchall()
    conn.close()

    assert first.runs_ingested == second.runs_ingested == 1
    assert first_tasks == second_tasks
    assert first_calls == second_calls
    assert first_runs == second_runs


def test_ingest_upserts_rather_than_duplicating_on_a_changed_record(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", tasks=[("p1", "t1", {"landed": True})])
    db_path = tmp_path / "stats.db"
    stats_ingest.ingest(runs_dir, db_path)

    _write_run(
        runs_dir, "run1",
        tasks=[("p1", "t1", {"landed": False})],
        node={"gate_diffs": [{"target": "t1", "outcome": "quarantined"}]},
    )
    stats_ingest.ingest(runs_dir, db_path)

    conn = connect(db_path)
    rows = conn.execute("SELECT outcome FROM tasks WHERE run_id = 'run1'").fetchall()
    conn.close()
    assert rows == [("quarantined",)]


def test_a_tasks_row_with_no_derivable_attempt_reads_back_as_none_through_the_real_schema(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", tasks=[("p1", "t1", {"ticket": "t1"})])
    db_path = tmp_path / "stats.db"
    stats_ingest.ingest(runs_dir, db_path)

    conn = connect(db_path)
    value = conn.execute("SELECT attempt FROM tasks WHERE task_id = 'run1:p1:t1'").fetchone()[0]
    conn.close()
    assert value is None


def test_ingest_raises_naming_the_absolute_path_when_the_runs_dir_is_missing(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError) as exc_info:
        stats_ingest.ingest(missing, tmp_path / "stats.db")
    assert str(missing.resolve()) in str(exc_info.value)


def test_ingest_raises_naming_the_absolute_path_when_the_runs_dir_holds_no_run_records(tmp_path):
    empty = tmp_path / "runs"
    empty.mkdir()
    with pytest.raises(FileNotFoundError) as exc_info:
        stats_ingest.ingest(empty, tmp_path / "stats.db")
    assert str(empty.resolve()) in str(exc_info.value)


def test_discover_runs_finds_a_run_that_only_has_a_node_record(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "run1:build.json").write_text("{}")
    assert stats_ingest.discover_runs(runs_dir) == ["run1"]


def test_ingest_does_not_report_a_runs_dir_empty_when_it_holds_only_node_records(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "run1:build.json").write_text(json.dumps({"cartridge_sha": "abc"}))

    report = stats_ingest.ingest(runs_dir, tmp_path / "stats.db")

    assert report.runs_ingested == 1
    conn = connect(tmp_path / "stats.db")
    row = conn.execute("SELECT cartridge_sha FROM runs WHERE run_id = 'run1'").fetchone()
    conn.close()
    assert row == ("abc",)


def test_ingest_reports_a_file_that_fails_to_parse_without_raising(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", tasks=[("p1", "t1", {"ticket": "t1"})])
    bad = runs_dir / "run1:node.json"
    bad.write_text("{not json")

    report = stats_ingest.ingest(runs_dir, tmp_path / "stats.db")
    assert report.unparsed_count == 1
    assert str(bad) in report.unparsed_sample[0]


def test_ingest_reports_a_non_utf8_record_instead_of_raising(tmp_path):
    """A torn or binary-corrupted write is a parse failure, not a crash: runs_detail_screen.py's
    own reader treats OSError the same way, per its 'missing, torn or unreadable reads as {}'."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", tasks=[("p1", "t1", {"ticket": "t1"})])
    bad = runs_dir / "run1:node.json"
    bad.write_bytes(b"\xff\xfe\x00not utf-8")

    report = stats_ingest.ingest(runs_dir, tmp_path / "stats.db")
    assert report.unparsed_count == 1
    assert str(bad) in report.unparsed_sample[0]


def test_ingest_reports_a_non_utf8_log_instead_of_raising(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", tasks=[("p1", "t1", {"ticket": "t1"})])
    bad = runs_dir / "run1.log"
    bad.write_bytes(b"\xff\xfe\x00not utf-8")

    report = stats_ingest.ingest(runs_dir, tmp_path / "stats.db")
    assert report.unparsed_count == 1
    assert str(bad) in report.unparsed_sample[0]


def test_a_budget_stop_log_line_never_sets_either_tickets_outcome(tmp_path):
    """Run 17 was refused for a fixture with no budget-stop signal in it; this one carries one."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(
        runs_dir, "run1",
        tasks=[("p1", "t1", {"ticket": "t1"}), ("p1", "t2", {"ticket": "t2"})],
        log_lines=["boom: error_max_budget_usd"],
    )
    stats_ingest.ingest(runs_dir, tmp_path / "stats.db")

    conn = connect(tmp_path / "stats.db")
    rows = conn.execute("SELECT ticket, outcome, outcome_source FROM tasks WHERE run_id = 'run1' ORDER BY ticket").fetchall()
    conn.close()
    assert rows == [("t1", "unknown", "unknown"), ("t2", "unknown", "unknown")]


def test_gate_diffs_are_ordered_by_the_graphs_node_sequence_not_the_node_files_alphabetical_name(tmp_path):
    """`arbitrate` sorts alphabetically before `review_charter` but runs after it in the
    real graph (agent_tools/runs_detail.py NODE_ORDER); resolve_outcome takes the LAST
    scoped gate_diffs entry, so a node-record read order that trusted the filename
    alphabet would let review_charter's stale 'landed' overwrite arbitrate's real
    'quarantined' verdict."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", tasks=[("p1", "t1", {"ticket": "t1"})])
    (runs_dir / "run1:review_charter.json").write_text(json.dumps({"gate_diffs": [{"target": "t1", "outcome": "landed"}]}))
    (runs_dir / "run1:arbitrate.json").write_text(json.dumps({"gate_diffs": [{"target": "t1", "outcome": "quarantined"}]}))

    stats_ingest.ingest(runs_dir, tmp_path / "stats.db")

    conn = connect(tmp_path / "stats.db")
    outcome = conn.execute("SELECT outcome FROM tasks WHERE task_id = 'run1:p1:t1'").fetchone()[0]
    conn.close()
    assert outcome == "quarantined"


def test_stats_ingest_help_exits_zero():
    from agent_tools.cli import build_parser

    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["stats", "ingest", "--help"])
    assert exc_info.value.code == 0


def test_cli_stats_ingest_exits_nonzero_and_names_the_path_on_an_empty_runs_dir(tmp_path, capsys):
    from agent_tools.cli import main

    empty = tmp_path / "runs"
    empty.mkdir()
    code = main(["stats", "ingest", str(empty), "--db", str(tmp_path / "stats.db")])
    out = capsys.readouterr().out
    assert code == 1
    assert str(empty.resolve()) in out


def test_cli_stats_ingest_exits_zero_on_a_clean_run(tmp_path):
    from agent_tools.cli import main

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", tasks=[("p1", "t1", {"landed": True})])
    code = main(["stats", "ingest", str(runs_dir), "--db", str(tmp_path / "stats.db")])
    assert code == 0
