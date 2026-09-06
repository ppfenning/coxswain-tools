"""Pure screen model for `agent-tools setup` with no subcommand: state in,
keys in, lines and actions out. No curses, no subprocess, no filesystem —
the edge (a later phase) draws `render`'s lines with curses, feeds it real
keys, runs the actions `handle` returns, and feeds the result back through
`with_output`.

`Screen` is a plain dict, not a dataclass: `screen["cursor"]`, never
`screen.cursor`. `focus` is the string `"menu"` or `"field:root"` /
`"field:team"` / `"field:workspace"` — never a tuple or an enum, so the
field name a test asserts on is always the literal that appears in the
key it sends. Every branch below returns one dict expression built with
`{**screen, ...}`; nothing here rebinds a local or writes into a dict it
did not just build.
"""

from __future__ import annotations

MENU = ["doctor", "install (dry run)", "install", "init cartridge", "edit cartridge", "quit"]
_FIELD_ORDER = ("root", "team", "workspace")
_FOCUS_ORDER = ("menu", "field:root", "field:team", "field:workspace")
_TOGGLE_KEYS = {"p": "plugins", "h": "hook", "f": "force_profile"}
_DESCRIPTIONS = {
    "doctor": "check this machine, read-only",
    "install (dry run)": "print the plan, change nothing",
    "install": "venvs, profile, plugin, hook",
    "init cartridge": "a team cartridge under the workspace",
    "edit cartridge": "resolved fields, editable by layer",
    "quit": "",
}
_FOOTER = "↑↓ move · Tab fields · Enter run · p/h/f toggles · q quit"


def initial(root: str, team: str, workspace: str) -> dict:
    """A fresh screen: cursor on the first menu item, every toggle off,
    fields prefilled from the profile (or empty strings when there is
    none)."""
    return {
        "menu": list(MENU),
        "cursor": 0,
        "toggles": {"plugins": False, "hook": False, "force_profile": False},
        "fields": {"root": root, "team": team, "workspace": workspace},
        "focus": "menu",
        "output": [],
        "status": "",
        "quit": False,
    }


def _missing_field(item: str, fields: dict) -> str | None:
    """The first empty field `item` needs, or None if it can run. `install`
    (either flavour) needs the whole checkout. `init cartridge` needs a
    team *and* the workspace, not team alone: its argv interpolates
    `workspace` into `--cartridges-dir`, so an empty workspace is just as
    fatal to the command as an empty team."""
    if item in ("install (dry run)", "install"):
        required = _FIELD_ORDER
    elif item == "init cartridge":
        required = ("team", "workspace")
    elif item == "edit cartridge":
        required = _FIELD_ORDER  # own tuple: cartridge_screen.main builds the probe path from root
    else:
        required = ()
    return next((name for name in required if not fields[name]), None)


def _argv_for(item: str, fields: dict, toggles: dict) -> list[str]:
    """Only ever called once `_missing_field` has cleared `item`, so every
    field an argv here reads is known non-empty."""
    root, team, workspace = fields["root"], fields["team"], fields["workspace"]
    if item == "doctor":
        return ["agent-tools", "setup", "doctor"]
    if item in ("install (dry run)", "install"):
        on_flags = [f"--{name.replace('_', '-')}" for name, on in toggles.items() if on]
        dry_run = ["--dry-run"] if item == "install (dry run)" else []
        return (["agent-tools", "setup", "install",
                  "--root", root, "--team", team, "--workspace", workspace]
                + dry_run + on_flags)
    if item == "init cartridge":
        return ["cartridge", "init", team, "--cartridges-dir", f"{workspace}/cartridges"]
    return []


def _activate(item: str, fields: dict, toggles: dict) -> tuple[list[dict], str]:
    """(actions, status) for ENTER on a menu item other than `quit`, which
    the caller handles itself since it changes `quit` rather than yielding
    an action."""
    missing = _missing_field(item, fields)
    if missing is not None:
        return [], f"{missing} is required"
    if item == "edit cartridge":
        return [{"kind": "mode", "mode": "editor"}], ""
    return [{"action": "run", "argv": _argv_for(item, fields, toggles)}], ""


def _menu_key(screen: dict, key: str) -> tuple[dict, list[dict]]:
    menu = screen["menu"]
    if key == "UP":
        return {**screen, "cursor": max(0, screen["cursor"] - 1), "status": ""}, []
    if key == "DOWN":
        return {**screen, "cursor": min(len(menu) - 1, screen["cursor"] + 1), "status": ""}, []
    if key == "ENTER":
        item = menu[screen["cursor"]]
        if item == "quit":
            return {**screen, "quit": True, "status": ""}, []
        actions, status = _activate(item, screen["fields"], screen["toggles"])
        return {**screen, "status": status}, actions
    if key == "q":
        return {**screen, "quit": True}, []
    if key in _TOGGLE_KEYS:
        name = _TOGGLE_KEYS[key]
        toggles = {**screen["toggles"], name: not screen["toggles"][name]}
        return {**screen, "toggles": toggles, "status": ""}, []
    return screen, []  # BACKSPACE and any other key: no-op on the menu.


