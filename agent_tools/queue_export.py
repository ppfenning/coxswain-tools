"""Pure export of queue rows to the intake/ and work/ trees, plus the edge that applies it.

`plan_export` takes the rows `run_store.read_queue` returned and the text currently on disk, and
says which files to write and which to delete to make the tree match the rows. It never touches a
filesystem; `export_files` is the edge that reads, plans, and writes. An `initiative.md` is written
when an initiative row holds it, and is never deleted, whatever `files_on_disk` holds for it.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import subprocess
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple

from agent_tools.queue_rows import render_item, row_path

Row = dict[str, Any]
RowKey = tuple[str, str]  # (initiative, task_id): the store's key for a row's last-exported hash
FileStatus = Literal["missing", "in_sync", "behind", "edited"]

_LOG = logging.getLogger(__name__)

_TREES = ("intake", "work")
_KEEP_PREFIXES = ("intake/held/", "intake/done/")  # never deleted, whether or not a row names them
_PATH_KEYS = {"task": ("initiative", "phase", "task_id"), "initiative": ("initiative",), "intake": ("task_id",)}
_BASELINE_RECORD = "runs/.export-baseline.json"  # this workspace's own record of what its exports wrote; never shared


class Export(NamedTuple):
    touched: list[str]
    skipped: list[str]


class Hashes(NamedTuple):
    """The store's last-exported hashes, read once, and the setter that records a new one."""

    stored: Mapping[RowKey, str]
    record: Callable[[str, str, str], bool]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def is_ahead_of_store(stored_hash: str | None, file_text: str) -> bool:
    """True when the store holds a hash for the row and the file text no longer hashes to it."""
    return stored_hash is not None and text_hash(file_text) != stored_hash


def blob_hash(text: str) -> str:
    """The git blob id `text` would have, so a file's content compares directly with the blob committed at HEAD."""
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def classify_file(file_hash: str | None, baselines: Collection[str], export_hash: str) -> FileStatus:
    """Pure: where a workspace file stands against what the export would now write.

    `baselines` are the hashes this workspace itself last produced for the file: the blob committed at HEAD and the
    text its own last export wrote. The per-row hash in the shared store is never one of them, since another machine
    records it. Missing is written; equal to the export is in_sync; equal to a baseline is behind and is rewritten
    silently; anything else is edited and left alone, including a file with no baseline at all."""
    if file_hash is None:
        return "missing"
    if file_hash == export_hash:
        return "in_sync"
    if file_hash in baselines:
        return "behind"
    return "edited"


def may_overwrite(disk_text: str | None, baselines: Collection[str], export_text: str) -> bool:
    """False only for a file the classifier calls edited: it exists and matches neither the export nor a baseline."""
    file_hash = None if disk_text is None else blob_hash(disk_text)
    return classify_file(file_hash, baselines, blob_hash(export_text)) != "edited"


def committed_blob(workspace: Path, path: str) -> str | None:
    """Edge: the blob id of `path` at the workspace's HEAD, None when it is untracked or git cannot answer."""
    try:
        done = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--verify", "--quiet", f"HEAD:./{path}"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    blob = done.stdout.strip()
    return blob if done.returncode == 0 and blob else None


def read_baseline_record(workspace: Path) -> dict[str, str]:
    """Edge: this workspace's record of the blob ids its exports last wrote, empty when it is absent or unreadable."""
    try:
        record = json.loads((workspace / _BASELINE_RECORD).read_text())
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in record.items()} if isinstance(record, dict) else {}


def write_baseline_record(workspace: Path, record: Mapping[str, str]) -> None:
    """Edge: persist the workspace's baseline record; a failure only costs the fallback, so it is logged, not raised."""
    full = workspace / _BASELINE_RECORD
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(json.dumps(dict(sorted(record.items())), indent=1) + "\n")
    except OSError as exc:
        _LOG.warning("could not write the export baseline record %s: %s", full, exc)


