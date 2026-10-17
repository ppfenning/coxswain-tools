"""The pure core behind `cox runs triage`: a task's facts built from plain rows handed in by readers."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agent_tools import run_store
from agent_tools.chair_facts import _action_doc, land_refusals_from_actions
from agent_tools.chair_read_stale import read_chair_actions

__all__ = [
    "TAIL_LINES",
    "Attempt",
    "FailedCheck",
    "GitMainReader",
    "GitReader",
    "LandRefusal",
    "Review",
    "StoreReader",
    "StoreTriageReader",
    "SurfaceStatus",
    "Triage",
    "TriageError",
    "main_lookup_argv",
    "pick_newest_run",
    "record_facts",
    "refusal_row",
    "render_json",
    "render_text",
    "run",
    "triage_task",
]

TAIL_LINES = 20
_NEW_SUFFIX = "(new)"


@dataclass(frozen=True)
class Attempt:
    seq: int
    kind: str
    cause: str
    reason: str


@dataclass(frozen=True)
class Review:
    verdict: str
    objection: str


@dataclass(frozen=True)
class FailedCheck:
    command: str
    output_tail: str


@dataclass(frozen=True)
class LandRefusal:
    time: str
    head: str
    reason: str


@dataclass(frozen=True)
class SurfaceStatus:
    path: str
    status: str


@dataclass(frozen=True)
class Triage:
    task_id: str
    initiative: str
    phase: str
    state: str
    newest_run_id: str | None
    attempts: tuple[Attempt, ...]
    review: Review | None
    failed_check: FailedCheck | None
    land_refusal: LandRefusal | None
    surfaces: tuple[SurfaceStatus, ...]


@dataclass(frozen=True)
class TriageError:
    task_id: str
    message: str


class StoreReader(Protocol):
    def task_record(self, task_id: str) -> Mapping[str, Any] | None:
        """None for an unknown id. Keys: initiative, phase, state, surfaces (list of str),
        review ({verdict, objection} or None), failed_check ({command, output} or None)."""
        ...

    def attempts(self, task_id: str) -> Sequence[Mapping[str, Any]]:
        """Rows with keys seq, kind, cause, reason, in any order."""
        ...

    def newest_run_id(self, task_id: str) -> str | None: ...

    def land_refusal(self, phase: str) -> Mapping[str, Any] | None:
        """Latest refusal for the phase: keys time, head, reason; None when there is none."""
        ...


class GitReader(Protocol):
    def path_exists_on_main(self, path: str) -> bool: ...


def tail_lines(text: str, n: int = TAIL_LINES) -> str:
    return "\n".join(text.splitlines()[-n:])


def surface_status(raw: str, exists: bool) -> SurfaceStatus:
    """A trailing "(new)" marks a surface expected to be missing on main."""
    if raw.endswith(_NEW_SUFFIX):
        return SurfaceStatus(raw[: -len(_NEW_SUFFIX)].rstrip(), "present" if exists else "new")
    return SurfaceStatus(raw, "present" if exists else "missing")


def _review(record: Mapping[str, Any]) -> Review | None:
    row = record.get("review")
    if not row:
        return None
    return Review(str(row["verdict"]), str(row["objection"]))


def _failed_check(record: Mapping[str, Any]) -> FailedCheck | None:
    row = record.get("failed_check")
    if not row:
        return None
    return FailedCheck(str(row["command"]), tail_lines(str(row["output"])))


def _land_refusal(row: Mapping[str, Any] | None) -> LandRefusal | None:
    if not row:
        return None
    return LandRefusal(str(row["time"]), str(row["head"]), str(row["reason"]))


def _surface(raw: str, git: GitReader) -> SurfaceStatus:
    path = raw[: -len(_NEW_SUFFIX)].rstrip() if raw.endswith(_NEW_SUFFIX) else raw
    return surface_status(raw, git.path_exists_on_main(path))


def triage_task(task_id: str, store: StoreReader, git: GitReader) -> Triage | TriageError:
    record = store.task_record(task_id)
    if record is None:
        return TriageError(task_id, f"unknown task id: {task_id}")
    phase = str(record["phase"])
    attempts = tuple(
        sorted(
            (
                Attempt(int(r["seq"]), str(r["kind"]), str(r["cause"]), str(r["reason"]))
                for r in store.attempts(task_id)
            ),
            key=lambda a: a.seq,
        )
    )
    return Triage(
        task_id=task_id,
        initiative=str(record["initiative"]),
        phase=phase,
        state=str(record["state"]),
        newest_run_id=store.newest_run_id(task_id),
        attempts=attempts,
        review=_review(record),
        failed_check=_failed_check(record),
        land_refusal=_land_refusal(store.land_refusal(phase)),
        surfaces=tuple(_surface(str(s), git) for s in record.get("surfaces") or ()),
    )


def _indent(text: str) -> list[str]:
    return [f"  {line}" for line in text.splitlines()]


def render_text(t: Triage) -> str:
    lines = [
        f"task {t.task_id}  initiative={t.initiative}  phase={t.phase}  state={t.state}"
        f"  newest_run={t.newest_run_id or '-'}"
    ]
    if t.attempts:
        lines.append("attempts:")
        lines += [f"  {a.seq} {a.kind} cause={a.cause} reason={a.reason}" for a in t.attempts]
    else:
        lines.append("attempts: none recorded")
    if t.review:
        lines.append(f"review: {t.review.verdict}")
        lines.append(f"  objection: {t.review.objection}")
    else:
        lines.append("review: none recorded")
    if t.failed_check:
        lines.append(f"failed check: {t.failed_check.command}")
        lines += _indent(t.failed_check.output_tail)
    else:
        lines.append("failed check: none recorded")
    if t.land_refusal:
        lines.append(f"land refusal: time={t.land_refusal.time} head={t.land_refusal.head}")
        lines.append(f"  reason: {t.land_refusal.reason}")
    else:
        lines.append("land refusal: none recorded")
    if t.surfaces:
        lines.append("surfaces:")
        lines += [f"  {s.status} {s.path}" for s in t.surfaces]
    else:
        lines.append("surfaces: none listed")
    return "\n".join(lines)


def render_json(t: Triage) -> str:
    doc = {
        "task_id": t.task_id,
        "initiative": t.initiative,
        "phase": t.phase,
        "state": t.state,
        "newest_run_id": t.newest_run_id,
        "attempts": [{"seq": a.seq, "kind": a.kind, "cause": a.cause, "reason": a.reason} for a in t.attempts],
        "review": ({"verdict": t.review.verdict, "objection": t.review.objection} if t.review else None),
        "failed_check": (
            {"command": t.failed_check.command, "output_tail": t.failed_check.output_tail} if t.failed_check else None
        ),
        "land_refusal": (
            {"time": t.land_refusal.time, "head": t.land_refusal.head, "reason": t.land_refusal.reason}
            if t.land_refusal
            else None
        ),
        "surfaces": [{"path": s.path, "status": s.status} for s in t.surfaces],
    }
    return json.dumps(doc, ensure_ascii=False, separators=(", ", ": "))


# The edge: store and git readers behind `run`. Everything above this line is pure.

_LAND_KINDS = ("land_phase", "partial_land")
_REASON_KEYS = ("reason", "detail", "error", "status")
_MAIN_REFS = ("main", "origin/main")


def _when(value: object) -> datetime | None:
    """An ISO text ("...Z" or "+00:00") or a datetime as an aware datetime; naive reads as UTC; None when unparseable."""
    try:
        at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def pick_newest_run(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """The run_id whose parsed started_at (else launched_at) is greatest. Never text order: the store mixes "Z" and "+00:00"."""
    stamped = [
        (at, str(r["run_id"]))
        for r in rows
        if (at := _when(r.get("started_at")) or _when(r.get("launched_at"))) is not None
    ]
    return max(stamped)[1] if stamped else None


def refusal_row(actions: Sequence[Mapping[str, Any]], initiative: str, phase: str) -> Mapping[str, Any] | None:
    """The latest land refusal for the initiative and phase, as the core's time/head/reason row."""
    mine = [r for r in land_refusals_from_actions(actions) if (r["initiative"], r["phase"]) == (initiative, phase)]
    if not mine:
        return None
    last = max(mine, key=lambda r: _when(r["last_refused_at"]) or datetime.min.replace(tzinfo=UTC))
    refused = [
        d
        for d in map(_action_doc, actions)
        if d.get("kind") in _LAND_KINDS
        and (d.get("initiative"), d.get("phase"), d.get("run")) == (initiative, phase, last["run"])
        and str(d.get("ts")) == last["last_refused_at"]
        and d.get("status") != "landed"
    ]
    reason = next((str(d[k]) for d in refused for k in _REASON_KEYS if d.get(k)), f"refused {last['attempts']} time(s)")
    return {"time": last["last_refused_at"], "head": last.get("head", ""), "reason": reason}


