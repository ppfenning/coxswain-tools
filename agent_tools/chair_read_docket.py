"""Readers for the chair's `docket` and `work_store_ready` sources.

The builder is `route.initiative_summaries`, the initiatives rows behind `route context`
(`route.context_document` assembles them into the `--json` doc). Its rows are {id, phase, ready,
awaiting_merge?}, so the ids, needs and landed sets FactsDeps asks for come from the same work
items the builder was given. The builder itself is called, never copied.

unknown: `max_in_flight` has no source in the profile or the pacing policy, so the caller passes it.
unknown: `started` is taken as any task in the initiative already in_progress, approved, done or dropped.
"""
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_tools import epic, queue_rows, route, run_store
from agent_tools.chair_read_quarantined import WorkFiles, task_files

Row = Mapping[str, Any]

_BEGUN = frozenset({"in_progress", "approved"}) | route.TERMINAL


def _task(i: Row) -> dict[str, Any]:
    return {"id": i["id"], "needs": list(i["needs"]), "requires": queue_rows.requires_of(i)}


def _held_needs(task: Row, own: Sequence[Row]) -> list[str]:
    """Ids in `task`'s needs that sit in the task's own phase and are approved but not done, in needs order."""
    held = {i["id"] for i in own if i["phase"] == task["phase"] and i["state"] == "approved"}
    return [n for n in task["needs"] if n in held]


def _held_task(i: Row, own: Sequence[Row]) -> dict[str, Any]:
    return {"id": i["id"], "phase": i["phase"], "needs": _held_needs(i, own)}


def _initiative_row(summary: Row, items: Sequence[Row]) -> dict[str, Any]:
    own = [i for i in items if i["initiative"] == summary["id"]]
    landed = {i["id"] for i in own if i["state"] in route.TERMINAL}
    held = [i for i in own if i["state"] == "ready" and _held_needs(i, own)]
    held_ids = {i["id"] for i in held}
    ready = [
        i for i in own
        if summary["ready"] and i["phase"] == summary["phase"] and i["state"] == "ready" and set(i["needs"]) <= landed
        and i["id"] not in held_ids
    ]
    # A ready task behind a need that has not landed is carried, not dropped, so the planner can report it.
    # A held task stays here too: it waits on an unlanded need, so `waiting_tasks` keeps its meaning.
    waiting = [i for i in own if i["state"] == "ready" and not set(i["needs"]) <= landed]
    return {
        "id": summary["id"],
        "started": any(i["state"] in _BEGUN for i in own),
        "ready_tasks": [_task(i) for i in ready],
        "waiting_tasks": [_task(i) for i in waiting],
        "held_tasks": [_held_task(i, own) for i in held],
        "landed": landed,
    }


def _priority(item: Row) -> int:
    """An item's `priority`; a missing key or a value that is not an int reads as 0."""
    try:
        return int(item.get("priority") or 0)
    except (TypeError, ValueError):
        return 0


def _launchable(row: Row, items: Sequence[Row]) -> list[Row]:
    """The items behind `row`'s `ready_tasks`: ready, needs landed, not held, not leased."""
    ids = {t["id"] for t in row["ready_tasks"]}
    return [i for i in items if i["initiative"] == row["id"] and i["id"] in ids]


def _initiative_priority(row: Row, items: Sequence[Row]) -> int:
    """The highest priority among `row`'s launchable items, 0 when it has none."""
    return max((_priority(i) for i in _launchable(row, items)), default=0)


def _age(value: object) -> datetime | None:
    """An `updated_at` as an aware UTC datetime; naive reads as UTC, and anything unparseable reads as None."""
    if isinstance(value, datetime):
        parsed: datetime | None = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
    else:
        parsed = None
    return None if parsed is None else (parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC))


def _ready_age(row: Row, items: Sequence[Row]) -> datetime | None:
    """The earliest `updated_at` among `row`'s launchable items, None when none has one."""
    return min((a for i in _launchable(row, items) if (a := _age(i.get("updated_at"))) is not None), default=None)


_NO_AGE = datetime.max.replace(tzinfo=UTC)


def _by_priority(rows: Sequence[Row], items: Sequence[Row]) -> list[dict[str, Any]]:
    """`rows` by priority descending, then oldest launchable task first with no age last, then id ascending."""
    return sorted(rows, key=lambda r: (-_initiative_priority(r, items), _ready_age(r, items) or _NO_AGE, r["id"]))


def _initiative_rows(summaries: Sequence[Row], items: Sequence[Row]) -> list[dict[str, Any]]:
    """One row per summary, then one per initiative `initiative_summaries` omitted that holds a waiting task.

    Wrong belief this guards: that an initiative missing from the summaries has nothing to report. The builder
    omits one whose only ready task waits on an unlanded need, and the chair must still say what it waits on."""
    listed = {s["id"] for s in summaries}
    omitted = sorted({i["initiative"] for i in items if i["state"] == "ready" and i["initiative"] not in listed})
    held = [_initiative_row({"id": initiative, "phase": None, "ready": 0}, items) for initiative in omitted]
    rows = [*(_initiative_row(s, items) for s in summaries), *(row for row in held if row["waiting_tasks"])]
    return _by_priority(rows, items)


def docket_from_builder(summaries: Sequence[Row], items: Sequence[Row], busy_lanes: int, max_in_flight: int) -> dict[str, Any]:
    """`summaries` is `route.initiative_summaries(items)`; `items` are the work items it was built from."""
    return {
        "initiatives": _initiative_rows(summaries, items),
        "busy_lanes": busy_lanes,
        "max_in_flight": max_in_flight,
    }


