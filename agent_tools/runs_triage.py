"""The pure core behind `cox runs triage`: a task's facts built from plain rows handed in by readers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "TAIL_LINES",
    "Attempt",
    "FailedCheck",
    "GitReader",
    "LandRefusal",
    "Review",
    "StoreReader",
    "SurfaceStatus",
    "Triage",
    "TriageError",
    "render_json",
    "render_text",
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
