"""`cox host`: the hosts table as a lane-host source. Pure rows and argv here; the gathers and the ssh sync take an injected `run`.

The table has no workspace_dir column. A host's `cox host beat` records its own workspace_dir and checkout paths in
`versions_json`, so a host added only to the table becomes launchable once it has beaten.
"""

from __future__ import annotations

import datetime
import json
import os
import platform
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from agent_tools.checkout_env import (
    CHECKOUT_ENV_KEY,
    LOCKFILE_NAME,
    CheckoutEnvEntry,
    blocks_launch,
    host_env_verdict,
    sync_instruction,
)
from agent_tools.lane_hosts import LaneHost
from agent_tools.remote_argv import ssh_argv

# Where a checkout records the lock hash its environment last matched, inside the environment directory.
STAMP_RELATIVE_PATH = ".venv/.cox-lock-hash"

Run = Callable[[list[str]], tuple[int, str]]
Row = Mapping[str, object]

__all__ = [
    "add_argv", "beat_argv", "beat_versions", "capacity_upsert_argv", "delete_argv", "dispatchable", "doctor_line", "env_rows", "format_host_list",
    "host_line", "host_rows_to_lane_hosts", "local_host_add_argv", "local_host_missing", "local_workspace_dir", "recorded_repos", "remove_refusal",
    "removed_line", "row_capabilities", "row_capacities", "row_weights", "set_state_argv", "shadowed", "sync_host", "versions_report",
]


def versions_report(cox: str | None, graphs: str | None, cartridges: str | None, claude: str | None, login_ok: bool | None) -> dict:
    return {"cox": cox, "graphs": graphs, "cartridges": cartridges, "claude": claude, "login_ok": login_ok}


def _versions(row: Row) -> dict:
    """`versions_json` as a dict: a JSON string from SQLite, already a mapping from a Postgres JSONB column."""
    raw = row.get("versions_json")
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        parsed = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _workspace(row: Row) -> str:
    recorded = _versions(row).get("workspace_dir")
    return recorded if isinstance(recorded, str) and recorded.startswith("/") else ""


def host_rows_to_lane_hosts(rows: Sequence[Row], profile_hosts: Sequence[LaneHost] = (), local: str = "") -> tuple[LaneHost, ...]:
    """Only `active` rows, minus one named `local`: the machine running the loop is never a remote lane-host
    candidate for itself. workspace_dir is the host's own beat's, else the same-named profile host's, else ''."""
    workspaces = {h.name: h.workspace_dir for h in profile_hosts}
    return tuple(
        LaneHost(str(r["name"]), str(r["ssh"]), _workspace(r) or workspaces.get(str(r["name"]), ""))
        for r in rows if r.get("state") == "active" and str(r["name"]) != local
    )


def dispatchable(hosts: Sequence[LaneHost]) -> tuple[list[str], list[str]]:
    """(names a launch can reach, names with no workspace_dir that `route launch --on` would refuse)."""
    return [h.name for h in hosts if h.workspace_dir], [h.name for h in hosts if not h.workspace_dir]


def local_workspace_dir(rows: Sequence[Row], local: str, profile_workspace_dir: str) -> str:
    """The local machine's own workspace_dir: the row named `local`'s own beat's recorded value when it has one,
    else the routing profile's top-level `workspace_dir`. The local host's checkout already is the profile's own,
    so looking this up never refuses the way an unbeaten remote host's empty workspace_dir would."""
    row = next((r for r in rows if str(r.get("name")) == local), None)
    return (_workspace(row) if row is not None else "") or profile_workspace_dir


def shadowed(profile_hosts: Sequence[LaneHost], table_names: Sequence[str]) -> list[str]:
    """Profile lane hosts that stop being lane hosts because the table has rows and does not name them."""
    return [h.name for h in profile_hosts if h.name not in set(table_names)]


def recorded_repos(row: Row | None) -> list[str]:
    """The checkout paths the host's own beat recorded; empty when it never beat with them."""
    repos = _versions(row).get("repos") if row is not None else None
    return [r for r in repos if isinstance(r, str) and r.startswith("/")] if isinstance(repos, list) else []


