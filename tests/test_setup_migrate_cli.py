import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_tools import chair_read_lease, cli, store_cli
from agent_tools.setup_migrate_hosts import ObservedHost

ORIGINAL_OBSERVE_HOSTS = cli._migrate_observe_hosts
ORIGINAL_TOOL_DIR = cli._migrate_tool_dir
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
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("COXSWAIN_HOME", str(tmp_path / "coxhome"))
    for var in ("AGENT_TOOLS_PROFILE", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: [])
    monkeypatch.setattr(cli, "_migrate_tool_dir", lambda: None)  # never let a test reach the real uv
    calls = []

    def acquire(runs_dir, repo, holder, ttl):
        calls.append(("acquire", runs_dir, repo, holder, ttl))
        return store_cli.LeaseGranted(len(calls), holder)

    monkeypatch.setattr(cli.land_repo_lease, "acquire", acquire)
    monkeypatch.setattr(
        cli.land_repo_lease, "release", lambda runs_dir, repo, holder, epoch: calls.append(("release", repo))
    )
    monkeypatch.setattr(cli.run_store, "live_lanes", lambda runs_dir, now: [])
    monkeypatch.setattr(cli.run_store, "hosts", lambda runs_dir: [])
    monkeypatch.setattr(cli.chair_read_lease, "read_lease", lambda runs_dir, now: dict(chair_read_lease.ABSENT))
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "here")
    monkeypatch.setattr(cli, "_migrate_beat", lambda locs: "beat: ok")  # never beat the real machine
    monkeypatch.setattr(cli, "_migrate_operator_files", lambda locs: {})
    return types_ns(
        user=user,
        repos=repos,
        cartridges=cartridges,
        profile=profile,
        installs=tmp_path / "coxhome" / "installs",
        bin=bin_dir,
        calls=calls,
    )


def types_ns(**fields):
    return argparse.Namespace(**fields)


def run(dry_run=False, apply=False, hosts=False, finish=False, old_root=None):
    return cli._setup_migrate(
        argparse.Namespace(home=None, dry_run=dry_run, apply=apply, hosts=hosts, finish=finish, old_root=old_root)
    )


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


def test_apply_lists_a_stale_script_then_the_beat_then_the_verify_line(layout, monkeypatch, capsys):
    script = str(layout.user / "start.sh")
    monkeypatch.setattr(cli, "_migrate_operator_files", lambda locs: {script: f"cd {layout.repos}/coxswain-tools\n"})
    assert run(apply=True) == 0
    lines = capsys.readouterr().out.rstrip().splitlines()
    assert lines[-3:] == [
        f"migrate: {script} still names {layout.repos}/coxswain-tools; edit it by hand",
        "beat: ok",
        "verify with: cox setup doctor",
    ]


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
    hosts = [
        ObservedHost(n, f"ssh-{n}", "/w", {"coxswain-tools": "/old/coxswain-tools"}, "/w", "beat")
        for n in ("a", "b", "c")
    ]
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

    hosts = [
        ObservedHost(n, f"ssh-{n}", "/w", {"coxswain-tools": "/old/coxswain-tools"}, "/w", "beat") for n in ("x", "y")
    ]
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
    layout.profile.write_text(
        good.replace(old_roots, f"skills_roots:\n  - {layout.repos}/coxswain-cartridges/skills\n")
    )
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
    layout.profile.write_text(
        text.replace(new_provider, f"provider_profile: {layout.repos}/coxswain-cartridges/provider.yaml\n")
    )
    capsys.readouterr()
    assert run(apply=True) == 0
    assert "profile provider_profile" in capsys.readouterr().out
    assert layout.profile.read_text() == text


def test_apply_flag_parses_for_the_remote_argv():
    args = cli.build_parser().parse_args(["setup", "migrate", "--apply"])
    assert args.apply is True and args.dry_run is False and args.hosts is False
    assert cli.build_parser().parse_args(["setup", "migrate", "--apply", "--hosts"]).hosts is True


def taken(layout, kind):
    return [c for c in layout.calls if c[0] == kind]


def test_a_live_local_lane_refuses_and_releases_every_lease(layout, monkeypatch, capsys):
    monkeypatch.setattr(
        cli.run_store, "live_lanes", lambda runs_dir, now: [cli.run_store.Lane("run-7", None, "t0", "t1")]
    )
    before = tree(layout.user)
    assert run(apply=True) == 1
    assert "migrate: refusing, live lane run-7 holds a run on this machine" in capsys.readouterr().out
    assert tree(layout.user) == before
    assert len(taken(layout, "acquire")) == 3
    assert sorted(c[1] for c in taken(layout, "release")) == sorted(c[2] for c in taken(layout, "acquire"))
    assert not (Path(cli.home_paths.resolve_here(None).state) / "migrate.json").exists()


