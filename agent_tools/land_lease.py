from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_tools import store_cli


@dataclass(frozen=True)
class Ran[T]:
    value: T
    epoch: int
    warning: str | None


@dataclass(frozen=True)
class Refused:
    epoch: int | None
    holder: str | None


@dataclass(frozen=True)
class Unavailable:
    detail: str


LeaseCall = Callable[..., store_cli.LeaseResult]


def _reason(result: store_cli.LeaseResult) -> str:
    if isinstance(result, store_cli.LeaseError):
        return result.detail
    if isinstance(result, store_cli.LeaseRefused):
        return f"held by {result.holder} at epoch {result.epoch}"
    if isinstance(result, store_cli.NotAvailable):
        return "no harness"
    return f"unexpected result {type(result).__name__}"


def _release_warning(name: str, result: store_cli.LeaseResult | Exception) -> str | None:
    if isinstance(result, store_cli.LeaseReleased):
        return None
    reason = f"{type(result).__name__}: {result}" if isinstance(result, Exception) else _reason(result)
    return f"warning: land lease {name} not released: {reason}"


def _release(give: LeaseCall, runs_dir: Path, name: str, holder: str, epoch: int) -> str | None:
    """Never raises an Exception: release runs on the failure path and must not mask the body's outcome."""
    try:
        return _release_warning(name, give(runs_dir=runs_dir, name=name, holder=holder, epoch=epoch))
    except Exception as exc:
        return _release_warning(name, exc)


def run_under_land_lease[T](
    runs_dir: Path,
    task: str,
    holder: str,
    ttl: int,
    body: Callable[[int], T],
    acquire: LeaseCall | None = None,
    release: LeaseCall | None = None,
) -> Ran[T] | Refused | Unavailable:
    """Refused and Unavailable run no body; a release warning rides on Ran, or as a note on the body's exception."""
    take = acquire or store_cli.lease_acquire
    give = release or store_cli.lease_release
    name = store_cli.land_lease_name(task)
    try:
        got = take(runs_dir=runs_dir, name=name, holder=holder, ttl=ttl)
    except OSError as exc:
        # Only a failed spawn is not-available; a TypeError or other bug propagates.
        return Unavailable(f"{type(exc).__name__}: {exc}")
    if isinstance(got, store_cli.LeaseRefused):
        return Refused(got.epoch, got.holder)
    if not isinstance(got, store_cli.LeaseGranted):
        return Unavailable(_reason(got))
    try:
        value = body(got.epoch)
    except BaseException as exc:
        stuck = _release(give, runs_dir, name, holder, got.epoch)
        if stuck is not None:
            exc.add_note(stuck)
        raise
    return Ran(value, got.epoch, _release(give, runs_dir, name, holder, got.epoch))
