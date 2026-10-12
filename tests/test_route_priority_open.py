"""`cox route priority --top`, `--bottom`, neighbour-aware `--up` and `--down`, and the open-queue listing."""

import pytest

from agent_tools import open_queue_priority, route, run_store
from agent_tools.cli import main


def _workspace(tmp_path, priority: int | None):
    ws = tmp_path / "workspace"
    phase = ws / "work" / "demo" / "1-build"
    phase.mkdir(parents=True)
    key = "" if priority is None else f"priority: {priority}\n"
    (phase / "a.md").write_text(f"---\nstate: ready\ntitle: T\n{key}---\nBody.\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "team: acme\n"
        f"workspace_dir: {ws}\n"
        "harness_dir: /opt/coxswain-graphs\n"
        "cartridges_dir: /opt/cartridges\n"
        "provider_profile: /opt/providers/acme.yaml\n"
    )
    return profile, phase / "a.md"


def _row(initiative: str, state: str, priority: int, kind: str = "task") -> dict:
    return {"kind": kind, "initiative": initiative, "task_id": f"{initiative}-{state}-{priority}", "state": state, "priority": priority}


def _store(monkeypatch, rows: list[dict], path=None, events: list | None = None) -> None:
    monkeypatch.setattr(run_store, "read_queue", lambda runs_dir, *a, **k: rows)

    def upsert(runs_dir, row):
        if events is not None:
            events.append(("store", path.read_text(), row["priority"]))
        return True

    monkeypatch.setattr(run_store, "upsert_row", upsert)


def _run(profile, *args: str) -> int:
    return main(["route", "priority", *args, "--profile", str(profile)])


OTHERS = [
    _row("beta", "ready", 5),
    _row("beta", "approved", 3),
    _row("gamma", "done", 10),
    _row("delta", "quarantined", -2),
    _row("epsilon", "done", -9),
]


# -- the pure parts, literals only -----------------------------------------------------------


def test_priority_rows_keep_tasks_and_intakes_and_read_extra_when_no_column():
    stored = [
        {"kind": "task", "initiative": "a", "state": "ready", "priority": 4},
        {"kind": "intake", "initiative": "b", "state": "queued", "extra": {"priority": "6"}},
        {"kind": "task", "initiative": "c", "state": "ready", "priority": None, "extra": None},
        {"kind": "initiative", "initiative": "d", "state": "ready", "priority": 9},
    ]
    assert route.priority_rows(stored) == [
        open_queue_priority.Row("a", "task", "ready", 4),
        open_queue_priority.Row("b", "intake", "queued", 6),
        open_queue_priority.Row("c", "task", "ready", 0),
    ]


def test_priority_target_quotes_the_measure_and_the_new_value():
    rows = [open_queue_priority.Row("x", "task", "ready", 5), open_queue_priority.Row("me", "task", "ready", 1)]
    assert route.priority_target(rows, "me", "top") == (6, ["me: open maximum elsewhere is 5, setting priority 6"])
    assert route.priority_target(rows, "me", "bottom") == (1, ["me: already at the bottom, priority 1 against open minimum 5"])
    assert route.priority_target(rows[1:], "me", "top") == (1, ["me: nothing else is open, priority stays 1"])


# -- the verb: top and bottom ----------------------------------------------------------------


def test_top_ignores_done_work_and_sets_the_open_maximum_plus_one(tmp_path, monkeypatch, capsys):
    profile, path = _workspace(tmp_path, 0)
    events: list = []
    _store(monkeypatch, OTHERS, path, events)
    assert _run(profile, "demo", "--top") == 0
    assert events == [("store", "---\nstate: ready\ntitle: T\npriority: 0\n---\nBody.\n", 6)]
    assert "priority: 6\n" in path.read_text()
    assert capsys.readouterr().out.splitlines() == [
        "demo: open maximum elsewhere is 5, setting priority 6",
        "demo: priority 6 on 1 item(s)",
    ]


