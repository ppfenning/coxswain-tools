"""Pure core for `agent-tools setup install`: the ordered plan, the
profile text it writes, and the hook entry it merges. No file reads, no
env access, no subprocess calls — every function here takes plain
arguments and returns plain values; the edge (built in a later phase)
executes the steps this module returns.

`route.parse_profile` never unquotes a scalar it reads. Whatever
`profile_text` writes must be readable back bare, verbatim. A value that
itself looks like a flow list/map (`[a]`, `{a: b}`) or that carries an
inline space-then-`#` would round-trip to something other than what was
written, so `profile_text` refuses those with `ValueError`.
"""

from __future__ import annotations

import re

REPOS = ("agent-cartridges", "agent-graphs", "agent-tools")
_HOOK_COMMAND = "agent-tools route context 2>/dev/null || true"


def profile_path(config_dir: str) -> str:
    """Where the profile lives under `config_dir`. The one place this path
    is formatted; the edge asks here rather than restating it."""
    return f"{config_dir}/agent-tools/profile.yaml"


def _unsafe_scalar(value: str) -> bool:
    stripped = value.strip()
    bracketed = (stripped.startswith("[") and stripped.endswith("]")) or (
        stripped.startswith("{") and stripped.endswith("}")
    )
    return bracketed or bool(re.search(r"\s#", value))


def profile_text(
    team: str,
    cartridges_dir: str,
    skills_root: str,
    provider_profile: str,
    harness_dir: str,
    workspace: str,
    assume: str = "a",
) -> str:
    """profile.yaml text that round-trips through `route.parse_profile`.
    Every value is emitted bare; one that parse_profile would read back
    as something else raises ValueError instead of being written."""
    fields = {
        "team": team,
        "cartridges_dir": cartridges_dir,
        "skills_root": skills_root,
        "provider_profile": provider_profile,
        "harness_dir": harness_dir,
        "workspace_dir": workspace,
        "assume": assume,
    }
    unsafe = [key for key, value in fields.items() if _unsafe_scalar(value)]
    if unsafe:
        raise ValueError(f"cannot round-trip through parse_profile: {unsafe}")
    lines = [
        "# Written by agent-tools setup install — rerun with --force-profile to rewrite",
        f"team: {team}",
        f"cartridges_dir: {cartridges_dir}",
        f"skills_roots: [{skills_root}]",
        f"provider_profile: {provider_profile}",
        f"harness_dir: {harness_dir}",
        f"workspace_dir: {workspace}",
        f"assume: {assume}",
    ]
    return "\n".join(lines) + "\n"


def _repo_steps(repo: str, root: str, python_exists: dict, cartridges_dir: str) -> list:
    cwd = f"{root}/{repo}"
    steps = []
    if not python_exists.get(repo, False):
        steps.append({"op": "run", "argv": ["uv", "venv", "-q"], "cwd": cwd,
                      "why": f"{repo} has no virtualenv yet"})
    install_argv = ["uv", "pip", "install", "-q", "-e", ".[dev]"]
    if repo == "agent-graphs":
        install_argv = install_argv + ["-e", cartridges_dir]
    steps.append({"op": "run", "argv": install_argv, "cwd": cwd,
                  "why": f"install {repo} and its dev extras"})
    return steps


def install_plan(
    *,
    root: str,
    team: str,
    workspace: str,
    provider_profile: str,
    skills_root: str,
    uv_on_path: bool,
    python_exists: dict,
    claude_on_path: bool,
    profile_exists: bool,
    force_profile: bool,
    plugins: bool,
    hook: bool,
    config_dir: str,
    claude_settings_path: str,
    assume: str = "a",
) -> list:
    """Ordered setup steps for a checkout at `root` holding
    agent-cartridges, agent-graphs and agent-tools side by side.
    `config_dir` and `claude_settings_path` are pre-resolved by the
    caller; this core never expands `~` or touches the filesystem.
    `cartridges_dir` here is the checkout `-e` installs and the plugin
    marketplace registers; the profile's own `cartridges_dir` field is a
    workspace path (`profile_cartridges_dir`), a different thing that
    happens to share a name in the field the routing layer reads."""
    cartridges_dir = f"{root}/agent-cartridges"
    harness_dir = f"{root}/agent-graphs"
    profile_cartridges_dir = f"{workspace}/cartridges"
    steps: list = []

    if not uv_on_path:
        steps.append({"op": "skip", "what": "venvs",
                      "why": "uv is not on PATH; install it from https://astral.sh/uv"})
    else:
        for repo in REPOS:
            steps.extend(_repo_steps(repo, root, python_exists, cartridges_dir))
        steps.append({"op": "run",
                      "argv": ["uv", "tool", "install", "-q", "-e", f"{root}/agent-tools"],
                      "why": "put agent-tools on PATH", "warn_only": True})

    if not profile_exists or force_profile:
        text = profile_text(team=team, cartridges_dir=profile_cartridges_dir,
                             skills_root=skills_root, provider_profile=provider_profile,
                             harness_dir=harness_dir, workspace=workspace, assume=assume)
        steps.append({"op": "write", "path": profile_path(config_dir), "text": text,
                      "why": "write the routing layer's profile"})
    else:
        steps.append({"op": "skip", "what": "profile",
                      "why": "exists; pass --force-profile to rewrite"})

    if plugins:
        if claude_on_path:
            steps.append({"op": "run",
                          "argv": ["claude", "plugin", "marketplace", "add", cartridges_dir],
                          "why": "register agent-cartridges as a plugin marketplace"})
            steps.append({"op": "run",
                          "argv": ["claude", "plugin", "install", "local-skills@agent-cartridges"],
                          "why": "install the local-skills plugin"})
        else:
            steps.append({"op": "skip", "what": "plugins",
                          "why": "claude is not on PATH; this step is provider-specific"})

    if hook:
        steps.append({"op": "hook", "path": claude_settings_path,
                      "why": "add a SessionStart hook that prints route context"})

    steps.append({"op": "print", "text": "verify with: agent-tools setup doctor"})
    return steps


def hook_settings(existing: dict) -> tuple:
    """(new_settings, changed): `existing` with a SessionStart hook entry
    running the route-context command appended under hooks.SessionStart,
    unless an entry with that exact command is already present. Never
    mutates `existing`."""
    entry = {"matcher": "", "hooks": [{"type": "command", "command": _HOOK_COMMAND}]}
    hooks = dict(existing.get("hooks", {}))
    session_start = list(hooks.get("SessionStart", []))
    for item in session_start:
        for sub in item.get("hooks", []):
            if sub.get("command") == _HOOK_COMMAND:
                return existing, False
    new_settings = dict(existing)
    hooks["SessionStart"] = session_start + [entry]
    new_settings["hooks"] = hooks
    return new_settings, True


def render_plan(steps: list) -> str:
    """One line per step, for `--dry-run`."""
    lines = []
    for step in steps:
        op = step["op"]
        if op == "run":
            cwd = f" ({step['cwd']})" if "cwd" in step else ""
            lines.append(f"run{cwd} {' '.join(step['argv'])}")
        elif op == "write":
            lines.append(f"write {step['path']}")
        elif op == "skip":
            lines.append(f"skip {step['what']} — {step['why']}")
        elif op == "hook":
            lines.append(f"hook {step['path']}")
        elif op == "print":
            lines.append(f"print {step['text']}")
        else:
            lines.append(f"{op} {step}")
    return "\n".join(lines)
