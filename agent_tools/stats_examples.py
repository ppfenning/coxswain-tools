"""Training examples for the local system-one backend, built from task records.

The state strings match what graphs slices out of the node prompt at run time:
`plan` and `change_facts` are Python `str()` of the record's dict, never JSON.
"""

from __future__ import annotations

import json
from collections.abc import Callable

VERDICTS = ("approve", "revise", "reject")


def _text(value: object) -> str | None:
    """The Python text of a dict or str field; None for any other type or an empty value."""
    return str(value) if isinstance(value, (dict, str)) and value else None


def _example(state: dict[str, str | None], label: str | None) -> dict | None:
    return {"state": state, "label": label} if label and all(state.values()) else None


def handoff_example(record: dict) -> dict | None:
    """`yes` when handoff.complete is True, `no` when it is False; a non-bool is skipped."""
    handoff = record.get("handoff")
    complete = handoff.get("complete") if isinstance(handoff, dict) else None
    label = {True: "yes", False: "no"}.get(complete) if isinstance(complete, bool) else None
    state = {"plan": _text(record.get("plan")), "change_facts": _text(record.get("change_facts"))}
    return _example(state, label)


def review_charter_example(record: dict) -> dict | None:
    """The state's `plan` is the ticket id, because graphs slices it from the Task line."""
    build, review = record.get("build"), record.get("review")
    verdict = review.get("verdict") if isinstance(review, dict) else None
    label = verdict if verdict in VERDICTS else None
    state = {
        "patch": _text(build.get("patch")) if isinstance(build, dict) else None,
        "plan": _text(record.get("ticket")),
    }
    return _example(state, label)


BUILDERS: dict[str, Callable[[dict], dict | None]] = {
    "handoff": handoff_example,
    "review_charter": review_charter_example,
}


def parse_record(text: str) -> dict | None:
    """The task record in `text`, or None when it is not a JSON object."""
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def since_ok(record: dict, since: str | None) -> bool:
    """`date` is an ISO string; only its first ten characters (YYYY-MM-DD) are compared."""
    date = record.get("date")
    return since is None or (isinstance(date, str) and date[:10] >= since)


def build_examples(records: list[dict | None], role: str, since: str | None) -> list[dict]:
    """One example per usable record; `None` (an unparseable file) and every other unusable record is dropped."""
    build = BUILDERS[role]
    return [
        example
        for record in records
        if record is not None and since_ok(record, since)
        if (example := build(record)) is not None
    ]


def format_lines(examples: list[dict]) -> str:
    return "".join(json.dumps(example, ensure_ascii=False) + "\n" for example in examples)