def test_a_refused_lease_releases_the_other_two(layout, monkeypatch, capsys):
    def acquire(runs_dir, repo, holder, ttl):
        layout.calls.append(("acquire", runs_dir, repo, holder, ttl))
        if repo.endswith("coxswain-graphs"):
            return store_cli.LeaseRefused(4, "land@other:9")
        return store_cli.LeaseGranted(len(layout.calls), holder)

    monkeypatch.setattr(cli.land_repo_lease, "acquire", acquire)
    before = tree(layout.user)
    assert run(apply=True) == 1
    assert f"migrate: refusing, land@other:9 holds {layout.repos}/coxswain-graphs" in capsys.readouterr().out
    released = sorted(c[1] for c in taken(layout, "release"))
    assert released == [f"{layout.repos}/{n}" for n in NAMES if n != "coxswain-graphs"]
    assert tree(layout.user) == before


def test_an_acquire_that_raises_is_an_error_outcome_not_a_crash(layout, monkeypatch, capsys):
    def acquire(runs_dir, repo, holder, ttl):
        raise OSError("no store")

    monkeypatch.setattr(cli.land_repo_lease, "acquire", acquire)
    assert run(apply=True) == 1
    assert "lease error: OSError: no store" in capsys.readouterr().out


def test_a_clean_preflight_writes_migrate_json_and_releases_after_the_last_step(layout, monkeypatch):
    seen = []
    real = cli._migrate_exec
    monkeypatch.setattr(
        cli, "_migrate_exec", lambda step, locs: seen.append(len(taken(layout, "release"))) or real(step, locs)
    )
    assert run(apply=True) == 0
    locs = cli.home_paths.resolve_here(None)
    written = json.loads((Path(locs.state) / "migrate.json").read_text())
    assert written == {"old_root": str(layout.repos), "new_root": str(layout.installs)}
    acquires = taken(layout, "acquire")
    assert len(acquires) == 3 and all(c[2].startswith(str(layout.repos)) and c[4] == 3600 for c in acquires)
    assert acquires[0][1] == layout.user / "ws" / "runs" and acquires[0][3] == f"migrate@here:{os.getpid()}"
    assert seen and all(n == 0 for n in seen) and len(taken(layout, "release")) == 3


def test_leases_are_released_when_a_local_step_raises(layout, monkeypatch):
    def boom(step, locs):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(cli, "_migrate_exec", boom)
    with pytest.raises(RuntimeError):
        run(apply=True)
    assert len(taken(layout, "release")) == 3


def test_a_plan_of_only_skip_steps_takes_no_lease(layout):
    assert run(apply=True) == 0
    layout.calls.clear()
    assert run(apply=True) == 0
    assert layout.calls == []


def test_dry_run_takes_no_lease(layout):
    assert run() == 0 and run(dry_run=True) == 0
    assert layout.calls == []


def test_needs_preflight_ignores_skip_and_print_steps():
    skip, move = {"op": "skip"}, {"op": "move"}
    assert not cli._migrate_needs_preflight([skip, {"op": "print"}], [], False)
    assert cli._migrate_needs_preflight([skip, move], [], False)


@pytest.fixture
def chair_unit(layout, monkeypatch):
    """An installed chair unit whose PATH names the old checkout root, with `which cox` and systemctl faked."""
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/local/bin/cox")
    monkeypatch.setattr(cli.sys, "platform", "linux")
    path = cli.chair_service.unit_path(layout.user)
    path.parent.mkdir(parents=True)
    old = cli.chair_service.unit_text(
        "/usr/local/bin/cox",
        f"{layout.user}/ws",
        "chair",
        60,
        None,
        str(layout.profile),
        f"{layout.repos}/coxswain-tools/.venv/bin:/usr/bin",
    )
    path.write_text(old, encoding="utf-8")
    calls = []
    answers = types_ns(active="inactive\n")

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=answers.active, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return types_ns(path=path, old=old, calls=calls, answers=answers)


def exec_chair_unit(layout):
    return cli._migrate_exec({"op": "chair_unit", "why": "x"}, types_ns(profile=layout.profile))


def test_observe_returns_the_unit_text_on_linux_with_the_file_present(layout, chair_unit):
    assert cli._migrate_observe(cli.home_paths.resolve_here(None))["chair_unit"] == chair_unit.old


