"""`call_events` reads a run's Parquet trace first, then the zst day files, then the loose file."""

import json
import sys
import types

import pytest

from agent_tools import run_store
from agent_tools.store_url import TracesRoot


def row(call, seq, event, run="r1"):
    return {"run_id": run, "call_id": call, "seq": seq, "day": "2026-09-25", "type": "t", "subtype": None, "tool": None,
            "event": event if isinstance(event, str) else json.dumps(event)}


def write_parquet(runs_dir, rows, run="r1", day="2026/09/25"):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = runs_dir / "traces" / day / f"{run}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def stand_in_zst(runs_dir, monkeypatch, rows, run="r1", day="2026/09/25"):
    """A zst day file the stand-in decompressor passes through as plain lines, so this runs without zstandard."""
    stand_in = types.SimpleNamespace(
        ZstdDecompressor=lambda: types.SimpleNamespace(stream_reader=lambda fh, read_across_frames: fh)
    )
    monkeypatch.setitem(sys.modules, "zstandard", stand_in)
    path = runs_dir / "traces" / day / f"{run}.jsonl.zst"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"run_id": run, **r}) + "\n" for r in rows))


@pytest.fixture(autouse=True)
def _no_routing_profile(tmp_path, monkeypatch):
    """A machine's own routing profile must not point these tests at its traces."""
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(tmp_path / "no-such-profile.yaml"))


def profile_with(tmp_path, monkeypatch, provider_lines):
    provider = tmp_path / "provider.yaml"
    provider.write_text(provider_lines)
    routing = tmp_path / "routing.yaml"
    routing.write_text(f"provider_profile: {provider}\n")
    monkeypatch.setenv("AGENT_TOOLS_PROFILE", str(routing))


def test_the_pure_part_filters_by_call_id_orders_by_seq_and_decodes_the_event_text():
    rows = [row("c1", 2, {"n": "c"}), row("c2", 0, {"n": "x"}), row("c1", 0, {"n": "a"}), row("c1", 1, {"n": "b"})]
    assert run_store._parquet_call_events(rows, "c1") == [{"n": "a"}, {"n": "b"}, {"n": "c"}]
    assert run_store._parquet_call_events(rows, "none") == []


def test_the_pure_part_drops_an_event_that_is_not_a_json_object():
    rows = [row("c1", 0, "not json"), row("c1", 1, "[1]"), row("c1", 2, {"n": "kept"}), {"call_id": "c1", "seq": 3, "event": None}]
    assert run_store._parquet_call_events(rows, "c1") == [{"n": "kept"}]


def test_call_events_reads_the_parquet_file_filtered_by_call_and_ordered_by_seq(tmp_path):
    write_parquet(tmp_path, [row("c1", 1, {"n": "b"}), row("c2", 0, {"n": "x"}), row("c1", 0, {"n": "a"})])
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "a"}, {"n": "b"}]
    assert run_store.call_events(tmp_path, "r1", {"id": "c2"}) == [{"n": "x"}]


def test_a_parquet_file_wins_over_a_zst_file_and_a_loose_file(tmp_path, monkeypatch):
    write_parquet(tmp_path, [row("c1", 0, {"n": "parquet"})])
    stand_in_zst(tmp_path, monkeypatch, [{"call_id": "c1", "seq": 0, "event": {"n": "zst"}}])
    loose = tmp_path / "loose.jsonl"
    loose.write_text('{"n":"loose"}\n')
    assert run_store.call_events(tmp_path, "r1", {"id": "c1", "trace": str(loose)}) == [{"n": "parquet"}]


def test_a_parquet_file_with_no_rows_for_the_call_falls_through_to_zst(tmp_path, monkeypatch):
    write_parquet(tmp_path, [row("c2", 0, {"n": "other"})])
    stand_in_zst(tmp_path, monkeypatch, [{"call_id": "c1", "seq": 0, "event": {"n": "zst"}}])
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "zst"}]


