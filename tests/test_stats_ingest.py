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


def _write_trace(runs_dir, run_id, name, events):
    d = runs_dir / f"{run_id}-trace"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


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
    assert all(r["recovered_from_trace"] == 0 for r in rows)


def test_recovered_call_rows_reads_cost_turns_and_failure_class_off_each_traces_final_result_line():
    traces = [
        ("runs/r1-trace/decompose-1.jsonl", [
            {"type": "result", "total_cost_usd": 0.3936, "subtype": "error_max_budget_usd", "num_turns": 12, "is_error": True},
        ]),
    ]
    rows = stats_ingest.recovered_call_rows("r1", traces)
    assert rows[0]["role"] == "decompose"
    assert rows[0]["attempt"] == 1
    assert rows[0]["cost_usd"] == 0.3936
    assert rows[0]["turns"] == 12
    assert rows[0]["failure_class"] == "budget_stop"
    assert rows[0]["trace_path"] == "runs/r1-trace/decompose-1.jsonl"
    assert rows[0]["recovered_from_trace"] == 1
    assert rows[0]["task_id"] is None and rows[0]["join_confidence"] is None


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


def _write_work_item(work_store_root, initiative, phase, ticket, state):
    d = work_store_root / initiative / phase
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ticket}.md").write_text(f"---\nstate: {state}\n---\n\nbody\n")


