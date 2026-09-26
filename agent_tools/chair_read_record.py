"""The chair action log: one JSON line per action the executor only records."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ACTION_LOG = "chair.actions.jsonl"  # in the runs directory


def action_line(action: Mapping[str, Any], epoch: int, ts: str) -> str:
    """Pure: one JSON line {"ts", "epoch", **action}, keys sorted."""
    return json.dumps({"ts": ts, "epoch": epoch, **action}, sort_keys=True)


def recorder(
    runs_dir: Path, epoch: Callable[[], int], now: Callable[[], str]
) -> Callable[[Mapping[str, Any]], None]:
    """Edge: a `record(action)` that appends `action_line(action, epoch(), now())` plus a newline to runs_dir / ACTION_LOG."""

    def record(action: Mapping[str, Any]) -> None:
        with (runs_dir / ACTION_LOG).open("a", encoding="utf-8") as log:
            log.write(action_line(action, epoch(), now()) + "\n")

    return record
