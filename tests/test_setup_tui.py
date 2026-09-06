from functools import reduce

from agent_tools.setup_tui import MENU, handle, initial, render, with_output


def _screen(**overrides):
    return {**initial("/root", "acme", "/root/workspace"), **overrides}


def after(keys: list[str], screen: dict | None = None) -> dict:
    """Fold `keys` through `handle`, returning the final screen. Pure: it
    never assigns `screen` more than once, it builds the fold's result in
    one expression instead."""
    return reduce(lambda s, k: handle(s, k)[0], keys, screen if screen is not None else initial(
        "/root", "acme", "/root/workspace"))


def actions_after(keys: list[str], screen: dict | None = None) -> list[dict]:
    """The actions `handle` returns for the LAST key in `keys`, after every
    earlier key has been folded through to build the screen it sees."""
    start = screen if screen is not None else initial("/root", "acme", "/root/workspace")
    prefix, last = keys[:-1], keys[-1]
    return handle(after(prefix, start), last)[1]


def test_initial_cursor_is_zero_and_menu_is_in_order():
    screen = initial("", "", "")
    assert screen["cursor"] == 0
    assert screen["menu"] == ["doctor", "install (dry run)", "install", "init cartridge", "edit cartridge", "quit"]
    assert screen["menu"] == MENU


def test_down_then_up_clamp_at_the_ends_not_wrap():
    down_keys = ["DOWN"] * (len(MENU) + 3)
    assert after(down_keys, _screen())["cursor"] == len(MENU) - 1
    up_keys = down_keys + ["UP"] * (len(MENU) + 3)
    assert after(up_keys, _screen())["cursor"] == 0


def test_tab_cycles_focus_through_the_three_fields_and_back():
    order = ["field:root", "field:team", "field:workspace", "menu"]
    for i, expected in enumerate(order):
        screen = after(["TAB"] * (i + 1), _screen())
        assert screen["focus"] == expected
    assert actions_after(["TAB"], _screen()) == []


def test_typing_into_a_focused_field_appends_and_backspace_removes():
    focused = _screen(focus="field:team")
    assert after(["x", "9"], focused)["fields"]["team"] == "acmex9"
    assert after(["x", "9", "BACKSPACE"], focused)["fields"]["team"] == "acmex"


def test_q_under_field_focus_types_a_letter_and_does_not_quit():
    screen = after(["q"], _screen(focus="field:team"))
    assert screen["fields"]["team"] == "acmeq"
    assert screen["quit"] is False
    assert actions_after(["q"], _screen(focus="field:team")) == []


def test_toggles_flip_on_p_h_f_and_appear_in_install_argv():
    toggled = after(["p", "h", "f"], _screen())
    assert toggled["toggles"] == {"plugins": True, "hook": True, "force_profile": True}
    assert actions_after(["ENTER"], {**toggled, "cursor": MENU.index("install")}) == [{
        "action": "run",
        "argv": ["agent-tools", "setup", "install",
                 "--root", "/root", "--team", "acme", "--workspace", "/root/workspace",
                 "--plugins", "--hook", "--force-profile"],
    }]


def test_enter_on_doctor_yields_the_doctor_argv_even_with_empty_fields():
    empty = _screen(fields={"root": "", "team": "", "workspace": ""})
    assert actions_after(["ENTER"], empty) == [
        {"action": "run", "argv": ["agent-tools", "setup", "doctor"]}
    ]


def test_enter_on_install_dry_run_carries_dry_run_and_the_on_toggles():
    dry_run = _screen(cursor=MENU.index("install (dry run)"))
    assert actions_after(["p", "ENTER"], dry_run) == [{
        "action": "run",
        "argv": ["agent-tools", "setup", "install",
                 "--root", "/root", "--team", "acme", "--workspace", "/root/workspace",
                 "--dry-run", "--plugins"],
    }]


def test_enter_on_install_with_empty_root_yields_no_action_and_names_root():
    missing_root = _screen(fields={"root": "", "team": "acme", "workspace": "/root/workspace"},
                            cursor=MENU.index("install"))
    assert actions_after(["ENTER"], missing_root) == []
    assert "root" in after(["ENTER"], missing_root)["status"]


