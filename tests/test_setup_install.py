import json

import pytest

from agent_tools import route
from agent_tools.setup_install import (
    hook_settings,
    install_plan,
    profile_text,
    render_plan,
)


def _plan(**overrides):
    kwargs = dict(
        root="/root",
        team="acme",
        workspace="/root/workspace",
        provider_profile="claude",
        skills_root="/root/skills",
        uv_on_path=True,
        python_exists={"agent-cartridges": False, "agent-graphs": False, "agent-tools": False},
        claude_on_path=True,
        profile_exists=False,
        force_profile=False,
        plugins=True,
        hook=True,
        config_dir="/config",
        claude_settings_path="/home/.claude/settings.json",
    )
    kwargs.update(overrides)
    return install_plan(**kwargs)


def test_install_plan_orders_venv_steps_with_graphs_carrying_the_cartridges_dep():
    steps = _plan()
    runs = [s for s in steps if s["op"] == "run"]
    argvs = [r["argv"] for r in runs]
    assert argvs[0] == ["uv", "venv", "-q"] and runs[0]["cwd"] == "/root/agent-cartridges"
    assert argvs[1] == ["uv", "pip", "install", "-q", "-e", ".[dev]"]
    assert runs[1]["cwd"] == "/root/agent-cartridges"
    assert argvs[2] == ["uv", "venv", "-q"] and runs[2]["cwd"] == "/root/agent-graphs"
    assert argvs[3] == [
        "uv", "pip", "install", "-q", "-e", ".[dev]", "-e", "/root/agent-cartridges",
    ]
    assert runs[3]["cwd"] == "/root/agent-graphs"
    assert argvs[4] == ["uv", "venv", "-q"] and runs[4]["cwd"] == "/root/agent-tools"
    assert argvs[5] == ["uv", "pip", "install", "-q", "-e", ".[dev]"]
    assert runs[5]["cwd"] == "/root/agent-tools"
    assert argvs[6] == ["uv", "tool", "install", "-q", "-e", "/root/agent-tools"]


def test_existing_venvs_skip_uv_venv_but_still_run_pip_install():
    steps = _plan(
        python_exists={"agent-cartridges": True, "agent-graphs": True, "agent-tools": True}
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
    writes = [s for s in steps if s["op"] == "write"]
    assert len(writes) == 1
    assert writes[0]["path"] == "/config/agent-tools/profile.yaml"


def test_profile_skipped_when_present():
    steps = _plan(profile_exists=True, force_profile=False)
    assert not [s for s in steps if s["op"] == "write"]
    skips = [s for s in steps if s["op"] == "skip" and s.get("what") == "profile"]
    assert len(skips) == 1
    assert "--force-profile" in skips[0]["why"]


def test_profile_written_with_force_even_when_present():
    steps = _plan(profile_exists=True, force_profile=True)
    writes = [s for s in steps if s["op"] == "write"]
    assert len(writes) == 1


def test_profile_text_round_trips_through_parse_profile():
    text = profile_text(
        team="acme",
        cartridges_dir="/root/agent-cartridges",
        skills_root="/root/skills",
        provider_profile="claude",
        harness_dir="/root/agent-graphs",
        workspace="/root/workspace",
        assume="b",
    )
    parsed = route.parse_profile(text)
    assert parsed == {
        "team": "acme",
        "cartridges_dir": "/root/agent-cartridges",
        "skills_roots": ["/root/skills"],
        "provider_profile": "claude",
        "harness_dir": "/root/agent-graphs",
        "workspace_dir": "/root/workspace",
        "assume": "b",
    }


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
    steps = _plan()
    json.dumps(steps)


def test_profile_write_step_carries_derived_paths_not_the_bare_root():
    steps = _plan(root="/r", workspace="/w", skills_root="/r/agent-cartridges/skills-plugins")
    writes = [s for s in steps if s["op"] == "write"]
    text = writes[0]["text"]
    assert "cartridges_dir: /r/agent-cartridges" in text
    assert "harness_dir: /r/agent-graphs" in text
    assert "skills_roots: [/r/agent-cartridges/skills-plugins]" in text
    assert "harness_dir: /r\n" not in text
    assert not any(line == "harness_dir: /r" for line in text.splitlines())
