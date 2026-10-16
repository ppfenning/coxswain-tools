from agent_tools import route_sync
from agent_tools.route_sync import Item

_KEY = "acme/widgets#12"
_ISSUES = {_KEY: {"title": "Fix the thing", "body": "Some body.", "labels": ["coxswain"]}}
_PR = "https://github.com/acme/widgets/pull/3"


def _item(**over) -> Item:
    base = {"id": "item-1", "title": "Fix the thing", "body": "Some body.", "repo": "acme/widgets",
            "state": "in_flight", "phase": "core", "run": "run-7", "cost_usd": 1.5, "gate": "tests",
            "issue": _KEY, "pr_url": _PR}
    return Item(**{**base, **over})


def _card(**over) -> dict:
    card = {"State": "Running", "Phase": "core", "Run": "run-7", "Cost": "$1.50", "Gate": "tests", "PR": _PR}
    return {_KEY: {**card, **over}}


def _plan(item: Item, project_items: dict) -> list[dict]:
    return route_sync.plan([item], _ISSUES, project_items, "github-projects")


def test_an_in_flight_item_plans_running_and_its_pr_link():
    steps = _plan(_item(), {_KEY: {}})
    sets = {s["field"]: s["value"] for s in steps if s["kind"] == "project_set"}
    assert sets["State"] == "Running" and sets["PR"] == _PR
    assert not any("was" in s for s in steps)


def test_a_card_already_matching_plans_no_step():
    assert _plan(_item(), _card()) == []


def test_a_card_edit_loses_and_the_report_says_it_was_overwritten():
    steps = _plan(_item(), _card(State="Landed"))
    assert steps == [{"kind": "project_set", "issue": _KEY, "field": "State", "value": "Running", "was": "Landed"}]
    assert "overwrote" in route_sync._line(steps[0])
