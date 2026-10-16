import importlib
import re
import subprocess
import sys
import types
from pathlib import Path

from agent_tools import install_exec


def _fake_run(scripted):
    calls = []

    def run(argv, cwd):
        calls.append((argv, cwd))
        return scripted.get(tuple(argv), (0, ""))

    return run, calls


def test_clone_argv_shape_uses_the_component_name_as_the_target_directory():
    steps = [{"kind": "clone", "component": "cartridges", "repo": "acme/cartridges-repo", "tag": "v1.0"}]
    run, calls = _fake_run({})
    results = install_exec.execute(steps, root="/root", run=run)
    argv = ["git", "clone", "--branch", "v1.0", "--depth", "1",
            "https://github.com/acme/cartridges-repo.git", "/root/coxswain-cartridges"]
    assert calls == [(argv, None)]
    assert results == [{"step": steps[0], "commands": [argv], "exit": 0, "output": ""}]


COMPONENTS = ("cartridges", "graphs", "tools")
PREFIXED = ["coxswain-cartridges", "coxswain-graphs", "coxswain-tools"]


def _clone_steps():
    return [{"kind": "clone", "component": name, "repo": f"acme/{name}", "tag": "v1.0"} for name in COMPONENTS]


def _mkdir_run(argv, cwd):
    if argv[:2] == ["git", "clone"]:
        Path(argv[-1]).mkdir(exist_ok=True)
    return 0, ""


def _layout(root):
    return sorted(p.name for p in root.iterdir())


def test_clone_step_writes_exactly_the_three_prefixed_directories(tmp_path):
    install_exec.execute(_clone_steps(), root=str(tmp_path), run=_mkdir_run)
    assert _layout(tmp_path) == ["coxswain-cartridges", "coxswain-graphs", "coxswain-tools"]


def test_setup_install_lookup_finds_each_prefixed_directory(tmp_path):
    from agent_tools import setup_install

    steps = setup_install.install_plan(
        root=str(tmp_path), team="acme", workspace=str(tmp_path / "ws"), provider_profile="p",
        skills_root="/skills", uv_on_path=True, python_exists={}, claude_on_path=False,
        profile_exists=True, force_profile=False, plugins=False, hook=False,
        config_dir="/cfg", claude_settings_path="/cfg/settings.json")
    venv_dirs = [s["cwd"] for s in steps if s.get("argv") == ["uv", "venv", "-q"]]
    expected = [str(tmp_path / name) for name in PREFIXED]
    assert venv_dirs == expected
    assert [install_exec.clone_dir(str(tmp_path), name) for name in COMPONENTS] == expected


def test_running_the_clone_step_twice_leaves_the_same_layout_as_once(tmp_path):
    install_exec.execute(_clone_steps(), root=str(tmp_path), run=_mkdir_run)
    once = _layout(tmp_path)
    install_exec.execute(_clone_steps(), root=str(tmp_path), run=_mkdir_run)
    assert _layout(tmp_path) == once == ["coxswain-cartridges", "coxswain-graphs", "coxswain-tools"]


def test_fetch_checkout_argv_shape_fetches_then_checks_out_the_pinned_tag():
    steps = [{"kind": "fetch_checkout", "component": "harness", "repo": "acme/harness", "tag": "v2.0"}]
    fetch_argv = ["git", "-C", "/root/harness", "fetch", "--tags"]
    checkout_argv = ["git", "-C", "/root/harness", "checkout", "v2.0"]
    run, calls = _fake_run({})
    results = install_exec.execute(steps, root="/root", run=run)
    assert calls == [(fetch_argv, None), (checkout_argv, None)]
    assert results[0]["commands"] == [fetch_argv, checkout_argv]
    assert results[0]["exit"] == 0


