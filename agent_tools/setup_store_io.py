"""Edge for the setup store choice: write the machine-local shared-store profile, git-ignore it, record `store.mode`.

The profile holds a connection URL with a password. Nothing here prints, logs or raises with it; messages name the
file path and the redacted summary only."""

import contextlib
import io
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path

import yaml

from agent_tools import cox_settings
from agent_tools.setup_store import SharedConnection, build_shared_profile, redacted_summary, validate_shared

# `read_provider_profile` takes any path; the routing profile reaches it through `provider_profile`. So the default is
# a sibling of the settings file, outside every repo. No agent_tools module outside cli.py handles git excludes.
PROFILE_FILE_NAME = "shared-store.yaml"
_OWNER_ONLY = 0o600
_REQUIRED_KEYS = ("storage_url", "traces_url", "object_store")


def default_profile_path(settings_path: Path) -> Path:
    """Pure. The machine-local profile beside the settings file, where `provider_profile` can name it."""
    return Path(settings_path).parent / PROFILE_FILE_NAME


def credentials_from_env(env: Mapping[str, str], names: Mapping[str, str]) -> dict:
    """Pure. `{"values": {field: value}, "unset": [variable names]}`; `names` maps a credential field to the
    variable the user chose. A variable that is missing or empty is unset, and its field is left out of `values`."""
    resolved = {field: env.get(var, "") for field, var in names.items()}
    return {
        "values": {field: value for field, value in resolved.items() if value},
        "unset": [var for field, var in names.items() if not resolved[field]],
    }


def _with_line(existing: str, line: str) -> str:
    """Pure. `existing` with `line` appended once; unchanged when a line already equals it."""
    if line in (row.strip() for row in existing.splitlines()):
        return existing
    separator = "" if not existing or existing.endswith("\n") else "\n"
    return f"{existing}{separator}{line}\n"


def _read_or_empty(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _replace_owner_only(path: Path, text: str) -> None:
    """Write `text` to a fresh 0600 temp file beside `path`, then rename it over `path`. The password is never in a
    file wider than 0600, a failed write leaves the old profile whole, and a symlink is replaced, not followed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


def write_shared_profile(path: Path, profile: dict, exclude_file: Path | None = None) -> Path:
    """Edge. Write `profile` as YAML, owner-only, to the machine-local `path`; identical content is left untouched.
    Refuses a profile missing a provider key, a symlink and a git-tracked file, and never merges the old content.
    With `exclude_file`, its line `path.name` is appended once."""
    missing = [key for key in _REQUIRED_KEYS if key not in profile]
    if missing:
        raise ValueError(f"profile for {path} lacks {', '.join(missing)}")
    if path.is_symlink():
        raise ValueError(f"{path} is a symlink; refusing to write the shared profile through it")
    if path.exists() and cox_settings.git_tracked(path):
        raise ValueError(f"{path} is tracked by git; the shared profile is machine-local")
    text = yaml.safe_dump(profile, sort_keys=False)
    if _read_or_empty(path) != text:
        _replace_owner_only(path, text)
    elif stat.S_IMODE(path.stat().st_mode) != _OWNER_ONLY:
        os.chmod(path, _OWNER_ONLY)
    if exclude_file is not None:
        current = _read_or_empty(exclude_file)
        updated = _with_line(current, path.name)
        if updated != current:
            exclude_file.parent.mkdir(parents=True, exist_ok=True)
            exclude_file.write_text(updated, encoding="utf-8")
    return path


def _refusal_reason(output: str) -> str:
    """Pure. The `error:` lines `run_set` printed, joined; the diff it also prints is left out."""
    reasons = [row.removeprefix("error:").strip() for row in output.splitlines() if row.startswith("error:")]
    return "; ".join(reasons) if reasons else "no reason given"


def record_store_mode(mode: str, settings_path: Path) -> None:
    """Edge. Set `store.mode` through `cox_settings.run_set`, the code path `cox settings set` uses; other keys stay.
    A refusal raises with the mode, the settings path and the reason `run_set` gave."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = cox_settings.run_set(Path(settings_path), "profile", "store.mode", mode, dry_run=False)
    if code != 0:
        reason = _refusal_reason(captured.getvalue())
        raise ValueError(f"could not record store.mode '{mode}' in {settings_path}: {reason}")


def apply_files_choice(settings_path: Path) -> None:
    """Edge. Choosing files records the mode and writes no profile."""
    record_store_mode("files", settings_path)


def apply_shared_choice(conn: SharedConnection, settings_path: Path, exclude_file: Path | None = None) -> str:
    """Edge. Validate `conn`, write the profile beside the settings file, then record `shared`; the profile lands
    first so the mode never names a store with no profile. Returns the path and redacted summary line."""
    problems = validate_shared(conn)
    if problems:
        raise ValueError(f"shared store settings are not valid: {' '.join(problems)}")
    path = write_shared_profile(default_profile_path(settings_path), build_shared_profile(conn), exclude_file)
    record_store_mode("shared", settings_path)
    return f"wrote {path}; {redacted_summary(conn)}"
