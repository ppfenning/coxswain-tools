import json
import sqlite3

import pytest

from agent_tools import run_store

COLUMNS = (
    "call_id TEXT PRIMARY KEY, run_id TEXT, seq INTEGER, phase_id TEXT, task_id TEXT, role TEXT, tier TEXT, "
    "model_alias TEXT, model_id TEXT, claude_code_version TEXT, cost_usd REAL, ceiling_usd REAL, "
    "ceiling_source TEXT, turns INTEGER, duration_ms INTEGER, input_tokens INTEGER, cache_read_tokens INTEGER, "
    "cache_creation_tokens INTEGER, input_total INTEGER, output_tokens INTEGER, ok BOOL, ts TEXT, "
    "requested_tier TEXT, chosen_tier TEXT, decision_reason TEXT, router_tier TEXT, router_reason TEXT, "
    "ticket_key TEXT, outcome_key TEXT, system_one_prediction TEXT, system_one_confidence REAL, "
    "decision_json TEXT, detail_json TEXT"
)

ROW = {
    "call_id": "c3a75b39", "run_id": "r1", "seq": 1, "task_id": None, "role": "scope_epic", "tier": "cheap",
    "model_alias": "haiku", "model_id": "claude-haiku-4-5-20251001", "cost_usd": 0.05987000000000001,
    "ceiling_usd": 0.15, "ceiling_source": "profile", "turns": 2, "duration_ms": 79013, "input_tokens": 10,
    "cache_read_tokens": 0, "cache_creation_tokens": 11595, "input_total": 11605, "output_tokens": 7334,
    "ok": 1, "ts": "2026-09-25T04:34:43.390253+00:00", "decision_json": None,
}