def test_with_no_parquet_file_call_events_falls_through_to_the_zst_file(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    stand_in_zst(tmp_path, monkeypatch, [{"call_id": "c1", "seq": 1, "event": {"n": "b"}}, {"call_id": "c1", "seq": 0, "event": {"n": "a"}}])
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "a"}, {"n": "b"}]


def test_with_no_parquet_and_no_zst_events_call_events_falls_through_to_the_loose_file(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    stand_in_zst(tmp_path, monkeypatch, [{"call_id": "other", "seq": 0, "event": {"n": "x"}}])
    loose = tmp_path / "loose.jsonl"
    loose.write_text('{"n":"loose"}\nnot json\n')
    assert run_store.call_events(tmp_path, "r1", {"id": "c1", "trace": str(loose)}) == [{"n": "loose"}]


def test_with_no_parquet_file_pyarrow_is_never_needed_and_nothing_found_is_none(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) is None


def test_a_parquet_file_without_pyarrow_raises_naming_the_parquet_extra(tmp_path, monkeypatch):
    path = tmp_path / "traces" / "2026" / "09" / "25" / "r1.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    with pytest.raises(run_store.TracesUnavailable, match=r"coxswain-tools\[parquet\]"):
        run_store.call_events(tmp_path, "r1", {"id": "c1"})


def test_an_s3_root_without_pyarrow_cannot_be_probed_and_raises_the_same_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    with pytest.raises(run_store.TracesUnavailable, match=r"coxswain-tools\[parquet\]"):
        run_store._parquet_rows(TracesRoot("s3://bucket/traces", True), "r1")


def test_parquet_readable_reports_pyarrow_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    assert run_store.parquet_readable(TracesRoot(str(tmp_path), False)) == run_store.ParquetCheck(False, "pyarrow missing")


def test_parquet_readable_is_ok_for_a_local_directory_and_unreachable_for_a_missing_one(tmp_path):
    pytest.importorskip("pyarrow.fs")
    assert run_store.parquet_readable(TracesRoot(str(tmp_path), False)) == run_store.ParquetCheck(True, "ok")
    assert run_store.parquet_readable(TracesRoot(str(tmp_path / "gone"), False)) == run_store.ParquetCheck(False, "root unreachable")


def test_a_provider_profile_traces_url_outside_the_runs_dir_is_where_parquet_events_are_read(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    write_parquet(elsewhere, [row("c1", 0, {"n": "profile"})])
    profile_with(tmp_path, monkeypatch, f"traces_url: {elsewhere / 'traces'}\n")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    assert run_store.call_events(runs_dir, "r1", {"id": "c1"}) == [{"n": "profile"}]


def test_with_no_traces_url_the_runs_dir_traces_directory_is_read(tmp_path, monkeypatch):
    write_parquet(tmp_path, [row("c1", 0, {"n": "local"})])
    profile_with(tmp_path, monkeypatch, "storage_url: sqlite:///unused.db\n")
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "local"}]


def test_two_calls_of_one_run_read_the_parquet_file_once(tmp_path, monkeypatch):
    pq = pytest.importorskip("pyarrow.parquet")
    write_parquet(tmp_path, [row("c1", 0, {"n": "a"}), row("c2", 0, {"n": "b"})])
    reads = []
    real = pq.read_table
    monkeypatch.setattr(pq, "read_table", lambda *a, **k: reads.append(a) or real(*a, **k))
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "a"}]
    assert run_store.call_events(tmp_path, "r1", {"id": "c2"}) == [{"n": "b"}]
    assert len(reads) == 1


def test_a_run_with_no_parquet_file_is_found_once_the_file_appears(tmp_path):
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) is None
    write_parquet(tmp_path, [row("c1", 0, {"n": "late"})])
    assert run_store.call_events(tmp_path, "r1", {"id": "c1"}) == [{"n": "late"}]
