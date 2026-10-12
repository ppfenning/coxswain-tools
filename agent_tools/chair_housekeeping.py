"""Chair housekeeping: lake sync, trace prune, runs clean, in order, none stopping the others."""

import re
from collections.abc import Callable

LINE_CUT = 80
REASON_CUT = 200
PRUNE_SKIPPED = "prune skipped: subcommand not available"
PRUNE_OBJECT_STORAGE = "prune: skipped, traces in object storage"
EXCEPTION_LINE = re.compile(r"^[A-Za-z_][\w.]*: .+")
# `cox runs clean` takes one run_id and has no exited, age or work-item-done filter.
CLEAN_MISSING = "exited, older-than-30-days and work-item-done filters"


def sync_argv() -> list[str]:
    return ["cox", "lake", "sync"]


def prune_argv(traces_root: str, days: int, python: str) -> list[str]:
    return [python, "-m", "harness.store_backfill_traces", "prune", traces_root, "--older-than", str(days)]


def clean_argv() -> list[str] | None:
    """None while `cox runs clean` cannot express the filters; nothing destructive runs."""
    return None


def _failure_line(lines: list[str]) -> str:
    # The last exception-shaped line wins. With none, the first line stands, so output
    # that is not a traceback reads as it always did rather than as its last word.
    excs = [line for line in lines if EXCEPTION_LINE.match(line)]
    return excs[-1] if excs else lines[0]


def step_line(name: str, code: int, output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if code == 0:
        head, chosen = f"{name}: ok", lines[0] if lines else ""
    else:
        head, chosen = f"{name}: FAILED exit {code}", _failure_line(lines) if lines else ""
    return f"{head} {chosen[:LINE_CUT]}".rstrip()


def join_reason(lines: list[str]) -> str:
    joined = "; ".join(lines)
    if len(joined) <= REASON_CUT or not lines:
        return joined
    share = (REASON_CUT - 2 * (len(lines) - 1)) // len(lines)
    return "; ".join(line[:share] for line in lines)


def _attempt(run: Callable[[list[str]], tuple[int, str]], name: str, argv: list[str]) -> str:
    try:
        code, output = run(argv)
    except Exception as exc:  # one step's failure must not stop the others
        return step_line(name, 1, f"{type(exc).__name__}: {exc}")
    return step_line(name, code, output)


def run_housekeeping(
    run: Callable[[list[str]], tuple[int, str]],
    traces_root: str,
    prune_available: Callable[[], bool],
    python: str,
    retention_days: int = 7,
) -> tuple[str, str]:
    clean = clean_argv()
    sync = _attempt(run, "lake sync", sync_argv())
    if traces_root.startswith("s3://"):
        prune = PRUNE_OBJECT_STORAGE
    elif prune_available():
        prune = _attempt(run, "prune", prune_argv(traces_root, retention_days, python))
    else:
        prune = PRUNE_SKIPPED
    lines = [
        sync,
        prune,
        _attempt(run, "clean", clean) if clean is not None else f"clean skipped: {CLEAN_MISSING}",
    ]
    failed = any(": FAILED" in line for line in lines)
    return ("failed" if failed else "done", join_reason(lines))
