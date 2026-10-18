"""Chair watchdog: a pure core (status-line parsing, loop-down and idle-on-hold verdicts) and a thin edge below it.

The core takes plain data and returns plain data; the clock is a parameter. The edge, from `StoreUnreadable` down,
reads the store read-only and hands alerts to the notifier. Store, notifier and clock are all injectable.
"""

import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_tools import run_store
from agent_tools.chair import LEASE_NAME
from agent_tools.notify_core import Event, NotifyConfig
from agent_tools.notify_dispatch import dispatch

LOOP_DOWN_TITLE = "Chair loop down"
_LANES = re.compile(r"lanes (\d+)/(\d+)(?: \(here \d+/\d+\))?")  # the suffix is the fleet line's local share
_NEEDS_ITEM_SPLIT = re.compile(r", (?=RUNAWAY |needs chair: |partial-land: )")
_NEEDS_PREFIXES = ("needs chair: ", "RUNAWAY ", "partial-land: ")
_WAITING_PREFIX = "waiting: "
_NEEDS_PREFIX = "needs chair: "


@dataclass(frozen=True)
class Alert:
    title: str
    body: str


@dataclass(frozen=True)
class StatusLine:
    busy: int
    total: int
    holds: tuple[str, ...]


def _needs_items(part: str) -> tuple[str, ...]:
    """The holds in one needs part. `needs chair: none` is no hold; the `needs chair: ` prefix is dropped."""
    items = _NEEDS_ITEM_SPLIT.split(part)
    return tuple(i.removeprefix(_NEEDS_PREFIX) for i in items if i != "needs chair: none")


def parse_status(line: str) -> StatusLine | None:
    """None when the line has no `lanes B/T` part, so an unreadable line is never an idle tick."""
    parts = line.split(" | ")
    lanes = next((m for m in (_LANES.fullmatch(p) for p in parts) if m), None)
    if lanes is None:
        return None
    waiting = tuple(p.removeprefix(_WAITING_PREFIX) for p in parts if p.startswith(_WAITING_PREFIX))
    needs = tuple(item for p in parts if p.startswith(_NEEDS_PREFIXES) for item in _needs_items(p))
    return StatusLine(int(lanes.group(1)), int(lanes.group(2)), waiting + needs)


def loop_down(holder: str | None, last_beat: datetime | None, now: datetime, stale_minutes: int = 10) -> Alert | None:
    """An absent lease row (`last_beat` None) is loop down with the holder reported as unknown."""
    if last_beat is None:
        return Alert(LOOP_DOWN_TITLE, f"No chair lease row. Holder: {holder or 'unknown'}. Last beat: never.")
    if now - last_beat <= timedelta(minutes=stale_minutes):
        return None
    return Alert(LOOP_DOWN_TITLE, f"Holder {holder or 'unknown'} last beat {last_beat.isoformat()}.")


def idle(rows: Sequence[tuple[datetime, str]], now: datetime, idle_minutes: int = 30) -> Alert | None:
    """Rows older than `idle_minutes` are ignored. An empty window is loop down; otherwise alert only when every
    row parses with zero busy lanes and the union of holds is non-empty."""
    window = [line for ts, line in rows if timedelta(0) <= now - ts <= timedelta(minutes=idle_minutes)]
    if not window:
        return Alert(LOOP_DOWN_TITLE, f"No status row seen in {idle_minutes} min.")
    parsed = [parse_status(line) for line in window]
    if any(p is None or p.busy > 0 for p in parsed):
        return None
    holds = tuple(dict.fromkeys(h for p in parsed if p is not None for h in p.holds))
    if not holds:
        return None
    return Alert(f"Lanes idle {idle_minutes} min", "Held: " + "; ".join(holds))


def evaluate(
    holder: str | None,
    last_beat: datetime | None,
    rows: Sequence[tuple[datetime, str]],
    now: datetime,
    stale_minutes: int = 10,
    idle_minutes: int = 30,
) -> tuple[Alert, ...]:
    """Both checks, with at most one loop-down alert; the heartbeat's wins because it names the holder."""
    alerts = tuple(a for a in (loop_down(holder, last_beat, now, stale_minutes), idle(rows, now, idle_minutes)) if a)
    down = tuple(a for a in alerts if a.title == LOOP_DOWN_TITLE)[:1]
    return down + tuple(a for a in alerts if a.title != LOOP_DOWN_TITLE)


