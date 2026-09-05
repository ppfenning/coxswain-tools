"""Curses edge over the pure model in `setup_tui.py`: reads keys, draws
`render`'s lines, runs the actions `handle` yields. `curses` is imported
inside each function that needs it, so this module imports on a machine
with no terminal and `key_name` stays testable with plain integer codes.
"""

from __future__ import annotations

import subprocess

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


def run_action(action: dict) -> tuple[list[str], int]:
    """Runs one action `handle` returned and reports its output back."""
    try:
        result = subprocess.run(action["argv"], capture_output=True, text=True)
    except OSError as exc:
        # A missing binary (`cartridge` is installed warn-only) is a line on the
        # screen, never an exception out of the curses loop.
        return [f"{action['argv'][0]}: {exc}"], 127
    return (result.stdout + result.stderr).splitlines(), result.returncode


def loop(stdscr, screen: dict, *, runner=run_action) -> None:
    """The curses read/draw/act cycle over the pure model in `setup_tui`."""
    import curses

    try:
        curses.curs_set(0)
    except curses.error:
        pass  # no real terminal behind stdscr, e.g. under test
    while not screen["quit"]:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        for row, line in enumerate(render(screen, width, height)):
            try:
                stdscr.addnstr(row, 0, line, width)
            except curses.error:
                pass
        stdscr.refresh()
        screen, actions = handle(screen, key_name(stdscr.getch()))
        for action in actions:
            lines, returncode = runner(action)
            screen = with_output(screen, lines, returncode)


def main(root: str, team: str, workspace: str) -> int:
    import curses

    curses.wrapper(lambda stdscr: loop(stdscr, initial(root, team, workspace)))
    return 0
