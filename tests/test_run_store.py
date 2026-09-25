import json
import os
import sqlite3
import sys
import types

import pytest

from agent_tools import epic, run_store

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


ENDED = "2026-09-25T05:03:46.812070+00:00"


def run_row(run_id, ended_at=ENDED):
    return {"run_id": run_id, "launched_at": "2026-09-25T04:59:39.238479+00:00", "ended_at": ended_at, "status": None if ended_at is None else "ok"}


def test_run_ids_lists_every_id_in_the_runs_table(tmp_path):
    runs_table(tmp_path, run_row("a-1"), run_row("a-3", ended_at=None))
    assert run_store.run_ids(tmp_path) == {"a-1", "a-3"}


def test_run_ids_is_empty_with_no_store_or_no_runs_table(tmp_path):
    assert run_store.run_ids(tmp_path) == set()
    store(tmp_path, ROW)
    assert run_store.run_ids(tmp_path) == set()


def test_the_file_is_returned_unchanged_even_when_the_store_has_rows(tmp_path):
    body = {"run_id": "r1", "calls": [], "summary": {"calls": 99}}
    (tmp_path / "r1.usage.json").write_text(json.dumps(body))
    store(tmp_path, ROW)
    assert run_store.usage(tmp_path, "r1") == body


def test_the_store_answers_when_the_file_is_absent_and_its_summary_matches(tmp_path):
    store(tmp_path, ROW, {**ROW, "call_id": "c2", "seq": 2, "model_alias": "opus", "cost_usd": 1.0})
    runs_table(tmp_path, run_row("r1"))
    got = run_store.usage(tmp_path, "r1")
    assert got["run_id"] == "r1"
    assert [c["model"] for c in got["calls"]] == ["haiku", "opus"]
    assert got["summary"] == run_store.summarize(got["calls"])
    assert got["summary"]["calls"] == 2


def test_the_store_answers_when_the_file_is_not_a_json_object(tmp_path):
    (tmp_path / "r1.usage.json").write_text("[1, 2]")
    store(tmp_path, ROW)
    runs_table(tmp_path, run_row("r1"))
    assert run_store.usage(tmp_path, "r1")["calls"][0]["id"] == "c3a75b39"


def test_calls_come_back_ordered_by_ts_then_seq(tmp_path):
    store(
        tmp_path,
        {**ROW, "call_id": "b", "seq": 2},
        {**ROW, "call_id": "a", "seq": 1},
        {**ROW, "call_id": "z", "seq": 9, "ts": "2026-09-25T00:00:00+00:00"},
    )
    runs_table(tmp_path, run_row("r1"))
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


def test_detail_json_carries_its_summary_and_commands_run_onto_the_call():
    detail = {"summary": {"result": "success"}, "commands_run": [{"command": "ls"}], "other": 1}
    got = run_store.call_from_row(ROW | {"detail_json": json.dumps(detail)})
    assert got["summary"] == {"result": "success"} and got["commands_run"] == [{"command": "ls"}]
    assert "other" not in got


@pytest.mark.parametrize("detail", [None, "[1, 2]", "not json", "{}"])
def test_a_row_with_no_usable_detail_json_is_unchanged(detail):
    assert run_store.call_from_row(ROW | {"detail_json": detail}) == run_store.call_from_row(ROW)
    assert "summary" not in run_store.call_from_row(ROW | {"detail_json": detail})


def test_a_stored_row_carries_its_detail_through_usage(tmp_path):
    store(tmp_path, ROW | {"detail_json": json.dumps({"summary": {"result": "success"}})})
    runs_table(tmp_path, run_row("r1"))
    assert run_store.usage(tmp_path, "r1")["calls"][0]["summary"] == {"result": "success"}


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
    runs_table(tmp_path, run_row("r1"))
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
    runs_table(tmp_path, run_row("r1"))
    assert run_store.usages(tmp_path)["r1"]["calls"][0]["id"] == "c3a75b39"


