"""`route edit` core: rewrite a queued intake, or an initiative that has not started, and say what changed as a diff.

`plan_edit` and `check_options` are pure. `apply_edit` is the edge: it reads the store and the file, asks
`route_guard` whether the target may change, and writes the store first and the file second, the order
`cox route file` uses. The id and the path never change on an edit.

unknown: the store has no row kind for `work/<id>/initiative.md` (`queue_rows.parse_item` returns None for it and
`route_import` skips it), so an initiative edit rewrites that file only. An initiative's task rows are not touched.
`live_runs` is required, with no default, so a caller cannot skip the live-run check by omission. Build it as
`{lane.run for lane in run_store.live_lanes(runs_dir, now)}`; a lane carries no initiative, so the guard refuses on any live run.
A store that cannot take an intake's row is one printed warning from `route.write_filed_item` and the file is still written.
A task ticket (a `work_items` row that `resolve_target` does not name) is edited store first: `run_store.upsert_row_detail`
takes the stored row with only the named fields changed, then `queue_export.export_file(force=True)` rewrites its one file
from that row and records the hash, so a hand-edited file is overwritten by the store text. `--surfaces` and `--state`
apply to a task ticket only, and a task ticket has no live-run guard.
"""

from __future__ import annotations

import difflib
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import NamedTuple

from agent_tools import queue_export, route, route_guard, run_store

DONE = route_guard.DONE
REFUSED = route_guard.REFUSED
FAILED = 1  # a read or write failed after the options and the guard passed, as in route.write_filed_item


class EditResult(NamedTuple):
    text: str  # the unified diff on exit 0, the reason otherwise
    code: int  # DONE, REFUSED or FAILED


def check_options(
    title: str | None, body: str | None, body_file: str | None, repo: str | None,
    surfaces: str | None = None, state: str | None = None,
) -> str | None:
    """The reason the options cannot be applied, or None. Body and body-file are mutually exclusive.
    `surfaces` or `state` alone counts as a change; whether they are valid is `check_task_options`."""
    if body is not None and body_file is not None:
        return "give either --body or --body-file, not both"
    if surfaces is None and state is None and title is None and body is None and body_file is None and repo is None:
        return "nothing to change: give --title, --body, --body-file, --repo, --surfaces or --state"
    if title == "" or repo == "":
        return "--title and --repo must not be empty"
    return None


def _split_surfaces(surfaces: str) -> list[str]:
    return [path.strip() for path in surfaces.split(",") if path.strip()]


def check_task_options(state: str | None, surfaces: str | None) -> str | None:
    """The reason a task ticket's `state` or `surfaces` cannot be applied, or None. A state must be one of
    `route.STATES`; `surfaces` is comma-separated and must name at least one path."""
    if state is not None and state not in route.STATES:
        return f"invalid state {state!r}: use one of {', '.join(sorted(route.STATES))}"
    if surfaces is not None and not _split_surfaces(surfaces):
        return "--surfaces must name at least one path"
    return None


def plan_task_row(
    row: dict, *, title: str | None = None, body: str | None = None, surfaces: str | None = None, state: str | None = None
) -> dict:
    """A copy of the stored task `row` with only the named fields replaced; `surfaces` replaces the list.
    A stored body opens with the newline after the closing fence, so a new one is wrapped the same way."""
    named = {
        "title": title,
        "body": None if body is None else f"\n{body}\n",
        "surfaces": None if surfaces is None else _split_surfaces(surfaces),
        "state": state,
    }
    return {**row, **{key: value for key, value in named.items() if value is not None}}


def _split(text: str) -> tuple[list[str], str] | None:
    """`(header lines, everything from the closing fence on)`, or None when `text` has no frontmatter."""
    opening, closing = "---\n", "\n---\n"
    close_index = text.find(closing, len(opening)) if text.startswith(opening) else -1
    if close_index == -1:
        return None
    return text[len(opening):close_index].split("\n"), text[close_index:]


