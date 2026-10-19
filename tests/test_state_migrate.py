from pathlib import PurePath

from agent_tools.state_migrate import Step, apply_plan, plan_migration

WS = PurePath("/w/workspace")
ROOT = PurePath("/s/coxswain")


def test_mixed_listing_plans_only_runtime_moves():
    plan = plan_migration(["r1.log", "r1.json", "r1.summary.json"], ["chair.json", "README.md"], set(), WS, ROOT)
    assert plan == (
        Step("move", WS / "runs" / "r1.log", ROOT / "r1.log"),
        Step("move", WS / "chair.json", ROOT / "chair.json"),
    )


def test_existing_destination_is_a_conflict_not_a_move():
    plan = plan_migration(["r1.log", "r2.log"], [], {"r1.log"}, WS, ROOT)
    assert plan == (
        Step("conflict", WS / "runs" / "r1.log", ROOT / "r1.log"),
        Step("move", WS / "runs" / "r2.log", ROOT / "r2.log"),
    )


def test_name_in_both_listings_conflicts_the_second_source():
    plan = plan_migration(["chair.json"], ["chair.json"], set(), WS, ROOT)
    assert [s.kind for s in plan] == ["move", "conflict"]


def test_replanning_the_post_move_listing_is_empty():
    assert plan_migration(["r1.json"], ["README.md"], {"r1.log", "chair.json"}, WS, ROOT) == ()


def test_apply_moves_runtime_files_and_never_overwrites(tmp_path):
    ws, state = tmp_path / "workspace", tmp_path / "state" / "coxswain"
    (ws / "runs").mkdir(parents=True)
    (ws / "runs" / "r1.log").write_text("log")
    (ws / "runs" / "r1.json").write_text("record")
    (ws / "chair.json").write_text("chair")
    (ws / "runs" / "r2.pid").write_text("new")
    state.mkdir(parents=True)
    (state / "r2.pid").write_text("kept")

    plan = plan_migration(["r1.log", "r1.json", "r2.pid"], ["chair.json"], {"r2.pid"}, ws, state)
    apply_plan(plan)

    assert (state / "r1.log").read_text() == "log"
    assert (state / "chair.json").read_text() == "chair"
    assert (ws / "runs" / "r1.json").read_text() == "record"
    assert (state / "r2.pid").read_text() == "kept"
    assert (ws / "runs" / "r2.pid").read_text() == "new"
    assert not (ws / "runs" / "r1.log").exists()
    assert not (ws / "chair.json").exists()
