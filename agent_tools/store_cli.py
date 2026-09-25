"""Wrapper for graphs' `python -m harness.store_cli`: pure argv builders, pure parsers, one thin edge.

The contract, from graphs-store-write-cli. Every command runs as
`<harness_dir>/.venv/bin/python -m harness.store_cli <command...>` and prints one JSON
object on stdout. Exit 0 is success, 3 a refused precondition, 2 bad arguments or an
unreadable store. `--store-url <url>` is optional; this module omits it.

    mark-landed <run_id> <phase> <task> --pr <url> --at <iso>
        exit 0 -> the task record as a JSON object; exit 3 -> the record is not in the store
    lease acquire <name> <holder> --ttl <seconds>
    lease renew   <name> <holder> <epoch> --ttl <seconds>
    lease release <name> <holder> <epoch>
        exit 0 -> {"ok": true, "epoch": <int>, "holder": "<holder>"}
        exit 3 -> {"ok": false, "epoch": <int or null>, "holder": "<current holder or null>"}

Exit 3 is a result, not an error. The lease name is the caller's value.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

from agent_tools.run_store import _harness_python


@dataclass(frozen=True)
class Landed:
    record: dict[str, Any]


@dataclass(frozen=True)
class NotInStore:
    pass


@dataclass(frozen=True)
class Failed:
    code: int
    detail: str


@dataclass(frozen=True)
class LeaseGranted:
    epoch: int
    holder: str


@dataclass(frozen=True)
class LeaseRefused:
    epoch: int | None
    holder: str | None


@dataclass(frozen=True)
class LeaseError:
    detail: str


@dataclass(frozen=True)
class NotAvailable:
    pass


MarkLandedResult = Landed | NotInStore | Failed | NotAvailable
LeaseResult = LeaseGranted | LeaseRefused | LeaseError | NotAvailable

_MODULE = ["-m", "harness.store_cli"]


def mark_landed_argv(python: str, run_id: str, phase: str, task: str, pr: str, at: str) -> list[str]:
    return [python, *_MODULE, "mark-landed", run_id, phase, task, "--pr", pr, "--at", at]


def lease_acquire_argv(python: str, name: str, holder: str, ttl: int) -> list[str]:
    return [python, *_MODULE, "lease", "acquire", name, holder, "--ttl", str(ttl)]


def lease_renew_argv(python: str, name: str, holder: str, epoch: int, ttl: int) -> list[str]:
    return [python, *_MODULE, "lease", "renew", name, holder, str(epoch), "--ttl", str(ttl)]


def lease_release_argv(python: str, name: str, holder: str, epoch: int) -> list[str]:
    return [python, *_MODULE, "lease", "release", name, holder, str(epoch)]


def _json_object(stdout: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(stdout)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_mark_landed(code: int, stdout: str) -> Landed | NotInStore | Failed:
    """Exit 0 with a JSON object is the record; exit 3 is not-in-store; anything else fails."""
    record = _json_object(stdout)
    if code == 3:
        return NotInStore()
    if code == 0 and record is not None:
        return Landed(record)
    return Failed(code, stdout.strip() or "no JSON object on stdout")


def parse_lease(code: int, stdout: str) -> LeaseGranted | LeaseRefused | LeaseError:
    """Exit 0 is granted, exit 3 is refused (epoch and holder may be None), exit 2 and the rest are errors."""
    body = _json_object(stdout)
    if body is None:
        return LeaseError(f"exit {code}: {stdout.strip() or 'no JSON object on stdout'}")
    if code == 0 and body.get("ok") is True and isinstance(body.get("epoch"), int) and isinstance(body.get("holder"), str):
        return LeaseGranted(body["epoch"], body["holder"])
    if code == 3:
        return LeaseRefused(body.get("epoch"), body.get("holder"))
    return LeaseError(f"exit {code}: {stdout.strip()}")


def _run(build: Any) -> tuple[int, str] | None:
    """Edge. Run the argv `build(python)` returns; None when the harness is missing. A failed spawn is code -1."""
    python = _harness_python()
    if python is None:
        return None
    try:
        done = subprocess.run(build(str(python)), capture_output=True, text=True, check=False)
    except OSError as exc:
        return -1, str(exc)
    return done.returncode, done.stdout or done.stderr


def mark_landed(run_id: str, phase: str, task: str, pr: str, at: str) -> MarkLandedResult:
    ran = _run(lambda python: mark_landed_argv(python, run_id, phase, task, pr, at))
    return NotAvailable() if ran is None else parse_mark_landed(*ran)


def lease_acquire(name: str, holder: str, ttl: int) -> LeaseResult:
    ran = _run(lambda python: lease_acquire_argv(python, name, holder, ttl))
    return NotAvailable() if ran is None else parse_lease(*ran)


def lease_renew(name: str, holder: str, epoch: int, ttl: int) -> LeaseResult:
    ran = _run(lambda python: lease_renew_argv(python, name, holder, epoch, ttl))
    return NotAvailable() if ran is None else parse_lease(*ran)


def lease_release(name: str, holder: str, epoch: int) -> LeaseResult:
    ran = _run(lambda python: lease_release_argv(python, name, holder, epoch))
    return NotAvailable() if ran is None else parse_lease(*ran)
