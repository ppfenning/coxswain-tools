"""A run's usage: the `<run_id>.usage.json` file first, the read-only SQLite
store `cox.db` only once that file is gone, and only for an ended run. A run has
ended when its `runs` row has an `ended_at`, or when it has no live pid: a
killed run never stamps `ended_at`. Calls alone do not mean the run is done:
graphs writes each call as it finishes. `usages` lists every run that way, and `run_started`
reads a run's `launched_at`. `phase_manifests` reads a run's
`<run_id>:<phase>.json` files first, then the `manifest_record` on the store's
phase rows, ended run or not: a recorded phase is final. Reads only; never
creates, migrates or writes the store."""

from __future__ import annotations

import functools
import io
import json
import os
import re
import sqlite3
import subprocess
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_tools import epic
from agent_tools.store_dialect import connect_readonly_url, is_postgres, placeholder
from agent_tools.store_url import TracesRoot, profile_traces_root, read_provider_profile, resolve_store_url

try:
    import psycopg

    _DB_ERRORS: tuple[type[BaseException], ...] = (sqlite3.DatabaseError, psycopg.Error)
except ImportError:
    _DB_ERRORS = (sqlite3.DatabaseError,)

__all__ = [
    "Lane", "ParquetCheck", "TracesUnavailable", "all_phase_manifests", "call_events", "call_from_row", "connect_readonly",
    "harness_python", "lease", "live_lanes", "parquet_readable", "phase_manifests", "phase_names", "remote_lanes", "run_ids", "run_spans",
    "run_started", "store_usages", "summarize", "usage", "usages",
]

STORE_FILENAME = "cox.db"
_PHASE_FILE = re.compile(r"^[^:]+:(.+)\.json$")
TRACES_DIRNAME = "traces"


class TracesUnavailable(Exception):
    """The trace store is needed but an optional package (`zstandard`, or `pyarrow` for Parquet traces) is not installed."""


# Columns that carry over unchanged from a node_calls row to a usage-file call.
_SAME = (
    "role", "task_id", "tier", "cost_usd", "ceiling_usd", "ceiling_source", "turns", "duration_ms",
    "input_tokens", "cache_read_tokens", "cache_creation_tokens", "input_total", "output_tokens", "ts",
)


