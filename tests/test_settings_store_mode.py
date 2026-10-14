from agent_tools import route
from agent_tools.settings_model import DEFAULT_STORE_MODE, STORE_MODES, setting_for
from agent_tools.settings_plan import Plan, PlanError, plan_set
from agent_tools.settings_sections.defaults import default_rows


def test_default_is_files():
    by_key = {r["key"]: r for r in default_rows([])}
    assert DEFAULT_STORE_MODE == "files"
    assert by_key["store.mode"]["value"] == "files"
    assert setting_for("profile", "store.mode").source_file == "profile.yaml"


def test_files_and_shared_validate():
    for mode in STORE_MODES:
        assert isinstance(plan_set("profile", "store.mode", mode, ""), Plan)


def test_cluster_is_rejected_with_the_allowed_values():
    plan = plan_set("profile", "store.mode", "cluster", "")
    assert plan == PlanError("store.mode must be one of files, shared, got 'cluster'")


def test_shared_round_trips_through_parse_profile_without_a_warning(capsys):
    route._WARNED_KEYS.discard("store")
    plan = plan_set("profile", "store.mode", "shared", "team: acme\n")
    assert plan.new_text == "team: acme\nstore:\n  mode: shared\n"
    assert route.parse_profile(plan.new_text)["store"] == {"mode": "shared"}
    assert capsys.readouterr().err == ""
    assert route.unknown_keys(plan.new_text) == []


def test_unknown_store_key_is_a_profile_error():
    try:
        route.parse_profile("store:\n  engine: pg\n")
    except route.ProfileError:
        return
    raise AssertionError("expected ProfileError")


def test_existing_blocks_serialise_as_before():
    spend = plan_set("profile", "node_cap_usd", "5", "team: acme\n")
    assert spend.new_text == "team: acme\nspend:\n  node_cap_usd: 5\n"
    chair = plan_set("profile", "chair.stale_days", "3", "team: acme\n")
    assert chair.new_text == "team: acme\nchair:\n  stale_days: 3\n"