def test_doctor_and_install_argv_parse_against_the_real_cli_parser():
    from agent_tools.cli import build_parser

    parser = build_parser()

    doctor_actions = actions_after(["ENTER"], _screen())
    doctor_parsed = parser.parse_args(doctor_actions[0]["argv"][1:])
    assert doctor_parsed.group == "setup"
    assert doctor_parsed.cmd == "doctor"

    install_actions = actions_after(["ENTER"], _screen(cursor=MENU.index("install")))
    install_parsed = parser.parse_args(install_actions[0]["argv"][1:])
    assert install_parsed.group == "setup"
    assert install_parsed.cmd == "install"
    assert install_parsed.root == "/root"
    assert install_parsed.team == "acme"
    assert install_parsed.workspace == "/root/workspace"

    dry_run_actions = actions_after(
        ["p", "h", "f", "ENTER"], _screen(cursor=MENU.index("install (dry run)"))
    )
    dry_run_parsed = parser.parse_args(dry_run_actions[0]["argv"][1:])
    assert dry_run_parsed.cmd == "install"
    assert dry_run_parsed.dry_run is True
    assert dry_run_parsed.plugins is True
    assert dry_run_parsed.hook is True
    assert dry_run_parsed.force_profile is True


def test_enter_on_init_cartridge_with_empty_team_yields_no_action_and_names_team():
    missing_team = _screen(fields={"root": "/root", "team": "", "workspace": "/root/workspace"},
                            cursor=MENU.index("init cartridge"))
    assert actions_after(["ENTER"], missing_team) == []
    assert "team" in after(["ENTER"], missing_team)["status"]


def test_enter_on_init_cartridge_with_fields_set_builds_the_cartridge_argv():
    ready = _screen(cursor=MENU.index("init cartridge"))
    assert actions_after(["ENTER"], ready) == [{
        "action": "run",
        "argv": ["cartridge", "init", "acme", "--cartridges-dir", "/root/workspace/cartridges"],
    }]


def test_enter_on_edit_cartridge_with_fields_set_yields_the_mode_action():
    ready = _screen(cursor=MENU.index("edit cartridge"))
    assert actions_after(["ENTER"], ready) == [{"kind": "mode", "mode": "editor"}]


def test_enter_on_edit_cartridge_with_empty_root_yields_no_action_and_names_root():
    missing_root = _screen(fields={"root": "", "team": "acme", "workspace": "/root/workspace"},
                            cursor=MENU.index("edit cartridge"))
    assert actions_after(["ENTER"], missing_root) == []
    assert "root" in after(["ENTER"], missing_root)["status"]


def test_q_under_menu_focus_sets_quit():
    assert after(["q"], _screen())["quit"] is True
    assert actions_after(["q"], _screen()) == []


def test_enter_on_quit_sets_quit_and_yields_no_action():
    quitting = _screen(cursor=MENU.index("quit"))
    assert after(["ENTER"], quitting)["quit"] is True
    assert actions_after(["ENTER"], quitting) == []


def test_with_output_stores_lines_and_the_exit_status():
    screen = with_output(_screen(), ["line one", "line two"], 0)
    assert screen["output"] == ["line one", "line two"]
    assert screen["status"] == "exit 0"


def test_render_returns_exactly_height_lines_within_width_on_a_normal_terminal():
    lines = render(_screen(), 80, 24)
    assert len(lines) == 24
    assert all(len(line) <= 80 for line in lines)


def test_render_returns_exactly_height_lines_within_width_on_a_tiny_terminal():
    lines = render(_screen(), 20, 8)
    assert len(lines) == 8
    assert all(len(line) <= 20 for line in lines)


def test_render_shows_a_title_bar_and_the_footer_last():
    lines = render(_screen(), 80, 24)
    assert "agent-tools setup" in lines[0]
    assert "q quit" in lines[-1]


def test_render_marks_the_focused_field_and_the_menu_cursor():
    lines = render(_screen(focus="field:team", cursor=2), 80, 24)
    assert any(line.startswith("▸") and "team :" in line for line in lines)
    assert any(line.startswith("▸") and MENU[2] in line and "venvs, profile, plugin, hook" in line
               for line in lines)


def test_render_shows_the_exit_status_in_the_output_header_after_with_output():
    screen = with_output(_screen(), ["line one"], 0)
    lines = render(screen, 80, 24)
    assert any("output" in line and "exit 0" in line for line in lines)


def test_render_on_a_tiny_terminal_still_shows_the_cursor_and_the_footer_last():
    screen_with_cursor_2 = after(["DOWN", "DOWN"], _screen())
    lines = render(screen_with_cursor_2, 20, 8)
    assert len(lines) == 8
    assert any(line.startswith("▸") and MENU[2] in line for line in lines)
    assert "↑↓ move" in lines[-1]