def row_capacities(rows: Sequence[Row]) -> dict[str, int]:
    """Capacity per active host, for hosts whose row carries one."""
    return {str(r["name"]): int(r["capacity"]) for r in rows if r.get("state") == "active" and r.get("capacity") is not None}  # type: ignore[call-overload]


def row_weights(rows: Sequence[Row]) -> dict[str, int]:
    """Weight per active host, for hosts whose row carries one. A store with no weight column yields none."""
    return {str(r["name"]): int(r["weight"]) for r in rows if r.get("state") == "active" and r.get("weight") is not None}  # type: ignore[call-overload]


def _capability_list(value: object) -> list[str]:
    """A list from Postgres, JSON list text or comma-separated text from SQLite, as `cox host add --capabilities` writes it."""
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value).strip()
    try:
        parsed = json.loads(text) if text.startswith("[") else None
    except ValueError:
        parsed = None
    return [str(v) for v in parsed] if isinstance(parsed, list) else [c.strip() for c in text.split(",") if c.strip()]


def row_capabilities(rows: Sequence[Row]) -> dict[str, list[str]]:
    """Capabilities per active host, for hosts whose row carries them. A store with no capabilities column yields none."""
    return {
        str(r["name"]): _capability_list(r["capabilities"])
        for r in rows if r.get("state") == "active" and r.get("capabilities") is not None
    }


def _login(row: Row) -> str:
    ok = _versions(row).get("login_ok")
    return "ok" if ok is True else "no" if ok is False else "?"


def _as_time(value: object) -> datetime.datetime | None:
    """`beat_at` as an aware time: a datetime from Postgres, ISO text from SQLite, None when absent or unreadable."""
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.UTC)


