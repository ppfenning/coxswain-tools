"""The effect runner and curses edge for the cartridge editor: the only
place that touches a subprocess, writes a fragment, rewrites the profile
file, or reads/draws curses. `editor_model.py` decides which `Effect` to
run and what a key or a result means; this module only runs the effect it
is handed and draws the frame it is given.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from pathlib import Path

from agent_tools.editor_model import Effect, State, fold_effects, frame, roster_from_probe, rows, step
from agent_tools.fragments import FragmentError, write_fragment
from agent_tools.route import parse_profile
from agent_tools.setup_screen import key_name, resolved_argv


def _present(path: str) -> bool:
    """Whether a binary is there. A path the process may not read — `/root` on a CI
    runner — is "not there", never an exception: the same rule `setup_screen.run_action`
    keeps, that a missing binary is a value on the screen."""
    try:
        return Path(path).exists()
    except OSError:
        return False


def _write_fragment(effect: Effect, ctx: dict, write) -> dict:
    team_dir = Path(ctx["cartridges_dir"]) / ctx["team"]
    try:
        write(team_dir, effect.payload["edits"])
    except (FragmentError, OSError) as exc:
        return {"provenance_error": str(exc)}
    return {}


def _launch(argv: list[str], run):
    """Runs `argv`; a binary that cannot launch is a value, never an exception
    out of the loop (`setup_screen.run_action` makes the same promise)."""
    try:
        return run(argv, capture_output=True, text=True), None
    except OSError as exc:
        return None, f"{argv[0]}: {exc}"


def _run_probe(ctx: dict, run) -> dict:
    result, failure = _launch(list(ctx["probe_argv"]), run)
    if failure is not None:
        return {"provenance_error": failure}
    if result.returncode != 0:
        return {"provenance_error": result.stderr or f"probe exited {result.returncode}"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"provenance_error": str(exc)}


def _init_cartridge(effect: Effect, ctx: dict, run) -> dict:
    name = effect.payload["name"]
    venv_cartridge = f"{ctx.get('root', '')}/agent-cartridges/.venv/bin/cartridge"
    argv = resolved_argv(["cartridge", "init", name, "--cartridges-dir", str(ctx["cartridges_dir"]),
                          "--extends", effect.payload["extends"]],
                         cartridge_on_path=shutil.which("cartridge") is not None,
                         venv_cartridge_exists=_present(venv_cartridge), venv_cartridge=venv_cartridge)
    result, failure = _launch(argv, run)
    if failure is not None:
        return {"returncode": 127, "team": name, "output": failure}
    output = "\n".join(((result.stdout or "") + (result.stderr or "")).splitlines()[-5:])
    return {"returncode": result.returncode, "team": name, "output": output}


def _set_profile_team(effect: Effect, ctx: dict) -> dict:
    team = effect.payload["team"]
    path = Path(ctx["profile_path"])
    lines = path.read_text().splitlines() if path.exists() else []
    rewritten = [f"team: {team}" if line.startswith("team:") else line for line in lines]
    if not any(line.startswith("team:") for line in lines):
        rewritten.append(f"team: {team}")
    path.write_text("\n".join(rewritten) + "\n")
    return {"returncode": 0, "team": team}


def run_effect(effect: Effect, ctx: dict, *, run=subprocess.run, write=write_fragment) -> dict:
    """Runs one `Effect`, chosen by the caller; every branch returns."""
    if effect.kind == "write_fragment":
        return _write_fragment(effect, ctx, write)
    if effect.kind == "run_probe":
        return _run_probe(ctx, run)
    if effect.kind == "init_cartridge":
        return _init_cartridge(effect, ctx, run)
    if effect.kind == "set_profile_team":
        return _set_profile_team(effect, ctx)
    return {"provenance_error": f"unknown effect kind {effect.kind!r}"}


def _expanded(value, home: str):
    if isinstance(value, str) and (value == "~" or value.startswith("~/")):
        return home + value[1:]
    if isinstance(value, list):
        return [_expanded(item, home) for item in value]
    return value


def profile_fields(text: str, home: str) -> dict:
    """Profile fields, `~` expanded against `home` — never `$HOME`/`expanduser()`."""
    return {key: _expanded(value, home) for key, value in parse_profile(text).items()}


def _should_quit(state: State, key: str) -> bool:
    """The curses session's own exit gesture, not the model's: a bare `q`
    while no row is being edited. `step` already treats that `q` as a
    no-op it never calls "unknown" (`_HANDLERS["q"] = replace`), leaving
    the session's lifecycle to this caller, never to a fact `step` holds."""
    return key == "q" and state.editing is None


def loop(stdscr, state: State, ctx: dict, *, runner=run_effect) -> None:
    """Reads a key, steps the model, runs any effects it asked for, and
    draws the frame it returns. `_should_quit` is the only decision here."""
    import curses

    with contextlib.suppress(curses.error):  # no real terminal behind stdscr, e.g. under test
        curses.curs_set(0)
    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        for row, line in enumerate(frame(state, width)[:height]):
            with contextlib.suppress(curses.error):
                stdscr.addnstr(row, 0, line, width)
        stdscr.refresh()
        ch = stdscr.getch()
        if ch == curses.KEY_RESIZE:
            continue
        key = key_name(ch)
        if _should_quit(state, key):
            return
        state = step(state, key)
        if state.effects:
            results = [(effect, runner(effect, ctx)) for effect in state.effects]
            state = fold_effects(state, results)


def main(fields: dict, *, probe=None) -> int:
    """Builds the initial rows from a first probe (`cli._run_core_probe` by
    default) and enters the curses loop; its `resolved`/`provenance` keys
    go straight to `editor_model.rows`, unreshaped."""
    import curses

    if probe is None:
        from agent_tools.cli import _run_core_probe
        probe = _run_core_probe
    root, team, workspace = fields.get("root", ""), fields.get("team", ""), fields.get("workspace", "")
    cartridges_dir = f"{workspace}/cartridges"
    python_path = f"{root}/agent-graphs/.venv/bin/python"
    facts = probe(python_path, cartridges_dir, team, [])
    state = State(rows=tuple(rows(facts, team)), roster=roster_from_probe(facts), cursor=0, pending={},
                  message="", team=team)
    venv_cartridge = f"{root}/agent-cartridges/.venv/bin/cartridge"
    ctx = {"cartridges_dir": cartridges_dir, "team": team, "root": root,
           "profile_path": fields.get("profile_path", ""),
           # same on-path/venv-fallback convention `_init_cartridge` uses for `cartridge`.
           "probe_argv": resolved_argv(["cartridge", "probe", "--cartridges-dir", cartridges_dir, "--team", team],
                                        cartridge_on_path=shutil.which("cartridge") is not None,
                                        venv_cartridge_exists=_present(venv_cartridge),
                                        venv_cartridge=venv_cartridge)}
    curses.wrapper(lambda stdscr: loop(stdscr, state, ctx))
    return 0