def _field_key(screen: dict, key: str) -> tuple[dict, list[dict]]:
    name = screen["focus"].split(":", 1)[1]
    field = screen["fields"][name]
    if key == "ENTER":
        return {**screen, "focus": "menu"}, []
    if key == "BACKSPACE":
        return {**screen, "fields": {**screen["fields"], name: field[:-1]}, "status": ""}, []
    if key in ("UP", "DOWN"):
        return screen, []
    if len(key) == 1:
        return {**screen, "fields": {**screen["fields"], name: field + key}, "status": ""}, []
    return screen, []  # any other multi-character key: no-op on a field.


def handle(screen: dict, key: str) -> tuple[dict, list[dict]]:
    """(new_screen, actions). TAB means the same thing regardless of focus,
    so it is resolved before focus is consulted; every other key is routed
    to `_menu_key` or `_field_key` by `screen["focus"]`."""
    if key == "TAB":
        idx = _FOCUS_ORDER.index(screen["focus"]) if screen["focus"] in _FOCUS_ORDER else 0
        focus = _FOCUS_ORDER[(idx + 1) % len(_FOCUS_ORDER)]
        return {**screen, "focus": focus, "status": ""}, []
    if screen["focus"] == "menu":
        return _menu_key(screen, key)
    return _field_key(screen, key)


def with_output(screen: dict, lines: list[str], returncode: int) -> dict:
    """The edge feeds a finished command's output back in."""
    return {**screen, "output": list(lines), "status": f"exit {returncode}"}


def _title_bar(width: int) -> str:
    label = " agent-tools setup "
    if len(label) >= width:
        return label[:width]
    left = (width - len(label)) // 2
    right = width - len(label) - left
    return ("─" * left) + label + ("─" * right)


def _toggle_line(toggles: dict) -> str:
    return "[{}] plugins (p)   [{}] hook (h)   [{}] force-profile (f)".format(
        "x" if toggles["plugins"] else " ",
        "x" if toggles["hook"] else " ",
        "x" if toggles["force_profile"] else " ",
    )


def _field_lines(fields: dict, focused: str) -> list[str]:
    label_width = max(len(name) for name in _FIELD_ORDER)
    return [
        f"{'▸ ' if focused == f'field:{name}' else '  '}"
        f"{name.rjust(label_width)} : {fields[name] or '(empty)'}"
        for name in _FIELD_ORDER
    ]


def _menu_lines(menu: list[str], cursor: int) -> list[str]:
    item_width = max(len(item) for item in menu)
    return [
        f"{'▸ ' if cursor == i else '  '}{item.ljust(item_width)}  {_DESCRIPTIONS[item]}"
        for i, item in enumerate(menu)
    ]


def _blocks(screen: dict, width: int) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """(title, field_lines, toggle_lines, rule, menu_lines): the sections
    `render` lays out above the output area, least-protected first. `menu`
    itself is never dropped."""
    fields, focused = screen["fields"], screen["focus"]
    return (
        [_title_bar(width)],
        _field_lines(fields, focused),
        [_toggle_line(screen["toggles"])],
        ["─" * width],
        _menu_lines(screen["menu"], screen["cursor"]),
    )


def _fit(lines: list[str], n: int) -> list[str]:
    """`lines` padded with blanks or trimmed to exactly `n` items."""
    return (lines + [""] * max(0, n - len(lines)))[:n]


def render(screen: dict, width: int, height: int) -> list[str]:
    """Exactly `height` lines, each at most `width` chars. The footer is
    always last; the menu (all five items, cursor marker included) is
    always kept when `height` allows five plus one lines. Above that
    floor, the title, the field lines, the toggles line and the rule
    above the menu are dropped in that order, then the rule and header
    above the output tail, before the tail itself shrinks. Never raises."""
    if height <= 0:
        return []
    title, field_lines, toggle_lines, rule, menu_lines = _blocks(screen, width)
    content_budget = max(0, height - 1)  # last line is the footer
    menu = menu_lines[:min(len(menu_lines), content_budget)]
    available = max(0, content_budget - len(menu))
    optional = [title, field_lines, toggle_lines, rule]
    while optional and sum(len(group) for group in optional) > available:
        optional.pop(0)
    fixed = [line for group in optional for line in group]
    bottom_budget = max(0, available - len(fixed))
    status = screen["status"]
    header = "output" + (f" · {status}" if status else "")
    bottom_head = (["─" * width, header])[:bottom_budget]
    tail_budget = max(0, bottom_budget - len(bottom_head))
    tail = list(screen["output"])[-tail_budget:] if tail_budget else []
    lines = _fit(fixed + menu + bottom_head + tail, content_budget) + [_FOOTER]
    return [line[:width] for line in lines[:height]]
