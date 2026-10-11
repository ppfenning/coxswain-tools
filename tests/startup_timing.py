"""Shared startup timing: a cox command measured against a bare interpreter spawned the same way."""

import statistics
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# Documented absolute local targets, in seconds, for the median wall time of one process.
HELP_TARGET_S = 0.25
CHAIR_STATUS_TARGET_S = 0.30

# Measured cox/python -c pass ratio 4.6x to 5.8x (4 rounds of 21 runs, 2026-10-11, Linux, py3.14); 8.0x is 1.5x the clean ratio for noise and under 3x it (13.7x), so a 3x regression (~16x) fails.
STARTUP_MULTIPLE = 8.0


@dataclass(frozen=True)
class StartupResult:
    ok: bool
    message: str


def time_command(argv: Sequence[str], env: Mapping[str, str] | None, runs: int, cwd: Path | None = None) -> float:
    """Median wall seconds over `runs` spawns of `argv`; one discarded warm-up run; a non-zero exit raises."""

    def once() -> float:
        start = time.perf_counter()
        subprocess.run(list(argv), capture_output=True, cwd=cwd, env=env, check=True)
        return time.perf_counter() - start

    once()
    return statistics.median(once() for _ in range(runs))


def time_baseline(env: Mapping[str, str] | None, runs: int, cwd: Path | None = None) -> float:
    """Median wall seconds of `python -c pass`, spawned with the same env, cwd and warm-up as the cox command."""
    return time_command([sys.executable, "-c", "pass"], env, runs, cwd)


def check_ceiling(cox_median: float, baseline_median: float, multiple: float, absolute_target: float) -> StartupResult:
    """Pass when the cox median is at most `multiple` times the baseline; the absolute target is reported only."""
    ratio = cox_median / baseline_median
    if ratio <= multiple:
        return StartupResult(True, "")
    return StartupResult(
        False,
        f"cox took {cox_median:.3f}s against a {baseline_median:.3f}s interpreter baseline: "
        f"ratio {ratio:.2f}x exceeds the {multiple:.2f}x multiple "
        f"(documented local target {absolute_target:.2f}s)",
    )