def test_a_live_run_with_calls_and_no_ended_at_is_none_and_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(epic, "run_live", lambda pid, pidfile, log=None: True)
    store(tmp_path, ROW, {**ROW, "call_id": "c9", "run_id": "r2"})
    runs_table(tmp_path, run_row("r1", None), run_row("r2", None))
    (tmp_path / "r1.pid").write_text("4242")
    (tmp_path / "r2.pid").write_text("4243")
    assert run_store.usage(tmp_path, "r1") is None
    assert run_store.usages(tmp_path) == {}


def test_a_run_with_no_ended_at_and_no_pidfile_reads_as_ended(tmp_path):
    store(tmp_path, ROW)
    runs_table(tmp_path, run_row("r1", None))
    assert [c["id"] for c in run_store.usage(tmp_path, "r1")["calls"]] == ["c3a75b39"]


def test_a_pidfile_decides_by_run_live(tmp_path, monkeypatch):
    store(tmp_path, ROW)
    runs_table(tmp_path, run_row("r1", None))
    (tmp_path / "r1.pid").write_text("4242\n")
    seen = []
    monkeypatch.setattr(epic, "run_live", lambda pid, pidfile, log=None: seen.append((pid, pidfile)) or True)
    assert run_store.usage(tmp_path, "r1") is None
    assert seen == [(4242, tmp_path / "r1.pid")]
    monkeypatch.setattr(epic, "run_live", lambda pid, pidfile, log=None: False)
    assert [c["id"] for c in run_store.usage(tmp_path, "r1")["calls"]] == ["c3a75b39"]


def test_a_pidfile_that_is_not_an_int_reads_as_ended(tmp_path, monkeypatch):
    monkeypatch.setattr(epic, "run_live", lambda pid, pidfile, log=None: True)
    store(tmp_path, ROW)
    runs_table(tmp_path, run_row("r1", None))
    (tmp_path / "r1.pid").write_text("not-a-pid")
    assert run_store.usage(tmp_path, "r1")["calls"][0]["id"] == "c3a75b39"


def test_usages_lists_the_dead_run_and_omits_the_live_one_checking_each_run_once(tmp_path, monkeypatch):
    store(tmp_path, ROW, {**ROW, "call_id": "c2", "seq": 2}, {**ROW, "call_id": "c9", "run_id": "r2"})
    runs_table(tmp_path, run_row("r1", None), run_row("r2", None))
    (tmp_path / "r1.pid").write_text("1")
    (tmp_path / "r2.pid").write_text("2")
    seen = []
    monkeypatch.setattr(epic, "run_live", lambda pid, pidfile, log=None: seen.append(pid) or pid == 2)
    got = run_store.usages(tmp_path)
    assert list(got) == ["r1"] and len(got["r1"]["calls"]) == 2
    assert sorted(seen) == [1, 2]


def test_a_run_with_calls_and_no_runs_row_is_none_and_left_out(tmp_path):
    store(tmp_path, ROW)
    runs_table(tmp_path, run_row("other"))
    assert run_store.usage(tmp_path, "r1") is None
    assert run_store.usages(tmp_path) == {}


def test_the_same_run_is_returned_once_ended_at_is_set(tmp_path, monkeypatch):
    monkeypatch.setattr(epic, "run_live", lambda pid, pidfile, log=None: True)
    store(tmp_path, ROW)
    runs_table(tmp_path, run_row("r1", None))
    (tmp_path / "r1.pid").write_text("4242")
    assert run_store.usage(tmp_path, "r1") is None
    conn = sqlite3.connect(tmp_path / "cox.db")
    conn.execute("UPDATE runs SET ended_at = ?, status = 'ok' WHERE run_id = 'r1'", (ENDED,))
    conn.commit()
    conn.close()
    got = run_store.usage(tmp_path, "r1")
    assert [c["id"] for c in got["calls"]] == ["c3a75b39"]
    assert run_store.usages(tmp_path) == {"r1": got}


def test_a_usage_file_is_returned_whatever_the_store_says(tmp_path):
    body = {"run_id": "r1", "calls": [], "summary": {"calls": 99}}
    (tmp_path / "r1.usage.json").write_text(json.dumps(body))
    store(tmp_path, ROW)
    runs_table(tmp_path, run_row("r1", None))
    assert run_store.usage(tmp_path, "r1") == body
    assert run_store.usages(tmp_path) == {"r1": body}