def test_an_edge_fetch_checkout_fetches_the_branch_and_resets_the_local_branch_onto_it():
    steps = [{"kind": "fetch_checkout", "component": "harness", "repo": "acme/harness", "tag": None,
              "ref": "main", "channel": "edge"}]
    fetch_argv = ["git", "-C", "/root/harness", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main"]
    checkout_argv = ["git", "-C", "/root/harness", "checkout", "-B", "main", "origin/main"]
    run, calls = _fake_run({})
    install_exec.execute(steps, root="/root", run=run)
    assert calls == [(fetch_argv, None), (checkout_argv, None)]


def test_from_plan_carries_the_channel_and_ref_so_an_edge_clone_uses_the_branch():
    manifest = {"components": {"harness": {"repo": "acme/harness", "ref": "trunk"}}}
    steps = install_exec.from_plan([{"kind": "clone", "component": "harness"}], manifest=manifest,
                                   options={"channel": "edge"})
    run, calls = _fake_run({})
    install_exec.execute(steps, root="/root", run=run)
    assert calls[0][0][:4] == ["git", "clone", "--branch", "trunk"]


def test_refuse_stops_before_any_later_step_runs():
    steps = [
        {"kind": "refuse", "component": "harness", "detail": "harness checkout is dirty"},
        {"kind": "clone", "component": "cartridges", "repo": "acme/cartridges", "tag": "v1.0"},
    ]
    run, calls = _fake_run({})
    results = install_exec.execute(steps, root="/root", run=run)
    assert calls == []
    assert results[0]["exit"] == 2 and results[0]["output"] == "harness checkout is dirty"
    assert results[1]["exit"] is None and results[1]["commands"] == []


def test_a_failing_clone_stops_the_rest_and_marks_them_not_run():
    steps = [
        {"kind": "clone", "component": "cartridges", "repo": "acme/cartridges", "tag": "v1.0"},
        {"kind": "doctor"},
    ]
    argv = ["git", "clone", "--branch", "v1.0", "--depth", "1",
            "https://github.com/acme/cartridges.git", "/root/coxswain-cartridges"]
    run, calls = _fake_run({tuple(argv): (128, "fatal: could not clone")})
    results = install_exec.execute(steps, root="/root", run=run)
    assert calls == [(argv, None)]
    assert results[0]["exit"] == 128
    assert results[1] == {"step": steps[1], "commands": [], "exit": None, "output": None}


def test_setup_install_omits_flags_whose_value_is_none():
    steps = [{"kind": "setup_install", "team": None, "workspace": None}]
    run, calls = _fake_run({})
    install_exec.execute(steps, root="/root", run=run)
    assert calls == [(["cox", "setup", "install", "--root", "/root"], None)]


def test_setup_install_includes_team_and_workspace_when_given():
    steps = [{"kind": "setup_install", "team": "acme", "workspace": "/ws"}]
    run, calls = _fake_run({})
    install_exec.execute(steps, root="/root", run=run)
    assert calls == [(["cox", "setup", "install", "--root", "/root",
                        "--team", "acme", "--workspace", "/ws"], None)]


def test_skip_runs_nothing_and_is_clean():
    steps = [{"kind": "skip", "component": "cartridges"}]
    run, calls = _fake_run({})
    results = install_exec.execute(steps, root="/root", run=run)
    assert calls == []
    assert results == [{"step": steps[0], "commands": [], "exit": 0, "output": ""}]


def test_from_plan_carries_repo_and_tag_onto_a_clone_step_and_leaves_skip_alone():
    manifest = {"components": {"cartridges": {"repo": "acme/cartridges", "tag": "v1.0"}}}
    steps = [
        {"kind": "clone", "component": "cartridges", "detail": "clone acme/cartridges at v1.0"},
        {"kind": "skip", "component": "harness", "detail": "already at v1.0"},
    ]
    enriched = install_exec.from_plan(steps, manifest=manifest, options={"team": "acme", "workspace": "/ws"})
    assert enriched[0]["repo"] == "acme/cartridges" and enriched[0]["tag"] == "v1.0"
    assert enriched[1] == steps[1]


def test_from_plan_carries_team_and_workspace_onto_setup_install():
    steps = [{"kind": "setup_install", "component": "setup_install", "detail": "team=acme workspace=/ws"}]
    enriched = install_exec.from_plan(steps, manifest={}, options={"team": "acme", "workspace": "/ws"})
    assert enriched[0]["team"] == "acme" and enriched[0]["workspace"] == "/ws"


def test_upgrade_refuses_when_a_present_checkout_is_dirty(monkeypatch, tmp_path, capsys):
    from agent_tools import cli

    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(
        "[providers.claude-code]\nstatus = \"supported\"\n\n"
        "[components.cartridges]\nrepo = \"acme/cartridges\"\ntag = \"v1.0\"\nrequired = true\n"
    )
    root = tmp_path / "root"
    (root / "cartridges" / ".git").mkdir(parents=True)
    monkeypatch.setattr(cli, "_checkout_facts",
                         lambda path: {"present": True, "tag": "v1.0", "dirty": True})

    parser = cli.build_parser()
    args = parser.parse_args(["upgrade", "--root", str(root), "--manifest", str(manifest_path)])
    exit_code = args.fn(args)
    out = capsys.readouterr().out

    assert exit_code == 2
    assert str(root / "cartridges") in out


def _stub_clone_creating_a_real_git_repo_at_the_requested_tag(argv):
    """Stands in for a real `git clone`: builds an actual tiny repo at the
    target directory, tagged exactly as the clone step asked, so a
    post-execution `_gather_checkout_facts` sees a real checkout at that
    tag instead of a directory that merely exists."""
    tag = argv[3]
    target = Path(argv[-1])
    target.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    (target / "f").write_text("x")
    subprocess.run(["git", "add", "."], cwd=target, check=True)
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                     "commit", "-q", "-m", "x"], cwd=target, check=True)
    subprocess.run(["git", "tag", tag], cwd=target, check=True)
    return 0, ""