def test_observe_returns_none_on_darwin(layout, chair_unit, monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    assert cli._migrate_observe(cli.home_paths.resolve_here(None))["chair_unit"] is None


def test_observe_returns_none_with_no_unit_file(layout, chair_unit):
    chair_unit.path.unlink()
    assert cli._migrate_observe(cli.home_paths.resolve_here(None))["chair_unit"] is None


def test_an_inactive_chair_is_reloaded_but_not_restarted(layout, chair_unit, capsys):
    assert exec_chair_unit(layout) is None
    assert chair_unit.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "is-active", "coxswain-chair.service"],
    ]
    assert capsys.readouterr().out == f"chair unit -> {chair_unit.path}\n"


def test_an_active_chair_is_reloaded_and_restarted(layout, chair_unit):
    chair_unit.answers.active = "active\n"
    assert exec_chair_unit(layout) is None
    assert [c[2] for c in chair_unit.calls] == ["daemon-reload", "is-active", "restart"]
    assert chair_unit.calls[-1] == ["systemctl", "--user", "restart", "coxswain-chair.service"]


def dropin_step(restart):
    pairs = [[f"{ROOT_OLD}/coxswain-tools", "/new/coxswain-tools"]]
    return {"op": "chair_unit_dropin", "why": "x", "dropin": "10-path.conf", "pairs": pairs, "restart": restart}


ROOT_OLD = "/old"


@pytest.fixture
def dropin(chair_unit):
    folder = chair_unit.path.parent / "coxswain-chair.service.d"
    folder.mkdir()
    conf = folder / "10-path.conf"
    conf.write_text("[Service]\nEnvironment=PATH=/old/coxswain-tools/bin:/usr/bin\n", encoding="utf-8")
    other = folder / "20-other.conf"
    other.write_text("[Service]\nEnvironment=PATH=/old/coxswain-tools/bin\n", encoding="utf-8")
    return types_ns(conf=conf, other=other)


def test_observe_reads_the_drop_ins_and_asks_systemd_only_when_there_are_some(layout, chair_unit, dropin):
    chair_unit.answers.active = "active\n"
    observed = cli._migrate_observe(cli.home_paths.resolve_here(None))
    assert [name for name, _ in observed["chair_unit_drop_ins"]] == ["10-path.conf", "20-other.conf"]
    assert observed["chair_active"] is True
    dropin.conf.unlink()
    dropin.other.unlink()
    chair_unit.calls.clear()
    observed = cli._migrate_observe(cli.home_paths.resolve_here(None))
    assert observed["chair_unit_drop_ins"] == () and observed["chair_active"] is False
    assert ["systemctl", "--user", "is-active", "coxswain-chair.service"] not in chair_unit.calls


def test_dropin_apply_writes_only_that_drop_in_and_restarts_only_when_planned(layout, chair_unit, dropin):
    unit_before, other_before = chair_unit.path.read_text(encoding="utf-8"), dropin.other.read_text(encoding="utf-8")
    assert cli._migrate_exec(dropin_step(True), types_ns(profile=layout.profile)) is None
    assert dropin.conf.read_text(encoding="utf-8") == "[Service]\nEnvironment=PATH=/new/coxswain-tools/bin:/usr/bin\n"
    assert chair_unit.path.read_text(encoding="utf-8") == unit_before
    assert dropin.other.read_text(encoding="utf-8") == other_before
    assert chair_unit.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "restart", "coxswain-chair.service"],
    ]


def test_dropin_apply_reloads_without_restart_when_the_service_was_not_active(layout, chair_unit, dropin):
    assert cli._migrate_exec(dropin_step(False), types_ns(profile=layout.profile)) is None
    assert chair_unit.calls == [["systemctl", "--user", "daemon-reload"]]


def test_the_written_unit_is_what_install_would_build_from_the_old_units_args(layout, chair_unit):
    args = cli.chair_service.unit_args(chair_unit.old)
    expected = cli._chair_unit_text(args["label"], args["interval"], args["env_file"], Path(args["profile"]))
    assert isinstance(expected, str) and expected != chair_unit.old
    assert exec_chair_unit(layout) is None
    assert chair_unit.path.read_text(encoding="utf-8") == expected


def test_a_unit_that_does_not_run_the_chair_is_left_alone_and_nothing_is_run(layout, chair_unit, capsys):
    chair_unit.path.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    assert exec_chair_unit(layout) is None
    assert chair_unit.path.read_text(encoding="utf-8") == "[Service]\nExecStart=/bin/true\n"
    assert chair_unit.calls == []
    assert "no `chair run` line" in capsys.readouterr().out