def work_store_ready(docket: Row) -> bool:
    return any(i["ready_tasks"] for i in docket["initiatives"])


def _claimed(row: Row, now: str) -> bool:
    return bool(row["holder"]) and row["expires_at"] > now


def _item_of(row: Row) -> dict[str, Any]:
    return {
        "id": row["task_id"], "initiative": row["initiative"], "phase": row["phase"], "state": row["state"],
        "needs": list(row["needs"]), "requires": queue_rows.row_requires(row),
        "priority": row.get("priority", (row.get("extra") or {}).get("priority")),
        "updated_at": row.get("updated_at"),
    }


def _with_item_ages(items: Sequence[Row], aged: Sequence[Row]) -> list[dict[str, Any]]:
    """`items` lacking `updated_at` take it from the `aged` work item with the same initiative and id."""
    ages = {(a["initiative"], a["id"]): a.get("updated_at") for a in aged}
    return [{**i, "updated_at": i.get("updated_at") or ages.get((i["initiative"], i["id"]))} for i in items]


def docket_from_rows(rows: Sequence[Row], now: str, aged: Sequence[Row] = ()) -> list[dict[str, Any]]:
    """The builder's `initiatives` over the task rows, less each task a live lease holds (`holder` set, `expires_at` after ISO `now`).

    Queue rows carry no `updated_at` (`run_store._QUEUE_COLUMNS`), so ages come from `aged`, the `_work_items` rows,
    unless a queue row supplies one. Ordering runs after the lease filter, so a leased task never sets an age."""
    tasks = [r for r in rows if r["kind"] == "task"]
    items = _with_item_ages([_item_of(r) for r in tasks], aged)
    claimed = {(r["initiative"], r["task_id"]) for r in tasks if _claimed(r, now)}
    unclaimed = [
        {
            **i,
            "ready_tasks": [t for t in i["ready_tasks"] if (i["id"], t["id"]) not in claimed],
            "waiting_tasks": [t for t in i["waiting_tasks"] if (i["id"], t["id"]) not in claimed],
        }
        for i in _initiative_rows(route.initiative_summaries(items), items)
    ]
    return _by_priority(unclaimed, items)


def _text(p: Path) -> str | None:
    try:
        return p.read_text()
    except OSError:
        return None


def _with_store_ages(items: Sequence[Row], rows: Sequence[Row]) -> list[dict[str, Any]]:
    """`items` with `updated_at` from the store row for the same initiative and task; an item with no row keeps its own."""
    ages = {(r.get("initiative"), r.get("task_id")): r.get("updated_at") for r in rows}
    return [{**i, "updated_at": ages.get((i["initiative"], i["id"])) or i.get("updated_at")} for i in items]


def _work_items(
    ws: Path, mode: str, files: WorkFiles | None = None, store_rows: Sequence[Mapping] | None = None,
) -> list[dict]:
    """Every work item under `work/<initiative>/<phase>/<task>.md`, its state from the store under mode "store".

    `files` and `store_rows` stand in for the work-file and store reads when a tick already holds them."""
    texts = (
        task_files(ws, files) if files is not None
        else {p: t for p in sorted((ws / "work").glob("*/*/*.md")) if (t := _text(p)) is not None}
    )
    items = [
        route.work_item(route.parse_frontmatter(text)[0], initiative=p.parent.parent.name, phase_dir=p.parent.name, stem=p.stem)
        for p, text in texts.items()
        if p.name != "initiative.md"
    ]
    rows = store_rows if store_rows is not None else (run_store.work_items(ws / "runs") if mode == "store" else [])
    return _with_store_ages(route.with_store_states(items, rows, mode), rows)


def local_runs(runs_dir: Path, now: str) -> tuple[int, set[str]]:
    """Edge. How many local pidfile runs are live, and every run a local pidfile names."""
    local = {p.stem: route.parse_pid(t) for p in sorted(runs_dir.glob("*.pid")) if (t := _text(p)) is not None}
    live = [rid for rid, pid in local.items() if epic.run_live(pid, runs_dir / f"{rid}.pid", now=now)]
    return len(live), set(local)


def _busy_lanes(runs_dir: Path, now: str) -> int:
    """Runs with a live local pidfile, plus live store leases no local pidfile names."""
    live, named = local_runs(runs_dir, now)
    return live + len(run_store.remote_lanes(run_store.live_lanes(runs_dir, now), named))


def read_docket(
    ws: Path, mode: str, max_in_flight: int, now: Callable[[], datetime] = lambda: datetime.now(UTC),
    files: WorkFiles | None = None, store_rows: Sequence[Mapping] | None = None,
) -> dict[str, Any]:
    """Edge: the builder over the workspace `ws` and its store. `mode` is `work_state.work_state_mode`'s answer."""
    items = _work_items(ws, mode, files, store_rows)
    busy = _busy_lanes(ws / "runs", now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    return docket_from_builder(route.initiative_summaries(items), items, busy, max_in_flight)


def docket_source(ws: Path, mode: str, max_in_flight: int) -> Callable[[], dict[str, Any]]:
    return lambda: read_docket(ws, mode, max_in_flight)


def work_store_ready_source(ws: Path, mode: str, max_in_flight: int) -> Callable[[], bool]:
    return lambda: work_store_ready(read_docket(ws, mode, max_in_flight))
