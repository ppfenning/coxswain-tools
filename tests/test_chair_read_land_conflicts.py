import json

from agent_tools.chair_read_land_conflicts import land_conflicts, read_land_conflicts

CURRENT = {("alpha", "p2"): ("b1", "m1")}


def squash(**over: object) -> dict:
    row = {
        "ts": "2026-10-13T10:00:00+00:00", "run": "alpha-3", "stopped_step": "squash_phase",
        "stop_detail": "squash of epic/alpha/p2 conflicts in: a.py, b.py",
        "conflict_paths": ["a.py", "b.py"], "branch_sha": "b1", "from_sha": "m1",
    }
    return {**row, **over}


def test_matching_commit_ids_return_the_paths():
    assert land_conflicts([squash()], CURRENT) == [
        {"initiative": "alpha", "phase": "p2", "run": "alpha-3", "paths": ["a.py", "b.py"]}
    ]


def test_moved_main_tip_is_dropped():
    assert land_conflicts([squash()], {("alpha", "p2"): ("b1", "m2")}) == []


def test_other_stopped_step_is_dropped():
    assert land_conflicts([squash(stopped_step="wait_checks")], CURRENT) == []


def test_row_without_commit_ids_is_dropped():
    assert land_conflicts([squash(branch_sha=None, from_sha=None)], CURRENT) == []


def test_only_the_newest_row_of_a_run_counts():
    older = squash(ts="2026-10-13T09:00:00+00:00", conflict_paths=["old.py"])
    newer = squash(ts="2026-10-13T11:00:00+00:00", branch_sha="b0")
    assert land_conflicts([older, newer], CURRENT) == []
    assert land_conflicts([newer, older], CURRENT) == []


def test_newest_matching_row_wins_over_an_older_one():
    older = squash(ts="2026-10-13T09:00:00+00:00", conflict_paths=["old.py"])
    newer = squash(ts="2026-10-13T11:00:00+00:00", conflict_paths=["new.py"])
    assert [c["paths"] for c in land_conflicts([newer, older], CURRENT)] == [["new.py"]]


def test_edge_without_a_runs_dir_reads_nothing():
    assert read_land_conflicts("", {"alpha": "/nowhere"}) == []


def test_edge_reads_the_log_and_resolves_ids_in_the_initiatives_repo(tmp_path):
    (tmp_path / "land.jsonl").write_text(json.dumps(squash()) + "\nnot json\n", encoding="utf-8")
    ids = {("/r", "epic/alpha/p2"): "b1", ("/r", "main"): "m1"}
    got = read_land_conflicts(str(tmp_path), {"alpha": "/r"}, lambda repo, ref: ids.get((repo, ref)))
    assert [c["paths"] for c in got] == [["a.py", "b.py"]]
    assert read_land_conflicts(str(tmp_path), {}, lambda repo, ref: ids.get((repo, ref))) == []