def test_a_refused_unit_is_left_alone_and_nothing_is_run(layout, chair_unit, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    assert exec_chair_unit(layout) is None
    assert chair_unit.path.read_text(encoding="utf-8") == chair_unit.old
    assert chair_unit.calls == []
    assert "chair service: cox is not on PATH" in capsys.readouterr().out
def uv_recorder(monkeypatch, tmp_path, events):
    """Fake `uv tool dir` to an empty folder, so every editable source reads as absent, and record each other uv call."""
    real = subprocess.run
    tool_dir = tmp_path / "uvtools"
    tool_dir.mkdir(exist_ok=True)

    def fake(argv, **kw):
        if argv[0] != "uv":
            return real(argv, **kw)
        events.append((argv, kw.get("cwd")))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cli, "_migrate_tool_dir", lambda: str(tool_dir))
    monkeypatch.setattr(cli.subprocess, "run", fake)
    return tool_dir


def reinstall_calls(installs):
    pip = ["uv", "pip", "install", "-q", "-e", ".[dev]"]
    venv = ["uv", "venv", "--clear", "-q"]
    tools, graphs, carts = (f"{installs}/coxswain-{n}" for n in ("tools", "graphs", "cartridges"))
    return [
        (venv, tools), (pip, tools),
        (venv, graphs), ([*pip, "-e", carts], graphs),
        (venv, carts), (pip, carts),
        (["uv", "tool", "install", "--force", "-q", "-e", tools], None),
        (["uv", "tool", "install", "--force", "-q", "-e", carts], None),
    ]


def test_apply_loads_the_lazy_modules_then_runs_the_eight_reinstall_steps_in_order(layout, monkeypatch, tmp_path, capsys):
    events = []
    uv_recorder(monkeypatch, tmp_path, events)
    monkeypatch.setattr(cli, "_load_lazy_modules", lambda: events.append("lazy"))
    assert run(apply=True) == 0
    assert events[0] == "lazy" and events.count("lazy") == 1
    assert events[1:] == reinstall_calls(layout.installs)
    assert "run: uv venv --clear -q" in capsys.readouterr().out


def test_a_failing_reinstall_step_stops_the_apply(layout, monkeypatch, tmp_path, capsys):
    events = []
    uv_recorder(monkeypatch, tmp_path, events)
    recording = cli.subprocess.run

    def fail_pip(argv, **kw):
        if argv[:2] == ["uv", "pip"]:
            return subprocess.CompletedProcess(argv, 3, "", "boom")
        return recording(argv, **kw)

    monkeypatch.setattr(cli.subprocess, "run", fail_pip)
    assert run(apply=True) == 1
    assert "FAILED step" in capsys.readouterr().out
    assert [argv[1] for argv, _ in events] == ["venv"]


def test_a_bare_command_prints_the_reinstall_steps_and_runs_none(layout, monkeypatch, tmp_path, capsys):
    events = []
    uv_recorder(monkeypatch, tmp_path, events)
    monkeypatch.setattr(cli, "_load_lazy_modules", lambda: events.append("lazy"))
    assert run() == 0
    out = capsys.readouterr().out
    assert sum(bool(re.match(r"\d+\. run ", line)) for line in out.splitlines()) == 8 and out.rstrip().endswith(CLOSING)
    assert events == []


def link_install(layout, git_checkout=True):
    """A 0.40 install: coxswain-tools is a symlink to the bare-name tools checkout."""
    (layout.repos / "coxswain-tools").rename(layout.repos / "tools")
    if not git_checkout:
        shutil.rmtree(layout.repos / "tools" / ".git")
    (layout.repos / "coxswain-tools").symlink_to("tools")


def test_a_link_to_a_git_checkout_moves_the_target_and_links_both_names(layout, monkeypatch, tmp_path, capsys):
    events = []
    uv_recorder(monkeypatch, tmp_path, events)
    link_install(layout)
    new = layout.installs / "coxswain-tools"
    assert run(dry_run=True) == 0
    out = capsys.readouterr().out
    assert f"move {layout.repos}/tools -> {new}" in out
    assert f"symlink {layout.repos}/coxswain-tools -> {new}" in out
    assert f"symlink {layout.repos}/tools -> {new}" in out
    assert (layout.repos / "tools" / ".git").is_dir() and events == []
    assert run(apply=True) == 0
    assert (new / ".git").is_dir()
    for name in ("coxswain-tools", "tools"):
        assert (layout.repos / name).is_symlink() and (layout.repos / name).resolve() == new.resolve()
    assert events == reinstall_calls(layout.installs)