def plan_edit(
    text: str, *, title: str | None = None, body: str | None = None, repo: str | None = None, path: str = ""
) -> tuple[str, str]:
    """`(new text, unified diff)` of `text` with the given overrides. A header line not named stays byte for byte.
    An empty `body` falls back to the title, as `route.intake_file` does. The diff is empty when nothing changed.
    `text` must carry frontmatter; one without it comes back unchanged."""
    parts = _split(text)
    if parts is None:
        return text, ""
    header_lines, rest = parts
    changes = [(key, value) for key, value in (("title", title), ("repo", repo)) if value is not None]
    header = "\n".join(route._with_header_fields(header_lines, changes))
    fields, _ = route.parse_frontmatter(text)
    new_body = None if body is None else (body or title or fields.get("title", ""))
    tail = rest if new_body is None else f"\n---\n\n{new_body}\n"
    new_text = f"---\n{header}{tail}"
    diff = "".join(
        difflib.unified_diff(
            text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return new_text, diff


def _rewrite(path: Path, runs_dir: Path, is_intake: bool, new_text: str) -> str | None:
    """Edge: the store first and the file second for an intake; the file alone for an initiative. A reason on failure."""
    if is_intake:
        return None if route.write_filed_item(runs_dir, path, "intake", (path.name,), new_text) == 0 else f"could not write {path}"
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return f"could not write {path}: {exc}"
    return None


def _edit_task(
    runs_dir: Path, ws: Path, row: dict, *, title: str | None, body: str | None, body_file: str | None,
    surfaces: str | None, state: str | None, dry_run: bool,
) -> EditResult:
    """Edge: the store row first through `upsert_row_detail`, then its one file through `export_file(force=True)`.
    The diff is the file on disk against the text the new row renders, so a hand-edited file shows what is overwritten."""
    try:
        new_body = Path(body_file).read_text(encoding="utf-8").removesuffix("\n") if body_file is not None else body
    except OSError as exc:
        return EditResult(f"cannot read: {exc}", FAILED)
    new_row = plan_task_row(row, title=title, body=new_body, surfaces=surfaces, state=state)
    if queue_export.is_contentless(new_row):
        return EditResult(f"task {row.get('task_id')!r} has no content to export", REFUSED)
    [(rel, new_text)] = queue_export.export_rows([new_row]).items()
    path = ws / rel
    try:
        old_text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError as exc:
        return EditResult(f"cannot read: {exc}", FAILED)
    diff = "".join(
        difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True), fromfile=f"a/{rel}", tofile=f"b/{rel}")
    )
    if dry_run or (not diff and new_row == row):
        return EditResult(diff, DONE)
    if new_row != row:
        reason = run_store.upsert_row_detail(runs_dir, new_row)
        if reason:
            return EditResult(f"the store did not take {row.get('task_id')}: {reason}", FAILED)
    try:
        queue_export.export_file(ws, new_row, force=True, hashes=queue_export.store_hashes(runs_dir))
    except OSError as exc:
        return EditResult(f"the store row was updated but {rel} could not be written: {exc}", FAILED)
    return EditResult(diff or f"store row {row.get('task_id')} updated; {rel} already matched", DONE)


def apply_edit(
    runs_dir: Path,
    ws: Path,
    id: str,
    *,
    title: str | None = None,
    body: str | None = None,
    body_file: str | None = None,
    repo: str | None = None,
    surfaces: str | None = None,
    state: str | None = None,
    dry_run: bool = False,
    live_runs: Collection[str],
) -> EditResult:
    """Edge: edit the queued intake, the initiative or the task ticket named `id`. Exit 0 returns the diff; exit 2 returns the
    reason and writes nothing, whether the options are wrong, the id is unknown, or the guard refuses. With
    `dry_run` the diff is returned and neither the file nor the store is written."""
    problem = check_options(title, body, body_file, repo, surfaces, state)
    if problem is not None:
        return EditResult(problem, REFUSED)
    rows: Sequence[dict] = run_store.read_queue(runs_dir)
    target = route_guard.resolve_target(rows, id)
    if isinstance(target, route_guard.NotFound):
        tickets = [row for row in rows if row.get("kind", "task") == "task" and row.get("task_id") == id]
        if not tickets:
            return EditResult(f"no queued intake or initiative named {id!r}", REFUSED)
        if len(tickets) > 1:
            initiatives = ", ".join(sorted(str(row.get("initiative")) for row in tickets))
            return EditResult(f"{id!r} names a task in several initiatives ({initiatives}); the id alone cannot pick one", REFUSED)
        if repo is not None:
            return EditResult("--repo applies to an intake or an initiative, not a task ticket", REFUSED)
        problem = check_task_options(state, surfaces)
        if problem is not None:
            return EditResult(problem, REFUSED)
        return _edit_task(
            runs_dir, ws, dict(tickets[0]), title=title, body=body, body_file=body_file, surfaces=surfaces, state=state, dry_run=dry_run
        )
    if surfaces is not None or state is not None:
        return EditResult("--surfaces and --state apply to a task ticket only", REFUSED)
    if isinstance(target, route_guard.QueuedIntake):
        path, is_intake = ws / "intake" / f"{target.task_id}.md", True
    else:
        tasks = [row for row in rows if row.get("kind", "task") == "task" and row.get("initiative") == id]
        guard = route_guard.initiative_guard(tasks, live_runs)
        if isinstance(guard, route_guard.Refusal):
            return EditResult(guard.reason, REFUSED)
        path, is_intake = ws / "work" / id / "initiative.md", False
    try:
        new_body = Path(body_file).read_text(encoding="utf-8").removesuffix("\n") if body_file is not None else body
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return EditResult(f"cannot read: {exc}", FAILED)
    if route.parse_frontmatter(text)[0] == {}:
        return EditResult(f"{path} has no frontmatter to edit", REFUSED)
    new_text, diff = plan_edit(text, title=title, body=new_body, repo=repo, path=str(path.relative_to(ws)))
    if dry_run or not diff:
        return EditResult(diff, DONE)
    failure = _rewrite(path, runs_dir, is_intake, new_text)
    return EditResult(diff, DONE) if failure is None else EditResult(failure, FAILED)
