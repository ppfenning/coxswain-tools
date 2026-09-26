import json
import sqlite3

import pytest

from agent_tools.cli import main

_SESSION = "c60eb194-67a1-493b-b972-f6e4674e8381"


def _assistant(inp, write, read, out):
    usage = {"input_tokens": inp, "cache_creation_input_tokens": write, "cache_read_input_tokens": read, "output_tokens": out}
    return json.dumps({"type": "assistant", "sessionId": _SESSION, "timestamp": "2026-09-24T14:30:29.810Z", "message": {"model": "claude-opus-5-5", "usage": usage}})


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def shape(tmp_path):
    """The 2026-09-24 shape: 38 task records, 36 landed, 34 merged by land, 2 finished by hand."""
    runs = tmp_path / "runs"
    for n in range(1, 39):
        _write(runs / "r1" / "tasks" / "p1" / f"t{n:02d}.json", json.dumps({"ticket": f"t{n:02d}", "landed": n <= 36}))
    merged = [{"run": "r1", "task": f"t{n:02d}", "steps_reached": ["pr", "merge"], "exit": 0, "pr": None} for n in range(1, 35)]
    failed = {"run": "r1", "task": "t35", "steps_reached": ["pr"], "exit": 1, "pr": None}
    _write(runs / "land.jsonl", "".join(json.dumps(r) + "\n" for r in [*merged, failed]))
    calls = [
        {"role": "scope_epic", "task_id": None, "cost_usd": 0.5, "model": "haiku", "tier": "cheap"},
        {"role": "build", "task_id": "t01", "cost_usd": 1.0, "model": "haiku", "tier": "cheap"},
        {"role": "build", "task_id": "t02", "cost_usd": 0.25, "model": "haiku", "tier": "cheap"},
    ]
    _write(runs / "r1.usage.json", json.dumps({"calls": calls}))
    chair = {"session": "chair-2026-09-24", "pid": 9597, "host": "omarchy", "runs": [], "claude_session": _SESSION}
    _write(runs / "chair.json", json.dumps(chair))
    work = tmp_path / "work"
    _write(work / "init" / "p1" / "t01.md", "---\nid: t01\nattempts:\n  - run: r1\n    cause: code\n---\n")
    _write(work / "init" / "p1" / "t02.md", "---\nid: t02\nattempts:\n  - run: r1\n    cause: ticket\n---\n")
    projects = tmp_path / "projects"
    _write(projects / "-home-x" / f"{_SESSION}.jsonl", _assistant(2, 26870, 27975, 347) + "\n" + _assistant(10, 0, 100000, 1000) + "\n")
    _write(projects / "-home-x" / _SESSION / "subagents" / "agent-1.jsonl", _assistant(0, 0, 0, 100000) + "\n")
    cartridges = tmp_path / "cartridges"
    _write(cartridges / "providers" / "catalog.yaml", "models:\n  claude-opus-5-5:\n    price: {input: 4.0, output: 20.0, cache_write: 5.0, cache_read: 0.4}\n")
    _write(tmp_path / "profile.yaml", f"cartridges_dir: {cartridges}\n")
    return ["stats", "chair", str(runs), "--work-store-root", str(work), "--projects-dir", str(projects), "--profile", str(tmp_path / "profile.yaml")]


def test_the_2026_09_24_shape_reports_36_prs_and_2_hand_finished(shape, capsys):
    assert main([*shape, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    chair_usd = 0.212528 + 2.0  # two main lines, plus one subagent line of 100000 output tokens at $20/M
    assert report["harness_prs"] == 36
    assert report["hand_finished"] == {"n": 2, "pct": 5.6}
    assert report["harness_usd"] == pytest.approx(1.75)
    assert report["chair_usd"] == pytest.approx(chair_usd)
    assert report["chair_usd_per_pr"] == pytest.approx(chair_usd / 36)
    assert report["quarantine_usd"] == {"ticket": 0.25, "code": 1.0, "harness": 0.0, "unknown": 0.0}


def test_a_session_given_twice_is_counted_once_and_the_table_renders(shape, capsys):
    assert main([*shape, "--session", _SESSION]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("  ")[-1].strip() for line in lines[1:6]] == ["36", "$1.75", "$2.21", "$0.06", "2 (5.6%)"]


def test_a_missing_catalog_leaves_chair_cost_unpriced_and_never_fails(shape, tmp_path, capsys):
    assert main([*shape[:-1], str(tmp_path / "absent.yaml"), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["chair_usd"], report["chair_usd_per_pr"], report["harness_prs"]) == (None, None, 36)


def _seed_store_run(runs, run_id, launched_at, cost):
    """An ended `runs` row and one `node_calls` row costing `cost`, with no usage file."""
    columns = (
        "call_id", "run_id", "seq", "role", "task_id", "tier", "model_alias", "cost_usd", "ceiling_usd", "ceiling_source",
        "turns", "duration_ms", "input_tokens", "cache_read_tokens", "cache_creation_tokens", "input_total", "output_tokens",
        "ok", "ts", "decision_json",
    )
    conn = sqlite3.connect(runs / "cox.db")
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, launched_at TEXT, ended_at TEXT)")
    conn.execute("CREATE TABLE node_calls (" + ", ".join(columns) + ")")
    conn.execute("INSERT INTO runs VALUES (?, ?, ?)", (run_id, launched_at, "2026-09-10T01:00:00+00:00"))
    row = (f"{run_id}-0", run_id, 0, "build", None, "cheap", "haiku", cost, None, None, 1, 1, 0, 0, 0, 0, 0, 1, launched_at, None)
    conn.execute("INSERT INTO node_calls VALUES (" + ", ".join("?" * len(columns)) + ")", row)
    conn.commit()
    conn.close()


def test_a_store_only_run_is_counted_and_windowed_by_its_launched_at_date(shape, tmp_path, capsys):
    _seed_store_run(tmp_path / "runs", "s1", "2026-09-10T08:00:00+00:00", 2.0)
    assert main([*shape, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["harness_usd"] == pytest.approx(3.75)
    assert main([*shape, "--json", "--since", "2026-09-10"]) == 0
    assert json.loads(capsys.readouterr().out)["harness_usd"] == pytest.approx(3.75)
    assert main([*shape, "--json", "--since", "2026-09-11"]) == 0
    assert json.loads(capsys.readouterr().out)["harness_usd"] == pytest.approx(1.75)


def test_since_after_every_file_date_leaves_nothing_in_the_window(shape, capsys):
    assert main([*shape, "--json", "--since", "2999-01-01"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["harness_prs"], report["harness_usd"], report["chair_usd"]) == (0, 0, 0)


def test_a_catalog_beside_the_provider_profile_prices_the_chair_and_wins_over_cartridges_dir(shape, tmp_path, capsys):
    _write(tmp_path / "prov" / "catalog.yaml", "models:\n  - id: claude-opus-5-5\n    aliases: [opus]\n    price: {input: 8.0, output: 40.0, cache_write: 10.0, cache_read: 0.8}\n")
    _write(tmp_path / "profile.yaml", f"cartridges_dir: {tmp_path / 'cartridges'}\nprovider_profile: {tmp_path / 'prov' / 'provider.yaml'}\n")
    assert main([*shape, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["chair_usd"] == pytest.approx(2 * (0.212528 + 2.0))
