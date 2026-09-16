"""Approved task records dropped without a quarantine (`docs/design/triage.md` §3)."""

from __future__ import annotations


def _scoped_item(record: dict, candidates: list[dict]) -> dict | None:
    """The one item this record's task names. A task id repeats across
    initiatives and across phases of the same initiative, so a
    `candidates` list with more than one entry resolves only when the
    record's own `initiative` and `phase` narrow it to one; otherwise this
    comes back `None` rather than guessing which item is meant."""
    initiative = record.get("initiative")
    phase = record.get("phase")
    scoped = [c for c in candidates
              if (initiative is None or c.get("initiative") == initiative)
              and (phase is None or c.get("phase") == phase)]
    return scoped[0] if len(scoped) == 1 else None


def _approved_and_unlanded(record: dict) -> bool:
    return (record.get("review", {}).get("verdict") == "approve"
            and record.get("arbitration", {}).get("verdict") == "approve"
            and not record.get("landed"))


def _remedy(record: dict, item: dict | None) -> str | None:
    """The exact `cox runs land` line, or `None` when no repo path can be
    resolved — a line that looks runnable and is not is worse than none."""
    repo = record.get("repo") or (item.get("repo") if item else None)
    if not repo:
        return None
    return f"cox runs land {record.get('run')} --task {record.get('task')} --repo {repo}"


def _row(record: dict, item: dict | None) -> dict:
    return {
        "run": record.get("run"), "task": record.get("task"), "phase": record.get("phase"),
        "branch": record.get("branch"), "remedy": _remedy(record, item),
    }


def stranded(records: list[dict], items: list[dict]) -> list[dict]:
    """Rows for approved, unlanded records whose matching item is not
    `done`. `items` are expected to carry a resolved `repo` (the edge
    reads it from the initiative's own frontmatter, since a work item's
    `id`/`initiative` name a ticket, not a filesystem path). A record
    whose task id does not resolve to one item under its own initiative
    and phase is still reported, with `remedy` left `None` rather than
    matched against a foreign item's state or repo."""
    by_id: dict[str, list[dict]] = {}
    for item in items:
        by_id.setdefault(item.get("id"), []).append(item)
    rows = []
    for record in records:
        if not _approved_and_unlanded(record):
            continue
        candidates = by_id.get(record.get("task"), [])
        if not candidates:
            continue
        item = _scoped_item(record, candidates)
        if item is not None and item.get("state") == "done":
            continue
        rows.append(_row(record, item))
    return rows
