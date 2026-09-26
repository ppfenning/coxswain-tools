"""Edge: copy an initiative to a lane host, then start the lane there. `run` takes an argv and returns its exit code."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agent_tools.lane_hosts import LaneHost
from agent_tools.remote_argv import launch_argv, rsync_push_argv, ssh_argv
from agent_tools.remote_lane import remote_record

__all__ = ["LaunchError", "launch_on_host"]


@dataclass(frozen=True)
class LaunchError:
    step: str
    message: str


def launch_on_host(
    host: LaneHost,
    initiative: str,
    run_id: str,
    label: str,
    launched_at: str,
    run: Callable[[list[str]], int],
    locate: Callable[[str], str] | None = None,
    repo: str | None = None,
) -> dict | LaunchError:
    """rsync needs the parent of the destination to exist; the copy keeps the local work/<id> layout."""
    place = locate if locate is not None else (lambda path: f"{host.ssh}:{path}")
    src = f"work/{initiative}"
    pushed = run(rsync_push_argv(src, place(f"{host.workspace_dir.rstrip('/')}/{src}")))
    if pushed != 0:
        return LaunchError("rsync", f"rsync of {src} to {host.name} exited {pushed}")
    # `route launch` reads --initiative as a path from its cwd, and an ssh command starts in the home directory.
    started = run(ssh_argv(host.ssh, launch_argv(f"{host.workspace_dir.rstrip('/')}/{src}", run_id, label)))
    if started != 0:
        return LaunchError("ssh", f"starting the lane on {host.name} exited {started}")
    return remote_record(host.name, launched_at, repo)
