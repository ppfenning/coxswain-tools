"""`cox route priority` on a queued intake that has no initiative or tasks yet."""

import pytest

from agent_tools import route, run_store
from agent_tools.route import parse_frontmatter


def _row(kind, initiative, task_id, state, priority, extra=None):
    return {
        "kind": kind,
        "initiative": initiative,
        "task_id": task_id,
        "phase": "",
        "state": state,
        "needs": [],
        "title": task_id,
        "surfaces": [],
        "body": "",
        "extra": extra or {},
        "priority": priority,
    }


def _seed() -> list[dict]:
    """One queued intake at 0, open tasks at 3 and 5, and a done task at 9 that must not count."""
    return [
        _row("intake", "intake", "idea", "queued", 0),
        _row("task", "alpha", "a1", "ready", 3),
        _row("task", "beta", "b1", "approved", 5),
        _row("task", "gamma", "g1", "done", 9),
    ]


def _fake_store(monkeypatch, rows: list[dict]) -> list[dict]:
    """Serves `rows` to `read_queue` and records every row `upsert_row` takes, in order."""
    taken: list[dict] = []

    def read_queue(runs_dir, initiative=None, kind=None):
        return [r for r in rows if kind in (None, r["kind"])]

    def upsert(runs_dir, row):
        taken.append(row)
        return True

    monkeypatch.setattr(run_store, "read_queue", read_queue)
    monkeypatch.setattr(run_store, "upsert_row", upsert)
    return taken


def test_top_before_decompose_is_open_maximum_plus_one(tmp_path, monkeypatch, capsys):
    taken = _fake_store(monkeypatch, _seed())

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", "top") == 0

    assert [(r["task_id"], r["priority"]) for r in taken] == [("idea", 6)]
    assert capsys.readouterr().out.splitlines() == [
        "idea: open maximum elsewhere is 5, setting priority 6",
        "idea: priority 6 on 1 item(s)",
    ]


def test_bottom_is_open_minimum_minus_one(tmp_path, monkeypatch, capsys):
    taken = _fake_store(monkeypatch, [_row("intake", "intake", "idea", "queued", 4), *_seed()[1:]])

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", "bottom") == 0

    assert [(r["task_id"], r["priority"]) for r in taken] == [("idea", 2)]
    assert "idea: open minimum elsewhere is 3, setting priority 2" in capsys.readouterr().out


@pytest.mark.parametrize(("flag", "expected"), [(7, 7), ("up", 4), ("down", -1)])
def test_set_up_and_down_compute_from_the_intakes_own_priority(tmp_path, monkeypatch, flag, expected):
    taken = _fake_store(monkeypatch, _seed())

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", flag) == 0

    assert [r["priority"] for r in taken] == [expected]


def test_the_intakes_own_stored_priority_is_current(tmp_path, monkeypatch, capsys):
    rows = [_row("intake", "intake", "idea", "queued", 8), *_seed()[1:]]
    taken = _fake_store(monkeypatch, rows)

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", "top") == 0

    assert [r["priority"] for r in taken] == [8]
    assert "already at the top" in capsys.readouterr().out


def test_another_queued_intake_counts_as_open(tmp_path, monkeypatch):
    taken = _fake_store(monkeypatch, [*_seed(), _row("intake", "intake", "other", "queued", 11)])

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", "top") == 0

    assert [r["priority"] for r in taken] == [12]


@pytest.mark.parametrize("state", ["done", "decomposed"])
def test_an_intake_that_is_not_queued_is_unknown(tmp_path, monkeypatch, capsys, state):
    taken = _fake_store(monkeypatch, [_row("intake", "intake", "idea", state, 0)])

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", "top") == 2

    assert taken == []
    assert capsys.readouterr().out.splitlines() == ["routing: unknown initiative idea"]


def test_an_unknown_id_exits_non_zero_with_one_line(tmp_path, monkeypatch, capsys):
    taken = _fake_store(monkeypatch, _seed())

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "nothing", "top") == 2

    assert taken == []
    assert capsys.readouterr().out.splitlines() == ["routing: unknown initiative nothing"]


def test_an_initiative_of_the_same_name_wins(tmp_path, monkeypatch):
    taken = _fake_store(monkeypatch, _seed())
    (tmp_path / "work" / "idea" / "1-a").mkdir(parents=True)
    (tmp_path / "work" / "idea" / "1-a" / "t1.md").write_text("---\nstate: ready\ntitle: t1\n---\nBody.\n")

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", 4) == 0

    assert [(r["kind"], r["priority"]) for r in taken] == [("task", 4)]


def test_the_store_row_is_taken_before_the_intake_file_changes(tmp_path, monkeypatch):
    rows = _seed()
    _fake_store(monkeypatch, rows)
    path = tmp_path / "intake" / "idea.md"
    path.parent.mkdir()
    path.write_text("---\ntitle: idea\npriority: 0\n---\nBody.\n")
    seen = []

    def upsert(runs_dir, row):
        seen.append((row["priority"], "priority: 0" in path.read_text()))
        return True

    monkeypatch.setattr(run_store, "upsert_row", upsert)

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", "top") == 0

    assert seen == [(6, True)]
    assert parse_frontmatter(path.read_text())[0]["priority"] == "6"


def test_an_intake_file_without_a_priority_key_is_left_alone(tmp_path, monkeypatch):
    taken = _fake_store(monkeypatch, _seed())
    path = tmp_path / "intake" / "idea.md"
    path.parent.mkdir()
    path.write_text("---\ntitle: idea\n---\nBody.\n")

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", "top") == 0

    assert [r["priority"] for r in taken] == [6]
    assert path.read_text() == "---\ntitle: idea\n---\nBody.\n"


def test_a_store_that_refuses_the_row_fails_the_call(tmp_path, monkeypatch, capsys):
    _fake_store(monkeypatch, _seed())
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: False)

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", "top") == 1

    assert capsys.readouterr().out.splitlines()[-1] == "routing: the store did not take the priority for intake idea"


def test_a_slug_mode_intake_is_found_by_its_frontmatter_id(tmp_path, monkeypatch):
    rows = [_row("intake", "intake", "2026-10-12-idea", "queued", 0, extra={"id": "idea"}), *_seed()[1:]]
    taken = _fake_store(monkeypatch, rows)
    path = tmp_path / "intake" / "2026-10-12-idea.md"
    path.parent.mkdir()
    path.write_text("---\nid: idea\npriority: 0\n---\nBody.\n")

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", "top") == 0

    assert [(r["task_id"], r["priority"]) for r in taken] == [("2026-10-12-idea", 6)]
    assert parse_frontmatter(path.read_text())[0]["priority"] == "6"


def test_a_store_that_refuses_the_row_leaves_the_intake_file_alone(tmp_path, monkeypatch, capsys):
    _fake_store(monkeypatch, _seed())
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: False)
    path = tmp_path / "intake" / "idea.md"
    path.parent.mkdir()
    path.write_text("---\ntitle: idea\npriority: 0\n---\nBody.\n")

    assert route.set_initiative_priority(tmp_path / "runs", tmp_path, "idea", "top") == 1

    assert "priority: 0" in path.read_text()
    assert capsys.readouterr().out.splitlines()[-1] == "routing: the store did not take the priority for intake idea"