def test_bottom_sets_the_open_minimum_minus_one_store_first(tmp_path, monkeypatch, capsys):
    profile, path = _workspace(tmp_path, 0)
    events: list = []
    _store(monkeypatch, OTHERS, path, events)
    assert _run(profile, "demo", "--bottom") == 0
    assert [(e[0], e[2]) for e in events] == [("store", -3)]
    assert "priority: 0" in events[0][1]
    assert "priority: -3\n" in path.read_text()
    assert capsys.readouterr().out.splitlines()[0] == "demo: open minimum elsewhere is -2, setting priority -3"


def test_top_says_so_when_already_in_place(tmp_path, monkeypatch, capsys):
    profile, path = _workspace(tmp_path, 8)
    events: list = []
    _store(monkeypatch, OTHERS, path, events)
    assert _run(profile, "demo", "--top") == 0
    assert capsys.readouterr().out.splitlines()[0] == "demo: already at the top, priority 8 against open maximum 5"
    assert [e[2] for e in events] == [8]


def test_top_says_so_when_nothing_else_is_open(tmp_path, monkeypatch, capsys):
    profile, path = _workspace(tmp_path, 2)
    _store(monkeypatch, [_row("gamma", "done", 10)], path, [])
    assert _run(profile, "demo", "--top") == 0
    assert capsys.readouterr().out.splitlines()[0] == "demo: nothing else is open, priority stays 2"


# -- the verb: neighbour-aware up and down ---------------------------------------------------


def test_up_passes_the_nearest_higher_open_neighbour_store_first(tmp_path, monkeypatch):
    profile, path = _workspace(tmp_path, 1)
    events: list = []
    _store(monkeypatch, OTHERS, path, events)
    assert _run(profile, "demo", "--up") == 0
    assert [(e[0], e[2]) for e in events] == [("store", 4)]
    assert "priority: 1\n" in events[0][1]
    assert "priority: 4\n" in path.read_text()


def test_down_passes_the_nearest_lower_open_neighbour_store_first(tmp_path, monkeypatch):
    profile, path = _workspace(tmp_path, 1)
    events: list = []
    _store(monkeypatch, OTHERS, path, events)
    assert _run(profile, "demo", "--down") == 0
    assert [(e[0], e[2]) for e in events] == [("store", -3)]
    assert "priority: -3\n" in path.read_text()


# -- the verb: listing ----------------------------------------------------------------------


def test_no_flag_lists_the_open_queue_highest_first_and_writes_nothing(tmp_path, monkeypatch, capsys):
    profile, _path = _workspace(tmp_path, 0)
    _store(monkeypatch, [*OTHERS, _row("zed", "queued", 5, kind="intake")], None, None)
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: pytest.fail("a listing must not write"))
    assert _run(profile) == 0
    assert capsys.readouterr().out.splitlines() == ["5  beta  2", "5  zed  1", "-2  delta  1"]


def test_an_empty_queue_prints_one_notice(tmp_path, monkeypatch, capsys):
    profile, _path = _workspace(tmp_path, 0)
    _store(monkeypatch, [_row("gamma", "done", 10)])
    assert _run(profile) == 0
    assert capsys.readouterr().out.splitlines() == ["routing: the open queue is empty"]


# -- refusals -------------------------------------------------------------------------------


def test_an_unknown_initiative_with_top_is_one_line_and_non_zero(tmp_path, monkeypatch, capsys):
    profile, _path = _workspace(tmp_path, 0)
    _store(monkeypatch, OTHERS)
    monkeypatch.setattr(run_store, "upsert_row", lambda runs_dir, row: pytest.fail("store must not be called"))
    assert _run(profile, "nope", "--top") != 0
    assert capsys.readouterr().out.splitlines() == ["routing: unknown initiative nope"]


def test_a_flag_without_an_initiative_is_one_line_and_non_zero(tmp_path, monkeypatch, capsys):
    profile, _path = _workspace(tmp_path, 0)
    _store(monkeypatch, OTHERS)
    assert _run(profile, "--top") != 0
    assert capsys.readouterr().out.startswith("routing: an initiative is required")


@pytest.mark.parametrize("pair", [("--top", "--bottom"), ("--top", "--up"), ("--set", "3", "--bottom"), ("--down", "--bottom")])
def test_the_write_flags_are_mutually_exclusive(tmp_path, pair):
    profile, _path = _workspace(tmp_path, 0)
    with pytest.raises(SystemExit):
        _run(profile, "demo", *pair)
