import argparse
import os
import subprocess
from pathlib import Path

import pytest

from agent_tools import cli
from agent_tools.setup_migrate_hosts import ObservedHost

ORIGINAL_OBSERVE_HOSTS = cli._migrate_observe_hosts
NAMES = ("coxswain-cartridges", "coxswain-graphs", "coxswain-tools")


def git(*argv, cwd):
    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """An old layout: three git checkouts under repos/, relative cartridge links, and a profile naming them."""
    user = tmp_path / "user"
    repos, cartridges = user / "repos", user / "ws" / "cartridges"
    for name in NAMES:
        (repos / name).mkdir(parents=True)
        git("init", "-q", cwd=repos / name)
    cartridges.mkdir(parents=True)
    for link in ("base", "local"):
        (repos / "coxswain-cartridges" / "cartridges" / link).mkdir(parents=True)
        (cartridges / link).symlink_to(f"../../repos/coxswain-cartridges/cartridges/{link}")
    profile = user / ".config" / "agent-tools" / "profile.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        "team: demo\n"
        f"cartridges_dir: {cartridges}\n"
        f"skills_roots: [{repos}/coxswain-cartridges/skills]\n"
        f"provider_profile: {repos}/coxswain-cartridges/provider.yaml\n"
        f"harness_dir: {repos}/coxswain-graphs\n"
        f"workspace_dir: {user}/ws\n"
    )
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("COXSWAIN_HOME", str(tmp_path / "coxhome"))
    for var in ("AGENT_TOOLS_PROFILE", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: [])
    return types_ns(user=user, repos=repos, cartridges=cartridges, profile=profile, installs=tmp_path / "coxhome" / "installs")


def types_ns(**fields):
    return argparse.Namespace(**fields)


def run(dry_run=False, apply=False, hosts=False):
    return cli._setup_migrate(argparse.Namespace(home=None, dry_run=dry_run, apply=apply, hosts=hosts))


def tree(root: Path):
    return sorted((str(p), os.readlink(p) if p.is_symlink() else p.is_dir()) for p in root.rglob("*"))


CLOSING = "no changes made; run with --apply to migrate"


def test_bare_command_changes_nothing_and_prints_the_closing_line(layout, capsys):
    before = tree(layout.user)
    assert run() == 0
    out = capsys.readouterr().out
    assert "1. move" in out and "rewrite_profile_key" in out
    assert out.rstrip().endswith(CLOSING)
    assert tree(layout.user) == before and not layout.installs.exists()


def test_dry_run_behaves_like_the_bare_command(layout, capsys):
    before = tree(layout.user)
    assert run() == 0
    bare = capsys.readouterr().out
    assert run(dry_run=True) == 0
    assert capsys.readouterr().out == bare
    assert tree(layout.user) == before and not layout.installs.exists()


def test_apply_moves_the_layout_and_leaves_nothing_dangling(layout, capsys):
    assert run(apply=True) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("verify with: cox setup doctor")
    for name in NAMES:
        assert (layout.installs / name).is_dir() and (layout.repos / name).is_symlink()
    for link in ("base", "local"):
        assert (layout.cartridges / link).resolve() == (
            layout.installs / "coxswain-cartridges" / "cartridges" / link
        ).resolve()
    for path in layout.user.rglob("*"):
        assert path.exists(), f"dangling: {path}"
    text = layout.profile.read_text()
    assert f"harness_dir: {layout.installs}/coxswain-graphs" in text
    assert str(layout.repos) not in text
    assert (layout.user / ".config" / "agent-tools" / "home").read_text().strip() == str(layout.installs.parent)


def test_second_apply_has_nothing_to_do(layout, capsys):
    assert run(apply=True) == 0
    capsys.readouterr()
    after = tree(layout.user)
    assert run(apply=True) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert tree(layout.user) == after


def test_conflicting_destination_stops_nonzero_and_keeps_the_source(layout, capsys):
    (layout.installs / "coxswain-graphs").mkdir(parents=True)
    assert run(apply=True) == 1
    out = capsys.readouterr().out
    assert "FAILED step 1 refuse" in out and "coxswain-graphs" in out
    assert (layout.repos / "coxswain-graphs" / ".git").is_dir()
    assert not (layout.repos / "coxswain-graphs").is_symlink()


