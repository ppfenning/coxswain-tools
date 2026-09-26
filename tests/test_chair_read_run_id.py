from agent_tools import chair_read_run_id as mod
from agent_tools.chair_types import Action


def test_with_none_taken_the_first_id_is_returned():
    assert mod.next_run_id("epic", frozenset()) == "epic-1"


def test_with_ids_1_and_2_taken_the_id_is_3():
    assert mod.next_run_id("epic", frozenset({"epic-1", "epic-2"})) == "epic-3"


def test_an_id_taken_only_in_the_store_is_skipped():
    taken = mod.taken_names([], ["epic-1", "epic-4"])
    assert mod.next_run_id("epic", taken) == "epic-5"


def test_a_remote_lane_file_counts_as_its_bare_id():
    assert mod.taken_names(["epic-2.remote.json"], []) >= {"epic-2"}


def test_a_relaunch_gets_a_fresh_id_not_the_old_one(tmp_path, monkeypatch):
    (tmp_path / "epic-1.log").write_text("")
    monkeypatch.setattr(mod.run_store, "run_ids", lambda _runs_dir: {"epic-2"})
    action: Action = {"kind": "relaunch", "initiative": "epic", "task_id": "t1"}
    assert mod.make_run_id(tmp_path)(action) == "epic-3"


def test_a_decompose_launch_without_an_initiative_uses_the_idea_id(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.run_store, "run_ids", lambda _runs_dir: set())
    action: Action = {"kind": "launch_decompose", "intake_ids": ["idea-a"]}
    assert mod.make_run_id(tmp_path / "missing")(action) == "idea-a-1"
