from pathlib import Path

import pytest

from agent_tools import cli
from agent_tools.cli import main


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _workspace(tmp_path: Path, repo: str) -> Path:
    ws = tmp_path / "ws"
    (ws / "work" / "demo" / "1-build").mkdir(parents=True)
    (ws / "work" / "demo" / "initiative.md").write_text(f"---\ntitle: Demo\nrepo: {repo}\n---\n\nAbout demo\n")
    (ws / "work" / "demo" / "1-build" / "task.md").write_text(f"---\nstate: ready\ntitle: Do it\nrepo: {repo}\n---\n\nDo it\n")
    return ws


def _change_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if " -> " in line]


def test_dry_run_prints_two_changes_and_writes_nothing(tmp_path: Path, home: Path, capsys) -> None:
    repo = str(home / "repos" / "demo")
    ws = _workspace(tmp_path, repo)
    before = {p: p.read_bytes() for p in ws.rglob("*.md")}
    assert main(["route", "paths", "--workspace", str(ws)]) == 0
    out = capsys.readouterr().out
    assert len(_change_lines(out)) == 2
    assert f"{repo} -> ~/repos/demo" in out
    assert out.splitlines()[-1] == "2 change(s)"
    assert {p: p.read_bytes() for p in ws.rglob("*.md")} == before


def test_apply_rewrites_both_files_and_a_second_run_finds_nothing(tmp_path: Path, home: Path, capsys) -> None:
    ws = _workspace(tmp_path, str(home / "repos" / "demo"))
    assert main(["route", "paths", "--workspace", str(ws), "--apply"]) == 0
    capsys.readouterr()
    for rel in ("work/demo/initiative.md", "work/demo/1-build/task.md"):
        assert "repo: ~/repos/demo\n" in (ws / rel).read_text()
    assert main(["route", "paths", "--workspace", str(ws)]) == 0
    out = capsys.readouterr().out
    assert _change_lines(out) == []
    assert out.splitlines()[-1] == "0 change(s)"


def _row(task: str, repo: str) -> dict:
    return {"kind": "task", "initiative": "demo", "task_id": task, "phase": "1-build", "state": "ready",
            "needs": [], "title": task, "surfaces": [], "body": "", "extra": {"repo": repo}}


def _store_mode(monkeypatch, rows: list[dict], upsert) -> list[tuple]:
    exports: list[tuple] = []
    monkeypatch.setattr(cli.work_state, "work_state_mode", lambda profile: "store")
    monkeypatch.setattr(cli.run_store, "read_queue", lambda runs_dir, *a, **k: rows)
    monkeypatch.setattr(cli.run_store, "upsert_row", upsert)
    monkeypatch.setattr(cli, "_export_board", lambda runs_dir, ws: exports.append((runs_dir, ws)) or [])
    return exports


def test_store_apply_upserts_only_changed_rows_and_exports_once(tmp_path: Path, home: Path, monkeypatch) -> None:
    ws = _workspace(tmp_path, "~/repos/demo")
    rows = [_row("a", str(home / "repos" / "a")), _row("b", "~/repos/b"), _row("c", str(home / "repos" / "c"))]
    upserted: list[dict] = []
    exports = _store_mode(monkeypatch, rows, lambda runs_dir, row: upserted.append(row) or True)
    assert main(["route", "paths", "--workspace", str(ws), "--apply"]) == 0
    assert upserted == [
        {"initiative": "demo", "task_id": "a", "extra": {"repo": "~/repos/a"}, "surfaces": []},
        {"initiative": "demo", "task_id": "c", "extra": {"repo": "~/repos/c"}, "surfaces": []},
    ]
    assert exports == [(ws / "runs", ws)]


def test_store_apply_never_sends_state_needs_or_claim_columns(tmp_path: Path, home: Path, monkeypatch) -> None:
    ws = _workspace(tmp_path, "~/repos/demo")
    claimed = {**_row("a", str(home / "repos" / "a")), "state": "doing", "needs": ["x"], "holder": "h", "epoch": 3, "expires_at": "t"}
    upserted: list[dict] = []
    _store_mode(monkeypatch, [claimed], lambda runs_dir, row: upserted.append(row) or True)
    assert main(["route", "paths", "--workspace", str(ws), "--apply"]) == 0
    assert set(upserted[0]) == {"initiative", "task_id", "extra", "surfaces"}


def test_an_empty_read_from_an_unreadable_store_is_an_error_not_zero_changes(
    tmp_path: Path, home: Path, monkeypatch, capsys
) -> None:
    ws = _workspace(tmp_path, "~/repos/demo")
    _store_mode(monkeypatch, [], lambda runs_dir, row: True)
    monkeypatch.setattr("agent_tools.store_fill._read_store", lambda runs_dir: None)
    assert main(["route", "paths", "--workspace", str(ws)]) == 2
    assert "cannot be read" in capsys.readouterr().out


def test_a_failed_export_after_the_upserts_is_reported_and_exits_non_zero(
    tmp_path: Path, home: Path, monkeypatch, capsys
) -> None:
    ws = _workspace(tmp_path, "~/repos/demo")
    _store_mode(monkeypatch, [_row("a", str(home / "repos" / "a"))], lambda runs_dir, row: True)

    def boom(runs_dir: Path, workspace: Path) -> list[str]:
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(cli, "_export_board", boom)
    assert main(["route", "paths", "--workspace", str(ws), "--apply"]) == 1
    assert "file export failed: store unreadable" in capsys.readouterr().out


def test_a_held_intake_file_is_migrated_in_file_mode(tmp_path: Path, home: Path, capsys) -> None:
    ws = _workspace(tmp_path, "~/repos/demo")
    (ws / "intake" / "held").mkdir(parents=True)
    held = ws / "intake" / "held" / "idea.md"
    held.write_text(f"---\ntitle: Idea\nrepo: {home / 'repos' / 'idea'}\n---\n\nBody\n")
    assert main(["route", "paths", "--workspace", str(ws), "--apply"]) == 0
    assert "repo: ~/repos/idea\n" in held.read_text()


def test_store_dry_run_writes_nothing(tmp_path: Path, home: Path, monkeypatch, capsys) -> None:
    ws = _workspace(tmp_path, "~/repos/demo")
    upserted: list[dict] = []
    exports = _store_mode(monkeypatch, [_row("a", str(home / "repos" / "a"))], lambda runs_dir, row: upserted.append(row) or True)
    assert main(["route", "paths", "--workspace", str(ws)]) == 0
    assert (upserted, exports) == ([], [])
    assert len(_change_lines(capsys.readouterr().out)) == 1


def test_a_refused_row_is_reported_the_rest_are_tried_and_the_exit_is_non_zero(
    tmp_path: Path, home: Path, monkeypatch, capsys
) -> None:
    ws = _workspace(tmp_path, "~/repos/demo")
    rows = [_row("a", str(home / "repos" / "a")), _row("b", str(home / "repos" / "b"))]
    tried: list[str] = []
    exports = _store_mode(monkeypatch, rows, lambda runs_dir, row: tried.append(row["task_id"]) or row["task_id"] != "a")
    assert main(["route", "paths", "--workspace", str(ws), "--apply"]) == 1
    assert tried == ["a", "b"]
    assert "store refused demo/a" in capsys.readouterr().out
    assert len(exports) == 1


def test_a_workspace_without_work_or_intake_is_refused(tmp_path: Path, home: Path, capsys) -> None:
    assert main(["route", "paths", "--workspace", str(tmp_path)]) == 2
    assert "no work/ or intake/" in capsys.readouterr().out
