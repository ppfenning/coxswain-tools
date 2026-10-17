import json
import posixpath

import pytest

from agent_tools import home, route
from agent_tools.setup_install import (
    _agent_steps,
    hook_settings,
    install_plan,
    marketplace_steps,
    profile_text,
    render_plan,
    venv_steps,
)

LOC = home.resolve({}, "/home/user", "linux")
INSTALLS = "/home/user/.local/share/coxswain/installs"
CARTRIDGES = f"{INSTALLS}/coxswain-cartridges"
WORKSPACE = "/home/user/.local/share/coxswain/workspaces/main"


def _plan(**overrides):
    kwargs = {
        "root": "/root",
        "team": "acme",
        "workspace": WORKSPACE,
        "provider_profile": "claude",
        "skills_root": f"{CARTRIDGES}/skills-plugins",
        "locations": LOC,
        "uv_on_path": True,
        "python_exists": {"coxswain-cartridges": False, "coxswain-graphs": False, "coxswain-tools": False},
        "claude_on_path": True,
        "profile_exists": False,
        "force_profile": False,
        "plugins": True,
        "hook": True,
        "config_dir": "/config",
        "claude_settings_path": "/home/user/.claude/settings.json",
    }
    kwargs.update(overrides)
    return install_plan(**kwargs)


def _writes(steps):
    """Write steps other than the home record, which every `locations` plan carries."""
    return [s for s in steps if s["op"] == "write" and s["path"] != LOC.record]


def test_install_plan_orders_venv_steps_with_graphs_carrying_the_cartridges_dep():
    steps = _plan()
    runs = [s for s in steps if s["op"] == "run"]
    argvs = [r["argv"] for r in runs]
    assert argvs[0] == ["uv", "venv", "-q"] and runs[0]["cwd"] == CARTRIDGES
    assert argvs[1] == ["uv", "pip", "install", "-q", "-e", ".[dev]"]
    assert runs[1]["cwd"] == CARTRIDGES
    assert argvs[2] == ["uv", "venv", "-q"] and runs[2]["cwd"] == f"{INSTALLS}/coxswain-graphs"
    assert argvs[3] == [
        "uv", "pip", "install", "-q", "-e", ".[dev]", "-e", CARTRIDGES,
    ]
    assert runs[3]["cwd"] == f"{INSTALLS}/coxswain-graphs"
    assert argvs[4] == ["uv", "venv", "-q"] and runs[4]["cwd"] == f"{INSTALLS}/coxswain-tools"
    assert argvs[5] == ["uv", "pip", "install", "-q", "-e", ".[dev]"]
    assert runs[5]["cwd"] == f"{INSTALLS}/coxswain-tools"
    assert argvs[6] == ["uv", "tool", "install", "-q", "-e", f"{INSTALLS}/coxswain-tools"]
    assert argvs[7] == ["uv", "tool", "install", "-q", "-e", CARTRIDGES]
    cartridge_tool_installs = [a for a in argvs if a[-1] == CARTRIDGES
                                and a[:3] == ["uv", "tool", "install"]]
    assert len(cartridge_tool_installs) == 1


def test_venv_steps_tools_without_clear():
    assert venv_steps("coxswain-tools", INSTALLS, CARTRIDGES, False) == [
        {"op": "run", "argv": ["uv", "venv", "-q"], "cwd": f"{INSTALLS}/coxswain-tools",
         "why": "coxswain-tools has no virtualenv yet"},
        {"op": "run", "argv": ["uv", "pip", "install", "-q", "-e", ".[dev]"],
         "cwd": f"{INSTALLS}/coxswain-tools",
         "why": "install coxswain-tools and its dev extras"},
    ]


def test_venv_steps_tools_with_clear():
    assert venv_steps("coxswain-tools", INSTALLS, CARTRIDGES, True) == [
        {"op": "run", "argv": ["uv", "venv", "--clear", "-q"], "cwd": f"{INSTALLS}/coxswain-tools",
         "why": "recreate the coxswain-tools virtualenv"},
        {"op": "run", "argv": ["uv", "pip", "install", "-q", "-e", ".[dev]"],
         "cwd": f"{INSTALLS}/coxswain-tools",
         "why": "install coxswain-tools and its dev extras"},
    ]