def summarize(calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pure: totals and a per-model breakdown over the recorded calls."""
    fields = ("input_tokens", "cache_read_tokens", "cache_creation_tokens", "output_tokens")

    def total(call: Mapping[str, Any]) -> int:
        # Older records carried only a summed `input_tokens`; newer ones split it.
        return int(call.get("input_total") if call.get("input_total") is not None else call.get("input_tokens") or 0)

    by_model: dict[str, dict[str, Any]] = {}
    for call in calls:
        row = by_model.setdefault(str(call.get("model")), {"calls": 0, "cost_usd": 0.0, "input_total": 0, **dict.fromkeys(fields, 0)})
        row["calls"] += 1
        row["cost_usd"] = round(row["cost_usd"] + float(call.get("cost_usd") or 0.0), 4)
        row["input_total"] += total(call)
        for f in fields:
            row[f] += int(call.get(f) or 0)
    return {
        "calls": len(calls),
        "cost_usd": round(sum(float(c.get("cost_usd") or 0.0) for c in calls), 4),
        "turns": sum(int(c.get("turns") or 0) for c in calls),
        "input_total": sum(total(c) for c in calls),
        **{f: sum(int(c.get(f) or 0) for c in calls) for f in fields},
        "by_model": by_model,
    }


def _detail_of(row: Mapping[str, Any]) -> dict[str, Any]:
    """The row's `detail_json` as an object; empty when the column is absent, null, unparseable or not an object."""
    raw = row["detail_json"] if "detail_json" in row.keys() else None  # noqa: SIM118 -- sqlite3.Row's `in` tests values, not keys
    try:
        detail = None if raw is None else json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return detail if isinstance(detail, dict) else {}


def call_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One `node_calls` row as a usage-file call. `summary` and `commands_run` ride along from `detail_json` when it holds them."""
    decision = row["decision_json"]
    detail = _detail_of(row)
    return {
        **{k: row[k] for k in _SAME},
        "id": row["call_id"],
        "model": row["model_alias"],
        "ok": bool(row["ok"]),
        "decision": None if decision is None else json.loads(decision),
        **{k: detail[k] for k in ("summary", "commands_run") if k in detail},
    }


# mirrors cli.DEFAULT_PROFILE; cli imports this module, so it cannot be imported back
_DEFAULT_PROFILE = "~/.config/agent-tools/profile.yaml"


def _sql(template: str, token: str) -> str:
    """Pure: `template` with each `{p}` marker replaced by the dialect's bind-parameter token."""
    return template.replace("{p}", token)


def _sqlite_path(url: str) -> Path:
    return Path(url.removeprefix("sqlite:///") if url.startswith("sqlite:") else url)


def _store_url(runs_dir: Path) -> str:
    """Edge. The store URL: the provider profile named by the routing profile (`$AGENT_TOOLS_PROFILE` or the default), else `cox.db` in `runs_dir`."""
    return _store_url_for(str(runs_dir), os.environ.get("AGENT_TOOLS_PROFILE") or _DEFAULT_PROFILE)


@functools.lru_cache(maxsize=32)
def _store_url_for(runs_dir: str, routing_profile: str) -> str:
    """Cached per process: liveness asks once per pidfile, and two YAML reads each time made `route context` slow."""
    routing = read_provider_profile(routing_profile)
    named = routing.get("provider_profile")
    return resolve_store_url(named if isinstance(named, str) else "", runs_dir)


def _traces_root(runs_dir: Path) -> TracesRoot:
    """Edge. The traces root: the `traces_url` of the provider profile the routing profile names, else `<runs_dir>/traces`."""
    return _traces_root_for(str(runs_dir), os.environ.get("AGENT_TOOLS_PROFILE") or _DEFAULT_PROFILE)


@functools.lru_cache(maxsize=32)
def _traces_root_for(runs_dir: str, routing_profile: str) -> TracesRoot:
    """Cached per process, like `_store_url_for`: a call's events are asked for once per call."""
    named = read_provider_profile(routing_profile).get("provider_profile")
    return profile_traces_root(read_provider_profile(named) if isinstance(named, str) and named else {}, runs_dir)


def harness_python(routing: Mapping[str, Any]) -> Path | None:
    """Edge: the routing profile's `<harness_dir>/.venv/bin/python` when that file exists, else None."""
    harness_dir = routing.get("harness_dir")
    if not isinstance(harness_dir, str) or not harness_dir:
        return None
    python = Path(harness_dir).expanduser() / ".venv" / "bin" / "python"
    return python if python.is_file() else None


@functools.lru_cache(maxsize=32)
def _harness_python_for(routing_profile: str) -> Path | None:
    """Cached per process: `harness_python` of the routing profile at that path."""
    return harness_python(read_provider_profile(routing_profile))


def _harness_python() -> Path | None:
    """Edge. `_harness_python_for` for `$AGENT_TOOLS_PROFILE` or the default routing profile."""
    return _harness_python_for(os.environ.get("AGENT_TOOLS_PROFILE") or _DEFAULT_PROFILE)


def _open(runs_dir: Path) -> tuple[Any, str] | None:
    """Edge. A read-only connection and its bind-parameter token; None when a SQLite store file is absent."""
    url = _store_url(Path(runs_dir))
    if not is_postgres(url) and not _sqlite_path(url).exists():
        return None
    return connect_readonly_url(url), placeholder(url)


def connect_readonly(runs_dir: Path) -> Any | None:
    """None when a SQLite store is absent. Opened read-only, so it can never create the file. Rows are addressable by column name."""
    opened = _open(runs_dir)
    return None if opened is None else opened[0]


def _lease_name(run_id: str) -> str:
    # mirrors graphs `harness/run_lease.lease_name`: the lease is per prefix, so `x-3` and `x-4` share `runs:x`
    return "runs:" + re.sub(r"-\d+$", "", run_id)


def lease(runs_dir: Path, run_id: str) -> tuple[str, str, str] | None:
    """Edge. The (holder, expires_at, heartbeat_at) of the store lease for `run_id`'s prefix; None with no store, no row, or an unreadable store."""
    return _lease_table(str(runs_dir), int(time.monotonic() // _LEASE_SNAPSHOT_S)).get(_lease_name(run_id))


_LEASE_SNAPSHOT_S = 2  # one read of the leases table serves every liveness check within this window


@functools.lru_cache(maxsize=8)
def _lease_table(runs_dir: str, _window: int) -> dict[str, tuple[str, str, str]]:
    """Every lease row by name, read once per `_LEASE_SNAPSHOT_S` window: a docket asks about ~800 pidfiles."""
    opened = _open(Path(runs_dir))
    if opened is None:
        return {}
    conn, _ = opened
    try:
        rows = conn.execute("SELECT name, holder, expires_at, heartbeat_at FROM leases").fetchall()
    except _DB_ERRORS:
        return {}
    finally:
        conn.close()
    return {r["name"]: (r["holder"], r["expires_at"], r["heartbeat_at"]) for r in rows}


@dataclass(frozen=True)
class Lane:
    """A live lease joined to its newest run row. `host` is None when the store's `runs` table has no `host` column."""

    run: str
    host: str | None
    launched_at: str
    heartbeat_at: str


def _runs_columns(conn: Any, token: str) -> set[str]:
    """Edge. The column names of `runs`, asked of the backend: a failed SELECT would abort a Postgres transaction."""
    if token == placeholder("postgres://"):
        sql = "SELECT column_name AS name FROM information_schema.columns WHERE table_name = {p} AND table_schema = current_schema()"
        return {r["name"] for r in conn.execute(_sql(sql, token), ("runs",)).fetchall()}
    return {r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}


def _newest_run(conn: Any, token: str, name: str, host_expr: str) -> Any | None:
    """Edge. The run row of lease `name`'s prefix with the latest `launched_at`; None when the prefix has no run."""
    prefix = name.removeprefix("runs:")
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "-%"
    sql = _sql(
        f"SELECT run_id, launched_at, {host_expr} FROM runs WHERE (run_id = {{p}} OR run_id LIKE {{p}} ESCAPE '\\') ORDER BY launched_at DESC",
        token,
    )
    return next((r for r in conn.execute(sql, (prefix, like)).fetchall() if _lease_name(r["run_id"]) == name), None)


def live_lanes(runs_dir: Path, now: str) -> list[Lane]:
    """Edge. Every `runs:` lease with `expires_at` later than `now` (ISO UTC, passed in), joined to its prefix's newest run row, by lease name.

    A lease with no run row is skipped. Empty with no store or an unreadable one."""
    table = _lease_table(str(runs_dir), int(time.monotonic() // _LEASE_SNAPSHOT_S))
    live = sorted((name, beat) for name, (_, expires, beat) in table.items() if name.startswith("runs:") and expires > now)
    opened = _open(runs_dir) if live else None
    if opened is None:
        return []
    conn, token = opened
    try:
        host_expr = "host" if "host" in _runs_columns(conn, token) else "NULL AS host"
        joined = [(_newest_run(conn, token, name, host_expr), beat) for name, beat in live]
    except _DB_ERRORS:
        return []
    finally:
        conn.close()
    return [Lane(r["run_id"], r["host"], r["launched_at"], beat) for r, beat in joined if r is not None]


def remote_lanes(lanes: Sequence[Lane], local_runs: Collection[str]) -> list[Lane]:
    """Pure: the lanes whose run no local pidfile names, in input order. A run with a pidfile is local even when its lease is live."""
    return [lane for lane in lanes if lane.run not in local_runs]


def run_ids(runs_dir: Path) -> set[str]:
    """Edge. Every `run_id` in the store's `runs` table; empty with no store or an unreadable one."""
    opened = _open(runs_dir)
    if opened is None:
        return set()
    conn, _ = opened
    try:
        return {row["run_id"] for row in conn.execute("SELECT run_id FROM runs")}
    except _DB_ERRORS:
        return set()
    finally:
        conn.close()


def run_spans(runs_dir: Path, since: str) -> list[tuple[str, str, str | None]]:
    """Edge. (run_id, launched_at, ended_at) of runs still open or ended at or after `since`, by launch time; empty with no store or an unreadable one.

    A run with no `ended_at` that is not live (killed, or from before ended_at was stamped) ends at its last
    recorded call, or at its launch when it has none; only a live run stays open."""
    opened = _open(runs_dir)
    if opened is None:
        return []
    conn, p = opened

    def last_call(run_id: str) -> str | None:
        try:
            return conn.execute(_sql("SELECT MAX(ts) AS last FROM node_calls WHERE run_id = {p}", p), (run_id,)).fetchone()["last"]
        except _DB_ERRORS:
            return None

    spans = []
    try:
        rows = conn.execute(
            _sql(
                "SELECT run_id, launched_at, ended_at FROM runs "
                "WHERE launched_at IS NOT NULL AND (ended_at IS NULL OR ended_at >= {p}) ORDER BY launched_at",
                p,
            ),
            (since,),
        ).fetchall()
        for row in rows:
            run_id, launched_at, ended_at = row["run_id"], row["launched_at"], row["ended_at"]
            if ended_at is None and _run_ended(Path(runs_dir), run_id, None):
                ended_at = last_call(run_id) or launched_at
                if ended_at < since:
                    continue
            spans.append((run_id, launched_at, ended_at))
    except _DB_ERRORS:
        return []
    finally:
        conn.close()
    return spans


def _read_file(path: Path) -> dict | None:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _run_ended(runs_dir: Path, run_id: str, ended_at: Any) -> bool:
    """Edge. True when `ended_at` is set, or `<run_id>.pid` is missing, unreadable, not an int, or not a live run."""
    if ended_at is not None:
        return True
    pidfile = Path(runs_dir) / f"{run_id}.pid"
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return True
    return not epic.run_live(pid, pidfile)


def _read_calls(runs_dir: Path, run_id: str) -> list[dict]:
    opened = _open(runs_dir)
    if opened is None:
        return []
    conn, p = opened
    try:
        rows = conn.execute(
            _sql(
                "SELECT n.*, r.ended_at AS run_ended_at FROM node_calls n JOIN runs r ON r.run_id = n.run_id "
                "WHERE n.run_id = {p} ORDER BY n.ts, n.seq",
                p,
            ),
            (run_id,),
        ).fetchall()
    except _DB_ERRORS:
        return []
    finally:
        conn.close()
    if not rows or not _run_ended(runs_dir, run_id, rows[0]["run_ended_at"]):
        return []
    return [call_from_row(r) for r in rows]


def usage(runs_dir: Path, run_id: str) -> dict | None:
    """The usage file unchanged when it parses as an object, else the store's rows for an ended run (`ended_at` set, or no live pid), else None."""
    from_file = _read_file(Path(runs_dir) / f"{run_id}.usage.json")
    if from_file is not None:
        return from_file
    calls = _read_calls(Path(runs_dir), run_id)
    return _store_usage(run_id, calls) if calls else None


def _store_usage(run_id: str, calls: list[dict]) -> dict:
    return {"run_id": run_id, "calls": calls, "summary": summarize(calls)}


def _store_runs(runs_dir: Path, exclude: Collection[str] = (), since: str | None = None) -> dict[str, list[dict]]:
    """Every run with `node_calls` rows and a `runs` row that has ended (`ended_at` set, or no live pid), its calls ordered by ts then seq.
    Runs in `exclude` are dropped before any row becomes a call and before the pid check. With `since`, a run whose `ended_at` is set and earlier is dropped in SQL."""
    opened = _open(runs_dir)
    if opened is None:
        return {}
    conn, p = opened
    where, params = ("WHERE r.ended_at IS NULL OR r.ended_at >= {p} ", (since,)) if since is not None else ("", ())
    try:
        rows = conn.execute(
            _sql(
                "SELECT n.*, r.ended_at AS run_ended_at FROM node_calls n JOIN runs r ON r.run_id = n.run_id "
                + where
                + "ORDER BY n.run_id, n.ts, n.seq",
                p,
            ),
            params,
        ).fetchall()
    except _DB_ERRORS:
        return {}
    finally:
        conn.close()
    skip = frozenset(exclude)
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row["run_id"] not in skip:
            grouped.setdefault(row["run_id"], []).append(row)
    return {
        rid: [call_from_row(r) for r in group]
        for rid, group in grouped.items()
        if _run_ended(runs_dir, rid, group[0]["run_ended_at"])
    }


def store_usages(runs_dir: Path, exclude: Collection[str] = (), since: str | None = None) -> dict[str, dict]:
    """Run id to usage for each ended store run not in `exclude`. `since` is an ISO timestamp: a run that ended before it is left out."""
    return {rid: _store_usage(rid, calls) for rid, calls in _store_runs(Path(runs_dir), exclude, since).items()}


def usages(runs_dir: Path) -> dict[str, dict]:
    """Run id to usage: every parsing usage file, then each ended store run (`ended_at` set, or no live pid) that has no file."""
    files = {
        path.name.removesuffix(".usage.json"): body
        for path in sorted(Path(runs_dir).glob("*.usage.json"))
        if (body := _read_file(path)) is not None
    }
    return {**files, **store_usages(runs_dir, exclude=files)}


def _manifest_files(runs_dir: Path) -> dict[str, list[dict]]:
    """Run id to its parsing `<run_id>:<phase>.json` manifests, sorted by file name. The phase follows the last colon."""
    named = [
        (path.name.removesuffix(".json").rpartition(":")[0], path)
        for path in sorted(Path(runs_dir).glob("*:*.json"))
        if not path.name.endswith(".usage.json")
    ]
    by_run: dict[str, list[dict]] = {}
    for run_id, path in named:
        if (body := _read_file(path)) is not None:
            by_run.setdefault(run_id, []).append(body)
    return by_run


def _manifest_record(record_json: Any) -> dict | None:
    try:
        record = json.loads(record_json)
    except (TypeError, ValueError):
        return None
    found = record.get("manifest_record") if isinstance(record, dict) else None
    return found if isinstance(found, dict) else None


def _store_manifests(runs_dir: Path) -> dict[str, list[dict]]:
    """Run id to the `manifest_record` of each of its `phases` rows, ended run or not, ordered by the row's ts."""
    opened = _open(runs_dir)
    if opened is None:
        return {}
    conn, _ = opened
    try:
        rows = conn.execute("SELECT run_id, record_json FROM phases ORDER BY run_id, ts").fetchall()
    except _DB_ERRORS:
        return {}
    finally:
        conn.close()
    by_run: dict[str, list[dict]] = {}
    for row in rows:
        if (manifest := _manifest_record(row["record_json"])) is not None:
            by_run.setdefault(row["run_id"], []).append(manifest)
    return by_run


def phase_manifests(runs_dir: Path, run_id: str) -> list[dict]:
    """The run's manifest files when any parse, else the `manifest_record` of its store phases, else []."""
    from_files = _manifest_files(Path(runs_dir)).get(run_id)
    return from_files or _store_manifests(Path(runs_dir)).get(run_id, [])


def all_phase_manifests(runs_dir: Path) -> dict[str, list[dict]]:
    """Run id to phase manifests: every run with manifest files, then each store run that has none."""
    files = _manifest_files(Path(runs_dir))
    stored = {rid: ms for rid, ms in _store_manifests(Path(runs_dir)).items() if rid not in files}
    return {**files, **stored}


def _store_phase_names(runs_dir: Path, run_id: str) -> list[str]:
    opened = _open(runs_dir)
    if opened is None:
        return []
    conn, p = opened
    try:
        rows = conn.execute(_sql("SELECT phase_id FROM phases WHERE run_id = {p} ORDER BY ts", p), (run_id,)).fetchall()
    except _DB_ERRORS:
        return []
    finally:
        conn.close()
    return [row["phase_id"] for row in rows]


def phase_names(runs_dir: Path, run_id: str) -> list[str]:
    """Phase names of the run's manifest files, oldest mtime first; else its store phases by ts, ended run or not."""
    paths = sorted(Path(runs_dir).glob(f"{run_id}:*.json"), key=lambda p: p.stat().st_mtime)
    matches = (_PHASE_FILE.match(p.name) for p in paths)
    return [m.group(1) for m in matches if m] or _store_phase_names(Path(runs_dir), run_id)


def _json_objects(lines: Any) -> list[dict]:
    """The lines that parse as a JSON object, in order; blank, unparseable and non-object lines are dropped."""
    def parsed(line: str) -> Any:
        try:
            return json.loads(line)
        except ValueError:
            return None

    return [row for line in lines if line.strip() if isinstance(row := parsed(line), dict)]


def _loose_events(path: Path) -> list[dict]:
    try:
        return _json_objects(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return []


def _store_events(traces: Path, run_id: str, call_id: str) -> list[dict]:
    """Events of one call from `YYYY/MM/DD/<run_id>.jsonl.zst` day files, ordered by each row's `seq`."""
    try:
        import zstandard
    except ImportError as exc:
        raise TracesUnavailable("reading traces needs zstandard: install coxswain-tools[traces]") from exc

    def rows(path: Path) -> list[dict]:
        with path.open("rb") as fh:
            reader = zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True)
            return _json_objects(list(io.TextIOWrapper(reader, encoding="utf-8")))

    mine = [r for path in sorted(traces.glob(f"*/*/*/{run_id}.jsonl.zst")) for r in rows(path) if r.get("call_id") == call_id]
    return [r["event"] for r in sorted(mine, key=lambda r: r["seq"])]


_PARQUET_COLUMNS = ["call_id", "seq", "event"]
_NEEDS_PYARROW = "reading Parquet traces needs pyarrow: pip install 'coxswain-tools[parquet]'"


def _parquet_call_events(rows: Sequence[Mapping[str, Any]], call_id: str) -> list[dict]:
    """Pure: the decoded `event` of each row for `call_id`, ordered by `seq`; an event that is not a JSON object is dropped."""
    mine = sorted((r for r in rows if r.get("call_id") == call_id), key=lambda r: r["seq"])
    return _json_objects(r["event"] for r in mine if isinstance(r.get("event"), str))


def _dump_rows(stdout: str) -> list[dict]:
    """Pure: the rows of `harness.store_traces dump` output, one JSON object per line, cut to `_PARQUET_COLUMNS`."""
    return [{c: row[c] for c in _PARQUET_COLUMNS} for row in map(json.loads, filter(str.strip, stdout.splitlines()))]


def _import_pyarrow() -> tuple[Any, Any]:
    """The only place pyarrow is imported: `(pyarrow.fs, pyarrow.parquet)`, else TracesUnavailable naming the extra."""
    try:
        import pyarrow.fs
        import pyarrow.parquet
    except ImportError as exc:
        raise TracesUnavailable(_NEEDS_PYARROW) from exc
    return pyarrow.fs, pyarrow.parquet


def _filesystem(pafs: Any, root: TracesRoot) -> tuple[Any, str]:
    """pyarrow's own resolution for a remote URL; a plain local path (possibly relative) gets the local filesystem."""
    if root.remote:
        return pafs.FileSystem.from_uri(root.url)
    return pafs.LocalFileSystem(), str(Path(root.url).absolute())


def _dump_failure(returncode: int, stderr: str) -> str:
    """Pure: the exit code and the last stderr line of a failed dump, for the TracesUnavailable message."""
    last = next((line.strip() for line in reversed(stderr.splitlines()) if line.strip()), "")
    return f"exit {returncode}: {last}" if last else f"exit {returncode}"


@functools.lru_cache(maxsize=8)
def _harness_dump(python: str, url: str, run_id: str) -> tuple[dict, ...] | str:
    """Edge, cached per process: the run's dumped rows, else the failure as text, so a broken harness runs once.

    Exit 3 raises LookupError, which lru_cache does not store: a live run's file can appear later."""
    argv = [python, "-m", "harness.store_traces", "dump", url, run_id]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "timed out after 60s"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{type(exc).__name__}: {exc}"
    if done.returncode == 3:
        raise LookupError(run_id)
    if done.returncode != 0:
        return _dump_failure(done.returncode, done.stderr)
    try:
        return tuple(_dump_rows(done.stdout))
    except (ValueError, KeyError, TypeError) as exc:
        return f"unreadable output: {type(exc).__name__}: {exc}"


def _harness_parquet_rows(root: TracesRoot, run_id: str, missing: TracesUnavailable) -> list[dict] | None:
    """Edge: the run's rows through the harness `store_traces dump`, None on exit 3, else `missing` re-raised."""
    python = _harness_python()
    if python is None:
        raise missing
    try:
        dumped = _harness_dump(str(python), root.url, run_id)
    except LookupError:
        return None
    if isinstance(dumped, str):
        raise TracesUnavailable(f"{missing} (the harness could not read it either): {dumped}") from missing
    return list(dumped)


def _harness_can_read(python: Path) -> bool:
    """Edge: whether the harness python imports pyarrow and `harness.store_traces`, the two things a dump needs."""
    argv = [str(python), "-c", "import pyarrow.parquet, harness.store_traces"]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _parquet_rows(root: TracesRoot, run_id: str) -> list[dict] | None:
    """Edge: the rows of the run's `YYYY/MM/DD/<run_id>.parquet` files, None when it has none.

    A local root is probed with a glob first, so a missing pyarrow only matters once a file exists. A remote root
    cannot be probed without pyarrow. Without pyarrow the rows come through the harness when it has a python,
    else TracesUnavailable is raised."""
    name = f"{run_id}.parquet"
    if root.remote:
        try:
            pafs, pq = _import_pyarrow()
        except TracesUnavailable as missing:
            return _harness_parquet_rows(root, run_id, missing)
        fs, base = _filesystem(pafs, root)
        selector = pafs.FileSelector(base, recursive=True, allow_not_found=True)
        found = sorted(i.path for i in fs.get_file_info(selector) if i.type == pafs.FileType.File and i.base_name == name)
    else:
        found = sorted(str(p) for p in Path(root.url).absolute().glob(f"*/*/*/{name}"))
        if not found:
            return None
        try:
            pafs, pq = _import_pyarrow()
        except TracesUnavailable as missing:
            return _harness_parquet_rows(root, run_id, missing)
        fs, _ = _filesystem(pafs, root)
    if not found:
        return None
    return [row for path in found for row in pq.read_table(path, filesystem=fs, columns=_PARQUET_COLUMNS).to_pylist()]


@functools.lru_cache(maxsize=8)
def _found_parquet_rows(url: str, remote: bool, run_id: str) -> tuple[dict, ...]:
    """Raises LookupError on a miss, which lru_cache does not store: a live run's file can appear later."""
    rows = _parquet_rows(TracesRoot(url, remote), run_id)
    if not rows:
        raise LookupError(run_id)
    return tuple(rows)


def _parquet_rows_once(root: TracesRoot, run_id: str) -> tuple[dict, ...] | None:
    """Edge: `_parquet_rows` read once per process for a run whose file exists; a run with none is asked again."""
    try:
        return _found_parquet_rows(root.url, root.remote, run_id)
    except LookupError:
        return None


def synthetic_call_id(run_id: str, trace: object) -> str | None:
    """Pure: the id the Parquet backfill gave a call, `<run_id>-<stem of its trace path>`; None without a trace."""
    return f"{run_id}-{Path(trace).stem}" if isinstance(trace, str) and trace else None


def call_events(runs_dir: Path, run_id: str, call: Mapping[str, Any]) -> list[dict] | None:
    """A call's stream events by `call["id"]`, first source holding any wins: the run's Parquet file (again under
    the backfill's synthetic id), then the `.jsonl.zst` day files, then the loose `trace` file. None when there is
    no trace store and no loose file."""
    call_id = str(call.get("id"))
    synthetic = synthetic_call_id(run_id, call.get("trace"))
    rows = _parquet_rows_once(_traces_root(Path(runs_dir)), run_id)
    if rows and (
        found := _parquet_call_events(rows, call_id) or (synthetic and _parquet_call_events(rows, synthetic))
    ):
        return found
    traces = Path(runs_dir) / TRACES_DIRNAME
    stored = _store_events(traces, run_id, call_id) if traces.is_dir() else None
    if stored:
        return stored
    loose = call.get("trace")
    if isinstance(loose, str) and loose and Path(loose).is_file():
        return _loose_events(Path(loose))
    return stored


@dataclass(frozen=True)
class ParquetCheck:
    """`readable` with the `reason`: `ok`, `through the harness`, `pyarrow missing` or `root unreachable`."""

    readable: bool
    reason: str


def parquet_readable(traces_root: TracesRoot, harness: Path | None) -> ParquetCheck:
    """Edge: whether Parquet traces under `traces_root` can be read, for the doctor. Opens no trace file.

    `harness` is the diagnosed profile's harness python; without pyarrow it counts only when it imports what a dump needs."""
    try:
        pafs, _ = _import_pyarrow()
    except TracesUnavailable:
        readable = harness is not None and _harness_can_read(harness)
        return ParquetCheck(True, "through the harness") if readable else ParquetCheck(False, "pyarrow missing")
    import pyarrow

    try:
        fs, base = _filesystem(pafs, traces_root)
        reachable = fs.get_file_info(base).type == pafs.FileType.Directory
    except (OSError, ValueError, pyarrow.ArrowException):
        reachable = False
    return ParquetCheck(True, "ok") if reachable else ParquetCheck(False, "root unreachable")


def run_started(runs_dir: Path, run_id: str) -> str | None:
    """The `launched_at` of the store's `runs` row, else None."""
    opened = _open(Path(runs_dir))
    if opened is None:
        return None
    conn, p = opened
    try:
        row = conn.execute(_sql("SELECT launched_at FROM runs WHERE run_id = {p}", p), (run_id,)).fetchone()
    except _DB_ERRORS:
        return None
    finally:
        conn.close()
    return None if row is None else row["launched_at"]