def store(runs_dir, *rows):
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute(f"CREATE TABLE node_calls ({COLUMNS})")
    for row in rows:
        conn.execute(f"INSERT INTO node_calls ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    conn.close()


RUNS_COLUMNS = (
    "run_id TEXT PRIMARY KEY, principal TEXT, launched_by TEXT, launched_at TEXT, cartridge_sha TEXT, "
    "cartridge_team TEXT, overlay_sha TEXT, provider_profile TEXT, started_at TEXT, ended_at TEXT, "
    "status TEXT, record_json TEXT"
)

RUN = {
    "run_id": "storage-sqlite-run-records-14", "principal": "epic-swarm(lifecycle-propose)", "launched_by": "cli",
    "launched_at": "2026-09-25T04:30:00.123456+00:00",
}


def runs_table(runs_dir, *rows):
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute(f"CREATE TABLE runs ({RUNS_COLUMNS})")
    for row in rows:
        conn.execute(f"INSERT INTO runs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    conn.close()


def test_the_file_is_returned_unchanged_even_when_the_store_has_rows(tmp_path):
    body = {"run_id": "r1", "calls": [], "summary": {"calls": 99}}
    (tmp_path / "r1.usage.json").write_text(json.dumps(body))
    store(tmp_path, ROW)
    assert run_store.usage(tmp_path, "r1") == body


def test_the_store_answers_when_the_file_is_absent_and_its_summary_matches(tmp_path):
    store(tmp_path, ROW, {**ROW, "call_id": "c2", "seq": 2, "model_alias": "opus", "cost_usd": 1.0})
    got = run_store.usage(tmp_path, "r1")
    assert got["run_id"] == "r1"
    assert [c["model"] for c in got["calls"]] == ["haiku", "opus"]
    assert got["summary"] == run_store.summarize(got["calls"])
    assert got["summary"]["calls"] == 2


def test_the_store_answers_when_the_file_is_not_a_json_object(tmp_path):
    (tmp_path / "r1.usage.json").write_text("[1, 2]")
    store(tmp_path, ROW)
    assert run_store.usage(tmp_path, "r1")["calls"][0]["id"] == "c3a75b39"


def test_calls_come_back_ordered_by_ts_then_seq(tmp_path):
    store(
        tmp_path,
        {**ROW, "call_id": "b", "seq": 2},
        {**ROW, "call_id": "a", "seq": 1},
        {**ROW, "call_id": "z", "seq": 9, "ts": "2026-09-25T00:00:00+00:00"},
    )
    assert [c["id"] for c in run_store.usage(tmp_path, "r1")["calls"]] == ["z", "a", "b"]


def test_another_runs_rows_are_not_returned(tmp_path):
    store(tmp_path, {**ROW, "run_id": "other"})
    assert run_store.usage(tmp_path, "r1") is None


def test_neither_source_gives_none(tmp_path):
    assert run_store.usage(tmp_path, "r1") is None


def test_a_store_without_node_calls_gives_none(tmp_path):
    sqlite3.connect(tmp_path / "cox.db").close()
    assert run_store.usage(tmp_path, "r1") is None


def test_a_row_becomes_the_usage_file_call():
    assert run_store.call_from_row(ROW | {"decision_json": None}) == {
        "role": "scope_epic", "task_id": None, "tier": "cheap", "model": "haiku", "cost_usd": 0.05987000000000001,
        "ceiling_usd": 0.15, "ceiling_source": "profile", "turns": 2, "duration_ms": 79013, "input_tokens": 10,
        "cache_read_tokens": 0, "cache_creation_tokens": 11595, "input_total": 11605, "output_tokens": 7334,
        "id": "c3a75b39", "ts": "2026-09-25T04:34:43.390253+00:00", "ok": True, "decision": None,
    }


def test_a_set_decision_json_parses():
    assert run_store.call_from_row(ROW | {"decision_json": '{"tier": "cheap"}'})["decision"] == {"tier": "cheap"}


@pytest.mark.parametrize(("stored", "expected"), [(1, True), (0, False)])
def test_ok_becomes_a_bool(stored, expected):
    assert run_store.call_from_row(ROW | {"ok": stored})["ok"] is expected


def test_summarize_totals_and_groups_by_model():
    calls = [
        {"model": "haiku", "cost_usd": 0.5, "turns": 2, "input_total": 10, "input_tokens": 1, "output_tokens": 3},
        {"model": "opus", "cost_usd": 1.0, "turns": 1, "input_tokens": 7, "output_tokens": 4},
    ]
    got = run_store.summarize(calls)
    assert (got["calls"], got["cost_usd"], got["turns"], got["input_total"], got["output_tokens"]) == (2, 1.5, 3, 17, 7)
    assert got["by_model"]["opus"]["input_total"] == 7


def test_connect_readonly_returns_none_without_a_store_and_creates_nothing(tmp_path):
    assert run_store.connect_readonly(tmp_path) is None
    assert not (tmp_path / "cox.db").exists()


def test_connect_readonly_refuses_writes(tmp_path):
    store(tmp_path, ROW)
    conn = run_store.connect_readonly(tmp_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM node_calls")
    conn.close()


def test_usages_lists_file_runs_and_store_only_runs(tmp_path):
    body = {"run_id": "f1", "calls": [], "summary": {"calls": 0}}
    (tmp_path / "f1.usage.json").write_text(json.dumps(body))
    store(tmp_path, ROW)
    got = run_store.usages(tmp_path)
    assert list(got) == ["f1", "r1"]
    assert got["f1"] == body
    assert got["r1"] == run_store.usage(tmp_path, "r1")


def test_usages_takes_a_run_in_both_from_its_file(tmp_path):
    body = {"run_id": "r1", "calls": [], "summary": {"calls": 99}}
    (tmp_path / "r1.usage.json").write_text(json.dumps(body))
    store(tmp_path, ROW)
    assert run_store.usages(tmp_path) == {"r1": body}


def test_usages_without_a_database_lists_the_files_only(tmp_path):
    (tmp_path / "f1.usage.json").write_text('{"run_id": "f1"}')
    (tmp_path / "bad.usage.json").write_text("[1]")
    assert run_store.usages(tmp_path) == {"f1": {"run_id": "f1"}}
    assert not (tmp_path / "cox.db").exists()


def test_usages_lets_the_store_answer_for_a_file_that_is_not_an_object(tmp_path):
    (tmp_path / "r1.usage.json").write_text("[1, 2]")
    store(tmp_path, ROW)
    assert run_store.usages(tmp_path)["r1"]["calls"][0]["id"] == "c3a75b39"


def test_run_started_reads_launched_at(tmp_path):
    runs_table(tmp_path, RUN)
    assert run_store.run_started(tmp_path, "storage-sqlite-run-records-14") == "2026-09-25T04:30:00.123456+00:00"


def test_run_started_is_none_for_a_run_the_store_does_not_have(tmp_path):
    runs_table(tmp_path, RUN)
    assert run_store.run_started(tmp_path, "other") is None
    assert run_store.run_started(tmp_path / "missing", "other") is None
