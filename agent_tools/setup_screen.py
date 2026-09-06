"""Curses edge over the pure model in `setup_tui.py`: reads keys, draws
`render`'s lines, runs the actions `handle` yields. `curses` is imported
inside each function that needs it, so this module imports on a machine
with no terminal and `key_name` stays testable with plain integer codes.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from functools import partial
from pathlib import Path

from agent_tools.setup_tui import handle, initial, render, with_output

_PRINTABLE = range(32, 127)


def key_name(ch: int) -> str:
    """A curses key code translated to the model's key strings. Anything
    the model does not recognise maps to "", which `handle` no-ops on."""
    import curses

    if ch in (10, 13):
        return "ENTER"
    if ch == 9:
        return "TAB"
    if ch in (curses.KEY_BACKSPACE, 127, 8):
        return "BACKSPACE"
    if ch == curses.KEY_UP:
        return "UP"
    if ch == curses.KEY_DOWN:
        return "DOWN"
    if ch in _PRINTABLE:
        return chr(ch)
    return ""


def resolved_argv(argv: list[str], *, cartridge_on_path: bool, venv_cartridge_exists: bool,
                   venv_cartridge: str) -> list[str]:
    """The argv `run_action` should actually execute: `cartridge` on PATH
    wins outright; a `cartridge` missing from PATH falls back to the venv
    binary only if it exists; anything else passes through unchanged."""
    if argv[0] == "cartridge" and not cartridge_on_path and venv_cartridge_exists:
        return [venv_cartridge, *argv[1:]]
    return list(argv)


def run_action(action: dict, *, root: str = "") -> tuple[list[str], int]:
    """Runs one action `handle` returned and reports its output back.
    `cartridge` is a console script that lives only in agent-cartridges'
    own venv; if PATH does not have it, fall back to the venv binary
    under `root` rather than let a missing-PATH entry read as a missing
    folder."""
    venv_cartridge = f"{root}/agent-cartridges/.venv/bin/cartridge"
    argv = resolved_argv(action["argv"], cartridge_on_path=shutil.which("cartridge") is not None,
                          venv_cartridge_exists=Path(venv_cartridge).exists(),
                          venv_cartridge=venv_cartridge)
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        # A missing binary (`cartridge` is installed warn-only) is a line on the
        # screen, never an exception out of the curses loop.
        return [f"{argv[0]}: {exc}"], 127
    return (result.stdout + result.stderr).splitlines(), result.returncode


def loop(stdscr, screen: dict, *, runner=run_action) -> tuple[dict, dict | None]:
    """The curses read/draw/act cycle over `setup_tui`. Returns `(screen,
    action)`: a `{"kind": "mode", ...}` action comes back unrun, since
    entering another screen is `main`'s job; `action` is `None` once
    `quit` is set."""
    import curses

    with contextlib.suppress(curses.error):  # no real terminal behind stdscr, e.g. under test
        curses.curs_set(0)
    while not screen["quit"]:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        for row, line in enumerate(render(screen, width, height)):
            with contextlib.suppress(curses.error):
                stdscr.addnstr(row, 0, line, width)
        stdscr.refresh()
        screen, actions = handle(screen, key_name(stdscr.getch()))
        for action in actions:
            if action.get("kind") == "mode":
                return screen, action
            lines, returncode = runner(action, root=screen["fields"]["root"])
            screen = with_output(screen, lines, returncode)
    return screen, None


def main(root: str, team: str, workspace: str) -> int:
    import curses

    from agent_tools import cartridge_screen

    screen = initial(root, team, workspace)
    while True:
        result = curses.wrapper(partial(loop, screen=screen))
        if not result:
            return 0
        screen, action = result
        if action is None:
            return 0
        cartridge_screen.main(screen["fields"])
        screen = {**screen, "quit": False}
