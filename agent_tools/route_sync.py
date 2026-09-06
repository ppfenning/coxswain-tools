"""The pure planner for the one-way GitHub Projects mirror: work-store items
in, `gh` steps out. No subprocess, filesystem or clock; the edge hands in plain data."""

from __future__ import annotations

from dataclasses import dataclass

_STATE_LABEL = {
    "intake": "Intake",
    "ready": "Ready",
    "in_flight": "In flight",
    "quarantined": "Quarantined",
    "done": "Done",
}

_LABEL = "coxswain"


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    body: str
    repo: str
    state: str
    phase: str
    run: str
    cost_usd: float
    gate: str
    issue: str | None


def _fields(item: Item) -> dict | None:
    """The board's five field values, or `None` for an unrecognized
    `item.state` — a step-less refusal, not a `KeyError` three calls up."""
    label = _STATE_LABEL.get(item.state)
    if label is None:
        return None
    return {
        "State": label,
        "Phase": item.phase,
        "Run": item.run,
        "Cost": f"${item.cost_usd:.2f}",
        "Gate": item.gate,
    }


def _issue_steps(item: Item, issues: dict) -> tuple[list[dict], str | None]:
    """`issue_create` with no issue key, `issue_edit` when the labelled issue
    on file has drifted, no step when `item.issue` is not in `issues` (it
    lost the `coxswain` label, or is gone — nothing on file to edit against).
    The paired key is the item's own issue key, or `None` for a fresh item —
    not a work-store id standing in for one — that the edge fills in once
    `issue_create` runs."""
    if item.issue is None:
        step = {"kind": "issue_create", "repo": item.repo, "title": item.title,
                 "body": item.body, "label": _LABEL, "item_id": item.id}
        return [step], None
    stored = issues.get(item.issue)
    if stored is not None and (stored.get("title") != item.title or stored.get("body") != item.body):
        return [{"kind": "issue_edit", "issue": item.issue, "title": item.title, "body": item.body}], item.issue
    return [], item.issue


def _project_steps(key: str | None, wanted: dict, project_items: dict) -> list[dict]:
    """`project_add` when no project item exists yet, then a `project_set`
    per differing field. A `None` key (fresh item) never matches, so both
    always plan, carrying `issue: None` for the edge to resolve."""
    current = project_items.get(key)
    known = current or {}
    add_step = [{"kind": "project_add", "issue": key}] if current is None else []
    set_steps = [{"kind": "project_set", "issue": key, "field": field, "value": value}
                 for field, value in wanted.items() if known.get(field) != value]
    return add_step + set_steps


def _item_steps(item: Item, issues: dict, project_items: dict) -> list[dict]:
    wanted = _fields(item)
    if wanted is None:
        return [{"kind": "refuse", "item_id": item.id, "detail": f"unknown state: {item.state!r}"}]
    issue_steps, key = _issue_steps(item, issues)
    project_steps = _project_steps(key, wanted, project_items)
    writeback = [{"kind": "writeback", "item_id": item.id, "issue": key}] if item.issue is None else []
    return issue_steps + project_steps + writeback


def plan(items: list[Item], issues: dict, project_items: dict, tracker: str) -> list[dict]:
    """Steps grouped per item, create/edit then add then sets then writeback,
    so the edge runs them top to bottom. Empty unless tracker is `github-projects`."""
    if tracker != "github-projects":
        return []
    return [step for item in items for step in _item_steps(item, issues, project_items)]


def _line(step: dict) -> str:
    kind = step["kind"]
    if kind == "issue_create":
        return f"issue_create {step['repo']}: {step['title']!r}"
    if kind == "issue_edit":
        return f"issue_edit {step['issue']}: {step['title']!r}"
    if kind == "project_add":
        return f"project_add {step['issue'] or '(new issue)'}"
    if kind == "project_set":
        return f"project_set {step['issue'] or '(new issue)'} {step['field']}={step['value']}"
    if kind == "writeback":
        return f"writeback {step['item_id']} -> {step['issue'] or '(new issue)'}"
    return f"refuse {step['item_id']}: {step['detail']}"


def render(steps: list[dict]) -> list[str]:
    """One line per step, for `--dry-run`."""
    return [_line(step) for step in steps]