def recorded_baselines(record: Mapping[str, str], target: dict[str, str], now_equal: set[str]) -> dict[str, str]:
    """Pure: `record` with each path in `now_equal` set to the blob id of its target text."""
    return {**record, **{path: blob_hash(target[path]) for path in now_equal}}


def store_hashes(runs_dir: Path) -> Hashes:
    """Edge: the run store's export hashes for `runs_dir`, empty when the store cannot be read."""
    from agent_tools import run_store  # imported here: the pure functions above need no store

    return Hashes(run_store.export_hashes(runs_dir), functools.partial(run_store.set_export_hash, runs_dir))


def is_contentless(row: Row) -> bool:
    """True for a row with nothing to render: no kind, or title, body and extra all None."""
    return row.get("kind") is None or all(row.get(key) is None for key in ("title", "body", "extra"))


def skipped_ids(rows: list[Row]) -> list[str]:
    """The task_id of each contentless row, in row order; `#<index in rows>` when it has none, so each stays distinct."""
    return [str(row.get("task_id") or f"#{i}") for i, row in enumerate(rows) if is_contentless(row)]


def _named_path(row: Row) -> str | None:
    """row_path when the kind and the fields it keys on are set, else None: a contentless row may still name its file."""
    keys = _PATH_KEYS.get(row.get("kind"))
    named = keys is not None and all(row.get(key) is not None for key in keys)
    return row_path({"state": None, **row}) if named else None


def _filled(row: Row) -> Row:
    """None in a content field becomes its empty value, so render_item never sees None where it expects text or a collection."""
    empty = {"title": "", "needs": [], "surfaces": [], "body": "", "extra": {}}
    return {**row, **{key: row.get(key) or value for key, value in empty.items()}}


def export_rows(rows: list[Row]) -> dict[str, str]:
    """Workspace-relative path to file text for every row with content: initiative, tickets and intake alike."""
    return {row_path(row): render_item(_filled(row)) for row in rows if not is_contentless(row)}


def plan_export(rows: list[Row], files_on_disk: dict[str, str]) -> tuple[list[tuple[str, str]], list[str]]:
    """(writes, deletes) to bring `files_on_disk` to match `rows`. `render_item` is deterministic,
    so a path whose text already matches is left alone, and a board that changed nothing gives ([], []).
    A contentless row is not written, but the path it names is kept, never deleted."""
    target = export_rows(rows)
    kept = {_named_path(row) for row in rows if is_contentless(row)}
    writes = [(path, text) for path, text in target.items() if files_on_disk.get(path) != text]
    deletes = [
        path
        for path in files_on_disk
        if path not in target
        and path not in kept
        and Path(path).name != "initiative.md"
        and not path.startswith(_KEEP_PREFIXES)
    ]
    return writes, deletes


def _read_files(workspace: Path) -> dict[str, str]:
    roots = [workspace / tree for tree in _TREES if (workspace / tree).is_dir()]
    return {
        path.relative_to(workspace).as_posix(): path.read_text()
        for root in roots
        for path in root.rglob("*.md")
    }


def _prune_empty_dirs(directory: Path, stop_at: set[Path]) -> None:
    while directory not in stop_at and directory.is_dir() and not any(directory.iterdir()):
        parent = directory.parent
        directory.rmdir()
        directory = parent


def _row_keys(rows: list[Row]) -> dict[str, RowKey]:
    """Workspace-relative path to the store key of the row that renders it, for every row with content."""
    return {row_path(row): (str(row.get("initiative") or ""), str(row.get("task_id") or "")) for row in rows if not is_contentless(row)}


def hashes_to_record(target: dict[str, str], written: set[str], on_disk: dict[str, str], keys: dict[str, RowKey], stored: Mapping[RowKey, str]) -> list[tuple[RowKey, str]]:
    """(key, hash) for each path now holding its target text, just written or already equal, whose stored hash is not that text's."""
    now_equal = sorted(written | {path for path, text in target.items() if on_disk.get(path) == text})
    digests = {path: text_hash(target[path]) for path in now_equal}
    return [(keys[path], digest) for path, digest in digests.items() if stored.get(keys[path]) != digest]


