import json
import os
import stat

import pytest

from agent_tools import route
from agent_tools.cli import build_parser


def _write_executable(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(argv):
    args = build_parser().parse_args(argv)
    return args.fn(args)


@pytest.fixture
def install_env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    for repo in ("agent-cartridges", "agent-graphs", "agent-tools"):
        (root / repo).mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log.txt"
    _write_executable(bin_dir / "uv", f'#!/bin/sh\necho "$@" >> {log}\nexit 0\n')
    _write_executable(bin_dir / "claude", f'#!/bin/sh\necho "$@" >> {log}\nexit 0\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return {"root": root, "workspace": workspace, "home": home, "log": log, "bin": bin_dir}


def _base_argv(env, *extra):
    return ["setup", "install", "--root", str(env["root"]), "--team", "acme",
            "--workspace", str(env["workspace"]), *extra]


def test_setup_install_help_lists_every_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["setup", "install", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--root", "--team", "--workspace", "--provider-profile", "--skills-root",
                 "--assume", "--plugins", "--hook", "--force-profile", "--dry-run"):
        assert flag in out


def test_dry_run_executes_nothing_and_prints_every_step(install_env, capsys):
    profile_path = install_env["home"] / ".config" / "agent-tools" / "profile.yaml"
    rc = _run(_base_argv(install_env, "--dry-run"))
    assert rc == 0
    assert not install_env["log"].exists()
    assert not profile_path.exists()
    out = capsys.readouterr().out
    assert "run" in out and "uv venv" in out
    assert f"write {profile_path}" in out
    assert "verify with: agent-tools setup doctor" in out


def test_full_install_calls_uv_in_order_and_writes_profile(install_env):
    rc = _run(_base_argv(install_env, "--assume", "r"))
    assert rc == 0
    lines = install_env["log"].read_text(encoding="utf-8").splitlines()
    assert lines[0] == "venv -q"
    assert lines[1] == "pip install -q -e .[dev]"
    assert lines[6] == f"tool install -q -e {install_env['root']}/agent-tools"
    profile_path = install_env["home"] / ".config" / "agent-tools" / "profile.yaml"
    profile = route.parse_profile(profile_path.read_text(encoding="utf-8"))
    assert profile["team"] == "acme"
    assert profile["assume"] == "r"
    assert profile["cartridges_dir"] == f"{install_env['workspace']}/cartridges"
    assert profile["harness_dir"] == f"{install_env['root']}/agent-graphs"


def test_second_run_without_force_profile_skips_the_write(install_env, capsys):
    _run(_base_argv(install_env))
    capsys.readouterr()
    rc = _run(_base_argv(install_env))
    assert rc == 0
    out = capsys.readouterr().out
    assert "skip profile" in out
    assert "--force-profile" in out


def test_hook_adds_entry_once_and_rerun_is_unchanged(install_env, capsys):
    argv = _base_argv(install_env, "--hook")
    _run(argv)
    first_out = capsys.readouterr().out
    settings_path = install_env["home"] / ".claude" / "settings.json"
    first = settings_path.read_text(encoding="utf-8")
    _run(argv)
    second_out = capsys.readouterr().out
    second = settings_path.read_text(encoding="utf-8")
    assert second == first
    assert "hook added to" in first_out
    assert "hook already present in" in second_out
    settings = json.loads(first)
    assert len(settings["hooks"]["SessionStart"]) == 1


def test_plugins_calls_fake_claude_twice(install_env):
    _run(_base_argv(install_env, "--plugins"))
    lines = install_env["log"].read_text(encoding="utf-8").splitlines()
    plugin_calls = [line for line in lines if line.startswith("plugin")]
    assert len(plugin_calls) == 2


def test_failing_uv_stops_at_first_failure_and_profile_not_written(install_env):
    # Fails from the 3rd logged call onward (the 2nd repo's `uv venv`), so a run
    # that kept going after the first failure would leave more than 3 lines.
    _write_executable(install_env["bin"] / "uv", f'''#!/bin/sh
echo "$@" >> {install_env["log"]}
n=$(wc -l < {install_env["log"]})
if [ "$n" -ge 3 ]; then echo boom 1>&2; exit 1; fi
exit 0
''')
    rc = _run(_base_argv(install_env))
    assert rc == 1
    lines = install_env["log"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert not (install_env["home"] / ".config" / "agent-tools" / "profile.yaml").exists()


def test_uv_tool_install_failure_is_a_warning_not_a_failure(install_env, capsys):
    _write_executable(install_env["bin"] / "uv", f'''#!/bin/sh
echo "$@" >> {install_env["log"]}
if [ "$1" = "tool" ]; then exit 1; fi
exit 0
''')
    rc = _run(_base_argv(install_env))
    assert rc == 0
    out = capsys.readouterr().out
    warn_lines = [line for line in out.splitlines() if line.startswith("warn:")]
    assert len(warn_lines) == 2
    assert any("agent-tools" in line for line in warn_lines)
    assert any("agent-cartridges" in line for line in warn_lines)
    profile_path = install_env["home"] / ".config" / "agent-tools" / "profile.yaml"
    assert profile_path.exists()


def test_an_unsafe_team_value_is_refused_not_a_traceback(install_env, capsys):
    argv = ["setup", "install", "--root", str(install_env["root"]), "--team", "acme #comment",
            "--workspace", str(install_env["workspace"])]
    rc = _run(argv)
    assert rc == 2
    out = capsys.readouterr().out
    assert "refusing" in out
    assert not install_env["log"].exists()


def test_a_missing_repo_directory_fails_gracefully_not_a_traceback(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    # agent-cartridges is deliberately never checked out here.
    for repo in ("agent-graphs", "agent-tools"):
        (root / repo).mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "uv", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    rc = _run(["setup", "install", "--root", str(root), "--team", "acme", "--workspace", str(workspace)])
    assert rc == 1
    assert not (home / ".config" / "agent-tools" / "profile.yaml").exists()


def test_a_settings_file_with_invalid_json_fails_gracefully(install_env, capsys):
    settings_path = install_env["home"] / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not valid json", encoding="utf-8")
    rc = _run(_base_argv(install_env, "--hook"))
    assert rc == 1
    out = capsys.readouterr().out
    assert "not valid JSON" in out
    assert settings_path.read_text(encoding="utf-8") == "{not valid json"