def test_venv_steps_graphs_installs_the_cartridges_checkout_editable():
    assert venv_steps("coxswain-graphs", INSTALLS, CARTRIDGES, True) == [
        {"op": "run", "argv": ["uv", "venv", "--clear", "-q"], "cwd": f"{INSTALLS}/coxswain-graphs",
         "why": "recreate the coxswain-graphs virtualenv"},
        {"op": "run", "argv": ["uv", "pip", "install", "-q", "-e", ".[dev]", "-e", CARTRIDGES],
         "cwd": f"{INSTALLS}/coxswain-graphs",
         "why": "install coxswain-graphs and its dev extras"},
    ]


def test_install_plan_venv_steps_are_the_venv_steps_output():
    runs = [s for s in _plan() if s["op"] == "run"]
    assert runs[2:4] == venv_steps("coxswain-graphs", INSTALLS, CARTRIDGES, False)


def test_existing_venvs_skip_uv_venv_but_still_run_pip_install():
    steps = _plan(
        python_exists={"coxswain-cartridges": True, "coxswain-graphs": True, "coxswain-tools": True}
    )
    runs = [s for s in steps if s["op"] == "run"]
    argvs = [r["argv"][:2] for r in runs]
    assert ["uv", "venv"] not in argvs
    pip_installs = [r for r in runs if r["argv"][:3] == ["uv", "pip", "install"]]
    assert len(pip_installs) == 3


def test_no_uv_gives_one_skip_and_no_runs():
    steps = _plan(uv_on_path=False)
    runs = [s for s in steps if s["op"] == "run" and s["argv"][0] == "uv"]
    skips = [s for s in steps if s["op"] == "skip" and s.get("what") == "venvs"]
    assert runs == []
    assert len(skips) == 1
    assert "uv is not on PATH" in skips[0]["why"]


def test_profile_written_when_absent():
    steps = _plan(profile_exists=False, force_profile=False)
    writes = _writes(steps)
    assert len(writes) == 1
    assert writes[0]["path"] == "/home/user/.config/agent-tools/profile.yaml"


def test_profile_skipped_when_present():
    steps = _plan(profile_exists=True, force_profile=False)
    assert not _writes(steps)
    skips = [s for s in steps if s["op"] == "skip" and s.get("what") == "profile"]
    assert len(skips) == 1
    assert "--force-profile" in skips[0]["why"]


def test_profile_written_with_force_even_when_present():
    steps = _plan(profile_exists=True, force_profile=True)
    writes = _writes(steps)
    assert len(writes) == 1


def test_profile_text_round_trips_through_parse_profile():
    text = profile_text(
        team="acme",
        cartridges_dir="/root/coxswain-cartridges",
        skills_root="/root/skills",
        provider_profile="claude",
        harness_dir="/root/coxswain-graphs",
        workspace="/root/workspace",
        assume="b",
    )
    parsed = route.parse_profile(text)
    assert parsed == {
        "team": "acme",
        "cartridges_dir": "/root/coxswain-cartridges",
        "skills_roots": ["/root/skills"],
        "provider_profile": "claude",
        "harness_dir": "/root/coxswain-graphs",
        "workspace_dir": "/root/workspace",
        "assume": "b",
    }


def test_profile_text_writes_the_spend_block_that_parse_profile_reads_back():
    text = profile_text(
        team="acme",
        cartridges_dir="/root/coxswain-cartridges",
        skills_root="/root/skills",
        provider_profile="claude",
        harness_dir="/root/coxswain-graphs",
        workspace="/root/workspace",
        window_ceiling_usd=50,
    )
    parsed = route.parse_profile(text)
    assert parsed["window_ceiling_usd"] == 50.0


def test_install_plan_threads_window_ceiling_usd_into_the_written_profile():
    steps = _plan(window_ceiling_usd=50)
    writes = [s for s in steps if s["op"] == "write" and s["path"].endswith("profile.yaml")]
    assert route.parse_profile(writes[0]["text"])["window_ceiling_usd"] == 50.0


def test_profile_text_rejects_a_value_that_would_not_round_trip():
    with pytest.raises(ValueError):
        profile_text(
            team="acme x #comment",
            cartridges_dir="/c",
            skills_root="/s",
            provider_profile="claude",
            harness_dir="/h",
            workspace="/w",
        )


def test_plugins_off_emits_no_claude_steps():
    steps = _plan(plugins=False)
    assert not [s for s in steps if s["op"] == "run" and s["argv"][0] == "claude"]
    assert not [s for s in steps if s.get("what") == "plugins"]


def test_plugins_on_without_claude_emits_a_skip():
    steps = _plan(plugins=True, claude_on_path=False)
    assert not [s for s in steps if s["op"] == "run" and s["argv"][0] == "claude"]
    skips = [s for s in steps if s["op"] == "skip" and s.get("what") == "plugins"]
    assert len(skips) == 1
    assert "claude is not on PATH" in skips[0]["why"]