def test_cli_install_without_dry_run_executes_and_the_table_reflects_what_landed(monkeypatch, tmp_path, capsys):
    """End-to-end through `cli.main`, with `cli._real_run` stubbed to build
    a real (tiny) checkout instead of touching the network: proves
    `_install_execute` re-reads checkout state after `execute` runs, so
    the rows table reports what is actually on disk, not the facts from
    before any step ran."""
    from agent_tools import cli

    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(
        "[components.harness]\nrepo = \"org/harness\"\ntag = \"v1.0.0\"\nrequired = true\n\n"
        "[providers.claude-code]\nstatus = \"supported\"\n"
    )
    calls = []

    def fake_run(argv, cwd):
        calls.append(argv)
        if argv[:2] == ["git", "clone"]:
            # `cli._gather_checkout_facts` still probes `<root>/<component>`, not the
            # prefixed clone target; the stub lands the repo where that probe looks.
            probed = [*argv[:-1], str(tmp_path / "harness")]
            return _stub_clone_creating_a_real_git_repo_at_the_requested_tag(probed)
        return 0, ""

    monkeypatch.setattr(cli, "_real_run", fake_run)

    rc = cli.main(["install", "--root", str(tmp_path), "--manifest", str(manifest_path),
                   "--provider", "claude-code", "--team", "example-team", "--workspace", str(tmp_path / "ws")])
    out = capsys.readouterr().out

    assert rc == 0
    assert any(argv[-1] == str(tmp_path / "coxswain-harness") for argv in calls)
    assert "ok: clone harness" in out
    assert "ok: setup_install setup_install" in out
    assert "ok: doctor doctor" in out
    assert "missing" not in out
    row = next(line for line in out.splitlines() if line.startswith("harness"))
    assert re.split(r"\s{2,}", row.strip())[-1] == "ok"
    assert any(argv[:2] == ["git", "clone"] for argv in calls)


