"""Where the work item files and the store's `work_items` table disagree. A report; the store is not authoritative."""

from __future__ import annotations

import json

from agent_tools import records

_COLUMNS = ("initiative", "task_id", "kind", "file_state", "store_state")


def drift(files: list[tuple[str, str, str]], rows: list[dict]) -> list[dict]:
    """Disagreements keyed on (initiative, task_id), sorted by that key.

    The file side's task_id is the frontmatter `id`, else the file stem, as the store mirror writes it.
    Kinds: `state` (both sides, states differ), `file_only`, `store_only`. The missing side's state is None."""
    on_file = {(i, t): s for i, t, s in files}
    in_store = {(r["initiative"], r["task_id"]): r["state"] for r in rows}
    found = [
        {"initiative": i, "task_id": t, "kind": kind, "file_state": on_file.get((i, t)), "store_state": in_store.get((i, t))}
        for i, t in on_file.keys() | in_store.keys()
        for kind in [
            "file_only" if (i, t) not in in_store
            else "store_only" if (i, t) not in on_file
            else "state" if on_file[(i, t)] != in_store[(i, t)]
            else None
        ]
        if kind is not None
    ]
    return sorted(found, key=lambda d: (d["initiative"], d["task_id"], d["kind"]))


def format_text(drift_rows: list[dict]) -> str:
    """The table, or `no drift` when there is nothing to show. A missing state prints as `-`."""
    shown = [{**d, "file_state": d["file_state"] or "-", "store_state": d["store_state"] or "-"} for d in drift_rows]
    return records.format_table(shown, _COLUMNS) if shown else "no drift"


def format_json(drift_rows: list[dict]) -> str:
    return json.dumps(drift_rows, indent=2, sort_keys=True)
