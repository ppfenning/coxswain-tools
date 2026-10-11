"""The relative startup check holds while every core is busy, and fails when startup is injected to run 3x slower."""

import hashlib
import os
import sys
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from tests.startup_timing import HELP_TARGET_S, STARTUP_MULTIPLE, check_ceiling, time_baseline, time_command

REPO_ROOT = Path(__file__).resolve().parent.parent

# Twice the core count keeps every core runnable while the timed subprocesses compete for them.
LOAD_THREADS = 2 * (os.cpu_count() or 1)

# Both tests saturate the CPU on purpose and compare cox's startup with the interpreter's. On a shared CI host the
# other jobs' load moves that ratio from 8x to 11x run to run (2026-10-11: tools main red on py315, then extras), so
# the comparison is meaningful only on a quiet machine. They run locally and skip under CI; the unloaded ceiling in
# test_cli_startup.py still runs everywhere.
pytestmark = pytest.mark.skipif(os.environ.get("CI") == "true", reason="load-sensitive timing; run locally")
LOAD_BUFFER = bytes(1 << 20)
RUNS = 7
# Added import time is twice the clean cox median, so total startup is about 3x clean.
REGRESSION_EXTRA_FACTOR = 2.0


def _env(extra_paths: Sequence[Path] = ()) -> dict[str, str]:
    """The parent environment with `extra_paths` then the repo root first on PYTHONPATH."""
    inherited = os.environ.get("PYTHONPATH", "")
    parts = [*map(str, extra_paths), str(REPO_ROOT), inherited]
    return {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in parts if p)}


def _help_argv() -> list[str]:
    return [sys.executable, "-m", "agent_tools.cli", "--help"]


@pytest.fixture
def busy_load() -> Iterator[None]:
    """Run GIL-releasing hash loops on LOAD_THREADS threads for the length of a test."""
    stop = threading.Event()

    def spin() -> None:
        while not stop.is_set():
            hashlib.sha256(LOAD_BUFFER).digest()

    threads = [threading.Thread(target=spin, daemon=True) for _ in range(LOAD_THREADS)]
    for thread in threads:
        thread.start()
    try:
        yield
    finally:
        stop.set()
        for thread in threads:
            thread.join()


def test_relative_check_holds_under_load(busy_load: None) -> None:
    baseline = time_baseline(_env(), RUNS, REPO_ROOT)
    cox = time_command(_help_argv(), _env(), RUNS, REPO_ROOT)
    result = check_ceiling(cox, baseline, STARTUP_MULTIPLE, HELP_TARGET_S)
    assert result.ok, result.message


def test_injected_3x_regression_fails_relative_check(busy_load: None, tmp_path: Path) -> None:
    baseline = time_baseline(_env(), RUNS, REPO_ROOT)
    clean = time_command(_help_argv(), _env(), RUNS, REPO_ROOT)

    site = tmp_path / "site"
    site.mkdir()
    delay = REGRESSION_EXTRA_FACTOR * clean
    (site / "sitecustomize.py").write_text(f"import time\ntime.sleep({delay!r})\n", encoding="utf-8")

    regressed = time_command(_help_argv(), _env([site]), RUNS, REPO_ROOT)
    result = check_ceiling(regressed, baseline, STARTUP_MULTIPLE, HELP_TARGET_S)
    assert not result.ok, (
        f"3x regression passed: clean {clean:.3f}s, regressed {regressed:.3f}s, baseline {baseline:.3f}s"
    )
    assert "exceeds" in result.message