def test_cli_upgrade_to_picks_the_umbrella_and_the_lockstep_component_takes_its_moved_manifest_tag(monkeypatch, tmp_path, capsys):
    """`--to VERSION` chooses the umbrella release only: it reaches the umbrella checkout argv, and the
    lockstep component moves to the tag the moved umbrella's manifest carries."""
    from agent_tools import cli

    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(
        "[components.harness]\nrepo = \"org/harness\"\ntag = \"v1.0.0\"\nrequired = true\n\n"
        "[providers.claude-code]\nstatus = \"supported\"\n"
    )
    root = tmp_path / "root"
    (root / "coxswain").mkdir(parents=True)
    (root / "coxswain" / "manifest.toml").write_text(manifest_path.read_text().replace("v1.0.0", "v2.0.0"))
    monkeypatch.setattr(cli, "_checkout_facts",
                         lambda path: {"present": True, "tag": "v1.0.0", "dirty": False})
    calls = []

    def fake_run(argv, cwd):
        calls.append(argv)
        return (0, "abc\trefs/tags/v2.0.0\n") if "ls-remote" in argv else (0, "")

    monkeypatch.setattr(cli, "_real_run", fake_run)

    rc = cli.main(["upgrade", "--root", str(root), "--manifest", str(manifest_path),
                   "--provider", "claude-code", "--to", "v2.0.0"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "moving harness: v1.0.0 to v2.0.0" in out
    assert ["git", "-C", str(root / "coxswain"), "checkout", "v2.0.0"] in calls
    assert ["git", "-C", str(root / "harness"), "checkout", "v2.0.0"] in calls


def _upgrade_with_moved_manifest(monkeypatch, tmp_path, pins, installed, offered, extra_args):
    """Runs `cox upgrade` whose moved umbrella manifest pins `pins` (component to tag). `installed` and
    `offered` map a component (the umbrella is `coxswain`) to its checkout tag and to the tags its remote offers."""
    from agent_tools import cli

    flag_manifest = tmp_path / "manifest.toml"
    flag_manifest.write_text(
        "[components.harness]\nrepo = \"org/harness\"\ntag = \"v1.0.0\"\nrequired = true\n\n"
        "[providers.claude-code]\nstatus = \"supported\"\n"
    )
    root = tmp_path / "root"
    (root / "coxswain").mkdir(parents=True)
    (root / "coxswain" / "manifest.toml").write_text(
        "".join(f"[components.{name}]\nrepo = \"org/{name}\"\ntag = \"{tag}\"\nrequired = true\n\n"
                for name, tag in pins.items())
        + "[providers.claude-code]\nstatus = \"supported\"\n"
    )
    monkeypatch.setattr(cli, "_checkout_facts",
                         lambda path: {"present": True, "tag": installed.get(path.name, "v1.0.0"), "dirty": False})
    calls = []

    def fake_run(argv, cwd):
        calls.append(argv)
        if "ls-remote" not in argv:
            return 0, ""
        name = next((n for n in offered if str(root / n) in argv), None)
        return 0, "".join(f"abc\trefs/tags/{tag}\n" for tag in offered.get(name, ()))

    monkeypatch.setattr(cli, "_real_run", fake_run)
    rc = cli.main(["upgrade", "--root", str(root), "--manifest", str(flag_manifest),
                   "--provider", "claude-code", *extra_args])
    return rc, calls, root


def test_cli_upgrade_to_leaves_a_component_pinned_below_the_release_at_its_pin(monkeypatch, tmp_path, capsys):
    rc, calls, root = _upgrade_with_moved_manifest(
        monkeypatch, tmp_path,
        pins={"dash": "v1.4.0", "harness": "v2.0.0"},
        installed={"dash": "v1.4.0"},
        offered={"dash": ("v1.4.0",), "harness": ("v2.0.0", "v1.0.0")},
        extra_args=["--to", "v2.0.0"],
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "left behind" not in out
    assert ["git", "-C", str(root / "coxswain"), "checkout", "v2.0.0"] in calls
    assert ["git", "-C", str(root / "harness"), "checkout", "v2.0.0"] in calls
    assert ["git", "-C", str(root / "dash"), "checkout", "v2.0.0"] not in calls


def test_cli_upgrade_to_still_reports_a_moved_manifest_pin_the_repository_lacks(monkeypatch, tmp_path, capsys):
    rc, calls, root = _upgrade_with_moved_manifest(
        monkeypatch, tmp_path,
        pins={"harness": "v9.9.9"},
        installed={},
        offered={"harness": ("v2.0.0", "v1.0.0")},
        extra_args=["--to", "v2.0.0"],
    )
    out = capsys.readouterr().out

    assert rc == 2
    assert "left behind: harness: pin_not_in_repo" in out
    assert ["git", "-C", str(root / "harness"), "checkout", "v9.9.9"] not in calls


def test_cli_upgrade_without_to_takes_the_newest_release_and_keeps_a_lower_pin(monkeypatch, tmp_path, capsys):
    rc, calls, root = _upgrade_with_moved_manifest(
        monkeypatch, tmp_path,
        pins={"dash": "v1.4.0", "harness": "v3.0.0"},
        installed={"dash": "v1.4.0"},
        offered={"coxswain": ("v3.1.0-rc1", "v3.0.0"), "dash": ("v1.4.0",), "harness": ("v3.0.0", "v1.0.0")},
        extra_args=[],
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "left behind" not in out
    assert ["git", "-C", str(root / "coxswain"), "checkout", "v3.0.0"] in calls
    assert ["git", "-C", str(root / "harness"), "checkout", "v3.0.0"] in calls
    assert not any(argv[:3] == ["git", "-C", str(root / "dash")] and "checkout" in argv for argv in calls)


def test_cli_upgrade_plans_components_from_the_moved_umbrella_manifest_not_the_manifest_flag(monkeypatch, tmp_path, capsys):
    """The pins come from `<root>/coxswain/manifest.toml` after the umbrella moves, a new component is cloned,
    and a prerelease tag is never the default target."""
    from agent_tools import cli

    old = "[components.harness]\nrepo = \"org/harness\"\ntag = \"v1.0.0\"\nrequired = true\n\n[providers.claude-code]\nstatus = \"supported\"\n"
    new = (old.replace("v1.0.0", "v3.0.0")
           + "\n[components.dash]\nrepo = \"org/dash\"\ntag = \"v3.0.0\"\nrequired = true\n")
    flag_manifest = tmp_path / "manifest.toml"
    flag_manifest.write_text(old)
    root = tmp_path / "root"
    (root / "coxswain").mkdir(parents=True)
    (root / "coxswain" / "manifest.toml").write_text(new)
    monkeypatch.setattr(cli, "_checkout_facts", lambda path: {"present": path.name != "dash", "tag": "v1.0.0", "dirty": False})
    calls = []

    def fake_run(argv, cwd):
        calls.append(argv)
        return (0, "a\trefs/tags/v3.1.0-rc1\nb\trefs/tags/v3.0.0\n") if "ls-remote" in argv else (0, "")

    monkeypatch.setattr(cli, "_real_run", fake_run)

    rc = cli.main(["upgrade", "--root", str(root), "--manifest", str(flag_manifest), "--provider", "claude-code"])

    assert rc == 0
    assert ["git", "-C", str(root / "coxswain"), "checkout", "v3.0.0"] in calls
    assert ["git", "-C", str(root / "harness"), "checkout", "v3.0.0"] in calls
    assert ["git", "clone", "https://github.com/org/dash.git", str(root / "dash")] in calls
    assert ["git", "-C", str(root / "harness"), "checkout", "v1.0.0"] not in calls


def test_cli_install_a_refuse_step_prints_refused_not_failed_and_never_calls_run(monkeypatch, tmp_path, capsys):
    from agent_tools import cli

    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(
        "[components.harness]\nrepo = \"org/harness\"\ntag = \"v1.0.0\"\nrequired = true\n\n"
        "[providers.claude-code]\nstatus = \"supported\"\n"
    )

    def fake_run(argv, cwd):
        raise AssertionError("run must not be called once the plan has refused")

    monkeypatch.setattr(cli, "_real_run", fake_run)

    rc = cli.main(["install", "--root", str(tmp_path), "--manifest", str(manifest_path),
                   "--provider", "claude-code", "--with", "bogus"])
    out = capsys.readouterr().out

    assert rc == 2
    assert "refused: refuse bogus" in out
    assert "FAILED" not in out


def test_a_failed_step_report_includes_the_captured_output(monkeypatch, capsys, tmp_path):
    from agent_tools import cli
    manifest = {"components": {"harness": {"repo": "o/harness", "tag": "v1", "required": True}}}
    steps = [{"kind": "clone", "component": "harness", "detail": ""}]
    monkeypatch.setattr(cli, "_real_run", lambda argv, cwd: (128, "fatal: could not read from remote repository"))
    monkeypatch.setattr(cli, "_gather_checkout_facts", lambda root, comps: {})
    code = cli._install_execute(steps, manifest, {}, tmp_path)
    out = capsys.readouterr().out
    assert code == 2 and "FAILED: clone harness" in out and "could not read from remote" in out


def _upgrade_argv(tmp_path):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(
        "[components.harness]\nrepo = \"org/harness\"\ntag = \"v1.0.0\"\nrequired = true\n\n"
        "[providers.claude-code]\nstatus = \"supported\"\n"
    )
    root = tmp_path / "root"
    (root / "coxswain").mkdir(parents=True)
    # the manifest the umbrella carries once it has moved to the target pins the target tag
    (root / "coxswain" / "manifest.toml").write_text(manifest_path.read_text().replace("v1.0.0", "v2.0.0"))
    return ["upgrade", "--root", str(root), "--manifest", str(manifest_path),
            "--provider", "claude-code", "--to", "v2.0.0"]


def test_cli_upgrade_imports_every_lazy_module_before_setup_replaces_the_environment(monkeypatch, tmp_path, capsys):
    """After the setup step runs, a module not yet imported is gone, as it is once the tool is reinstalled."""
    from agent_tools import cli

    argv = _upgrade_argv(tmp_path)
    monkeypatch.setattr(cli, "_checkout_facts", lambda path: {"present": True, "tag": "v1.0.0", "dirty": False})
    monkeypatch.delitem(sys.modules, "agent_tools.records", raising=False)
    real_import = importlib.import_module
    replaced = {"yes": False}

    def fake_import(name, package=None):
        if replaced["yes"] and name not in sys.modules:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, package)

    def fake_run(run_argv, cwd):
        replaced["yes"] = replaced["yes"] or run_argv[:3] == ["cox", "setup", "install"]
        return (0, "abc\trefs/tags/v2.0.0\n") if "ls-remote" in run_argv else (0, "")

    monkeypatch.setattr(cli, "_real_run", fake_run)
    monkeypatch.setattr(importlib, "import_module", fake_import)

    rc = cli.main(argv)
    captured = capsys.readouterr()

    assert replaced["yes"]
    assert rc == 0
    assert "Traceback" not in captured.out + captured.err
    assert "ModuleNotFoundError" not in captured.out + captured.err


def test_load_lazy_modules_loads_every_registered_module_once(monkeypatch):
    from agent_tools import cli

    monkeypatch.setattr(cli, "_LAZY_MODULES", [])
    loaded = []
    monkeypatch.setattr(importlib, "import_module",
                        lambda name, package=None: loaded.append(name) or types.ModuleType(name))
    cli._LazyModule("fake_pkg.first")
    cli._LazyModule("fake_pkg.second")

    cli._load_lazy_modules()

    assert loaded == ["fake_pkg.first", "fake_pkg.second"]


def test_cli_upgrade_without_a_setup_step_loads_no_lazy_modules(monkeypatch, tmp_path):
    from agent_tools import cli

    argv = _upgrade_argv(tmp_path)
    monkeypatch.setattr(cli, "_checkout_facts", lambda path: {"present": True, "tag": "v1.0.0", "dirty": False})
    monkeypatch.setattr(cli.install, "plan", lambda manifest, facts, options: [
        {"kind": "doctor", "component": "doctor", "detail": "run cox setup doctor"}])
    monkeypatch.setattr(cli, "_real_run",
                        lambda run_argv, cwd: (0, "abc\trefs/tags/v2.0.0\n") if "ls-remote" in run_argv else (0, ""))
    calls = []
    monkeypatch.setattr(cli, "_load_lazy_modules", lambda: calls.append(1))

    rc = cli.main(argv)

    assert rc == 0
    assert calls == []
