"""Deprecation shim: `agent_tools.chair` is the real module now.

Exit condition: delete this file once every consumer (cli.py, home_model.py,
home_screen.py, records.py, notify.py, runs_top.py, runs_top_screen.py and
pyproject.toml) imports `agent_tools.chair` directly instead of `leader`.
That move is tracked by the wire-consumers phase; this file carries no logic
of its own until then.
"""

from __future__ import annotations

from agent_tools.chair import (  # noqa: F401
    CHAIR_FILENAME,
    DEFAULT_HEARTBEAT_MINUTES,
    beat,
    chair_path,
    clear,
    guard,
    leader_path,
    liveness,
    locked,
    pid_alive,
    read,
    release,
    take,
    write,
)

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