def test_move_onto_an_existing_destination_fails_without_touching_the_source(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "keep").mkdir(parents=True)
    dst.mkdir()
    reason = cli._migrate_exec({"op": "move", "src": str(src), "dst": str(dst), "why": "x"}, None)
    assert reason is not None and "already exists" in reason
    assert (src / "keep").is_dir()


def test_each_host_is_reported_and_one_failure_does_not_hide_the_others(layout, monkeypatch, capsys):
    hosts = [ObservedHost(n, f"ssh-{n}", "/w", {"coxswain-tools": "/old/coxswain-tools"}, "/w", "beat") for n in ("a", "b", "c")]
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: hosts)
    called = []

    def fake(step, locs):
        called.append((step.host, step.argv))
        return (False, "connection refused") if step.host == "b" else (True, "migrated")

    monkeypatch.setattr(cli, "_migrate_run_host_step", fake)
    assert run(apply=True, hosts=True) == 1
    out = capsys.readouterr().out
    assert "host a: remote_migrate: migrated" in out and "host c: remote_migrate: migrated" in out
    assert "host b: FAILED step 1 remote_migrate: connection refused" in out
    assert [h for h, _ in called] == ["a", "b", "c"]
    assert out.rstrip().endswith("verify with: cox setup doctor")


def test_a_failed_local_step_runs_no_host_step(layout, monkeypatch):
    (layout.installs / "coxswain-graphs").mkdir(parents=True)
    hosts = [ObservedHost("a", "ssh-a", "/w", {"coxswain-tools": "/old/coxswain-tools"}, "/w", "beat")]
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: hosts)
    monkeypatch.setattr(cli, "_migrate_run_host_step", lambda step, locs: pytest.fail("host step ran"))
    assert run(apply=True, hosts=True) == 1


def test_apply_alone_runs_no_host_step_and_says_so(layout, monkeypatch, capsys):
    hosts = [ObservedHost("a", "ssh-a", "/w", {"coxswain-tools": "/old/coxswain-tools"}, "/w", "beat")]
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: hosts)
    monkeypatch.setattr(cli, "_migrate_run_host_step", lambda step, locs: pytest.fail("host step ran"))
    assert run(apply=True) == 0
    out = capsys.readouterr().out
    assert "hosts: not touched; rerun with --apply --hosts on the chair's machine" in out
    assert "host a:" not in out


def test_a_host_the_planner_cannot_judge_is_not_reported_as_migrated(layout, monkeypatch, capsys):
    hosts = [ObservedHost("d", "ssh-d", "", {}, "/w", "fallback")]
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: hosts)
    assert run(apply=True, hosts=True) == 0
    out = capsys.readouterr().out
    assert "host d: print: d has no beat row, so its paths cannot be judged until it beats." in out
    assert "host d: ok" not in out


def test_remote_migrate_runs_the_planned_argv_and_reports_each_host(layout, monkeypatch, capsys):
    real = subprocess.run
    seen = []

    def fake(argv, **kw):
        if argv[0] != "ssh":
            return real(argv, **kw)
        seen.append(argv)
        bad = argv[1] == "ssh-y"
        return subprocess.CompletedProcess(argv, 255 if bad else 0, "", "no route" if bad else "")

    hosts = [ObservedHost(n, f"ssh-{n}", "/w", {"coxswain-tools": "/old/coxswain-tools"}, "/w", "beat") for n in ("x", "y")]
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: hosts)
    monkeypatch.setattr(cli.subprocess, "run", fake)
    assert run(apply=True, hosts=True) == 1
    out = capsys.readouterr().out
    assert seen[0] == ["ssh", "ssh-x", "cox", "setup", "migrate", "--apply"] and len(seen) == 2
    assert "host x: remote_migrate: ran ssh ssh-x cox setup migrate --apply" in out
    assert "host y: FAILED step 1 remote_migrate: ssh ssh-y cox setup migrate --apply exited 255: no route" in out


def test_apply_with_hosts_builds_the_remote_argv_without_hosts(layout, monkeypatch, capsys):
    real = subprocess.run
    seen = []

    def fake(argv, **kw):
        if argv[0] != "ssh":
            return real(argv, **kw)
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    hosts = [ObservedHost("x", "ssh-x", "/w", {"coxswain-tools": "/old/coxswain-tools"}, "/w", "beat")]
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: hosts)
    monkeypatch.setattr(cli.subprocess, "run", fake)
    assert run(apply=True, hosts=True) == 0
    assert seen == [["ssh", "ssh-x", "cox", "setup", "migrate", "--apply"]]
    assert "hosts: not touched" not in capsys.readouterr().out


