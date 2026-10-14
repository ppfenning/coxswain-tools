"""Switch engine: move an existing store and its traces between the files and shared backends.

The engine plans and runs the copy only. It never writes the profile or the settings; the command wiring does
that after `run_plan` reports every step ok. The source is never deleted or truncated.

Backends: a postgres:// store and an s3:// traces root are shared; anything else is files."""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_tools import lake_config

DIRECTIONS = ("to_shared", "to_files")
# The layout lake_traces matches. lake_traces itself imports pyiceberg at load, which this engine must not require.
_LAYOUT = re.compile(r"\d{4}/\d{2}/\d{2}/[^/]+\.parquet")
_QUERY_SECRET = re.compile(r"(?i)\b(password|passwd|sslpassword|secret|secret_key|access_key)=[^&\s]*")
_COUNT_LINE = re.compile(r"^\s*([\w.]+)\s*[:=]\s*(\d+)\s*$")


@dataclass(frozen=True)
class Step:
    """One copy. `source` and `target` are the real values; the `*_display` fields are the only strings to show."""

    kind: str
    source: str
    target: str
    source_display: str
    target_display: str


def display(url: str) -> str:
    """The URL with any password, in the userinfo or the query, replaced by `***`."""
    return _QUERY_SECRET.sub(r"\1=***", lake_config.redact(url))


def _step(kind: str, source: str, target: str) -> Step:
    return Step(kind, source, target, display(source), display(target))


def _shared_store(url: str) -> bool:
    return url.lower().startswith(("postgres://", "postgresql://", "postgresql+"))


def plan_switch(
    direction: str, source_store_url: str, target_store_url: str, source_traces_root: str, target_traces_root: str
) -> list[Step]:
    """Store step first, then traces. [] when the store is already on the backend `direction` names.
    A target equal to its source, or a target store not on that backend, is rejected."""
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be to_shared or to_files, not {direction!r}")
    if source_store_url == target_store_url:
        raise ValueError(f"the target store is the same as the source store ({display(source_store_url)})")
    if source_traces_root.rstrip("/") == target_traces_root.rstrip("/"):
        raise ValueError(f"the target traces root is the same as the source traces root ({display(source_traces_root)})")
    to_shared = direction == "to_shared"
    if _shared_store(source_store_url) == to_shared:
        return []
    if _shared_store(target_store_url) != to_shared:
        backend = "a postgresql:// store" if to_shared else "a files store, not postgresql://"
        raise ValueError(f"{direction} needs {backend} as the target, not {display(target_store_url)}")
    return [
        _step("copy_store", source_store_url, target_store_url),
        _step("copy_traces", source_traces_root, target_traces_root),
    ]


def verify_counts(source_counts: Mapping[str, int], target_counts: Mapping[str, int]) -> list[str]:
    """One sentence per table or prefix where the target holds fewer rows than the source; a missing target counts 0.
    Both inputs are totals the caller measures at each side, not run_plan's per-run copied counts."""
    return [
        f"{name} holds {target_counts.get(name, 0)} rows at the target but {count} at the source."
        for name, count in sorted(source_counts.items())
        if target_counts.get(name, 0) < count
    ]


def render_plan(steps: Iterable[Step]) -> str:
    """One line per step, from the display fields only."""
    lines = [f"{n}. {s.kind}: {s.source_display} -> {s.target_display}" for n, s in enumerate(steps, 1)]
    return "\n".join(lines) if lines else "Nothing to do: the store is already on that backend."


def parse_counts(stdout: str) -> dict[str, int]:
    """Per-table counts from harness.store_copy output: a JSON object on the last line, else `table: N` lines.
    Output in neither shape gives {}, which run_plan treats as an unverified copy, never as zero copied."""
    # unknown: the exact harness.store_copy output format. Both shapes are accepted and the counts are read as
    # rows newly copied by this run.
    lines = [line for line in stdout.splitlines() if line.strip()]
    try:
        data = json.loads(lines[-1]) if lines else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        return {str(k): int(v) for k, v in data.items() if isinstance(v, int) and not isinstance(v, bool)}
    return {m.group(1): int(m.group(2)) for line in lines if (m := _COUNT_LINE.match(line))}


def default_copy_store(source_url: str, target_url: str) -> dict[str, int]:
    """Edge. Run `python -m harness.store_copy SOURCE TARGET` and return its per-table counts.

    That copy is idempotent and refuses to drop anything, so re-running a half-finished switch is safe.
    This function keeps that property: it only calls it, and never drops, deletes or truncates."""
    done = subprocess.run(
        [sys.executable, "-m", "harness.store_copy", source_url, target_url], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"harness.store_copy exited {done.returncode}: {scrub(tail[0], (source_url, target_url))}")
    return parse_counts(done.stdout)


def scrub(text: str, urls: Iterable[str]) -> str:
    """`text` with each real URL replaced by its display form, then any stray password pattern masked."""
    return display(functools.reduce(lambda acc, url: acc.replace(url, display(url)), urls, text))


