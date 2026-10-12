"""The `approved` source of a chair tick: approved work items with phase_done and needs.

`approved_rows` is pure. `read_approved` is the edge: it loads items through the existing work-store readers
(`route.parse_frontmatter`, `route.work_item`, `route.with_store_states`, `run_store.work_items`) and hands them over.
"""
import json
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_tools import route, run_store
from agent_tools.chair_facts import run_initiative
from agent_tools.chair_read_quarantined import WorkFiles, task_files
from agent_tools.chair_types import ApprovedTask, PhaseTasks
from agent_tools.remote_lane import fetched_record_path, land_needs_fetch, remote_record_path

Item = Mapping[str, Any]


_COMPLETE = frozenset({"done", "approved", "dropped"})


def phase_done(item: Item, items: Sequence[Item]) -> bool:
    """True when every other task in the item's initiative and phase is done, approved or dropped; a lone task is True.

    Approved siblings count: requiring them to be landed first deadlocked every phase with two approved tasks."""
    return all(
        other["state"] in _COMPLETE
        for other in items
        if other is not item
        and other["initiative"] == item["initiative"]
        and other["phase"] == item["phase"]
    )


def approved_rows(items: Sequence[Item]) -> list[ApprovedTask]:
    """One row per item whose state is `approved`. `repo` is the item's own, `""` when the caller found none."""
    return [
        {
            "id": item["id"],
            "initiative": item["initiative"],
            "repo": item.get("repo", ""),
            "phase": item["phase"],
            "phase_done": phase_done(item, items),
            "needs": list(item.get("needs", [])),
            "run": "",
            "needs_fetch": False,
        }
        for item in items
        if item["state"] == "approved"
    ]


def phase_task_rows(items: Sequence[Item]) -> list[PhaseTasks]:
    """One row per (initiative, phase) holding an approved item, listing every item of the phase as (id, state)."""
    keys = dict.fromkeys((i["initiative"], i["phase"]) for i in items if i["state"] == "approved")
    return [
        {
            "initiative": initiative,
            "phase": phase,
            "tasks": [(i["id"], i["state"]) for i in items if (i["initiative"], i["phase"]) == (initiative, phase)],
        }
        for initiative, phase in keys
    ]


def mark_landed(items: Sequence[Item], landed: Collection[str]) -> list[Item]:
    """Each approved item whose id is in `landed` reads `done`, so a phase plan leaves it out; other items are unchanged."""
    return [{**i, "state": "done"} if i["state"] == "approved" and i["id"] in landed else i for i in items]


def _run_order(run: str) -> tuple[int, str]:
    """`x-10` sorts after `x-2`: by the numeric suffix, then the name."""
    tail = run.rsplit("-", 1)[-1]
    return (int(tail) if tail.isdigit() else -1, run)


def with_runs(
    tasks: Sequence[ApprovedTask],
    stranded: Sequence[Mapping[str, Any]],
    fetch_facts: Mapping[str, tuple[bool, bool]] = {},
) -> list[ApprovedTask]:
    """Each task gets the newest run with a stranded row for it under its initiative and phase, empty when none has.

    `fetch_facts` maps run -> (has_remote_record, fetched); `needs_fetch` is False for a run it does not list.
    """
    runs = {
        (run_initiative(str(row["run"])), str(row["phase"]), str(row["task"])): str(row["run"])
        for row in sorted(stranded, key=lambda s: _run_order(str(s["run"])))
    }
    return [
        {**t, "run": run, "needs_fetch": bool(run) and land_needs_fetch(*fetch_facts.get(run, (False, False)))}
        for t in tasks
        for run in [runs.get((t["initiative"], t["phase"], t["id"]), "")]
    ]


def read_fetch_facts(runs_dir: Path, stranded: Sequence[Mapping[str, Any]]) -> dict[str, tuple[bool, bool]]:
    """Edge. run -> (has_remote_record, fetched) for every run a stranded row names."""
    return {
        run: (remote_record_path(runs_dir, run).exists(), fetched_record_path(runs_dir, run).exists())
        for run in {str(row["run"]) for row in stranded}
        if run
    }


def _repo_from_text(text: str) -> str:
    return str(route.parse_frontmatter(text)[0].get("repo", ""))


def _repo_of(initiative_md: Path) -> str:
    try:
        text = initiative_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    return _repo_from_text(text)


def _load_items(ws: Path, files: WorkFiles | None = None) -> list[dict]:
    repos: dict[str, str] = {}
    items = []
    if files is None:
        texts = {}
        for path in sorted((ws / "work").glob("*/*/*.md")):
            try:
                texts[path] = path.read_text(encoding="utf-8")
            except OSError:
                continue
    else:
        texts = task_files(ws, files)
    for path, text in texts.items():
        if path.name == "initiative.md":
            continue
        initiative = path.parent.parent.name
        if initiative not in repos:
            initiative_md = path.parent.parent / "initiative.md"
            if files is None:
                repos[initiative] = _repo_of(initiative_md)
            else:
                text_md = files.get(initiative_md)
                repos[initiative] = _repo_from_text(text_md) if text_md is not None else ""
        item = route.work_item(
            route.parse_frontmatter(text)[0], initiative=initiative, phase_dir=path.parent.name, stem=path.stem
        )
        items.append({"repo": repos[initiative], **item})
    return items


def read_approved(
    ws: Path, mode: str = "files", files: WorkFiles | None = None, store_rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[ApprovedTask]:
    """Edge. Approved rows for the work store under `ws`; under mode "store" each state comes from the run store.

    `files` and `store_rows` stand in for the work-file and store reads when a tick already holds them."""
    items = _load_items(ws, files)
    rows = store_rows if store_rows is not None else (run_store.work_items(ws / "runs") if mode == "store" else [])
    return approved_rows(route.with_store_states(items, rows, mode))


def _is_landed(path: Path) -> bool:
    """Edge. True when the task record at `path` has `landed: true`; an unreadable or malformed record is not landed."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and record.get("landed") is True


def read_landed_task_ids(runs_dir: Path) -> frozenset[str]:
    """Edge. Ids of tasks whose record under `runs_dir/<run>/tasks/*/<task>.json` says landed, the fact `runs clean` reads."""
    return frozenset(p.stem for p in runs_dir.glob("*/tasks/*/*.json") if _is_landed(p))


def read_phase_tasks(
    ws: Path, mode: str = "files", files: WorkFiles | None = None, store_rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[PhaseTasks]:
    """Edge. The task states of every phase with approved work, from the same reads as `read_approved`.

    An approved task whose record already says landed reads `done`: the work-item state can lag the record, and the
    land refuses a task another machine landed."""
    items = _load_items(ws, files)
    rows = store_rows if store_rows is not None else (run_store.work_items(ws / "runs") if mode == "store" else [])
    return phase_task_rows(mark_landed(route.with_store_states(items, rows, mode), read_landed_task_ids(ws / "runs")))
