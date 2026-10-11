"""`cox route priority` in store mode: every item's stored row takes the new priority, and so does its file."""

from agent_tools import route, run_store
from agent_tools.route import parse_frontmatter


def _workspace(tmp_path, items: dict[str, dict[str, int | None]]):
    ws = tmp_path / "workspace"
    for phase, named in items.items():
        (ws / "work" / "alpha" / phase).mkdir(parents=True)
        for name, priority in named.items():
            key = "" if priority is None else f"priority: {priority}\n"
            (ws / "work" / "alpha" / phase / f"{name}.md").write_text(f"---\nstate: ready\ntitle: {name}\n{key}---\nBody.\n")
    return ws


def _fake_store(monkeypatch) -> dict:
    """A store keyed (initiative, task_id) whose `priority` column is the row's top-level `priority`, default 0."""
    table: dict = {}

    def upsert(runs_dir, row):
        table[(row["initiative"], row["task_id"])] = {**row, "priority": row.get("priority", 0)}
        return True

    monkeypatch.setattr(run_store, "upsert_row", upsert)
    return table


def test_set_gives_every_stored_row_and_file_the_new_priority(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, {"1-a": {"t1": None, "t2": 4}, "2-b": {"t3": 1, "t4": None}})
    table = _fake_store(monkeypatch)

    assert route.set_initiative_priority(tmp_path / "runs", ws, "alpha", 2) == 0

    assert sorted(table) == [("alpha", "t1"), ("alpha", "t2"), ("alpha", "t3"), ("alpha", "t4")]
    assert [row["priority"] for row in table.values()] == [2, 2, 2, 2]
    files = sorted((ws / "work" / "alpha").glob("*/*.md"))
    assert len(files) == 4
    assert [int(parse_frontmatter(f.read_text())[0]["priority"]) for f in files] == [2, 2, 2, 2]


def test_up_steps_from_the_highest_and_stores_the_int(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, {"1-a": {"t1": 1, "t2": 3}})
    table = _fake_store(monkeypatch)

    assert route.set_initiative_priority(tmp_path / "runs", ws, "alpha", "up") == 0

    assert {row["priority"] for row in table.values()} == {4}


def test_the_store_row_is_taken_before_the_file_changes(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, {"1-a": {"t1": 1}})
    seen = []

    def upsert(runs_dir, row):
        seen.append((row["priority"], "priority: 1" in (ws / "work/alpha/1-a/t1.md").read_text()))
        return True

    monkeypatch.setattr(run_store, "upsert_row", upsert)

    route.set_initiative_priority(tmp_path / "runs", ws, "alpha", 5)

    assert seen == [(5, True)]