def test_run_started_reads_launched_at(tmp_path):
    runs_table(tmp_path, RUN)
    assert run_store.run_started(tmp_path, "storage-sqlite-run-records-14") == "2026-09-25T04:30:00.123456+00:00"


def test_run_started_is_none_for_a_run_the_store_does_not_have(tmp_path):
    runs_table(tmp_path, RUN)
    assert run_store.run_started(tmp_path, "other") is None
    assert run_store.run_started(tmp_path / "missing", "other") is None


def day_file(runs_dir, day, run_id, *frames):
    """Write one zstd file, one frame per append, as graphs does: `traces/YYYY/MM/DD/<run_id>.jsonl.zst`."""
    import zstandard

    def frame(rows):
        text = "".join(json.dumps({"run_id": run_id, **r}, sort_keys=True, separators=(",", ":")) + "\n" for r in rows)
        return zstandard.ZstdCompressor().compress(text.encode())

    path = runs_dir / "traces" / day / f"{run_id}.jsonl.zst"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(frame(rows) for rows in frames))


def test_call_events_prefers_the_loose_file_and_skips_a_line_that_is_not_an_object(tmp_path):
    loose = tmp_path / "r1-trace" / "scope_epic-1.jsonl"
    loose.parent.mkdir()
    loose.write_text('{"type":"a"}\nnot json\n[1]\n\n{"type":"b"}\n')
    (tmp_path / "traces").mkdir()
    assert run_store.call_events(tmp_path, "r1", {"id": "c1", "trace": str(loose)}) == [{"type": "a"}, {"type": "b"}]


def test_call_events_reads_the_store_in_seq_order_across_days_ignoring_other_calls(tmp_path):
    pytest.importorskip("zstandard")

    def row(call, seq, name):
        return {"call_id": call, "seq": seq, "event": {"n": name}}

    day_file(tmp_path, "2026/09/24", "r1", [row("c1", 1, "b"), row("c2", 0, "x")], [row("c1", 0, "a")])
    day_file(tmp_path, "2026/09/25", "r1", [row("c1", 3, "d"), row("c1", 2, "c")])
    day_file(tmp_path, "2026/09/25", "r2", [row("c1", 0, "other run")])
    missing = {"id": "c1", "trace": str(tmp_path / "gone.jsonl")}
    assert run_store.call_events(tmp_path, "r1", missing) == [{"n": "a"}, {"n": "b"}, {"n": "c"}, {"n": "d"}]
    assert run_store.call_events(tmp_path, "r1", {"id": "c2"}) == [{"n": "x"}]


def test_call_events_store_glue_runs_without_zstandard_against_a_stand_in_decompressor(tmp_path, monkeypatch):
    """Runs where zstandard is absent: the day files hold plain lines and the stand-in passes bytes through."""
    stand_in = types.SimpleNamespace(
        ZstdDecompressor=lambda: types.SimpleNamespace(stream_reader=lambda fh, read_across_frames: fh)
    )
    monkeypatch.setitem(sys.modules, "zstandard", stand_in)
    for day, seqs in (("2026/09/24", (1, 0)), ("2026/09/25", (2,))):
        path = tmp_path / "traces" / day / "r1.jsonl.zst"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"run_id": "r1", "call_id": "c1", "seq": s, "event": {"n": s}} for s in seqs]
        rows.append({"run_id": "r1", "call_id": "c2", "seq": 0, "event": {"n": "other"}})
        path.write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows))
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": 0}, {"n": 1}, {"n": 2}]


def test_call_events_is_none_with_no_loose_file_and_no_trace_store(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "zstandard", None)
    assert run_store.call_events(tmp_path, "r1", {"id": "c1", "trace": str(tmp_path / "gone.jsonl")}) is None
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) is None


def test_call_events_without_zstandard_raises_naming_the_extra(tmp_path, monkeypatch):
    (tmp_path / "traces").mkdir()
    monkeypatch.setitem(sys.modules, "zstandard", None)
    with pytest.raises(run_store.TracesUnavailable, match=r"coxswain-tools\[traces\]"):
        run_store.call_events(tmp_path, "r1", {"id": "c1"})