def test_hook_settings_adds_once():
    settings, changed = hook_settings({})
    assert changed is True
    entries = settings["hooks"]["SessionStart"]
    assert len(entries) == 1
    assert entries[0]["hooks"][0]["command"] == "agent-tools route context 2>/dev/null || true"


def test_hook_settings_second_call_is_a_noop():
    first, _ = hook_settings({})
    second, changed = hook_settings(first)
    assert changed is False
    assert second == first
    assert len(second["hooks"]["SessionStart"]) == 1


def test_hook_settings_preserves_unrelated_keys():
    existing = {
        "hooks": {"PreToolUse": [{"matcher": "", "hooks": []}]},
        "other": "value",
    }
    settings, changed = hook_settings(existing)
    assert changed is True
    assert settings["other"] == "value"
    assert settings["hooks"]["PreToolUse"] == [{"matcher": "", "hooks": []}]
    assert existing["hooks"].get("SessionStart") is None


def test_render_plan_mentions_every_path():
    steps = _plan()
    rendered = render_plan(steps)
    for step in steps:
        for key in ("path", "cwd"):
            if key in step:
                assert step[key] in rendered
        if step["op"] == "run":
            for token in step["argv"]:
                assert token in rendered


def test_steps_are_json_serialisable():
    for registered in (None, CARTRIDGES, "/elsewhere"):
        steps = _plan(registered_marketplace=registered)
        assert json.loads(json.dumps(steps)) == steps


def test_profile_write_step_carries_derived_paths_under_the_resolved_root():
    steps = _plan()
    writes = [s for s in steps if s["op"] == "write"]
    text = writes[0]["text"]
    assert writes[0]["path"] == LOC.profile
    assert f"cartridges_dir: {WORKSPACE}/cartridges" in text
    assert f"harness_dir: {INSTALLS}/coxswain-graphs" in text
    assert f"skills_roots: [{CARTRIDGES}/skills-plugins]" in text
    assert not any(line == f"harness_dir: {LOC.home}" for line in text.splitlines())
    assert "/root" not in text


def test_profile_cartridges_dir_is_workspace_based_not_the_repo_checkout():
    # The repo checkout under installs is what pip installs edit and the
    # plugin marketplace registers; the profile field the routing layer
    # reads is a workspace path, and the two must not be conflated.
    steps = _plan()
    writes = [s for s in steps if s["op"] == "write"]
    assert f"cartridges_dir: {WORKSPACE}/cartridges" in writes[0]["text"]
    runs = [s for s in steps if s["op"] == "run"]
    graphs_install = [r for r in runs if r.get("cwd") == f"{INSTALLS}/coxswain-graphs" and "pip" in r["argv"]]
    assert graphs_install[0]["argv"][-1] == CARTRIDGES


def test_marketplace_add_location_equals_the_resolved_location_from_the_design():
    steps = _plan()
    adds = [s["argv"] for s in steps if s["op"] == "run" and s["argv"][:4] == ["claude", "plugin", "marketplace", "add"]]
    assert adds == [["claude", "plugin", "marketplace", "add", posixpath.join(LOC.installs, "coxswain-cartridges")]]


def test_marketplace_listed_elsewhere_is_removed_then_added():
    steps = _plan(registered_marketplace="/home/user/projects/coxswain-cartridges")
    market = [s["argv"][3:] for s in steps if s["op"] == "run" and s["argv"][:3] == ["claude", "plugin", "marketplace"]]
    assert market == [["remove", "coxswain-cartridges"], ["add", CARTRIDGES]]


def test_public_marketplace_call_removes_then_adds_when_the_source_differs():
    steps = marketplace_steps(CARTRIDGES, "/home/user/projects/coxswain-cartridges")
    assert [s["argv"] for s in steps] == [
        ["claude", "plugin", "marketplace", "remove", "coxswain-cartridges"],
        ["claude", "plugin", "marketplace", "add", CARTRIDGES],
    ]


def test_public_marketplace_call_only_skips_when_the_source_matches():
    steps = marketplace_steps(CARTRIDGES, CARTRIDGES)
    assert [s["op"] for s in steps] == ["skip"]
    assert steps[0]["what"] == "marketplace"


