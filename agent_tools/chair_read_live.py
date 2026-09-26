import glob
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_tools import epic, run_store


@dataclass(frozen=True)
class Lease:
    holder: str
    expires_at: str  # YYYY-MM-DDTHH:MM:SSZ UTC, compared as a string like `epic.run_live`


def live_initiatives(leases: Mapping[str, Lease | None], pid_live: Mapping[str, bool], now: str) -> list[str]:
    """Pure. Live: the lease is held and unexpired at `now`, else the pid check says alive. Sorted."""
    by_lease = {name for name, lease in leases.items() if lease is not None and lease.holder and lease.expires_at > now}
    by_pid = {name for name, alive in pid_live.items() if alive}
    return sorted(by_lease | by_pid)


def _pidfiles(runs_dir: Path, initiative: str) -> list[Path]:
    """Edge. `<initiative>.pid` and `<initiative>-<N>.pid` in `runs_dir`."""
    own = re.compile(re.escape(initiative) + r"(-\d+)?\.pid")
    return sorted(p for p in runs_dir.glob(f"{glob.escape(initiative)}*.pid") if own.fullmatch(p.name))


def _pid_of(pidfile: Path) -> int | None:
    try:
        text = pidfile.read_text().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def _run_live(pidfile: Path, now: str) -> bool:
    pid = _pid_of(pidfile)
    return pid is not None and epic.run_live(pid, pidfile, now=now)


def read_live_initiatives(runs_dir: Path, initiatives: Sequence[str], now: str) -> list[str]:
    """Edge. Reads each `runs:<initiative>` lease row and runs `epic.run_live`, the pid check `route status` uses, on its pidfiles. `now` is passed in."""
    rows = {name: run_store.lease(runs_dir, name) for name in initiatives}
    leases = {name: None if row is None else Lease(row[0], row[1]) for name, row in rows.items()}
    pid_live = {name: any(_run_live(p, now) for p in _pidfiles(runs_dir, name)) for name in initiatives}
    return live_initiatives(leases, pid_live, now)
