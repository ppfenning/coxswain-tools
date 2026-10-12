from dataclasses import replace

from agent_tools.chair_exec import perform
from tests.test_chair_exec import _clear, _deps, _touched


class PendingLands:
    """A land sink with lands in flight; `landing` is what it exposes of each."""

    def __init__(self, landing: list[dict[str, str]]) -> None:
        self._landing = landing

    def pending(self, repo: str) -> bool:
        return any(land["repo"] == repo for land in self._landing)

    def landing(self) -> list[dict[str, str]]:
        return self._landing

    def submit(self, action, work):
        raise AssertionError("no land is submitted in these plans")

    def collect(self):
        return []


def _tick(landing: list[dict[str, str]]) -> tuple[list, list]:
    calls: list = []
    deps = replace(_deps(calls), lands=PendingLands(landing))
    actions = [{**_clear("B"), "repo": "R"}, {"kind": "relaunch", "initiative": "B", "repo": "R", "epoch": 1}]
    return perform(actions, deps, lambda: 1, False), calls


def test_another_initiative_s_pending_land_does_not_hold_back_the_clear_and_relaunch() -> None:
    results, calls = _tick([{"initiative": "A", "phase": "p", "repo": "R"}])
    assert [r["status"] for r in results] == ["done", "done"]
    assert ("delete", "R", "epic/B/*") in calls
    assert any(c[0] == "run" and c[1][0] == "cox" for c in _touched(calls))


def test_the_same_initiative_s_pending_land_still_skips_the_clear_and_relaunch() -> None:
    results, calls = _tick([{"initiative": "B", "phase": "p", "repo": "R"}])
    assert [r["status"] for r in results] == ["skipped", "skipped"]
    assert results[0]["reason"] == "a land in R is still running"
    assert _touched(calls) == []


def test_a_pending_land_of_unknown_initiative_still_skips_the_clear_and_relaunch() -> None:
    results, calls = _tick([{"initiative": "", "phase": "p", "repo": "R"}])
    assert [r["status"] for r in results] == ["skipped", "skipped"]
    assert _touched(calls) == []