def test_a_link_to_a_directory_that_is_not_a_git_checkout_refuses_and_moves_nothing(layout, capsys):
    link_install(layout, git_checkout=False)
    before = tree(layout.repos)
    assert run(apply=True) == 1
    assert "FAILED step 1 refuse" in capsys.readouterr().out
    assert tree(layout.repos) == before and not (layout.installs / "coxswain-tools").exists()


def test_uv_tool_dir_is_none_when_uv_fails_or_is_missing(monkeypatch):
    def missing(argv, **kw):
        raise FileNotFoundError("uv")

    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 2, "/t\n", "no"))
    assert ORIGINAL_TOOL_DIR() is None
    monkeypatch.setattr(cli.subprocess, "run", missing)
    assert ORIGINAL_TOOL_DIR() is None
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "/t/dir\n", ""))
    assert ORIGINAL_TOOL_DIR() == "/t/dir"


def test_no_tool_dir_observes_no_install_bins(layout):
    assert cli._migrate_install_bins(cli.home_paths.resolve_here(None)) is None


def final_install_bins(installs, tool_dir):
    for name, dist in (("coxswain-graphs", "agent_graphs"), ("coxswain-tools", "coxswain_tools")):
        info = Path(installs) / name / ".venv" / "lib" / "python3.13" / "site-packages" / f"{dist}-0.1.dist-info"
        info.mkdir(parents=True)
        (info / "direct_url.json").write_text(json.dumps({"url": f"file://{installs}/{name}"}))
    for name, folder in (("coxswain-tools", "coxswain-tools"), ("coxswain-cartridges", "agent-cartridges")):
        receipt = tool_dir / folder
        receipt.mkdir(parents=True)
        requirement = f'{{ name = "{folder}", editable = "{installs}/{name}" }}'
        (receipt / "uv-receipt.toml").write_text(f"[tool]\nrequirements = [{requirement}]\n")


def test_a_final_layout_with_matching_install_bins_runs_no_step(layout, monkeypatch, tmp_path, capsys):
    assert run(apply=True) == 0
    events = []
    tool_dir = uv_recorder(monkeypatch, tmp_path, events)
    final_install_bins(layout.installs, tool_dir)
    monkeypatch.setattr(cli, "_load_lazy_modules", lambda: events.append("lazy"))
    capsys.readouterr()
    assert run(apply=True) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert events == []


def test_the_doctor_reports_the_install_bins_row(layout, monkeypatch, tmp_path, capsys):
    uv_recorder(monkeypatch, tmp_path, [])
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    rows = {row["check"]: row for row in cli._doctor_home_rows(cli._migrate_profile(cli.home_paths.resolve_here(None)))}
    assert rows["install bins"]["warn"] is True  # this layout is not migrated yet, so a stale source warns
    assert "missing graphs venv, tools venv, tools receipt, cartridges receipt" in rows["install bins"]["detail"]


def test_the_doctor_flattens_the_observation_into_the_four_named_sources():
    bins = {"coxswain-graphs": {"source": "/g"}, "coxswain-tools": {"source": None},
            "tool:coxswain-tools": {"editable": "/t", "extras": []}, "tool:coxswain-cartridges": {"editable": None, "extras": []}}
    assert cli._doctor_install_sources(bins) == {
        "graphs venv": "/g", "tools venv": None, "tools receipt": "/t", "cartridges receipt": None}
    assert cli._doctor_install_sources(None) is None


MARKET = "coxswain-cartridges"