PHASES_COLUMNS = (
    "run_id TEXT, phase_id TEXT, ts TEXT, principal TEXT, human_minutes REAL, totals_json TEXT, record_json TEXT, "
    "PRIMARY KEY (run_id, phase_id)"
)


def manifest(run_id, phase, ts="2026-09-25T04:40:00+00:00"):
    return {
        "cartridge_sha": "abc123", "cartridge_team": "pat", "gate_diffs": [], "human_minutes": 1.5, "overlay_sha": None,
        "principal": "epic-swarm", "proposals": [], "provider_profile": "default", "run_id": f"{run_id}:{phase}",
        "totals": {"cost_usd": 0.1}, "ts": ts,
    }


def phase_row(run_id, phase, ts, record=None):
    record = {"phase": phase, "status": "ok", "ts": ts} if record is None else record
    return {"run_id": run_id, "phase_id": phase, "ts": ts, "record_json": json.dumps(record)}


def with_record(run_id, phase, ts):
    record = {"phase": phase, "status": "ok", "ts": ts, "manifest": f"{run_id}:{phase}", "manifest_record": manifest(run_id, phase, ts)}
    return phase_row(run_id, phase, ts, record)


def phases_table(runs_dir, *rows):
    conn = sqlite3.connect(runs_dir / "cox.db")
    conn.execute(f"CREATE TABLE phases ({PHASES_COLUMNS})")
    for row in rows:
        conn.execute(f"INSERT INTO phases ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    conn.close()


def test_manifest_files_win_over_the_store_and_come_back_sorted_by_file_name(tmp_path):
    for phase in ("scope", "build", "plan"):
        (tmp_path / f"r1:{phase}.json").write_text(json.dumps(manifest("r1", phase)))
    (tmp_path / "r1:broken.json").write_text("[1, 2]")
    (tmp_path / "r1.usage.json").write_text(json.dumps({"run_id": "r1"}))
    runs_table(tmp_path, run_row("r1"))
    phases_table(tmp_path, with_record("r1", "store", "2026-09-25T04:41:00+00:00"))
    got = run_store.phase_manifests(tmp_path, "r1")
    assert [m["run_id"] for m in got] == ["r1:build", "r1:plan", "r1:scope"]


def test_the_store_answers_when_no_file_exists_ordered_by_row_ts(tmp_path):
    runs_table(tmp_path, run_row("r1"))
    phases_table(
        tmp_path,
        with_record("r1", "a-late", "2026-09-25T04:50:00+00:00"),
        with_record("r1", "z-early", "2026-09-25T04:40:00+00:00"),
    )
    got = run_store.phase_manifests(tmp_path, "r1")
    assert [m["run_id"] for m in got] == ["r1:z-early", "r1:a-late"]


def test_the_store_answers_for_a_run_that_has_not_ended_and_for_one_with_no_runs_row(tmp_path):
    runs_table(tmp_path, run_row("r1", ended_at=None))
    phases_table(tmp_path, with_record("r1", "plan", "2026-09-25T04:40:00+00:00"), with_record("r2", "plan", "2026-09-25T04:40:00+00:00"))
    assert run_store.phase_manifests(tmp_path, "r1") == [manifest("r1", "plan")]
    assert [m["run_id"] for m in run_store.phase_manifests(tmp_path, "r2")] == ["r2:plan"]


def test_phase_names_come_from_files_oldest_mtime_first(tmp_path):
    for phase, mtime in (("a-new", 2000), ("z-old", 1000)):
        path = tmp_path / f"r1:{phase}.json"
        path.write_text("{}")
        os.utime(path, (mtime, mtime))
    (tmp_path / "r2:other.json").write_text("{}")
    assert run_store.phase_names(tmp_path, "r1") == ["z-old", "a-new"]


def test_phase_names_come_from_store_rows_by_ts_for_a_run_with_no_ended_at(tmp_path):
    runs_table(tmp_path, run_row("r1", ended_at=None))
    phases_table(
        tmp_path,
        phase_row("r1", "a-late", "2026-09-25T04:50:00+00:00"),
        phase_row("r1", "z-early", "2026-09-25T04:40:00+00:00"),
        phase_row("r2", "other", "2026-09-25T04:30:00+00:00"),
    )
    assert run_store.phase_names(tmp_path, "r1") == ["z-early", "a-late"]


def test_phase_names_files_win_over_the_store(tmp_path):
    (tmp_path / "r1:file-phase.json").write_text("{}")
    phases_table(tmp_path, phase_row("r1", "store-phase", "2026-09-25T04:40:00+00:00"))
    assert run_store.phase_names(tmp_path, "r1") == ["file-phase"]


def test_phase_names_with_neither_files_nor_store_is_empty(tmp_path):
    assert run_store.phase_names(tmp_path, "r1") == []


def test_a_phase_row_without_manifest_record_is_skipped(tmp_path):
    runs_table(tmp_path, run_row("r1"))
    phases_table(
        tmp_path,
        phase_row("r1", "plan", "2026-09-25T04:40:00+00:00"),
        with_record("r1", "build", "2026-09-25T04:41:00+00:00"),
    )
    assert [m["run_id"] for m in run_store.phase_manifests(tmp_path, "r1")] == ["r1:build"]


def test_no_files_and_no_store_return_empty(tmp_path):
    assert run_store.phase_manifests(tmp_path, "r1") == []
    assert run_store.all_phase_manifests(tmp_path) == {}


def test_all_phase_manifests_lists_file_runs_and_store_only_runs_once_each(tmp_path):
    (tmp_path / "r1:plan.json").write_text(json.dumps(manifest("r1", "plan")))
    runs_table(tmp_path, run_row("r1"), run_row("r2"), run_row("r3", ended_at=None))
    phases_table(
        tmp_path,
        with_record("r1", "store-only-phase", "2026-09-25T04:40:00+00:00"),
        with_record("r2", "build", "2026-09-25T04:41:00+00:00"),
        with_record("r3", "build", "2026-09-25T04:42:00+00:00"),
    )
    got = run_store.all_phase_manifests(tmp_path)
    assert sorted(got) == ["r1", "r2", "r3"]
    assert [m["run_id"] for m in got["r1"]] == ["r1:plan"]
    assert [m["run_id"] for m in got["r2"]] == ["r2:build"]
    assert [m["run_id"] for m in got["r3"]] == ["r3:build"]


def _three_runs(tmp_path):
    store(tmp_path, ROW, {**ROW, "call_id": "c2", "run_id": "r2"}, {**ROW, "call_id": "c3", "run_id": "r3"})
    runs_table(
        tmp_path,
        run_row("r1", "2026-09-01T00:00:00+00:00"),
        run_row("r2", "2026-09-20T00:00:00+00:00"),
        run_row("r3", None),
    )


def test_store_usages_never_checks_an_excluded_run(tmp_path, monkeypatch):
    _three_runs(tmp_path)
    seen = []
    real = run_store._run_ended
    monkeypatch.setattr(run_store, "_run_ended", lambda d, rid, ended: seen.append(rid) or real(d, rid, ended))
    got = run_store.store_usages(tmp_path, exclude={"r1", "r3"})
    assert list(got) == ["r2"]
    assert seen == ["r2"]


def test_store_usages_since_drops_a_run_that_ended_before_it_and_keeps_the_rest(tmp_path):
    _three_runs(tmp_path)
    assert list(run_store.store_usages(tmp_path)) == ["r1", "r2", "r3"]
    assert list(run_store.store_usages(tmp_path, since="2026-09-10T00:00:00+00:00")) == ["r2", "r3"]


def test_usages_equals_the_files_plus_every_store_run_without_a_file(tmp_path):
    _three_runs(tmp_path)
    body = {"run_id": "r2", "calls": [], "summary": {"calls": 99}}
    (tmp_path / "r2.usage.json").write_text(json.dumps(body))
    rest = {rid: run_store._store_usage(rid, calls) for rid, calls in run_store._store_runs(tmp_path).items() if rid != "r2"}
    assert run_store.usages(tmp_path) == {"r2": body, **rest}
    assert list(run_store.usages(tmp_path)) == ["r2", "r1", "r3"]
