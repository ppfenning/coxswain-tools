import logging
import sqlite3
import sys
from pathlib import Path

from agent_tools import run_store
from agent_tools.queue_export import Hashes, export_file, export_files, is_ahead_of_store, text_hash

ROW = {
    "kind": "task",
    "initiative": "queue",
    "task_id": "codec",
    "phase": "q",
    "state": "doing",
    "needs": [],
    "title": "Codec: rows",
    "surfaces": [],
    "body": "Body line.\n",
    "extra": {},
}
PATH = "work/queue/q/codec.md"
KEY = ("queue", "codec")


def fake_store(stored: dict | None = None) -> Hashes:
    held = {} if stored is None else stored
    return Hashes(held, lambda initiative, task_id, digest: held.update({(initiative, task_id): digest}) or True)


def test_unedited_file_is_exported_as_before_and_its_hash_recorded(tmp_path: Path) -> None:
    hashes = fake_store()
    assert export_files(tmp_path, [ROW], hashes=hashes).touched == [PATH]
    text = (tmp_path / PATH).read_text()
    assert hashes.stored == {KEY: text_hash(text)}
    newer = {**ROW, "title": "Codec: rows, again"}
    assert export_files(tmp_path, [newer], hashes=hashes).touched == [PATH]
    assert "again" in (tmp_path / PATH).read_text()
    assert export_files(tmp_path, [newer], hashes=hashes).touched == []


def test_hand_edited_file_is_left_byte_identical_and_one_line_logged(tmp_path: Path, caplog) -> None:
    hashes = fake_store()
    export_files(tmp_path, [ROW], hashes=hashes)
    before = dict(hashes.stored)
    edited = (tmp_path / PATH).read_text() + "\nhand edit\n"
    (tmp_path / PATH).write_text(edited)
    with caplog.at_level(logging.INFO):
        result = export_files(tmp_path, [{**ROW, "title": "Changed in the store"}], hashes=hashes)
    assert (tmp_path / PATH).read_bytes() == edited.encode()
    assert result.touched == []
    assert hashes.stored == before
    assert len(caplog.records) == 1
    line = caplog.records[0].getMessage()
    assert str(tmp_path / PATH) in line
    assert f"cox route import --workspace {tmp_path}" in line


def test_row_with_no_hash_and_a_differing_file_is_written_and_recorded(tmp_path: Path) -> None:
    (tmp_path / PATH).parent.mkdir(parents=True)
    (tmp_path / PATH).write_text("older history\n")
    hashes = fake_store()
    assert export_files(tmp_path, [ROW], hashes=hashes).touched == [PATH]
    assert hashes.stored == {KEY: text_hash((tmp_path / PATH).read_text())}


def test_row_with_no_hash_and_an_equal_file_records_without_writing(tmp_path: Path) -> None:
    export_files(tmp_path, [ROW])
    hashes = fake_store()
    assert export_files(tmp_path, [ROW], hashes=hashes).touched == []
    assert hashes.stored == {KEY: text_hash((tmp_path / PATH).read_text())}


def test_forced_export_overwrites_a_hand_edit_and_records_the_hash(tmp_path: Path) -> None:
    hashes = fake_store()
    export_file(tmp_path, ROW, hashes=hashes)
    (tmp_path / PATH).write_text("hand edit\n")
    assert export_file(tmp_path, ROW, hashes=hashes).touched == []
    assert (tmp_path / PATH).read_text() == "hand edit\n"
    assert export_file(tmp_path, ROW, force=True, hashes=hashes).touched == [PATH]
    text = (tmp_path / PATH).read_text()
    assert "hand edit" not in text
    assert hashes.stored == {KEY: text_hash(text)}


def test_is_ahead_of_store_on_literals() -> None:
    digest = text_hash("abc\n")
    assert is_ahead_of_store(digest, "abc\n") is False
    assert is_ahead_of_store(digest, "abc edited\n") is True
    assert is_ahead_of_store(None, "abc\n") is False


def test_export_hashes_round_trip_is_additive_and_safe_to_rerun(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "cox.db"
    sqlite3.connect(db).close()
    monkeypatch.setattr(run_store, "_harness_python", lambda: Path(sys.executable))
    monkeypatch.setattr(run_store, "_store_url", lambda runs_dir: f"sqlite:///{db}")
    assert run_store.export_hashes(tmp_path) == {}
    assert run_store.set_export_hash(tmp_path, "queue", "codec", "aaa")
    assert run_store.set_export_hash(tmp_path, "queue", "codec", "bbb")
    assert run_store.set_export_hash(tmp_path, "queue", "other", "ccc")
    assert run_store.export_hashes(tmp_path) == {("queue", "codec"): "bbb", ("queue", "other"): "ccc"}
