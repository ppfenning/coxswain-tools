import sqlite3

from agent_tools.stats_schema import (
    CALLS_COLUMNS,
    FAILURE_CLASSES,
    OUTCOMES,
    RUNS_COLUMNS,
    TASKS_COLUMNS,
    Column,
    column_ddl,
    connect,
    ensure_schema,
    table_sql,
)


def test_column_ddl_renders_a_not_null_integer_default():
    col = Column("challenger", "INTEGER", default=0, not_null=True)
    assert column_ddl(col) == "challenger INTEGER NOT NULL DEFAULT 0"


def test_column_ddl_renders_a_string_default():
    col = Column("vendor", "TEXT", default="claude-code")
    assert column_ddl(col) == "vendor TEXT DEFAULT 'claude-code'"


def test_runs_columns_match_spec_names_verbatim():
    assert [c.name for c in RUNS_COLUMNS] == [
        "run_id",
        "started_at",
        "ended_at",
        "cartridge_sha",
        "cartridge_team",
        "provider_profile",
        "vendor",
        "host",
        "launched_by",
        "principal",
        "human_minutes",
        "schema_version",
    ]


def test_calls_columns_match_spec_names_verbatim_and_keep_the_join_columns():
    assert [c.name for c in CALLS_COLUMNS] == [
        "run_id",
        "seq",
        "role",
        "attempt",
        "tier",
        "model",
        "cost_usd",
        "turns",
        "duration_ms",
        "input_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "output_tokens",
        "tools",
        "trace_path",
        "failure_class",
        "challenger",
        "task_id",
        "join_confidence",
    ]


def test_challenger_column_has_a_zero_default_and_is_not_null():
    challenger = next(c for c in CALLS_COLUMNS if c.name == "challenger")
    assert challenger.default == 0
    assert challenger.not_null is True


def test_tasks_columns_match_spec_names_verbatim():
    assert [c.name for c in TASKS_COLUMNS] == [
        "run_id",
        "task_id",
        "ticket",
        "phase",
        "initiative",
        "attempt",
        "outcome",
        "outcome_source",
        "review_rounds",
        "arbitration_verdict",
        "fix_loop_rounds",
        "cost_usd",
        "reason",
    ]


def test_outcomes_enum_is_the_closed_set():
    assert OUTCOMES == ("landed", "quarantined", "budget_stop", "skipped", "unknown")
    assert len(set(OUTCOMES)) == len(OUTCOMES)


def test_failure_classes_enum_is_the_closed_set():
    assert FAILURE_CLASSES == (
        "budget_stop",
        "tool_error",
        "empty_patch",
        "refused",
        "ok",
        "unknown",
    )
    assert len(set(FAILURE_CLASSES)) == len(FAILURE_CLASSES)


def test_table_sql_renders_the_runs_table_with_a_primary_key():
    sql = table_sql("runs", RUNS_COLUMNS, "run_id")
    assert sql.startswith("CREATE TABLE IF NOT EXISTS runs (")
    assert "run_id TEXT" in sql
    assert "PRIMARY KEY (run_id)" in sql


def test_connect_creates_all_three_tables(tmp_path):
    conn = connect(tmp_path / "stats.db")
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"runs", "calls", "tasks"}.issubset(names)


def test_ensure_schema_adds_a_missing_column_with_its_default_without_dropping_rows(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "stats.db"))
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT)")
    conn.execute("INSERT INTO runs (run_id, started_at) VALUES ('r1', 't0')")
    conn.commit()

    ensure_schema(conn)

    row = conn.execute("SELECT run_id, vendor FROM runs WHERE run_id = 'r1'").fetchone()
    assert row == ("r1", "claude-code")


def test_a_calls_row_inserted_without_challenger_reads_back_zero_not_null(tmp_path):
    conn = connect(tmp_path / "stats.db")
    conn.execute("INSERT INTO calls (run_id, seq, role) VALUES ('r1', 1, 'build')")
    conn.commit()

    value = conn.execute("SELECT challenger FROM calls WHERE run_id = 'r1'").fetchone()[0]

    assert value == 0
    assert value is not None