@pytest.fixture
def fake_claude(layout):
    """A claude first on PATH that logs every argv and answers the two list queries."""
    log = layout.bin / "claude.log"
    script = layout.bin / "claude"
    plugins = json.dumps([{"id": f"base@{MARKET}"}, {"id": "other@elsewhere"}])

    def install(registered: str) -> None:
        market = json.dumps([{"name": MARKET, "path": registered}])
        script.write_text(
            "#!/bin/sh\n"
            f"printf '%s\n' \"$*\" >> {log}\n"
            'case "$*" in\n'
            f"  'plugin marketplace list --json') printf '%s\\n' '{market}' ;;\n"
            f"  'plugin list --json') printf '%s\\n' '{plugins}' ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        script.chmod(0o755)

    def argvs() -> list[str]:
        queries = ("plugin marketplace list --json", "plugin list --json")
        return [line for line in log.read_text().splitlines() if line not in queries] if log.exists() else []

    return types_ns(install=install, argvs=argvs, new=str(layout.installs / MARKET))


def test_apply_re_registers_the_marketplace_and_reinstalls_the_plugin_in_order(layout, fake_claude):
    fake_claude.install(f"{layout.repos}/{MARKET}")
    assert run(apply=True) == 0
    assert fake_claude.argvs() == [
        f"plugin marketplace remove {MARKET}",
        f"plugin marketplace add {fake_claude.new}",
        f"plugin install base@{MARKET}",
    ]


def test_without_apply_the_marketplace_steps_are_printed_and_not_run(layout, fake_claude, capsys):
    fake_claude.install(f"{layout.repos}/{MARKET}")
    assert run() == 0
    assert fake_claude.argvs() == []
    assert "run - register coxswain-cartridges as a plugin marketplace" in capsys.readouterr().out


def test_a_marketplace_at_the_final_path_runs_no_remove_add_or_install(layout, fake_claude):
    fake_claude.install(fake_claude.new)
    assert run(apply=True) == 0
    assert fake_claude.argvs() == []


def test_claude_absent_from_path_prints_the_not_checked_line_and_runs_no_claude(layout, monkeypatch, capsys):
    real, spawned = subprocess.run, []

    def spy(argv, *args, **kwargs):
        spawned.append(argv[0])
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", spy)
    assert run(apply=True) == 0
    assert "claude" not in spawned
    assert "The Claude marketplace was not checked because claude is not on PATH." in capsys.readouterr().out


def test_observe_reports_the_marketplace_keys(layout, fake_claude):
    fake_claude.install(f"{layout.repos}/{MARKET}")
    seen = cli._migrate_observe(cli.home_paths.resolve_here(None))
    assert (seen["claude_on_path"], seen["marketplace"], seen["marketplace_plugins"]) == (
        True, f"{layout.repos}/{MARKET}", [f"base@{MARKET}"])


def test_observe_without_claude_reports_nothing_registered(layout):
    seen = cli._migrate_observe(cli.home_paths.resolve_here(None))
    assert (seen["claude_on_path"], seen["marketplace"], seen["marketplace_plugins"]) == (False, None, [])


def test_plugin_ids_keep_only_this_marketplace_in_order():
    parsed = [
        {"id": f"b@{MARKET}"},
        {"id": "x@other"},
        {"id": f"a@{MARKET}"},
        {"name": f"n@{MARKET}"},
        "c@coxswain-cartridges",
        {"id": 3},
    ]
    assert cli._marketplace_plugin_ids(parsed) == [f"b@{MARKET}", f"a@{MARKET}"]
    assert cli._marketplace_plugin_ids({"id": f"a@{MARKET}"}) == []


def host(under="installs"):
    return ObservedHost("mac", "mac.local", "/m/ws", {"coxswain-tools": f"/m/{under}/coxswain-tools"}, "/m/ws", "beat")


@pytest.fixture
def migrated(layout, monkeypatch):
    """The layout after a plain apply: the old paths are links, the marker names the old root, one task file names an old path."""
    task = layout.user / "ws" / "work" / "demo" / "1-build" / "task.md"
    task.parent.mkdir(parents=True)
    task.write_text(f"---\nstate: ready\ntitle: Do it\nrepo: {layout.repos}/coxswain-tools\n---\n\nDo it\n")
    (layout.repos / "coxswain-cartridges" / "provider.yaml").write_text("# files mode\n")
    assert run(apply=True) == 0
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: [host()])
    layout.task = task
    layout.marker = layout.user / ".local" / "state" / "coxswain" / "migrate.json"
    return layout


def test_finish_without_a_marker_or_old_root_exits_two(layout, capsys):
    assert run(finish=True) == 2
    assert capsys.readouterr().out.strip() == "finish: no old root known; pass --old-root"


def test_finish_with_an_unmigrated_host_exits_one_and_touches_nothing(migrated, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: [host(under="repos")])
    before, text = tree(migrated.user), migrated.task.read_text()
    capsys.readouterr()
    assert run(finish=True, apply=True) == 1
    assert "finish: waiting on host mac to migrate" in capsys.readouterr().out
    assert tree(migrated.user) == before and migrated.task.read_text() == text and migrated.marker.exists()


def test_finish_apply_rewrites_rows_and_removes_only_the_links(migrated, capsys):
    capsys.readouterr()
    assert run(finish=True, apply=True) == 0
    out = capsys.readouterr().out
    assert f"finish: remove {migrated.repos}/coxswain-tools" in out
    assert f"repo: {migrated.installs}/coxswain-tools\n" in migrated.task.read_text()
    for name in NAMES:
        assert not (migrated.repos / name).exists() and (migrated.installs / name).is_dir()
    assert not migrated.marker.exists()


