"""Edge: run `cox setup doctor` on a lane host over ssh; the caller prints what comes back."""

from collections.abc import Callable

from agent_tools.lane_hosts import LaneHost
from agent_tools.remote_argv import doctor_argv, ssh_argv


def doctor_on_host(host: LaneHost, run: Callable[[list[str]], tuple[int, str]]) -> tuple[int, list[str]]:
    """`run` takes an argv and returns (exit code, combined output); result is (exit code, output rows)."""
    code, output = run(ssh_argv(host.ssh, doctor_argv()))
    return code, output.splitlines()
