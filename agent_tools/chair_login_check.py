"""Edge: check a lane host's claude login, then record the result with `cox host beat`.

Reuses `remote_argv.ssh_argv`/`auth_status_argv` for the ssh invocation. The runner returns an
`ArgvResult`-shaped reply and `claude auth status` is parsed from its stdout only: the wrong belief was that
mixed stdout and stderr parse as JSON, so one stderr line made a logged-in host read as unreadable. Reuses
`host_cmd.beat_argv` for the store_cli write, so the write goes through the one path (`cox host beat`, an
injected runner) that already knows how to reach store_cli.
This module never logs a host in. It does check the loop's own machine: the wrong belief was that every
host row is reached over ssh, but the local host runs no sshd, so ssh to it always failed and recorded a
false `login_lapsed`. A host whose name equals `socket.gethostname()` runs `claude auth status` directly.

`claude auth status` never renews an expired access token, so a host that only needs renewal reads as logged
out. A logged-out reply therefore runs one `claude -p` to renew before it counts as a failure, unless the
refresh token itself is expired. A failure blocks the host only on its second in a row.

A host authenticated by an env-provided `oauth_token` reports `loggedIn` true, but only to a login shell
that reads the startup file exporting it. The wrong belief was that the plain-shell reply was the host's
status: it reads logged out, and neither the renew nor the keychain lookup can see the token. `ssh_argv`
supplies the login shell, so a token host takes the logged-in return in `_claude_check` and never renews.

A host running a `runner` other than `claude-code` has no `claude` CLI to ask, so its readiness check
reads `env_names` (a provider profile's `auth_env`/`endpoint_env`) off the host instead, one `printenv`
per name, over the same local/ssh split. `claude` is never invoked on that branch.
"""
from __future__ import annotations

import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from agent_tools import chair_login_watch, host_cmd
from agent_tools.remote_argv import auth_status_argv, env_check_argv, json_object_in, ssh_argv

BLOCK_AFTER_FAILURES = 2
REASON_OUTPUT_CHARS = 200
_RENEW_ARGV = ["claude", "-p", "ok", "--max-turns", "1", "--model", "haiku"]
_CREDENTIALS_SCRIPT = (
    'if [ "$(uname -s)" = Darwin ]; then security find-generic-password -s "Claude Code-credentials" -w; '
    "else cat ~/.claude/.credentials.json; fi"
)