def _objection(record: Mapping[str, Any]) -> str:
    review, arbitration, adversary = record.get("review"), record.get("arbitration"), record.get("adversary")
    findings = adversary if isinstance(adversary, list) else [adversary]
    candidates = [
        review.get("objection") if isinstance(review, Mapping) else None,
        arbitration.get("reasoning") if isinstance(arbitration, Mapping) else None,
        *(f.get("why_wrong") for f in findings if isinstance(f, Mapping)),
    ]
    return next((c.strip() for c in candidates if isinstance(c, str) and c.strip()), "")


def record_facts(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """The `review` and `failed_check` keys of a task record, as the StoreReader contract wants them.

    The failed check is the last entry of `build.commands_run` (`{command, output, source}`, docs/design/observed-record.md
    section 2) that states a failure: a nonzero `exit_code` or `returncode`, `ok` false, or `is_error` true.
    unknown: that doc names no exit-status field, so an entry without one is never called failed."""
    rec = record or {}
    review, build = rec.get("review"), rec.get("build")
    verdict = review.get("verdict") if isinstance(review, Mapping) else None
    commands = build.get("commands_run") if isinstance(build, Mapping) else None
    failed = next((e for e in reversed(commands) if _check_failed(e)), None) if isinstance(commands, list) else None
    return {
        "review": {"verdict": verdict, "objection": _objection(rec)} if isinstance(verdict, str) else None,
        "failed_check": (
            {"command": str(failed.get("command", "")), "output": str(failed.get("output") or "")} if failed else None
        ),
    }


def _check_failed(entry: object) -> bool:
    if not isinstance(entry, Mapping):
        return False
    code = entry.get("exit_code", entry.get("returncode"))
    return (isinstance(code, int) and code != 0) or entry.get("ok") is False or entry.get("is_error") is True


def _strings(raw: object) -> list[str]:
    value = json.loads(raw) if isinstance(raw, str) and raw.startswith("[") else raw
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


class StoreTriageReader:
    """Edge. The StoreReader over the run store, opened the way the sibling run_store readers open it.

    An absent or unreachable store reads as empty, and `unreadable` says so, so `run` can tell a store it could not
    read from a task id that is not in it. `land_refusal` is scoped to the initiative `task_record` returned last,
    because the Protocol hands it only the phase; before any `task_record` it returns None."""

    def __init__(
        self,
        runs_dir: Path,
        actions_reader: Callable[[Path], list[dict]] = read_chair_actions,
        queue_reader: Callable[..., list[dict]] = run_store.read_queue,
    ) -> None:
        self._runs_dir = runs_dir
        self._actions_reader = actions_reader
        self._queue_reader = queue_reader
        self._initiative: str | None = None
        self.unreadable = False

    def _query(self, template: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        try:
            opened = run_store._open(self._runs_dir)
        except (*run_store._db_errors(), RuntimeError):  # an unreachable Postgres, or psycopg not installed
            self.unreadable = True
            return []
        if opened is None:
            self.unreadable = True
            return []
        conn, token = opened
        try:
            return [dict(r) for r in conn.execute(run_store._sql(template, token), tuple(params)).fetchall()]
        except run_store._db_errors():
            self.unreadable = True
            return []
        finally:
            conn.close()

    def newest_run_id(self, task_id: str) -> str | None:
        rows = self._query(
            "SELECT * FROM runs WHERE run_id IN (SELECT run_id FROM task_records WHERE task_id = {p})"
            " OR run_id IN (SELECT run_id FROM attempts WHERE task_id = {p})",
            (task_id, task_id),
        )
        return pick_newest_run(rows)

    def task_record(self, task_id: str) -> Mapping[str, Any] | None:
        queue = self._queue_reader(self._runs_dir, None, "task")
        item = next((r for r in queue if r.get("task_id") == task_id), None)
        if item is None:
            self.unreadable = self.unreadable or not queue  # an empty board is the harness or store failing to answer
            return None
        self._initiative = str(item["initiative"])
        run_id = self.newest_run_id(task_id)
        sql = "SELECT phase_id, record_json FROM task_records WHERE run_id = {p} AND task_id = {p}"
        rows = self._query(sql, (run_id, task_id)) if run_id else []
        mine = [r for r in rows if r.get("phase_id") == item.get("phase")] or rows
        record = run_store._record_of(mine[0].get("record_json")) if mine else None
        return {
            "initiative": self._initiative,
            "phase": str(item["phase"]),
            "state": str(item["state"]),
            "surfaces": _strings(item.get("surfaces")),
            **record_facts(record),
        }

    def attempts(self, task_id: str) -> Sequence[Mapping[str, Any]]:
        rows = self._query("SELECT * FROM attempts WHERE task_id = {p}", (task_id,))
        return [
            {
                "seq": r["seq"],
                "kind": r.get("kind") or "",
                "cause": r.get("cause") or "",
                "reason": r.get("reason") or "",
            }
            for r in rows
            if r.get("seq") is not None
        ]

    def land_refusal(self, phase: str) -> Mapping[str, Any] | None:
        if self._initiative is None:
            return None
        return refusal_row(self._actions_reader(self._runs_dir), self._initiative, phase)


def main_lookup_argv(repo: Path, ref: str, path: str) -> list[str]:
    """`git cat-file -e <ref>:<path>` reads the object database only; it never opens the working tree."""
    return ["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{path}"]


class GitMainReader:
    """Edge. The GitReader over a repository's main branch: `main`, else `origin/main`."""

    def __init__(self, repo: Path, runner: Callable[..., Any] = subprocess.run) -> None:
        self._repo = repo
        self._runner = runner

    def _ok(self, argv: list[str]) -> bool:
        try:
            return self._runner(argv, capture_output=True, text=True, timeout=60).returncode == 0
        except OSError, subprocess.SubprocessError:
            return False

    def path_exists_on_main(self, path: str) -> bool:
        def has_ref(ref: str) -> bool:
            return self._ok(["git", "-C", str(self._repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])

        ref = next((r for r in _MAIN_REFS if has_ref(r)), None)
        return ref is not None and self._ok(main_lookup_argv(self._repo, ref, path))


def run(
    task_id: str,
    as_json: bool,
    *,
    store: StoreReader | None = None,
    git: GitReader | None = None,
    runs_dir: Path | None = None,
    repo: Path | None = None,
) -> tuple[int, str]:
    """Edge. (exit code, output): 0 and the rendered triage, or 1 and a one-line message for an unknown task id.

    Readers default to the run store under `runs_dir` and the repository at `repo`; each defaults to the current directory."""
    here = Path.cwd()
    reader = store or StoreTriageReader(runs_dir or here)
    result = triage_task(task_id, reader, git or GitMainReader(repo or here))
    if isinstance(result, TriageError):
        hint = (
            " (the run store could not be read, or holds no work items)" if getattr(reader, "unreadable", False) else ""
        )
        return 1, " ".join(result.message.split()) + hint
    return 0, render_json(result) if as_json else render_text(result)