def test_finish_keeps_a_real_directory_at_the_old_path_and_prints_it(migrated, capsys):
    (migrated.repos / "coxswain-graphs").unlink()
    (migrated.repos / "coxswain-graphs").mkdir()
    capsys.readouterr()
    assert run(finish=True, apply=True) == 0
    assert f"finish: kept {migrated.repos}/coxswain-graphs: a real directory" in capsys.readouterr().out
    assert (migrated.repos / "coxswain-graphs").is_dir() and not (migrated.repos / "coxswain-tools").exists()


def test_finish_without_apply_changes_nothing(migrated, capsys):
    before, text = tree(migrated.user), migrated.task.read_text()
    capsys.readouterr()
    assert run(finish=True) == 0
    out = capsys.readouterr().out
    assert "finish: would rewrite" in out and "finish: would remove" in out and "finish: would delete the marker" in out
    assert tree(migrated.user) == before and migrated.task.read_text() == text and migrated.marker.exists()


def test_finish_rerun_has_nothing_to_do(migrated, capsys):
    assert run(finish=True, apply=True) == 0
    capsys.readouterr()
    assert run(finish=True, old_root=str(migrated.repos)) == 0
    assert capsys.readouterr().out.strip() == "finish: nothing to do"


@pytest.fixture
def umbrella(layout, monkeypatch):
    """The three-checkout layout plus the umbrella checkout beside the tools checkout, every host already migrated."""
    (layout.repos / "coxswain-cartridges" / "provider.yaml").write_text("# files mode\n")
    monkeypatch.setattr(cli, "_migrate_observe_hosts", lambda locs: [host()])
    (layout.repos / "coxswain").mkdir()
    git("init", "-q", cwd=layout.repos / "coxswain")
    return layout


def umbrella_steps(plan):
    return [s for s in plan if any(str(s.get(k, "")).endswith("/coxswain") for k in ("src", "dst", "path", "link", "target"))]


def test_observe_reports_the_umbrella_like_the_other_checkouts(umbrella):
    locs = cli.home_paths.resolve_here()
    observed = cli._migrate_observe(locs)
    entries = observed["entries"]
    assert entries["coxswain"].keys() == entries["coxswain-tools"].keys()
    assert entries["coxswain"]["old"] == str(umbrella.repos / "coxswain")
    assert (entries["coxswain"]["old_kind"], entries["coxswain"]["new_kind"]) == ("dir", "absent")
    steps = umbrella_steps(cli.setup_migrate.migrate_plan(observed, locs))
    assert [s["op"] for s in steps] == ["move", "symlink"]
    assert (steps[0]["src"], steps[0]["dst"]) == (str(umbrella.repos / "coxswain"), str(umbrella.installs / "coxswain"))


def test_an_absent_umbrella_is_not_observed(layout):
    locs = cli.home_paths.resolve_here()
    assert "coxswain" not in cli._migrate_observe(locs)["entries"]


def test_a_folder_named_coxswain_does_not_move_the_old_root(layout):
    locs = cli.home_paths.resolve_here()
    odd = {"skills_roots": [str(layout.user / ".config" / "coxswain" / "skills")], "harness_dir": f"{layout.repos}/coxswain-graphs"}
    assert cli._migrate_old_root(odd, locs) == str(layout.repos)


def test_a_final_umbrella_layout_has_nothing_to_do(umbrella):
    assert run(apply=True) == 0
    locs = cli.home_paths.resolve_here()
    assert umbrella_steps(cli.setup_migrate.migrate_plan(cli._migrate_observe(locs), locs)) == []


def test_apply_links_the_umbrella_and_only_finish_removes_the_link(umbrella, capsys):
    assert run(apply=True) == 0
    link = umbrella.repos / "coxswain"
    assert link.is_symlink() and os.readlink(link) == str(umbrella.installs / "coxswain")
    capsys.readouterr()
    assert run(finish=True, apply=True) == 0
    assert f"finish: remove {link}" in capsys.readouterr().out
    assert not link.exists() and (umbrella.installs / "coxswain").is_dir()


def test_finish_keeps_a_real_directory_at_the_umbrella_old_path(umbrella, capsys):
    assert run(apply=True) == 0
    (umbrella.repos / "coxswain").unlink()
    (umbrella.repos / "coxswain").mkdir()
    capsys.readouterr()
    assert run(finish=True, apply=True) == 0
    assert f"finish: kept {umbrella.repos}/coxswain: a real directory" in capsys.readouterr().out


def test_finish_refuses_to_mix_with_hosts(layout, capsys):
    before = tree(layout.user)
    assert run(finish=True, apply=True, hosts=True) == 2
    assert "--finish is its own mode" in capsys.readouterr().out
    assert tree(layout.user) == before


