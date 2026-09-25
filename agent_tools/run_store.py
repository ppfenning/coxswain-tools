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

import io
import json
import re
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_tools import epic

__all__ = [
    "TracesUnavailable", "all_phase_manifests", "call_events", "call_from_row", "connect_readonly", "phase_manifests",
    "phase_names", "run_ids", "run_started", "store_usages", "summarize", "usage", "usages",
]

STORE_FILENAME = "cox.db"
_PHASE_FILE = re.compile(r"^[^:]+:(.+)\.json$")
TRACES_DIRNAME = "traces"


class TracesUnavailable(Exception):
    """The trace store is needed but the optional `zstandard` package is not installed."""


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


def connect_readonly(runs_dir: Path) -> sqlite3.Connection | None:
    """None when `cox.db` is absent. Opened `mode=ro`, so it can never create the file."""
    path = (Path(runs_dir) / STORE_FILENAME).resolve()
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) if path.exists() else None


def run_ids(runs_dir: Path) -> set[str]:
    """Edge. Every `run_id` in the store's `runs` table; empty with no store or an unreadable one."""
    conn = connect_readonly(runs_dir)
    if conn is None:
        return set()
    try:
        return {row[0] for row in conn.execute("SELECT run_id FROM runs")}
    except sqlite3.DatabaseError:
        return set()
    finally:
        conn.close()


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
    conn = connect_readonly(runs_dir)
    if conn is None:
        return []
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT n.*, r.ended_at AS run_ended_at FROM node_calls n JOIN runs r ON r.run_id = n.run_id "
            "WHERE n.run_id = ? ORDER BY n.ts, n.seq",
            (run_id,),
        ).fetchall()
    except sqlite3.DatabaseError:
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
    conn = connect_readonly(runs_dir)
    if conn is None:
        return {}
    conn.row_factory = sqlite3.Row
    where, params = ("WHERE r.ended_at IS NULL OR r.ended_at >= ? ", (since,)) if since is not None else ("", ())
    try:
        rows = conn.execute(
            "SELECT n.*, r.ended_at AS run_ended_at FROM node_calls n JOIN runs r ON r.run_id = n.run_id "
            f"{where}ORDER BY n.run_id, n.ts, n.seq",
            params,
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()
    skip = frozenset(exclude)
    grouped: dict[str, list[sqlite3.Row]] = {}
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
    conn = connect_readonly(runs_dir)
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT run_id, record_json FROM phases ORDER BY run_id, ts"
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()
    by_run: dict[str, list[dict]] = {}
    for run_id, record_json in rows:
        if (manifest := _manifest_record(record_json)) is not None:
            by_run.setdefault(run_id, []).append(manifest)
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
    conn = connect_readonly(runs_dir)
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT phase_id FROM phases WHERE run_id = ? ORDER BY ts", (run_id,)).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()
    return [phase_id for (phase_id,) in rows]


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


def call_events(runs_dir: Path, run_id: str, call: Mapping[str, Any]) -> list[dict] | None:
    """A call's stream events: its loose `trace` file when that exists, else the trace store by `call["id"]`, else None."""
    loose = call.get("trace")
    if isinstance(loose, str) and loose and Path(loose).is_file():
        return _loose_events(Path(loose))
    traces = Path(runs_dir) / TRACES_DIRNAME
    return _store_events(traces, run_id, str(call.get("id"))) if traces.is_dir() else None


def run_started(runs_dir: Path, run_id: str) -> str | None:
    """The `launched_at` of the store's `runs` row, else None."""
    conn = connect_readonly(Path(runs_dir))
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT launched_at FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    return None if row is None else row[0]