class _LocalTree:
    """Edge. Parquet day files under a local directory."""

    def __init__(self, root: str) -> None:
        self._root = Path(root.removeprefix("file://"))

    def sizes(self) -> dict[str, int]:
        found = ((p.relative_to(self._root).as_posix(), p) for p in self._root.rglob("*.parquet") if p.is_file())
        return {key: p.stat().st_size for key, p in found if _LAYOUT.fullmatch(key)}

    def open_in(self, key: str):
        return (self._root / key).open("rb")

    def write(self, key: str, src) -> None:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        with part.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(part, path)


class _ArrowTree:
    """Edge. Parquet day files under a bucket path of a pyarrow filesystem."""

    def __init__(self, fs: Any, root: str) -> None:
        self._fs, self._root = fs, root.rstrip("/")

    def sizes(self) -> dict[str, int]:
        from pyarrow.fs import FileSelector, FileType

        infos = self._fs.get_file_info(FileSelector(self._root, recursive=True, allow_not_found=True))
        base = self._root + "/"
        found = {i.path[len(base) :]: i.size for i in infos if i.type == FileType.File and i.path.startswith(base)}
        return {key: size for key, size in found.items() if _LAYOUT.fullmatch(key)}

    def open_in(self, key: str):
        return self._fs.open_input_stream(f"{self._root}/{key}")

    def write(self, key: str, src) -> None:
        with self._fs.open_output_stream(f"{self._root}/{key}") as dst:
            shutil.copyfileobj(src, dst)


def _tree(root: str, object_store: Mapping[str, Any] | None, env: Mapping[str, str]):
    """Edge. The tree for a traces root: s3:// through pyarrow and the profile's object_store block, else local."""
    if urlparse(root).scheme.lower() != "s3":
        return _LocalTree(root)
    if not object_store:
        raise ValueError(f"the traces root {display(root)} is s3:// but no object_store block names the endpoint")
    # Same route store_backup takes to Garage: pyarrow is what the lake already uses, so no new dependency.
    from agent_tools import store_backup

    props = lake_config.iceberg_properties(object_store, env)
    settings = store_backup.GarageSettings(
        bucket="",
        endpoint=props["s3.endpoint"],
        access_key=props["s3.access-key-id"],
        secret_key=props["s3.secret-access-key"],
        region=props.get("s3.region"),
        path_style=props.get("s3.force-virtual-addressing") == "false",
    )
    from pyarrow.fs import S3FileSystem

    parsed = urlparse(root)
    return _ArrowTree(S3FileSystem(**store_backup.s3_options(settings)), f"{parsed.netloc}{parsed.path}")


def traces_copier(
    object_store: Mapping[str, Any] | None = None, env: Mapping[str, str] = os.environ
) -> Callable[[str, str], dict[str, int]]:
    """Edge. A copy_traces callable: copy each Parquet day file under the source root to the target root.

    An object already at the target with the same size is skipped, so a rerun copies nothing new.
    The source is only read."""

    def copy(source_root: str, target_root: str) -> dict[str, int]:
        source, target = _tree(source_root, object_store, env), _tree(target_root, object_store, env)
        have = target.sizes()
        wanted = source.sizes()
        todo = sorted(key for key, size in wanted.items() if have.get(key) != size)
        for key in todo:
            with source.open_in(key) as src:
                target.write(key, src)
        return {"copied": len(todo), "skipped": len(wanted) - len(todo)}

    return copy


default_copy_traces = traces_copier()


def _unverified(kind: str, counts: Mapping[str, int]) -> str:
    """The failure sentence for a copier result that proves nothing; "" when it carries counts."""
    if kind == "copy_store":
        return "" if counts else "copy_store reported no per-table counts, so the store copy is unverified."
    return "" if "copied" in counts else "copy_traces reported no copied count, so the traces copy is unverified."


def run_plan(
    steps: Iterable[Step],
    copy_store: Callable[[str, str], Mapping[str, int]] = default_copy_store,
    copy_traces: Callable[[str, str], Mapping[str, int]] = default_copy_traces,
) -> list[dict[str, Any]]:
    """Edge. Run the steps in order and return one result dict per step; the first failure stops the run.

    The copiers are idempotent and drop nothing, so re-running a half-finished switch is safe and a second
    run reports zero newly copied. Results carry display strings only, with credentials redacted.
    A step whose copier returns no counts fails: absence of an error is not evidence that anything was copied."""
    copiers = {"copy_store": copy_store, "copy_traces": copy_traces}
    results: list[dict[str, Any]] = []
    for step in steps:
        head = {"kind": step.kind, "source": step.source_display, "target": step.target_display}
        try:
            counts = dict(copiers[step.kind](step.source, step.target))
        except Exception as err:  # the edge reports any copier failure as a value and stops
            message = scrub(f"{type(err).__name__}: {err}", (step.source, step.target))
            return [*results, {**head, "ok": False, "copied": 0, "counts": {}, "error": message}]
        unverified = _unverified(step.kind, counts)
        if unverified:
            return [*results, {**head, "ok": False, "copied": 0, "counts": counts, "error": unverified}]
        copied = counts.get("copied", 0) if step.kind == "copy_traces" else sum(counts.values())
        results.append({**head, "ok": True, "copied": copied, "counts": counts})
    return results