class Reply(Protocol):
    """What the runner hands back for one argv: `chair_exec.ArgvResult` in production."""

    @property
    def returncode(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def mixed(self) -> str: ...


SshRun = Callable[[list[str]], Reply]
CliRun = Callable[[list[str]], dict]


@dataclass(frozen=True)
class _Check:
    ok: bool
    expired: bool
    reason: str | None
    output: str


def _remote_argv(host_name: str, ssh: str, argv: list[str]) -> list[str]:
    """`argv` unwrapped on the loop's own host (`host_name == socket.gethostname()`); `ssh_argv`-wrapped
    everywhere else."""
    return argv if host_name == socket.gethostname() else ssh_argv(ssh, argv)


def _stdout_verdict(stdout: str) -> bool | None:
    """True when stdout is JSON with `loggedIn` true; False for any other JSON object; None when stdout is
    not a JSON object."""
    status = json_object_in(stdout)
    return status.get("loggedIn") is True if status is not None else None


def _failure_reason(returncode: int, output: str) -> str:
    """The exit code and the first `REASON_OUTPUT_CHARS` characters of the trimmed output."""
    return f"exit {returncode}: {output.strip()[:REASON_OUTPUT_CHARS]}"


def _failure_count(versions: dict) -> int:
    """The stored `login_failures`; 0 when absent or not a positive int."""
    stored = versions.get("login_failures")
    return stored if isinstance(stored, int) and not isinstance(stored, bool) and stored > 0 else 0


def _refresh_expiry(credentials: str) -> datetime | None:
    """`claudeAiOauth.refreshTokenExpiresAt` of a credentials blob, from epoch seconds, epoch milliseconds
    or an ISO string; None when the blob or the key cannot be read."""
    try:
        blob = json.loads(credentials)
    except ValueError:
        return None
    oauth = blob.get("claudeAiOauth") if isinstance(blob, dict) else None
    raw = oauth.get("refreshTokenExpiresAt") if isinstance(oauth, dict) else None
    if isinstance(raw, str):
        return chair_login_watch._parsed(raw)
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    try:
        return datetime.fromtimestamp(raw / 1000 if raw > 1e11 else raw, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _refresh_expired(expires: datetime | None, now: str) -> bool:
    """True only when `expires` is known and not after `now`; unreadable is never expired."""
    moment = chair_login_watch._parsed(now)
    return expires is not None and moment is not None and expires <= moment


def _claude_check(host_name: str, ssh: str, ssh_run: SshRun, now: str) -> _Check:
    """`claude auth status` once. Logged out with a live refresh token, or credentials that cannot be read,
    runs one `claude -p` to renew, re-checks, and records that re-check. Logged out with an expired refresh
    token never renews. An unreadable reply neither reads credentials nor renews."""
    status = ssh_run(_remote_argv(host_name, ssh, auth_status_argv()))
    verdict = _stdout_verdict(status.stdout)
    if verdict is True:
        return _Check(True, False, None, status.mixed)
    if verdict is None:
        return _Check(False, False, _failure_reason(status.returncode, status.mixed), status.mixed)
    credentials = ssh_run(_remote_argv(host_name, ssh, ["sh", "-c", _CREDENTIALS_SCRIPT]))
    if credentials.returncode == 0 and _refresh_expired(_refresh_expiry(credentials.stdout), now):
        return _Check(False, True, _failure_reason(status.returncode, status.mixed), status.mixed)
    ssh_run(_remote_argv(host_name, ssh, _RENEW_ARGV))
    again = ssh_run(_remote_argv(host_name, ssh, auth_status_argv()))
    if _stdout_verdict(again.stdout) is True:
        return _Check(True, False, None, again.mixed)
    return _Check(False, False, _failure_reason(again.returncode, again.mixed), again.mixed)


def _login_ok(host_name: str, ssh: str, ssh_run: Callable[[list[str]], tuple[int, str]]) -> bool | None:
    """True when `claude auth status` output says logged in; False when it says explicitly it is not; None
    when the reply cannot be read at all. Takes a `(exit code, text)` runner and never renews."""
    return _stdout_verdict(ssh_run(_remote_argv(host_name, ssh, auth_status_argv()))[1])


def _env_check(host_name: str, ssh: str, ssh_run: SshRun, env_names: tuple[str, ...]) -> tuple[bool, str | None]:
    """True with no reason when every name in `env_names` is present; False naming the first missing one,
    `"missing <name>"`, otherwise. False with a fixed reason when `env_names` is empty: a provider profile
    naming neither `auth_env` nor `endpoint_env`. `claude` is never invoked here.

    Present means exit 0 and non-empty trimmed stdout. The wrong belief is that non-empty output alone
    proves the variable is set: an ssh failure (exit 255, "Connection refused" on stderr) must not read as
    present. `printenv` exits 1 on an unset name, so the exit code separates set from missing and from
    unreachable."""
    if not env_names:
        return False, "no auth_env or endpoint_env configured"
    for name in env_names:
        reply = ssh_run(_remote_argv(host_name, ssh, env_check_argv(name)))
        if reply.returncode != 0 or not reply.stdout.strip():
            return False, f"missing {name}"
    return True, None


def check_login_on_host(
    host_name: str,
    ssh: str,
    current_versions: dict,
    now: str,
    ssh_run: SshRun,
    cli_run: CliRun,
    *,
    runner: str = "claude-code",
    env_names: tuple[str, ...] = (),
) -> dict:
    """Runs the readiness check, locally on the loop's own host and over ssh elsewhere, merges its
    `login_ok`, `check`, `reason` and `now` into `current_versions`, writes the merge through the reused
    `cox host beat` argv, and returns the row `cli_run` prints. `ssh_run` returns an `ArgvResult`.

    `runner == "claude-code"` (the default) asks `claude auth status` (see `_claude_check`). A pass stores
    `login_ok` True and `login_failures` 0. A failure stores `reason` and adds one to `login_failures`;
    `login_ok` is False once that count reaches `BLOCK_AFTER_FAILURES` or the refresh token is expired, and
    None before. A failed check also stores the first https URL in its output as `login_url`; a passing
    check clears any stored one. Any other `runner` checks `env_names` instead, with no failure count;
    `claude` is never invoked on that branch."""
    if runner == "claude-code":
        check = _claude_check(host_name, ssh, ssh_run, now)
        failures = 0 if check.ok else _failure_count(current_versions) + 1
        login_ok = True if check.ok else (False if check.expired or failures >= BLOCK_AFTER_FAILURES else None)
        login_url = None if check.ok else chair_login_watch.login_url_from_output(check.output)
        kept = {key: value for key, value in current_versions.items() if key != "reason"}
        merged = {
            **chair_login_watch.merge_login_versions(
                kept, login_ok, now, check="claude_auth", reason=check.reason, login_url=login_url
            ),
            "login_failures": failures,
        }
    else:
        env_ok, env_reason = _env_check(host_name, ssh, ssh_run, env_names)
        merged = chair_login_watch.merge_login_versions(
            current_versions, env_ok, now, check="env_vars", reason=env_reason
        )
    return cli_run(host_cmd.beat_argv(host_name, merged))