def test_finish_dry_run_plans_even_with_apply(migrated, capsys):
    before = tree(migrated.user)
    capsys.readouterr()
    assert run(finish=True, apply=True, dry_run=True) == 0
    assert "finish: would remove" in capsys.readouterr().out
    assert tree(migrated.user) == before and migrated.marker.exists()


def test_finish_with_an_unusable_provider_profile_exits_two_and_keeps_every_link(migrated, capsys):
    (migrated.installs / "coxswain-cartridges" / "provider.yaml").unlink()
    before = tree(migrated.user)
    capsys.readouterr()
    assert run(finish=True, apply=True) == 2
    assert "cannot tell where the queue lives (provider profile not readable" in capsys.readouterr().out
    assert tree(migrated.user) == before and migrated.marker.exists()


def test_finish_apply_refuses_while_a_lane_is_live_and_keeps_every_link(migrated, monkeypatch, capsys):
    monkeypatch.setattr(cli.run_store, "live_lanes", lambda runs_dir, now: [types_ns(run="r1", host=None)])
    before = tree(migrated.user)
    capsys.readouterr()
    assert run(finish=True, apply=True) == 1
    assert "live lane r1 holds a run on this machine" in capsys.readouterr().out
    assert tree(migrated.user) == before and migrated.marker.exists()


def store_queue(monkeypatch, repo, broken=False, break_on_upsert=False):
    """Store mode over an in-memory queue of one task. `state["broken"]` makes the store unreadable: no rows, no store."""
    from agent_tools import store_fill

    state = {"broken": broken, "upserts": []}
    row = {"kind": "task", "initiative": "demo", "task_id": "a", "extra": {"repo": repo}, "surfaces": []}

    def upsert(runs_dir, partial):
        state["upserts"].append(partial)
        row.update(partial)
        state["broken"] = state["broken"] or break_on_upsert
        return True

    monkeypatch.setattr(cli.work_state, "work_state_mode", lambda profile: "store")
    monkeypatch.setattr(cli.run_store, "read_queue", lambda runs_dir, *a, **k: [] if state["broken"] else [dict(row)])
    monkeypatch.setattr(cli.run_store, "upsert_row", upsert)
    monkeypatch.setattr(cli, "_export_board", lambda runs_dir, ws: [])
    monkeypatch.setattr(store_fill, "_read_store", lambda runs_dir: None if state["broken"] else object())
    return state, row


def test_finish_in_store_mode_upserts_the_rows_then_removes_the_links(migrated, monkeypatch, capsys):
    state, row = store_queue(monkeypatch, f"{migrated.repos}/coxswain-tools")
    capsys.readouterr()
    assert run(finish=True, apply=True) == 0
    assert row["extra"]["repo"] == f"{migrated.installs}/coxswain-tools"
    assert state["upserts"] and not (migrated.repos / "coxswain-tools").exists()
    assert not migrated.marker.exists()


def test_finish_with_an_unreadable_store_exits_two_and_keeps_every_link(migrated, monkeypatch, capsys):
    store_queue(monkeypatch, f"{migrated.repos}/coxswain-tools", broken=True)
    before = tree(migrated.user)
    capsys.readouterr()
    assert run(finish=True, apply=True) == 2
    assert "cannot be read; no link or marker was removed" in capsys.readouterr().out
    assert tree(migrated.user) == before and migrated.marker.exists()


def test_finish_stops_before_removing_links_when_the_store_goes_unreadable_mid_apply(migrated, monkeypatch, capsys):
    state, row = store_queue(monkeypatch, f"{migrated.repos}/coxswain-tools", break_on_upsert=True)
    capsys.readouterr()
    assert run(finish=True, apply=True) == 2
    assert "cannot be read; no link or marker was removed" in capsys.readouterr().out
    assert all((migrated.repos / name).is_symlink() for name in NAMES) and migrated.marker.exists()


def test_finish_stops_before_removing_links_when_the_board_export_fails(migrated, monkeypatch, capsys):
    store_queue(monkeypatch, f"{migrated.repos}/coxswain-tools")

    def failing(runs_dir, ws):
        raise RuntimeError("no export")

    monkeypatch.setattr(cli, "_export_board", failing)
    capsys.readouterr()
    assert run(finish=True, apply=True) == 1
    assert "rewriting the queue failed; no link or marker was removed" in capsys.readouterr().out
    assert all((migrated.repos / name).is_symlink() for name in NAMES) and migrated.marker.exists()


def test_old_root_without_finish_is_refused(layout, capsys):
    assert run(old_root="/x") == 2
    assert "--old-root belongs to --finish" in capsys.readouterr().out