def test_ingest_reads_landed_from_the_work_store_when_the_run_record_carries_no_landed_field(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", tasks=[("p1", "t1", {"ticket": "t1", "initiative": "init1"})])
    work_store_root = tmp_path / "work"
    _write_work_item(work_store_root, "init1", "p1", "t1", "done")
    db_path = tmp_path / "stats.db"

    stats_ingest.ingest(runs_dir, db_path, work_store_root=work_store_root)

    conn = connect(db_path)
    row = conn.execute("SELECT outcome, outcome_source FROM tasks WHERE task_id = 'run1:p1:t1'").fetchone()
    conn.close()
    assert row == ("landed", "work_store")


def test_ingest_leaves_the_task_unknown_when_no_work_store_root_is_given(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", tasks=[("p1", "t1", {"ticket": "t1", "initiative": "init1"})])
    db_path = tmp_path / "stats.db"

    stats_ingest.ingest(runs_dir, db_path)

    conn = connect(db_path)
    row = conn.execute("SELECT outcome, outcome_source FROM tasks WHERE task_id = 'run1:p1:t1'").fetchone()
    conn.close()
    assert row == ("unknown", "unknown")


def test_discover_runs_finds_a_run_that_only_has_a_node_record(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "run1:build.json").write_text("{}")
    assert stats_ingest.discover_runs(runs_dir) == ["run1"]


def test_discover_runs_finds_a_run_that_only_has_a_trace_directory(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_trace(runs_dir, "run1", "build-1.jsonl", [{"type": "result", "total_cost_usd": 0.1}])
    assert stats_ingest.discover_runs(runs_dir) == ["run1"]


def test_load_run_reads_trace_files_only_when_there_is_no_usage_record(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    path = _write_trace(runs_dir, "run1", "build-1.jsonl", [{"type": "result", "total_cost_usd": 0.1, "num_turns": 3}])

    loaded = stats_ingest.load_run(runs_dir, "run1")

    assert loaded["traces"] == [(str(path), [{"type": "result", "total_cost_usd": 0.1, "num_turns": 3}])]


def test_load_run_ignores_a_trace_directory_when_a_usage_record_is_present(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", usage={"calls": []})
    _write_trace(runs_dir, "run1", "build-1.jsonl", [{"type": "result", "total_cost_usd": 0.1}])

    loaded = stats_ingest.load_run(runs_dir, "run1")

    assert loaded["traces"] == []


def test_load_run_orders_trace_files_by_node_order_not_alphabetical_filename(tmp_path):
    """`scope_epic` precedes `build` in NODE_ORDER but follows it alphabetically;
    `_read_traces` must sort by the graph's own order the same way `_node_sort_key`
    already does for `<run>:<node>.json`, so a recovered call's `seq` reflects
    execution order rather than the accident of two role names' alphabet."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_trace(runs_dir, "run1", "build-1.jsonl", [{"type": "result", "total_cost_usd": 0.1}])
    _write_trace(runs_dir, "run1", "scope_epic-1.jsonl", [{"type": "result", "total_cost_usd": 0.2}])

    loaded = stats_ingest.load_run(runs_dir, "run1")

    ordered_paths = [path for path, _ in loaded["traces"]]
    assert ordered_paths == [
        str(runs_dir / "run1-trace" / "scope_epic-1.jsonl"),
        str(runs_dir / "run1-trace" / "build-1.jsonl"),
    ]


def test_ingest_assigns_seq_to_recovered_calls_in_node_order_not_alphabetical_order(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_trace(runs_dir, "run1", "build-1.jsonl", [{"type": "result", "total_cost_usd": 0.1}])
    _write_trace(runs_dir, "run1", "scope_epic-1.jsonl", [{"type": "result", "total_cost_usd": 0.2}])

    stats_ingest.ingest(runs_dir, tmp_path / "stats.db")

    conn = connect(tmp_path / "stats.db")
    rows = conn.execute("SELECT seq, role FROM calls WHERE run_id = 'run1' ORDER BY seq").fetchall()
    conn.close()
    assert rows == [(0, "scope_epic"), (1, "build")]


def test_ingest_recovers_a_budget_stopped_runs_cost_from_its_trace_result_line(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_trace(
        runs_dir, "run1", "build-1.jsonl",
        [{"type": "result", "total_cost_usd": 0.3936, "subtype": "error_max_budget_usd", "num_turns": 12, "is_error": True}],
    )

    stats_ingest.ingest(runs_dir, tmp_path / "stats.db")

    conn = connect(tmp_path / "stats.db")
    row = conn.execute(
        "SELECT cost_usd, failure_class, recovered_from_trace FROM calls WHERE run_id = 'run1'"
    ).fetchone()
    conn.close()
    assert row == (0.3936, "budget_stop", 1)


def test_ingest_is_idempotent_for_a_trace_recovered_run(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_trace(
        runs_dir, "run1", "build-1.jsonl",
        [{"type": "result", "total_cost_usd": 0.3936, "subtype": "error_max_budget_usd", "num_turns": 12, "is_error": True}],
    )
    db_path = tmp_path / "stats.db"

    stats_ingest.ingest(runs_dir, db_path)
    conn = connect(db_path)
    first = conn.execute("SELECT * FROM calls WHERE run_id = 'run1'").fetchall()
    conn.close()

    stats_ingest.ingest(runs_dir, db_path)
    conn = connect(db_path)
    second = conn.execute("SELECT * FROM calls WHERE run_id = 'run1'").fetchall()
    conn.close()

    assert first == second


def test_ingest_recovers_the_tickets_own_measured_decompose_failure_cost(tmp_path):
    """2026-09-07: a decompose run died on error_max_budget_usd having spent $0.4454
    with no usage.json — the first of the two runs the ticket measured as $0.84
    invisible to every usage-based aggregate."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_trace(
        runs_dir, "run1", "decompose-1.jsonl",
        [{"type": "result", "total_cost_usd": 0.4454, "subtype": "error_max_budget_usd", "num_turns": 8, "is_error": True}],
    )

    stats_ingest.ingest(runs_dir, tmp_path / "stats.db")

    conn = connect(tmp_path / "stats.db")
    cost = conn.execute("SELECT cost_usd FROM calls WHERE run_id = 'run1'").fetchone()[0]
    conn.close()
    assert cost == 0.4454


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


def test_run_join_holds_when_attempts_sum_to_the_build_call_count():
    assert stats_ingest.run_join_holds([2, 1], 3) is True


def test_run_join_holds_is_false_when_attempts_undercount_the_build_calls():
    assert stats_ingest.run_join_holds([2, 1], 4) is False


def test_assign_task_ids_pairs_build_calls_to_tickets_in_ticket_order_times_attempts():
    calls = [{"role": "build"}, {"role": "build"}, {"role": "build"}, {"role": "review"}]
    rows = stats_ingest.assign_task_ids(calls, ["t1", "t2"], [2, 1])
    assert [r["task_id"] for r in rows] == ["t1", "t1", "t2", None]
    assert [r["join_confidence"] for r in rows] == ["heuristic", "heuristic", "heuristic", "none"]


def test_assign_task_ids_leaves_every_call_unjoined_when_the_identity_fails():
    calls = [{"role": "build"}, {"role": "build"}]
    rows = stats_ingest.assign_task_ids(calls, ["t1"], [5])
    assert [r["task_id"] for r in rows] == [None, None]
    assert [r["join_confidence"] for r in rows] == ["none", "none"]


def test_fill_failure_classes_reads_ok_off_a_successful_calls_trace():
    calls = [{"trace_path": "run1-trace/build-1.jsonl", "failure_class": None}]
    traces = {
        "run1-trace/build-1.jsonl": [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit"}]}},
            {"type": "result", "is_error": False},
        ]
    }
    rows = stats_ingest.fill_failure_classes(calls, traces)
    assert rows[0]["failure_class"] == "ok"


def test_fill_failure_classes_reads_the_enum_value_off_a_failed_calls_trace():
    calls = [{"trace_path": "run1-trace/build-1.jsonl", "failure_class": None}]
    traces = {"run1-trace/build-1.jsonl": [{"type": "result", "is_error": True, "subtype": "error_max_budget_usd"}]}
    rows = stats_ingest.fill_failure_classes(calls, traces)
    assert rows[0]["failure_class"] == "budget_stop"


def test_fill_failure_classes_leaves_a_call_with_no_matching_trace_unset():
    calls = [{"trace_path": None, "failure_class": None}]
    rows = stats_ingest.fill_failure_classes(calls, {})
    assert rows[0]["failure_class"] is None


def _write_ledger(tmp_path, rows):
    path = tmp_path / "ledger.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def test_provider_profile_for_reads_the_ledgers_run_colon_node_keyed_row():
    rows = [{"key": "run1:build", "provider_profile": "vendor-a"}]
    assert stats_ingest.provider_profile_for("run1", rows) == "vendor-a"


def test_provider_profile_for_is_none_when_no_ledger_row_names_the_run():
    assert stats_ingest.provider_profile_for("run1", [{"key": "run2:build", "provider_profile": "vendor-a"}]) is None


def test_ingest_applies_the_join_only_where_the_counting_identity_holds(tmp_path):
    """Corpus-level coverage assertion: the fraction of calls carrying a non-NULL
    task_id must equal exactly what the identity predicts, not merely be > 0."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(
        runs_dir, "run1",
        tasks=[("p1", "t1", {"ticket": "t1", "fix_loop": {"attempts": 2}}), ("p1", "t2", {"ticket": "t2"})],
        usage={"calls": [{"role": "build"}, {"role": "build"}, {"role": "build"}, {"role": "review"}]},
    )
    _write_run(
        runs_dir, "run2",
        tasks=[("p1", "t3", {"ticket": "t3", "fix_loop": {"attempts": 5}})],
        usage={"calls": [{"role": "build"}, {"role": "build"}, {"role": "review"}]},
    )
    db_path = tmp_path / "stats.db"

    stats_ingest.ingest(runs_dir, db_path)

    conn = connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    joined = conn.execute("SELECT COUNT(*) FROM calls WHERE task_id IS NOT NULL").fetchone()[0]
    run1_task_ids = conn.execute(
        "SELECT task_id FROM calls WHERE run_id = 'run1' AND role = 'build' ORDER BY seq"
    ).fetchall()
    run2_confidence = conn.execute("SELECT DISTINCT join_confidence FROM calls WHERE run_id = 'run2'").fetchall()
    conn.close()

    assert (total, joined) == (7, 3)
    assert run1_task_ids == [("run1:p1:t1",), ("run1:p1:t1",), ("run1:p1:t2",)]
    assert run2_confidence == [("none",)]


def test_ingest_fills_failure_class_from_each_calls_own_trace_and_never_leaves_a_resulted_call_null(tmp_path):
    """Two failing calls in one run: a review call whose own trace shows a tool
    error (no budget signal in it at all) and a build call whose own trace shows
    a real budget stop, plus a run-level log line naming budget_stop that
    belongs to the build call, not the review one. A failure_class reader that
    shared the whole run's log across both calls, instead of scoping to each
    call's own trace, would bleed that log line onto the review call too and
    misclassify it budget_stop; it must read tool_error there instead."""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(
        runs_dir, "run1",
        usage={"calls": [
            {"role": "review", "trace": "run1-trace/review-1.jsonl"},
            {"role": "build", "trace": "run1-trace/build-1.jsonl"},
        ]},
        log_lines=["boom: error_max_budget_usd"],
    )
    _write_trace(
        runs_dir, "run1", "review-1.jsonl",
        [
            {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True}]}},
            {"type": "result", "is_error": True},
        ],
    )
    _write_trace(
        runs_dir, "run1", "build-1.jsonl",
        [{"type": "result", "is_error": True, "subtype": "error_max_budget_usd"}],
    )
    db_path = tmp_path / "stats.db"

    stats_ingest.ingest(runs_dir, db_path)

    conn = connect(db_path)
    rows = conn.execute("SELECT role, failure_class FROM calls WHERE run_id = 'run1' ORDER BY seq").fetchall()
    conn.close()
    assert rows == [("review", "tool_error"), ("build", "budget_stop")]


def test_ingest_fills_host_and_provider_profile_from_the_ledger_for_every_run(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "run1", tasks=[("p1", "t1", {"landed": True})])
    _write_run(runs_dir, "run2", tasks=[("p1", "t2", {"landed": True})])
    ledger = _write_ledger(
        tmp_path,
        [
            {"key": "run1:build", "provider_profile": "vendor-a"},
            {"key": "run2:build", "provider_profile": "vendor-b"},
        ],
    )
    db_path = tmp_path / "stats.db"

    stats_ingest.ingest(runs_dir, db_path, ledger_path=ledger)

    conn = connect(db_path)
    rows = conn.execute("SELECT run_id, host, provider_profile FROM runs ORDER BY run_id").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["run1", "run2"]
    assert all(r[1] is not None for r in rows)
    assert [r[2] for r in rows] == ["vendor-a", "vendor-b"]
