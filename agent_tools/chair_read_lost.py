"""The `lost_runs` source of a chair tick: initiatives whose lane sits on a host that has gone quiet
and whose run carries no exit record in the store.

`stale_hosts` and `lost_runs_of` are pure. `read_lost_runs` is the edge: it composes `run_store.hosts`,
`run_store.live_lanes` and `chair_read_exits.exit_rows`. The exit rule is `chair_read_exits.row_exited`,
the one `run_exited` applies, keyed here by run id rather than by initiative: a lane names its run.
"""
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_tools import epic, run_store
from agent_tools.chair_facts import run_initiative
from agent_tools.chair_read_exits import exit_rows, row_exited
from agent_tools.chair_read_remote_unfetched import is_remote_unfetched
from agent_tools.remote_lane import fetched_record_path, remote_record_path

Row = Mapping[str, Any]

DEFAULT_THRESHOLD_S = 600


def _parsed(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _quiet(beat_at: object, now: str, threshold_s: int) -> bool:
    """A beat more than `threshold_s` seconds before `now`, blank, missing, or unparsable."""
    beat = _parsed(beat_at) if beat_at else None
    now_parsed = _parsed(now) or datetime.min.replace(tzinfo=UTC)
    return beat is None or (now_parsed - beat).total_seconds() > threshold_s


def stale_hosts(hosts: Sequence[Row], now: str, threshold_s: int = DEFAULT_THRESHOLD_S) -> set[str]:
    """The name of every host row whose `beat_at` is more than `threshold_s` seconds before `now`, blank,
    missing, or unparsable. Never beaten counts as stale."""
    return {str(row.get("name") or "") for row in hosts if _quiet(row.get("beat_at"), now, threshold_s)}


def _expired(expires_at: object, now: datetime | None) -> bool:
    """A lease end that parses and is not later than `now`, the boundary `epic.run_live` uses (live only while
    `expires_at > now`). Blank, missing or unparsable is not expired."""
    expires = _parsed(expires_at) if expires_at else None
    return expires is not None and now is not None and expires <= now


def ghost_runs_of(rows: Sequence[Row], live: Collection[str], unfetched: Collection[str], now: str) -> dict[str, str]:
    """Pure. Each initiative's newest run by `launched_at` that has no exit record (`row_exited`), whose `expires_at`
    lease is parsed and not later than `now`, and whose run id is in neither `live` nor `unfetched`. Both are sets of
    run ids, decided per run: an older unfetched run of the same initiative never hides a newer ghost. A blank or
    unparsable lease is not expired. A local trace is not an input here; it only decides `unfetched`."""
    newest: dict[str, Row] = {}
    for row in rows:
        initiative = run_initiative(str(row.get("run_id") or ""))
        held = newest.get(initiative)
        if held is None or str(row.get("launched_at") or "") > str(held.get("launched_at") or ""):
            newest[initiative] = row
    now_parsed = _parsed(now)
    return {
        i: str(row["run_id"])
        for i, row in newest.items()
        if str(row["run_id"]) not in unfetched
        and str(row["run_id"]) not in live
        and not row_exited(row)
        and _expired(row.get("expires_at"), now_parsed)
    }


def lost_runs_of(
    lanes: Sequence[run_store.Lane], stale: set[str], exited: Mapping[str, bool], now: str, threshold_s: int = DEFAULT_THRESHOLD_S,
    *, rows: Sequence[Row] = (), live: Collection[str] = (), unfetched: Collection[str] = (),
) -> dict[str, str]:
    """Pure. A lane whose host is in `stale`, whose own lease heartbeat is quiet, and whose run does not read `True`
    in `exited` (a run missing from `exited` counts as not exited) contributes its initiative, mapped onto that
    lane's run. A lane still renewing its lease is alive whatever its host row says: hosts are beaten far less often.
    `rows` (the store's runs, each with the `expires_at` of its lease), `live` and `unfetched` add the ghost runs
    `ghost_runs_of` finds. The result holds one run per initiative and the lane rule's entries are kept as they are.
    A lane needs its prefix lease unexpired and a ghost needs it expired, so one initiative cannot be both; should
    the inputs disagree, the lane's run wins. With no `rows` the result is the lane rule alone."""
    found = {
        run_initiative(lane.run): lane.run
        for lane in lanes
        if lane.host in stale and _quiet(lane.heartbeat_at, now, threshold_s) and exited.get(lane.run) is not True
    }
    return {**ghost_runs_of(rows, live, unfetched, now), **found}


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _pid_alive(pidfile: Path) -> bool:
    """Edge. The pid itself answers, probed before any file is read. Not `epic.run_live`: it returns the lease verdict
    whenever a lease row exists, so for an expired lease it would never probe the pid."""
    pid = (_text(pidfile) or "").strip()
    try:
        if not pid.isdigit() or int(pid) <= 0 or not epic.alive(int(pid)):
            return False
        logged = epic.launched_epoch(_text(pidfile.with_suffix(".launched.json")))
        launched = logged if logged is not None else pidfile.stat().st_mtime
        return epic.run_alive(int(pid), launched, _text(pidfile.with_suffix(".log")))
    except (OSError, OverflowError):
        return False


def _unfetched(runs_dir: Path, run_id: str) -> bool:
    """Edge. `is_remote_unfetched` for this one run, on the four facts `read_remote_unfetched` reads."""
    return is_remote_unfetched(
        remote_record_path(runs_dir, run_id).exists(),
        (runs_dir / run_id).is_dir(),
        (runs_dir / f"{run_id}.log").exists(),
        fetched_record_path(runs_dir, run_id).exists(),
    )


def _lease_expiry(runs_dir: Path, run_id: str) -> str:
    """Edge. The prefix lease's `expires_at` when `run_id` holds it; blank with no lease row or another holder."""
    row = run_store.lease(runs_dir, run_id)
    return row[1] if row is not None and row[0] == run_id else ""


def read_lost_runs(runs_dir: Path, now: str) -> dict[str, str]:
    """Edge. `gather_facts`'s `lost_runs` source: initiative id to run id for each lane whose host has
    gone stale (`stale_hosts`) and whose run carries no exit record in the store's `runs` table, plus each
    ghost run (`ghost_runs_of`): an expired lease, no exit record, no live pid, and not remote-unfetched.
    Liveness and the remote facts are read only for the runs the store rows already make candidates."""
    stale = stale_hosts(run_store.hosts(runs_dir), now)
    rows = exit_rows(runs_dir)
    exited = {str(row.get("run_id") or ""): row_exited(row) for row in rows}
    leased = [{**row, "expires_at": _lease_expiry(runs_dir, str(row.get("run_id") or ""))} for row in rows]
    candidates = ghost_runs_of(leased, (), (), now).values()
    live = {run for run in candidates if _pid_alive(runs_dir / f"{run}.pid")}
    unfetched = {run for run in candidates if _unfetched(runs_dir, run)}
    return lost_runs_of(run_store.live_lanes(runs_dir, now), stale, exited, now, rows=leased, live=live, unfetched=unfetched)