def test_a_second_run_over_its_own_result_changes_nothing():
    steps = _plan(
        python_exists=dict.fromkeys(("coxswain-cartridges", "coxswain-graphs", "coxswain-tools"), True),
        profile_exists=True,
        registered_marketplace=CARTRIDGES,
    )
    assert not _writes(steps)
    assert not [s for s in steps if s["op"] == "run" and s["argv"][:3] == ["claude", "plugin", "marketplace"]]
    assert [s for s in steps if s.get("what") == "marketplace"]
    first, _ = hook_settings({})
    assert hook_settings(first) == (first, False)


def test_the_only_settings_step_is_the_session_start_hook():
    steps = _plan()
    assert [s["path"] for s in steps if s["op"] == "hook"] == ["/home/user/.claude/settings.json"]


@pytest.mark.parametrize("plugins", [True, False])
@pytest.mark.parametrize("registered", [None, CARTRIDGES, "/elsewhere/coxswain-cartridges"])
def test_no_user_project_folder_appears_for_an_ordinary_home(plugins, registered):
    # A legacy root and config_dir under projects must be ignored entirely.
    steps = _plan(root="/home/user/projects", config_dir="/home/user/projects/cfg",
                  plugins=plugins, registered_marketplace=registered)
    settings, _ = hook_settings({})
    blob = json.dumps(steps) + json.dumps(settings) + render_plan(steps)
    assert "projects" not in blob
    assert "/root" not in blob


def test_tool_install_step_is_warn_only():
    steps = _plan()
    tool_install = [s for s in steps if s["op"] == "run" and s["argv"][:2] == ["uv", "tool"]]
    assert len(tool_install) == 2
    assert all(step["warn_only"] is True for step in tool_install)


def test_assume_flows_into_the_written_profile():
    steps = _plan(assume="r")
    writes = [s for s in steps if s["op"] == "write"]
    assert "assume: r" in writes[0]["text"]


def test_agent_steps_writes_an_enabled_seat():
    steps = _agent_steps({"reviewer": {"enabled": True, "path": "/p", "text": "t"}})
    assert steps == [{"op": "write", "path": "/p", "text": "t", "why": "install the reviewer seat"}]


def test_agent_steps_treats_an_absent_enabled_key_as_enabled():
    steps = _agent_steps({"reviewer": {"path": "/p", "text": "t"}})
    assert steps == [{"op": "write", "path": "/p", "text": "t", "why": "install the reviewer seat"}]


def test_agent_steps_removes_a_disabled_seat():
    steps = _agent_steps({"reviewer": {"enabled": False, "path": "/p", "text": "t"}})
    assert steps == [{"op": "remove", "path": "/p", "why": "reviewer seat is disabled"}]


def test_install_plan_splices_agent_steps_after_the_profile_block():
    steps = _plan(cast={"reviewer": {"enabled": False, "path": "/p", "text": "t"}})
    profile_index = next(i for i, s in enumerate(steps)
                          if s.get("what") == "profile" or s.get("path", "").endswith("profile.yaml"))
    remove_index = next(i for i, s in enumerate(steps) if s["op"] == "remove")
    assert steps[remove_index] == {"op": "remove", "path": "/p", "why": "reviewer seat is disabled"}
    assert remove_index > profile_index


def test_render_plan_prints_a_remove_line():
    assert render_plan([{"op": "remove", "path": "/p", "why": "x"}]) == "remove /p"


def test_render_plan_prints_a_mkdir_line():
    assert render_plan([{"op": "mkdir", "path": "/w", "why": "x"}]) == "mkdir /w"


def test_locations_without_a_workspace_makes_the_folders_and_records_the_home():
    steps = _plan(workspace="")
    main = f"{LOC.workspaces}/main"
    mkdirs = [s["path"] for s in steps if s["op"] == "mkdir"]
    assert mkdirs == [main, *(f"{main}/{n}" for n in ("work", "runs", "intake", "cartridges"))]
    writes = [s for s in steps if s["op"] == "write"]
    assert writes[-1]["path"] == LOC.record
    assert writes[-1]["text"] == f"{LOC.home}\n"
    assert sum(s["path"] == LOC.record for s in writes) == 1


def test_an_existing_workspace_folder_gets_no_mkdir_but_still_the_record():
    steps = _plan(workspace="", workspace_exists=True)
    assert not [s for s in steps if s["op"] == "mkdir"]
    assert [s for s in steps if s["op"] == "write"][-1]["path"] == LOC.record


def test_root_plan_has_neither_the_mkdir_steps_nor_the_record_write():
    steps = _plan(locations=None, workspace="")
    assert not [s for s in steps if s["op"] == "mkdir"]
    assert all(s["path"] != LOC.record for s in steps if s["op"] == "write")
    assert "mkdir" not in render_plan(steps)