_STATUS_ROW_CAP = 2000  # `ts` is text in mixed ISO shapes, so the window is cut in Python; the cap only bounds the read


class StoreUnreadable(RuntimeError):
    """The store could not be read. Distinct from an empty store, which is a verdict and not an error."""


def parse_ts(raw: object) -> datetime | None:
    """An ISO timestamp as an aware datetime; a naive one is read as UTC. None when it does not parse."""
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def status_rows(raw: Iterable[tuple[object, object]], now: datetime, idle_minutes: int) -> list[tuple[datetime, str]]:
    """(ts, action_json cell) pairs as (ts, line) pairs from the last `idle_minutes`. A row with no parseable ts or no string `line` drops."""
    cutoff = now - timedelta(minutes=idle_minutes)
    decoded = ((parse_ts(ts), run_store._json_cell(cell)) for ts, cell in raw)
    return [
        (ts, doc["line"])
        for ts, doc in decoded
        if ts is not None and ts >= cutoff and isinstance(doc, dict) and isinstance(doc.get("line"), str)
    ]


def to_event(alert: Alert) -> Event:
    """The key is the title, so a repeat of the same alert falls inside `notify_core.due`'s window."""
    return Event("needs_chair", f"watchdog:{alert.title}", alert.title, alert.body)


def _open_store(runs_dir: Path) -> tuple[Any, str]:
    opened = run_store._open(runs_dir)
    if opened is None:
        raise StoreUnreadable(f"no store under {runs_dir}")
    return opened


def read_lease_row(runs_dir: Path) -> tuple[str | None, datetime | None]:
    """Edge, read-only. (holder, heartbeat_at) of the `chair` lease; (None, None) when the store has no such row.
    Raises on an unreadable store, unlike the other chair readers, which fold that into an empty answer."""
    conn, token = _open_store(runs_dir)
    try:
        sql = run_store._sql("SELECT holder, heartbeat_at FROM leases WHERE name = {p}", token)
        row = conn.execute(sql, (LEASE_NAME,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None, None
    return (row["holder"] or None), parse_ts(row["heartbeat_at"])


def read_status_rows(runs_dir: Path, now: datetime, idle_minutes: int) -> list[tuple[datetime, str]]:
    """Edge, read-only. The `status` rows of `chair_actions` from the last `idle_minutes`, as (ts, line). Raises on an unreadable store."""
    conn, token = _open_store(runs_dir)
    try:
        sql = run_store._sql(
            f"SELECT ts, action_json FROM chair_actions WHERE kind = {{p}} ORDER BY ts DESC LIMIT {_STATUS_ROW_CAP}", token
        )
        raw = [(r["ts"], r["action_json"]) for r in conn.execute(sql, ("status",)).fetchall()]
    finally:
        conn.close()
    return status_rows(raw, now, idle_minutes)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def run_watchdog(
    runs_dir: Path,
    *,
    notify_config: NotifyConfig | None,
    state_path: Path,
    idle_minutes: int = 30,
    stale_minutes: int = 10,
    dry_run: bool = False,
    clock: Callable[[], datetime] = _utc_now,
    notifier: Callable[..., list[str]] = dispatch,
    read_lease: Callable[[Path], tuple[str | None, datetime | None]] = read_lease_row,
    read_rows: Callable[[Path, datetime, int], list[tuple[datetime, str]]] = read_status_rows,
) -> int:
    """Exit code: 0 whether or not anything was pushed, 1 only when the store cannot be read (reported on stderr).
    `--dry-run` prints each alert's title and body and pushes nothing; it does not consult the `due` window."""
    now = clock()
    try:
        holder, last_beat = read_lease(runs_dir)
        rows = read_rows(runs_dir, now, idle_minutes)
    except (*run_store._db_errors(), RuntimeError, OSError) as exc:
        print(f"watchdog: cannot read store: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    alerts = evaluate(holder, last_beat, rows, now, stale_minutes, idle_minutes)
    if dry_run:
        for alert in alerts:
            print(f"{alert.title}\n{alert.body}")
        return 0
    try:
        notifier([to_event(a) for a in alerts], notify_config, state_path, now.timestamp())
    except Exception as exc:  # a failed push is not an exit status; the next run tries again
        print(f"watchdog: notify failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0
