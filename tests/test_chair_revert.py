from agent_tools.chair_revert import revert_check, revert_stopped
from agent_tools.chair_types import LandedMain


def _landed(
    ci: str,
    smoke: str,
    ci_output: str = "",
    smoke_output: str = "",
    green_after: bool | None = None,
    commit: str = "abc123",
) -> LandedMain:
    m: LandedMain = {
        "initiative": "init-a",
        "phase": "p1",
        "repo": "/repos/a",
        "pr": 7,
        "commit": commit,
        "ci": ci,  # type: ignore[typeddict-item]
        "ci_output": ci_output,
        "smoke": smoke,  # type: ignore[typeddict-item]
        "smoke_output": smoke_output,
    }
    return m if green_after is None else {**m, "green_after": green_after}


def _rerun(commit: str = "abc123") -> dict[str, object]:
    return {
        "kind": "rerun_main",
        "initiative": "init-a",
        "phase": "p1",
        "repo": "/repos/a",
        "commit": commit,
    }


def _revert(reason: str) -> dict[str, object]:
    return {
        "kind": "revert_land",
        "initiative": "init-a",
        "phase": "p1",
        "repo": "/repos/a",
        "pr": 7,
        "commit": "abc123",
        "reason": reason,
    }


def test_red_ci_reverts_with_ci_output() -> None:
    assert revert_check([_landed("red", "pending", ci_output="3 failed")]) == [_revert("3 failed")]


def test_green_ci_and_smoke_ok_plans_nothing() -> None:
    assert revert_check([_landed("green", "ok")]) == []


def test_green_ci_and_failed_smoke_reverts_with_smoke_output() -> None:
    assert revert_check([_landed("green", "failed", smoke_output="exit 1")]) == [_revert("exit 1")]


def test_pending_ci_and_pending_smoke_plans_nothing() -> None:
    assert revert_check([_landed("pending", "pending")]) == []


def test_red_ci_and_failed_smoke_give_one_action_with_ci_output() -> None:
    assert revert_check([_landed("red", "failed", ci_output="3 failed", smoke_output="exit 1")]) == [
        _revert("3 failed")
    ]


def test_infra_ci_reruns_main_and_does_not_revert() -> None:
    assert revert_check([_landed("infra", "pending")]) == [_rerun()]


def test_red_ci_with_green_after_plans_nothing() -> None:
    assert revert_check([_landed("red", "pending", ci_output="3 failed", green_after=True)]) == []


def test_infra_ci_with_green_after_plans_nothing() -> None:
    assert revert_check([_landed("infra", "pending", green_after=True)]) == []


def test_failed_smoke_with_green_after_plans_nothing() -> None:
    assert revert_check([_landed("green", "failed", smoke_output="exit 1", green_after=True)]) == []


def test_red_ci_with_green_after_false_still_reverts() -> None:
    assert revert_check([_landed("red", "pending", ci_output="3 failed", green_after=False)]) == [_revert("3 failed")]


def test_infra_then_red_give_rerun_then_revert_in_input_order() -> None:
    landed = [_landed("infra", "pending", commit="aaa111"), _landed("red", "pending", ci_output="3 failed")]
    assert revert_check(landed) == [_rerun("aaa111"), _revert("3 failed")]


def test_two_reverted_stop() -> None:
    assert revert_stopped(["reverted", "reverted"]) is True


def test_held_between_reverted_does_not_stop() -> None:
    assert revert_stopped(["reverted", "held", "reverted"]) is False


def test_one_reverted_does_not_stop() -> None:
    assert revert_stopped(["reverted"]) is False
