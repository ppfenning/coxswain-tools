"""Pure core for `agent-tools setup doctor`: judges gathered Facts, never
gathers them. No file reads, no subprocess, no clock — the CLI edge (next
phase) builds a Facts mapping and hands it to `checks`."""

from __future__ import annotations

from collections.abc import Mapping

from agent_tools import records, route

__all__ = ["checks", "exit_code", "render"]

_MISSING = object()
_SKIPPED = "skipped: no profile"
_NOT_CHECKED = "not checked"


def _skip(check: str) -> dict:
    return {"check": check, "ok": False, "detail": _SKIPPED}


def _not_checked(check: str) -> dict:
    return {"check": check, "ok": False, "detail": _NOT_CHECKED}


def _profile_row(facts: Mapping) -> tuple[dict, bool, dict | None]:
    """Returns the row, whether the profile itself failed (as opposed to
    simply not having been checked, which is what triggers the cascade), and
    the parsed profile (or None) so later rows can check against what the
    profile actually configures rather than only what got gathered."""
    path = facts.get("profile_path", "<profile>")
    text = facts.get("profile_text", _MISSING)
    if text is _MISSING:
        return _not_checked("profile"), False, None
    if text is None:
        return {"check": "profile", "ok": False, "detail": f"missing: {path}"}, True, None
    try:
        parsed = route.parse_profile(text)
    except route.ProfileError as err:
        return {"check": "profile", "ok": False, "detail": str(err)}, True, None
    return {"check": "profile", "ok": True, "detail": path}, False, parsed


def _expected_paths(parsed: Mapping) -> set[str]:
    """The path strings the profile configures: the same keys `paths_exist`
    is meant to carry, per Facts. Used to catch a path the profile names
    that the edge never gathered, not just one it gathered and found missing.

    Contract the edge must honour: `paths_exist` is keyed by the exact string
    the profile carries (e.g. a literal `~/foo`), not a normalised or
    expanded form. `cli.py`'s existing `Path(...).expanduser()` convention
    must not be used to build these keys, or an installed machine whose
    profile uses `~` will show every one of its own paths as "not checked"
    here."""
    singles = (parsed.get(k) for k in ("cartridges_dir", "provider_profile", "harness_dir", "workspace_dir"))
    roots = parsed.get("skills_roots") or []
    return {p for p in (*singles, *roots) if p}


def _paths_rows(facts: Mapping, cascade: bool, parsed: dict | None) -> list[dict]:
    """One row per configured path that is missing or never gathered; a
    single ok row naming the count when every configured path is present."""
    if cascade:
        return [_skip("profile paths")]
    paths = facts.get("paths_exist", _MISSING)
    if paths is _MISSING:
        return [_not_checked("profile paths")]
    if not paths:
        return [{"check": "profile paths", "ok": False, "detail": "no paths were configured to check"}]
    missing = sorted(p for p, exists in paths.items() if not exists)
    if missing:
        return [{"check": "profile paths", "ok": False, "detail": f"missing: {p}"} for p in missing]
    if parsed is not None:
        ungathered = sorted(_expected_paths(parsed) - set(paths))
        if ungathered:
            return [{"check": "profile paths", "ok": False, "detail": f"not checked: {p}"} for p in ungathered]
    return [{"check": "profile paths", "ok": True, "detail": f"{len(paths)} paths present"}]


def _flag_row(check: str, facts: Mapping, key: str, cascade: bool, missing_detail: str) -> dict:
    if cascade:
        return _skip(check)
    val = facts.get(key, _MISSING)
    if val is _MISSING:
        return _not_checked(check)
    return {"check": check, "ok": bool(val), "detail": "ok" if val else missing_detail}


def _none_ok_row(check: str, facts: Mapping, key: str, cascade: bool) -> dict:
    if cascade:
        return _skip(check)
    val = facts.get(key, _MISSING)
    if val is _MISSING:
        return _not_checked(check)
    if val is None:
        return {"check": check, "ok": True, "detail": "ok"}
    return {"check": check, "ok": False, "detail": str(val)}


def _overlay_row(facts: Mapping, cascade: bool) -> dict:
    if cascade:
        return _skip("project overlay")
    errors = facts.get("overlay_errors", _MISSING)
    if errors is _MISSING:
        return _not_checked("project overlay")
    if errors is None:
        return {"check": "project overlay", "ok": True, "detail": "no project overlay"}
    if not errors:
        return {"check": "project overlay", "ok": True, "detail": "ok"}
    return {"check": "project overlay", "ok": False, "detail": str(errors[0])}


