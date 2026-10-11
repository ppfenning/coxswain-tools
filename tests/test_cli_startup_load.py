"""Startup stays near an in-loop reference program while every core is busy, and a 3x injected regression stands out.

Each round times `cox --help` and a reference program back to back, so load present during the round lands on both
and divides out of the cox/reference ratio. The regression test times clean cox and regressed cox inside the same
rounds, so a load change during the test reaches both medians alike. The verdicts come from the pure core in
tests/startup_load_verdict.py; no absolute time, interpreter version or CI flag enters them, so these tests run
under CI. The unloaded bound, including the absolute help target, stays in tests/test_cli_startup.py.

Headroom, measured 2026-10-11 under the load fixture on a local Linux host: clean median ratios 1.01 to 1.31, two
clean medians from separate loops differed by at most 1.14x, and the 3x fixture moved the paired median ratio by
2.91x to 2.96x. The margin of 1.8 sits about 1.6x from each side. Shared CI hosts have not been measured; if their
jitter is wider than 1.14x, this headroom is the thing to remeasure.
"""

import hashlib
import os
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import pytest

from tests.startup_load_verdict import Round, check_clean, check_regression

REPO_ROOT = Path(__file__).resolve().parent.parent

# Twice the core count keeps every core runnable while the timed subprocesses compete for them.
LOAD_THREADS = 2 * (os.cpu_count() or 1)
LOAD_BUFFER = bytes(1 << 20)

# Rounds per measurement; each verdict is a median over them.
ROUNDS = 15
# Clean median ratios measured 1.01 to 1.31; 2.0 is about 1.5x the worst, and a 3x regression (about 3.0) fails it.
CLEAN_CEILING = 2.0
# Clean-to-clean spread measured 1.14x at most and the 3x fixture 2.91x at least; 1.8 is about 1.6x from each.
REGRESSION_MARGIN = 1.8
# Added import time is twice the clean cox median, so total startup is about 3x clean.
REGRESSION_EXTRA_FACTOR = 2.0

# Mirrors the weight of cox's startup imports: the stdlib modules agent_tools/cli.py imports at top level, plus yaml.
REFERENCE_MODULES = (
    "argparse",
    "collections",
    "contextlib",
    "dataclasses",
    "datetime",
    "getpass",
    "importlib.metadata",
    "importlib.util",
    "io",
    "json",
    "re",
    "secrets",
    "shlex",
    "shutil",
    "socket",
    "sqlite3",
    "subprocess",
    "tempfile",
    "tomllib",
    "urllib.parse",
    "uuid",
    "yaml",
)
# Subcommands the reference parser builds and formats, standing in for the interpreted work behind cox's --help.
REFERENCE_SUBCOMMANDS = 60
REFERENCE_PROGRAM = (
    f"import {', '.join(REFERENCE_MODULES)}\n"
    "parser = argparse.ArgumentParser(prog='ref')\n"
    "sub = parser.add_subparsers()\n"
    f"helps = [sub.add_parser(f'c{{i}}', help='x').format_help() for i in range({REFERENCE_SUBCOMMANDS})]\n"
    "print(parser.format_help())\n"
)


def _env(extra_paths: Sequence[Path] = ()) -> dict[str, str]:
    """The parent environment with `extra_paths` then the repo root first on PYTHONPATH."""
    inherited = os.environ.get("PYTHONPATH", "")
    parts = [*map(str, extra_paths), str(REPO_ROOT), inherited]
    return {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in parts if p)}


def _help_argv() -> list[str]:
    return [sys.executable, "-m", "agent_tools.cli", "--help"]


def _reference_argv() -> list[str]:
    return [sys.executable, "-c", REFERENCE_PROGRAM]


def _time_run(argv: Sequence[str], env: Mapping[str, str]) -> float:
    """Wall seconds of one subprocess run on a monotonic clock; a non-zero exit raises."""
    start = time.monotonic()
    subprocess.run(list(argv), capture_output=True, cwd=REPO_ROOT, env=env, check=True)
    return time.monotonic() - start


def _interleaved_rounds(cox_envs: Sequence[Mapping[str, str]]) -> list[list[Round]]:
    """One (cox, reference) series per cox env, all taken in the same rounds; the first round is a discarded warm-up."""
    rounds = [
        [(_time_run(_help_argv(), env), _time_run(_reference_argv(), _env())) for env in cox_envs]
        for _ in range(ROUNDS + 1)
    ]
    return [list(series) for series in zip(*rounds[1:], strict=True)]


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
    [rounds] = _interleaved_rounds([_env()])
    verdict = check_clean(rounds, CLEAN_CEILING)
    assert verdict.ok, str(verdict)


def test_injected_3x_regression_fails_relative_check(busy_load: None, tmp_path: Path) -> None:
    [sizing] = _interleaved_rounds([_env()])

    site = tmp_path / "site"
    site.mkdir()
    delay = REGRESSION_EXTRA_FACTOR * statistics.median(cox for cox, _ in sizing)
    (site / "sitecustomize.py").write_text(f"import time\ntime.sleep({delay!r})\n", encoding="utf-8")

    clean, regressed = _interleaved_rounds([_env(), _env([site])])
    verdict = check_regression(clean, regressed, REGRESSION_MARGIN)
    assert verdict.ok, f"3x regression not detected: {verdict}"
