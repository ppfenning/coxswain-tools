"""The chair lock, `runs/chair.json`: pure liveness/take/beat/release/clear over the (session, pid, host) triple; a malformed record reads as stale."""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
from pathlib import Path
from typing import Any

__all__ = [
    "CHAIR_FILENAME",
    "DEFAULT_HEARTBEAT_MINUTES",
    "beat",
    "chair_path",
    "clear",
    "guard",
    "leader_path",
    "liveness",
    "locked",
    "pid_alive",
    "read",
    "release",
    "take",
    "write",
]

CHAIR_FILENAME = "chair.json"
_LEGACY_LEADER_FILENAME = "leader.json"

DEFAULT_HEARTBEAT_MINUTES = 10


def _is_holder(record: dict[str, Any], session: str, pid: int, host: str) -> bool:
    """A caller holds `record` only when its session, pid and host all match what     was recorded — the label alone is not proof of identity."""
    return record.get("session") == session and record.get("pid") == pid and record.get("host") == host


def liveness(record: dict[str, Any] | None, pid_alive_: bool, now: datetime.datetime, host: str, heartbeat_minutes: int = DEFAULT_HEARTBEAT_MINUTES) -> str:
    """Pure: pid_alive_ is consulted only when record's host is host; a fresh heartbeat from another host is live regardless."""
    if record is None:
        return "none"
    try:
        heartbeat_at = datetime.datetime.fromisoformat(record["heartbeat_at"])
        age = now - heartbeat_at
    except (KeyError, TypeError, ValueError):
        return "stale"
    if age > datetime.timedelta(minutes=heartbeat_minutes):
        return "stale"
    if record.get("host") == host and not pid_alive_:
        return "crashed"
    return "live"


def _held_by_line(record: dict[str, Any]) -> str:
    return f"held by {record.get('session', '?')} (pid {record.get('pid', '?')}) on {record.get('host', '?')}"


def take(
    record: dict[str, Any] | None,
    session: str,
    pid: int,
    host: str,
    now: datetime.datetime,
    heartbeat_minutes: int,
    pid_alive_: bool,
    steal: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    """Pure."""
    state = liveness(record, pid_alive_, now, host, heartbeat_minutes)
    if state == "live":
        return None, f"chair: {_held_by_line(record)}"
    if state in ("stale", "crashed") and not steal:
        return None, f"chair: {_held_by_line(record)} ({state}; pass --steal to take over)"
    taken_at = now.isoformat()
    new_record = {"session": session, "pid": pid, "host": host, "taken_at": taken_at, "heartbeat_at": taken_at, "runs": []}
    return new_record, ""


def beat(record: dict[str, Any] | None, session: str, pid: int, host: str, now: datetime.datetime, run_id: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """Pure."""
    if record is None or not _is_holder(record, session, pid, host):
        return None, f"chair: not held by {session} (pid {pid}) on {host}"
    existing_runs = record.get("runs", [])
    runs = existing_runs if run_id is None or run_id in existing_runs else [*existing_runs, run_id]
    return {**record, "heartbeat_at": now.isoformat(), "runs": runs}, ""


def release(record: dict[str, Any] | None, session: str, pid: int, host: str) -> tuple[dict[str, Any] | None, str]:
    """Pure."""
    if record is None:
        return None, "chair: no lock held"
    if not _is_holder(record, session, pid, host):
        return record, f"chair: not held by {session} (pid {pid}) on {host}"
    return None, ""


def guard(record: dict | None, holder: str, state: str) -> str | None:
    """The one-line refusal when a LIVE lock belongs to another holder, else None."""
    if record is None or state != "live" or record.get("session") == holder:
        return None
    return f"refusing: the landing loop is held by {record.get('session')} (live); pass --force to override"


def chair_path(runs_dir: Path) -> Path:
    return Path(runs_dir) / CHAIR_FILENAME


# Back-compat alias for the pre-rename name; consumers still spell this
# `leader_path` until the wire-consumers phase moves them to `chair_path`.
leader_path = chair_path


def read(runs_dir: Path) -> dict[str, Any] | None:
    """Edge. Tries chair.json first; falls back to leader.json so a lock taken before this rename is not orphaned."""
    runs_dir = Path(runs_dir)
    try:
        text = chair_path(runs_dir).read_text(encoding="utf-8")
    except FileNotFoundError:
        try:
            text = (runs_dir / _LEGACY_LEADER_FILENAME).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
    return json.loads(text)


def write(runs_dir: Path, record: dict[str, Any] | None) -> None:
    """Edge. Always clears the legacy leader.json too, so a lock read through the fallback is actually released, and a beat leaves at most one lock file behind."""
    runs_dir = Path(runs_dir)
    path = chair_path(runs_dir)
    legacy = runs_dir / _LEGACY_LEADER_FILENAME
    if record is None:
        path.unlink(missing_ok=True)
        legacy.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    legacy.unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def clear(runs_dir: Path, force: bool = False) -> tuple[Path, dict[str, Any]] | None:
    """Edge. Removes whichever lock file is present unless its recorded pid is live and force is False; returns the path and holder removed, or None."""
    runs_dir = Path(runs_dir)
    primary = chair_path(runs_dir)
    path = primary if primary.exists() else runs_dir / _LEGACY_LEADER_FILENAME
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if pid_alive(record.get("pid")) and not force:
        return None
    path.unlink(missing_ok=True)
    return path, record


@contextlib.contextmanager
def locked(runs_dir: Path):
    """Edge."""
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(runs_dir / "chair.lock", os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