def _edited_warning(workspace: Path, path: str) -> str:
    return (
        f"{workspace / path} was edited by hand: it matches neither this workspace's last committed or exported copy "
        f"nor the store's export, so the export left it alone. Compare it first: git -C {workspace} diff HEAD -- {path}. "
        f"To keep the edit, `cox route import --workspace {workspace}` overwrites the store with the file."
    )


def _write_guarded(
    workspace: Path, target: dict[str, str], writes: list[tuple[str, str]], on_disk: dict[str, str],
    keys: dict[str, RowKey], hashes: Hashes | None, force: bool,
) -> list[str]:
    """Edge: write `writes` under `workspace`, except files the classifier calls edited, which are left untouched and
    logged once each. A file behind this workspace's own baseline is rewritten silently. Records the hash of every file
    that now holds its target text, in the store and in the workspace's baseline record. Returns the paths written.
    `hashes=None` is no guard."""
    stored = {} if hashes is None else hashes.stored
    record = {} if hashes is None else read_baseline_record(workspace)

    def baselines(path: str) -> set[str]:
        return {blob for blob in (committed_blob(workspace, path), record.get(path)) if blob}

    held = {
        path for path, text in writes
        if hashes is not None and not force and path in on_disk and not may_overwrite(on_disk[path], baselines(path), text)
    }
    for path in sorted(held):
        _LOG.warning("%s", _edited_warning(workspace, path))
    written = [(path, text) for path, text in writes if path not in held]
    for path, text in written:
        full = workspace / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text)
    if hashes is not None:
        written_paths = {path for path, _ in written}
        for (initiative, task_id), digest in hashes_to_record(target, written_paths, on_disk, keys, stored):
            hashes.record(initiative, task_id, digest)
        now_equal = written_paths | {path for path, text in target.items() if on_disk.get(path) == text}
        updated = recorded_baselines(record, target, now_equal)
        if updated != record:
            write_baseline_record(workspace, updated)
    return [path for path, _ in written]


def export_file(workspace: Path, row: Row, *, force: bool = False, hashes: Hashes | None = None) -> Export:
    """Edge: write the one file `row` renders under `workspace`, and record its hash. A hand-edited file is left
    untouched and logged unless `force`, which writes regardless. A contentless row writes nothing and is skipped."""
    if is_contentless(row):
        return Export([], skipped_ids([row]))
    workspace = Path(workspace)
    target = export_rows([row])
    [(path, text)] = target.items()
    on_disk = {path: (workspace / path).read_text()} if (workspace / path).is_file() else {}
    writes = [(path, text)] if on_disk.get(path) != text else []
    return Export(sorted(_write_guarded(workspace, target, writes, on_disk, _row_keys([row]), hashes, force)), [])


def export_files(workspace: Path, rows: list[Row], *, hashes: Hashes | None = None) -> Export:
    """Edge: write and delete files under `workspace` so intake/ and work/ match `rows`.
    Returns the sorted paths touched and the ids of contentless rows skipped. Nothing under
    intake/held/ or intake/done/ is deleted. Rows with no content to export mean the store was
    unavailable or degraded, not an empty board, so the tree is left unchanged.
    With `hashes`, a hand-edited file is left untouched and logged instead of rewritten; one merely behind this
    workspace's own baseline is rewritten."""
    skipped = skipped_ids(rows)
    if len(skipped) == len(rows):
        return Export([], skipped)
    workspace = Path(workspace)
    on_disk = _read_files(workspace)
    writes, deletes = plan_export(rows, on_disk)
    written = _write_guarded(workspace, export_rows(rows), writes, on_disk, _row_keys(rows), hashes, False)
    stop_at = {workspace, workspace / "intake", workspace / "work"}
    for path in deletes:
        full = workspace / path
        full.unlink()
        _prune_empty_dirs(full.parent, stop_at)
    return Export(sorted(set(written) | set(deletes)), skipped)