def _age(beat_at: object, now: datetime.datetime) -> str:
    beat = _as_time(beat_at)
    if beat is None:
        return "never"
    seconds = max(0, int((now - beat).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago" if seconds < 86400 else f"{seconds // 86400}d ago"


def _display_state(row: Row, live_by_host: Mapping[str, int]) -> str:
    """`draining` with no live lane reads `drained`; every other state is the row's own."""
    state = str(row["state"])
    return "drained" if state == "draining" and live_by_host.get(str(row["name"]), 0) == 0 else state


def host_line(row: Row, now: datetime.datetime, live_by_host: Mapping[str, int] | None = None) -> str:
    state = _display_state(row, live_by_host or {})
    return f"{row['name']}  {state}  cap {row['capacity']}  beat {_age(row.get('beat_at'), now)}  login {_login(row)}"


def env_rows(row: Row) -> list[str]:
    """One row per repo in the stored `checkout_env` record, as the planner reads it; one unknown row when there is none."""
    versions = _versions(row)
    record = versions.get(CHECKOUT_ENV_KEY)
    if not isinstance(record, dict) or not record:
        return ["checkout env: unknown"]
    name = str(row["name"])

    def one(repo: str) -> str:
        verdict = host_env_verdict(versions, repo)
        base = f"checkout env: {repo} {verdict}"
        return f"{base}  {sync_instruction(name, repo)}" if blocks_launch(verdict) else base

    return [one(str(repo)) for repo in record]


def doctor_line(row: Row | None, now: datetime.datetime) -> str:
    """The table's line for `host doctor`: state, beat age, the recorded `login_ok` and workspace_dir, then the env rows."""
    if row is None:
        return "not in the hosts table"
    workspace = _workspace(row) or "none recorded"
    table = f"table: {row['state']}  beat {_age(row.get('beat_at'), now)}  login_ok {json.dumps(_versions(row).get('login_ok'))}  workspace {workspace}"
    return "\n".join([table, *env_rows(row)])


def format_host_list(rows: Sequence[Row], now: datetime.datetime, live_by_host: Mapping[str, int] | None = None) -> list[str]:
    return [host_line(r, now, live_by_host) for r in rows]


def add_argv(name: str, ssh: str, capacity: int, weight: int, capabilities: str, by: str) -> list[str]:
    """`host upsert`'s argv: only what harness/store_cli.py's parser accepts. `weight` and `capabilities` are
    taken but never sent — the hosts table has no column for either, so they stay off the wire.
    """
    del weight, capabilities
    return ["host", "upsert", name, "--ssh", ssh, "--capacity", str(capacity), "--by", by]


def capacity_upsert_argv(row: Row, n: int, by: str) -> list[str]:
    """`host upsert`'s argv with only capacity changed: ssh comes from `row`, capacity from `n`, nothing else is
    sent — the store has no column for weight or capabilities.

    No `--state`: no `host upsert` caller here sends one, and state moves only through `set-state`, so it is left as it is.
    """
    return add_argv(str(row["name"]), str(row["ssh"]), n, 1, "", by)


def local_host_missing(rows: Sequence[Row], hostname: str) -> bool:
    """True when no row in `rows` names `hostname`."""
    return not any(r.get("name") == hostname for r in rows)


def local_host_add_argv(hostname: str, capacity: int, by: str) -> list[str]:
    """The `host upsert` argv to add the local machine: `--ssh` is its own hostname, since it has no separate ssh destination."""
    return ["host", "upsert", hostname, "--ssh", hostname, "--capacity", str(capacity), "--by", by]


def set_state_argv(name: str, state: str, by: str) -> list[str]:
    return ["host", "set-state", name, state, "--by", by]


def delete_argv(name: str, by: str) -> list[str]:
    return ["host", "delete", name, "--by", by]


def remove_refusal(row: Row | None, live_runs: Sequence[str], name: str) -> str | None:
    """Why `host remove` must write nothing, or None when the row is a draining host with no live run."""
    if row is None:
        return f"refused {name}: no such host"
    if row["state"] != "draining":
        return f"refused {name}: {row['state']}; run `cox host drain {name}` first"
    if live_runs:
        return f"refused {name}: live run {', '.join(live_runs)} still on it; wait for it to finish"
    return None


def removed_line(row: Row) -> str:
    """The whole row as JSON, then the `host add` call that restores it; a null ssh falls back to the name, as `local_host_add_argv` does."""
    ssh = row.get("ssh") or row["name"]
    restore = f"cox host add {row['name']} --ssh {ssh} --capacity {row['capacity']}"
    return f"{row['name']}: {json.dumps(dict(row), sort_keys=True, default=str)}\n  restore: {restore}"


def beat_argv(name: str, versions: dict) -> list[str]:
    return ["host", "beat", name, "--versions", json.dumps(versions, sort_keys=True)]


def _out(run: Run, argv: list[str]) -> str | None:
    """First stdout line of `argv`; None on any failure, so a missing tool is a null value and never a refusal."""
    try:
        code, output = run(argv)
    except Exception:
        return None
    lines = output.strip().splitlines()
    return lines[0].strip() if code == 0 and lines else None


def _logged_in(run: Run) -> bool | None:
    try:
        code, output = run(["claude", "auth", "status"])
        status = json.loads(output)
    except Exception:
        return None
    return status.get("loggedIn") if code == 0 and isinstance(status, dict) and isinstance(status.get("loggedIn"), bool) else None


def _mem_total_gb() -> float:
    """MemTotal from /proc/meminfo, in GiB to one decimal; 0.0 when the file or the line is unreadable."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / (1024 * 1024), 1)
    except (OSError, ValueError, IndexError):
        return 0.0
    return 0.0


def _host_facts() -> dict:
    """This machine's own os, cpu count and total memory -- never injected through `run`, since they are
    local process facts rather than another tool's output."""
    return {"os": platform.platform(), "cpu_count": os.cpu_count() or 0, "mem_total_gb": _mem_total_gb()}


def _checkout_detail(run: Run, repo: str) -> tuple[str, dict]:
    """`(repo dir name, {branch, behind_main})` for one checkout, both read through `run` with no fetch --
    `behind_main` counts commits against the last-fetched `origin/main`. A repo that fails either read
    yields branch "" and behind_main 0."""
    branch = _out(run, ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"]) or ""
    behind_raw = _out(run, ["git", "-C", repo, "rev-list", "--count", "HEAD..origin/main"])
    try:
        behind_main = int(behind_raw) if behind_raw is not None else 0
    except ValueError:
        behind_main = 0
    return Path(repo).name, {"branch": branch, "behind_main": behind_main}


def _checkouts(run: Run, repos: Sequence[str]) -> dict:
    return dict(_checkout_detail(run, repo) for repo in repos)


def _env_check_argv(repo: str) -> list[str]:
    # unknown: `--check` is the uv flag that exits non-zero when the environment lags uv.lock and
    # installs nothing; confirm against the host's `uv sync --help`.
    return ["uv", "sync", "--check", "--frozen", "--all-extras", "--directory", repo]


def _env_matches(run: Run, repo: str) -> bool:
    """True only when the read-only environment check exits 0; any failure is False."""
    try:
        code, _ = run(_env_check_argv(repo))
    except Exception:
        return False
    return code == 0


def _record_stamp(run: Run, repo: str, lock_hash: str | None) -> None:
    """Write lock_hash into the checkout's stamp when the read-only check says the environment matches it.
    A mismatch, a failed check or a failed write leaves the stamp as it was."""
    if lock_hash is None or not _env_matches(run, repo):
        return
    try:
        run(["sh", "-c", 'printf %s "$1" > "$2"', "sh", lock_hash, f"{repo}/{STAMP_RELATIVE_PATH}"])
    except Exception:
        return


def _checkout_env(run: Run, repo: str) -> tuple[str, CheckoutEnvEntry]:
    """`(repo dir name, {lock_hash, synced_hash})`. The stamp is attempted before it is read, so a
    matching environment reports synced_hash equal to lock_hash. A failed read yields None."""
    lock_hash = _out(run, ["git", "-C", repo, "hash-object", LOCKFILE_NAME])
    _record_stamp(run, repo, lock_hash)
    synced_hash = _out(run, ["cat", f"{repo}/{STAMP_RELATIVE_PATH}"])
    return Path(repo).name, {"lock_hash": lock_hash, "synced_hash": synced_hash}


def beat_versions(
    run: Run, package_version: str | None, harness_dir: str | None, cartridges_dir: str | None,
    workspace_dir: str | None = None, repos: Sequence[str] = (),
) -> dict:
    """This machine's versions, plus its workspace_dir, checkout paths, host facts, per-checkout branch
    and commits-behind-main, and per-checkout lock and synced hashes. `cox --version` text if it answers,
    else the package version."""
    def tag(directory: str | None) -> str | None:
        return _out(run, ["git", "-C", directory, "describe", "--tags"]) if directory else None

    versions = versions_report(
        _out(run, ["cox", "--version"]) or package_version, tag(harness_dir), tag(cartridges_dir),
        _out(run, ["claude", "--version"]), _logged_in(run),
    )
    return {
        **versions, "workspace_dir": workspace_dir, "repos": list(repos),
        "host_facts": _host_facts(), "checkouts": _checkouts(run, repos),
        CHECKOUT_ENV_KEY: dict(_checkout_env(run, repo) for repo in repos),
    }


def _pull_line(ssh: str, repo: str, run: Run) -> tuple[bool, str]:
    code, output = run(ssh_argv(ssh, ["git", "-C", repo, "pull", "--ff-only"]))
    if code == 0:
        _, head = run(ssh_argv(ssh, ["git", "-C", repo, "rev-parse", "--short", "HEAD"]))
        return True, f"{Path(repo).name}  {(head.strip().splitlines() or ['?'])[-1]}"
    why = (
        f"no checkout at {repo} on the host" if "cannot change to" in output or "No such file" in output
        else "not a fast-forward" if "ast-forward" in output
        else "pull failed"
    )
    return False, f"{Path(repo).name}  not updated ({why}): {' '.join(output.split())[:120]}"


def sync_host(ssh: str, repos: Sequence[str], run: Run) -> tuple[int, list[str]]:
    """`git pull --ff-only` in each repo over ssh; one line per repo with its new head. A non-fast-forward is reported, not forced."""
    results = [_pull_line(ssh, repo, run) for repo in repos]
    return (0 if all(ok for ok, _ in results) else 1), [line for _, line in results]
