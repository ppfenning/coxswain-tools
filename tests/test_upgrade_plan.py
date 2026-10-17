from agent_tools.upgrade_plan import (
    NO_TAG_AT_TARGET,
    PIN_NOT_IN_REPO,
    LeftBehind,
    Plan,
    Step,
    plan_upgrade,
    release_tag,
)


def test_release_tag_adds_missing_v():
    assert release_tag("0.43.0") == "v0.43.0"
    assert release_tag(" 0.43.0 ") == "v0.43.0"
    assert release_tag("v0.43.0") == "v0.43.0"
    for given in ("0.43.0", "v0.43.0"):
        plan = plan_upgrade(release_tag(given), "v0.42.0", {}, {}, {})
        assert plan.steps == (Step("umbrella", "umbrella", "v0.42.0", "v0.43.0"),)


def test_umbrella_step_precedes_component_steps():
    plan = plan_upgrade(
        "v2.0", "v1.0",
        {"cartridges": "v2.0", "tools": "v2.0"},
        {"cartridges": "v1.0", "tools": "v1.0"},
        {"cartridges": frozenset({"v1.0", "v2.0"}), "tools": frozenset({"v1.0", "v2.0"})},
    )
    assert plan == Plan(
        steps=(
            Step("umbrella", "umbrella", "v1.0", "v2.0"),
            Step("component", "cartridges", "v1.0", "v2.0"),
            Step("component", "tools", "v1.0", "v2.0"),
        ),
        left_behind=(),
    )


def test_component_at_its_pin_gets_no_step():
    plan = plan_upgrade(
        "v2.0", "v1.0",
        {"graphs": "v2.0"},
        {"graphs": "v2.0"},
        {"graphs": frozenset({"v2.0"})},
    )
    assert plan == Plan(steps=(Step("umbrella", "umbrella", "v1.0", "v2.0"),), left_behind=())


def test_crew_and_dash_are_planned_like_cartridges():
    plan = plan_upgrade(
        "v2.0", "v1.0",
        {"cartridges": "v2.0", "crew": "v2.0", "dash": "v2.0"},
        {"cartridges": "v1.0", "crew": "v1.0", "dash": "v1.0"},
        {name: frozenset({"v1.0", "v2.0"}) for name in ("cartridges", "crew", "dash")},
    )
    assert plan.steps[1:] == (
        Step("component", "cartridges", "v1.0", "v2.0"),
        Step("component", "crew", "v1.0", "v2.0"),
        Step("component", "dash", "v1.0", "v2.0"),
    )


def test_pin_missing_from_available_tags_is_left_behind():
    plan = plan_upgrade(
        "v2.0", "v1.0",
        {"tools": "v2.0"},
        {"tools": "v1.0"},
        {"tools": frozenset({"v1.0"})},
    )
    assert plan == Plan(
        steps=(Step("umbrella", "umbrella", "v1.0", "v2.0"),),
        left_behind=(LeftBehind("tools", PIN_NOT_IN_REPO, "pinned tag v2.0 is not offered by its repository"),),
    )


def test_crew_without_a_tag_at_the_target_is_left_behind():
    plan = plan_upgrade(
        "v2.0", "v1.0",
        {"crew": None},
        {"crew": "v1.0"},
        {"crew": frozenset({"v1.0"})},
    )
    assert plan.steps == (Step("umbrella", "umbrella", "v1.0", "v2.0"),)
    assert [(b.name, b.reason) for b in plan.left_behind] == [("crew", NO_TAG_AT_TARGET)]


def test_plan_is_identical_for_identical_inputs():
    available = {"crew": frozenset({"v1.0", "v2.0"}), "dash": frozenset({"v2.0"})}
    first = plan_upgrade("v2.0", "v1.0", {"crew": "v2.0", "dash": "v2.0"}, {"crew": "v1.0"}, available)
    again = plan_upgrade("v2.0", "v1.0", {"crew": "v2.0", "dash": "v2.0"}, {"crew": "v1.0"}, available)
    reordered = plan_upgrade("v2.0", "v1.0", {"dash": "v2.0", "crew": "v2.0"}, {"crew": "v1.0"}, available)
    assert first == again == reordered
