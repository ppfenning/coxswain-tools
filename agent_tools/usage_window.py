"""Edge for agent_tools/pacing.py: gathers the current spend window and hands
it to the pure planner. Every impure part lives here — invoking ccusage,
reading usage files, and the clock — so pacing.py never touches any of it.

Preferred source is `ccusage blocks --active --json`. When ccusage is
unavailable, answers empty, or has no block marked active, the fallback is
the run records' own `*.usage.json` files: a window of `window_hours` ending
at `now`, summing only the files whose run started inside it. Neither path
has a known dollar ceiling yet — that comes from `policy.pacing`, in the
coxswain-cartridges repository, once it lands — so `ceiling_usd` is left
unset and `pacing.assess` reports the unmeasured case: pace and projection,
no headroom or verdict past `go`.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent_tools.pacing import Policy, Window

__all__ = ["DEFAULT_POLICY", "gather", "window_from"]

# Used wherever the resolved cartridge dict carries no `policy.pacing` key
# yet (the cross-repository policy has not landed). These are the real
# ladders `cartridges-pacing-policy` declares, not placeholders: an absent
# policy must read as unmeasured-and-uncapped, i.e. tier_ceiling="deep" and
# effort_ceiling="high", never as a tighten nobody asked for.
DEFAULT_POLICY = Policy(
    pace_thresholds=(1.2, 1.5, 2.0, 2.5),
    tier_ladder=("deep", "standard", "cheap"),
    effort_ladder=("high", "low"),
    min_headroom_usd=0.0,
)


def _active_block(blocks_json: dict[str, Any]) -> dict[str, Any] | None:
    """The one block ccusage marked active and that parses cleanly, or None.
    A block that fails to parse is ignored, not raised."""
    for block in blocks_json.get("blocks") or []:
        if not isinstance(block, dict) or not block.get("isActive"):
            continue
        try:
            start = datetime.fromisoformat(str(block["startTime"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(block["endTime"]).replace("Z", "+00:00"))
            spent = float(block["costUSD"])
        except (KeyError, TypeError, ValueError):
            continue
        return {"start": start, "end": end, "spent": spent}
    return None


def window_from(
    blocks_json: dict[str, Any],
    usage_files: list[tuple[datetime, dict[str, Any]]],
    now: datetime,
    window_hours: float = 5.0,
) -> Window:
    """Pure. `usage_files` is `(start, parsed usage.json)` pairs; the caller
    supplies each start, this function never stats a file."""
    active = _active_block(blocks_json)
    if active is not None:
        span_hours = max((active["end"] - active["start"]).total_seconds() / 3600, 1e-9)
        return Window(
            start=active["start"], end=active["end"],
            spent_usd=active["spent"], ceiling_usd=None,
            burn_usd_per_hour=active["spent"] / span_hours, runs_in_flight=0,
        )

    start = now - timedelta(hours=window_hours)
    in_window = [usage for ts, usage in usage_files if start <= ts <= now]
    spent_usd = sum(float(u.get("cost_usd") or 0.0) for u in in_window)
    elapsed_hours = max((now - start).total_seconds() / 3600, 1e-9)
    return Window(
        start=start, end=now,
        spent_usd=spent_usd, ceiling_usd=None,
        burn_usd_per_hour=spent_usd / elapsed_hours, runs_in_flight=len(in_window),
    )


def gather(runs_dir: Path | str, now: datetime, run: Any = subprocess.run, window_hours: float = 5.0) -> Window:
    """Impure edge: launches ccusage, reads the run directory's usage files,
    and folds whichever answers into a `Window` via `window_from`."""
    blocks_json: dict[str, Any] = {}
    try:
        result = run(
            ["npx", "-y", "ccusage@latest", "blocks", "--active", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            blocks_json = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        blocks_json = {}

    usage_files: list[tuple[datetime, dict[str, Any]]] = []
    for path in Path(runs_dir).glob("*.usage.json"):
        try:
            usage = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ts = usage.get("ts") or usage.get("started")
        try:
            started = datetime.fromisoformat(str(ts)) if ts else datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
        except ValueError:
            started = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
        usage_files.append((started, usage))

    return window_from(blocks_json, usage_files, now, window_hours)