LANES = "lane_hosts:\n  - name: h\n    ssh: ssh-h\n    workspace_dir: /old/ws\n  - name: k\n    ssh: ssh-k\n    workspace_dir: /k/ws\n"


def test_lane_host_workspace_is_repointed_through_the_real_runner(layout, monkeypatch):
    layout.profile.write_text(layout.profile.read_text() + LANES)
    moved = {"coxswain-tools": "/new/installs/coxswain-tools"}
    hosts = [ObservedHost("h", "ssh-h", "/new/ws", moved, "/old/ws", "beat")]
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: hosts)
    assert run(apply=True, hosts=True) == 0
    parsed = cli._profile_lane_hosts(layout.profile.read_text())
    assert [(h.name, h.workspace_dir) for h in parsed] == [("h", "/new/ws"), ("k", "/k/ws")]


def test_edit_lane_host_changes_only_the_named_entry_and_refuses_flow_style():
    edited, reason = cli._migrate_edit_lane_host(LANES, "k", "/k/new")
    assert reason == "" and edited == LANES.replace("/k/ws", "/k/new")
    flow = "lane_hosts: [{name: h, ssh: s, workspace_dir: /w}]\n"
    assert cli._migrate_edit_lane_host(flow, "h", "/x")[0] is None
    assert cli._migrate_edit_lane_host(LANES, "nope", "/x")[0] is None


def test_edit_key_replaces_one_line_and_refuses_block_lists():
    edited, _ = cli._migrate_edit_key("a: 1\nroots: [/o]\nb: 2\n", "roots", ["/n", "/m"])
    assert edited == "a: 1\nroots: [/n, /m]\nb: 2\n"
    assert cli._migrate_edit_key("roots:\n  - /o\n", "roots", ["/n"])[0] is None
    assert cli._migrate_edit_key("a: 1\n", "roots", ["/n"])[0] is None


def test_observed_hosts_fall_back_to_the_profile_when_the_table_is_empty(layout, monkeypatch):
    layout.profile.write_text(layout.profile.read_text() + LANES)
    locs = cli.home_paths.resolve_here(None)
    found = ORIGINAL_OBSERVE_HOSTS(locs)
    assert [(h.name, h.ssh, h.source, h.profile_workspace_dir) for h in found] == [
        ("h", "ssh-h", "fallback", "/old/ws"),
        ("k", "ssh-k", "fallback", "/k/ws"),
    ]


def test_rerun_after_a_stop_resumes_from_the_remaining_steps(layout, capsys):
    good = layout.profile.read_text()
    old_roots = f"skills_roots: [{layout.repos}/coxswain-cartridges/skills]\n"
    layout.profile.write_text(good.replace(old_roots, f"skills_roots:\n  - {layout.repos}/coxswain-cartridges/skills\n"))
    assert run(apply=True) == 1
    assert "FAILED step" in capsys.readouterr().out
    assert all((layout.installs / n).is_dir() for n in NAMES)
    layout.profile.write_text(good)
    assert run(apply=True) == 0
    out = capsys.readouterr().out
    assert f"move {layout.repos}" not in out and "profile skills_roots" in out
    assert str(layout.repos) not in layout.profile.read_text()


def test_rerun_rewrites_a_setting_left_pointing_at_the_old_path(layout, capsys):
    assert run(apply=True) == 0
    text = layout.profile.read_text()
    new_provider = f"provider_profile: {layout.installs}/coxswain-cartridges/provider.yaml\n"
    layout.profile.write_text(text.replace(new_provider, f"provider_profile: {layout.repos}/coxswain-cartridges/provider.yaml\n"))
    capsys.readouterr()
    assert run(apply=True) == 0
    assert "profile provider_profile" in capsys.readouterr().out
    assert layout.profile.read_text() == text


def test_apply_flag_parses_for_the_remote_argv():
    args = cli.build_parser().parse_args(["setup", "migrate", "--apply"])
    assert args.apply is True and args.dry_run is False and args.hosts is False
    assert cli.build_parser().parse_args(["setup", "migrate", "--apply", "--hosts"]).hosts is True
