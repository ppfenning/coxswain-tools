"""The land freeze: a `land-freeze` row in the store's `leases` table whose holder is the reason and whose expiry ends the freeze.

A row past its expiry is no freeze, so a forgotten freeze lifts itself and nothing has to delete it.
"""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_tools import run_store, store_connect
from agent_tools.store_dialect import is_postgres, placeholder

LAND_FREEZE_NAME = "land-freeze"
NOT_FROZEN = "lands are not frozen"
USAGE = "usage: freeze MINUTES REASON, freeze off, or freeze with no arguments"
_STAMP = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class LandFreeze:
    reason: str
    expires_at: datetime


@dataclass(frozen=True)
class UsageError:
    message: str


def _expiry(expires_at: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def active_freeze(row: Mapping[str, Any] | None, now: datetime) -> LandFreeze | None:
    """An expiry at or before `now`, or one that does not parse, is no freeze."""
    if row is None:
        return None
    expiry = _expiry(row.get("expires_at"))
    if expiry is None or expiry <= now:
        return None
    return LandFreeze(reason=row.get("holder") or "", expires_at=expiry)


def freeze_text(freeze: LandFreeze) -> str:
    """HH:MM is the expiry in UTC."""
    return f"lands frozen until {freeze.expires_at.astimezone(UTC):%H:%M} ({freeze.reason})"


def status_text(freeze: LandFreeze | None) -> str:
    return NOT_FROZEN if freeze is None else freeze_text(freeze)


def expiry_after(now: datetime, minutes: int) -> datetime | UsageError:
    if minutes < 1:
        return UsageError(f"MINUTES must be at least 1, got {minutes}")
    return now + timedelta(minutes=minutes)


def _open_writable(runs_dir: Path) -> tuple[Any, str] | None:
    """Edge. A writable connection and its bind token; None when a SQLite store file is absent, so it never creates one.

    `run_store._open` is read-only, so a DELETE or INSERT through it always fails."""
    url = run_store._store_url(runs_dir)
    if is_postgres(url):
        return store_connect.connect(url), placeholder(url)
    path = run_store._sqlite_path(url)
    return (sqlite3.connect(path), placeholder(url)) if path.exists() else None


def read_freeze(runs_dir: Path, now: datetime) -> LandFreeze | None:
    """Edge. None with no store, no row, an expired row, or an unreadable store."""
    opened = run_store._open(runs_dir)
    if opened is None:
        return None
    conn, mark = opened
    try:
        row = conn.execute(f"SELECT * FROM leases WHERE name = {mark}", (LAND_FREEZE_NAME,)).fetchone()
    except run_store._DB_ERRORS:
        return None
    finally:
        conn.close()
    return active_freeze(None if row is None else dict(row), now)


def _write(runs_dir: Path, statements: list[tuple[str, tuple[Any, ...]]]) -> bool:
    """Edge. Runs `statements` in one transaction; False with no store or a database error."""
    try:
        opened = _open_writable(Path(runs_dir))
    except (*run_store._DB_ERRORS, RuntimeError):
        return False
    if opened is None:
        return False
    conn, token = opened
    try:
        for sql, params in statements:
            conn.execute(run_store._sql(sql, token), params)
        conn.commit()
    except (*run_store._DB_ERRORS, RuntimeError):
        return False
    finally:
        conn.close()
    return True


_DELETE = "DELETE FROM leases WHERE name = {p}"
_INSERT = "INSERT INTO leases (name, holder, epoch, heartbeat_at, expires_at) VALUES ({p}, {p}, {p}, {p}, {p})"


def write_freeze(runs_dir: Path, reason: str, now: datetime, expires_at: datetime) -> bool:
    """Edge. Replaces any land-freeze row with one held by `reason`. unknown: NOT NULL columns of the harness table beyond these five."""
    row = (LAND_FREEZE_NAME, reason, 0, now.astimezone(UTC).strftime(_STAMP), expires_at.astimezone(UTC).strftime(_STAMP))
    return _write(runs_dir, [(_DELETE, (LAND_FREEZE_NAME,)), (_INSERT, row)])


def clear_freeze(runs_dir: Path) -> bool:
    """Edge. True when the delete ran, whether or not a row was there."""
    return _write(runs_dir, [(_DELETE, (LAND_FREEZE_NAME,))])


def _set_freeze(runs_dir: Path, now: datetime, minutes: int, reason: str) -> str:
    expires_at = expiry_after(now, minutes)
    if isinstance(expires_at, UsageError):
        return f"{expires_at.message}\n{USAGE}"
    if not write_freeze(runs_dir, reason, now, expires_at):
        return "could not write the land freeze: no store or a database error"
    return freeze_text(LandFreeze(reason, expires_at))


def run_freeze(runs_dir: Path, now: datetime, minutes: int | None, reason: str | None, off: bool) -> str:
    """The text to print. Setting needs both `minutes` and `reason`; any other mix is a usage error returned as text."""
    wants_set = minutes is not None or reason is not None
    if off and wants_set:
        return USAGE
    if off:
        return NOT_FROZEN if clear_freeze(runs_dir) else "could not clear the land freeze: no store or a database error"
    if not wants_set:
        return status_text(read_freeze(runs_dir, now))
    if minutes is None or not (reason or "").strip():
        return USAGE
    return _set_freeze(runs_dir, now, minutes, reason or "")