def _skills_row(facts: Mapping, cascade: bool, parsed: dict | None) -> dict:
    if cascade:
        return _skip("skills")
    counts = facts.get("skill_roots_indexed", _MISSING)
    if counts is _MISSING:
        return _not_checked("skills")
    if not counts:
        return {"check": "skills", "ok": False, "detail": "no skill roots were configured to check"}
    empty = sorted(root for root, n in counts.items() if not n)
    if empty:
        return {"check": "skills", "ok": False, "detail": "0 skills: " + ", ".join(empty)}
    if parsed is not None:
        ungathered = sorted(set(parsed.get("skills_roots") or []) - set(counts))
        if ungathered:
            return {"check": "skills", "ok": False, "detail": "not indexed: " + ", ".join(ungathered)}
    return {"check": "skills", "ok": True, "detail": f"{len(counts)} roots indexed"}


def _provider_row(facts: Mapping, cascade: bool) -> dict:
    if cascade:
        return _skip("provider")
    on_path = facts.get("provider_on_path", _MISSING)
    if on_path is _MISSING:
        return _not_checked("provider")
    # `command` distinguishes three gathered states: an explicit None means
    # the edge tried to read `command:` from the provider profile and could
    # not; `_MISSING` means that fact simply was not gathered, which is not
    # the same claim and must not be reported as "unreadable".
    command = facts.get("provider_command", _MISSING)
    if command is None:
        return {"check": "provider", "ok": False, "detail": "provider command unreadable from provider profile"}
    display_command = command if command is not _MISSING else "<command>"
    if on_path is None:
        return {"check": "provider", "ok": False, "detail": f"provider on PATH unknown: {display_command}"}
    if not on_path:
        return {"check": "provider", "ok": False, "detail": f"not on PATH: {display_command}"}
    version = facts.get("provider_version") or display_command
    return {"check": "provider", "ok": True, "detail": version}


def _workspace_row(facts: Mapping, cascade: bool) -> dict:
    if cascade:
        return _skip("workspace")
    dirs = facts.get("workspace_dirs", _MISSING)
    if dirs is _MISSING:
        return _not_checked("workspace")
    if not dirs:
        return {"check": "workspace", "ok": False, "detail": "no workspace dirs were configured to check"}
    missing = sorted(d for d, exists in dirs.items() if not exists)
    if missing:
        return {"check": "workspace", "ok": False, "detail": "missing: " + ", ".join(missing)}
    return {"check": "workspace", "ok": True, "detail": f"{len(dirs)} dirs present"}


def checks(facts: Mapping) -> list[dict]:
    """Judge a Facts mapping. Returns rows `{"check", "ok", "detail"}` in a
    fixed check order: profile, profile paths, harness venv, core
    importable, cartridge, project overlay, skills, provider, workspace. A fact that was
    never gathered fails as "not checked"; a profile that fails to parse
    fails every row after it as "skipped: no profile"; an empty collection
    where paths, skill roots or workspace dirs belong fails naming that
    nothing was configured to check, rather than passing vacuously."""
    profile_row, cascade, parsed = _profile_row(facts)
    return [
        profile_row,
        *_paths_rows(facts, cascade, parsed),
        _flag_row("harness venv", facts, "harness_python_exists", cascade, "venv missing"),
        _none_ok_row("core importable", facts, "core_import", cascade),
        _none_ok_row("cartridge", facts, "cartridge_load", cascade),
        _overlay_row(facts, cascade),
        _skills_row(facts, cascade, parsed),
        _provider_row(facts, cascade),
        _workspace_row(facts, cascade),
    ]


def exit_code(rows: list[dict]) -> int:
    """0 when every row is ok, else 1."""
    return 0 if all(row["ok"] for row in rows) else 1


def render(rows: list[dict]) -> str:
    """A text table plus a one-line summary of how many rows passed."""
    display = [{"check": r["check"], "ok": "ok" if r["ok"] else "FAIL", "detail": r["detail"]} for r in rows]
    n_ok = sum(1 for r in rows if r["ok"])
    n_fail = len(rows) - n_ok
    table = records.format_table(display, ["check", "ok", "detail"])
    return f"{table}\ndoctor: {n_ok} ok, {n_fail} failing"
