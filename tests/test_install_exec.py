import re
import subprocess
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
            "https://github.com/acme/cartridges-repo.git", "/root/cartridges"]
    assert calls == [(argv, None)]
    assert results == [{"step": steps[0], "commands": [argv], "exit": 0, "output": ""}]


def test_fetch_checkout_argv_shape_fetches_then_checks_out_the_pinned_tag():
    steps = [{"kind": "fetch_checkout", "component": "harness", "repo": "acme/harness", "tag": "v2.0"}]
    fetch_argv = ["git", "-C", "/root/harness", "fetch", "--tags"]
    checkout_argv = ["git", "-C", "/root/harness", "checkout", "v2.0"]
    run, calls = _fake_run({})
    results = install_exec.execute(steps, root="/root", run=run)
    assert calls == [(fetch_argv, None), (checkout_argv, None)]
    assert results[0]["commands"] == [fetch_argv, checkout_argv]
    assert results[0]["exit"] == 0


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
            "https://github.com/acme/cartridges.git", "/root/cartridges"]
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
            return _stub_clone_creating_a_real_git_repo_at_the_requested_tag(argv)
        return 0, ""

    monkeypatch.setattr(cli, "_real_run", fake_run)

    rc = cli.main(["install", "--root", str(tmp_path), "--manifest", str(manifest_path),
                   "--provider", "claude-code", "--team", "pat", "--workspace", str(tmp_path / "ws")])
    out = capsys.readouterr().out

    assert rc == 0
    assert "ok: clone harness" in out
    assert "ok: setup_install setup_install" in out
    assert "ok: doctor doctor" in out
    assert "missing" not in out
    row = next(line for line in out.splitlines() if line.startswith("harness"))
    assert re.split(r"\s{2,}", row.strip())[-1] == "ok"
    assert any(argv[:2] == ["git", "clone"] for argv in calls)


def test_cli_upgrade_to_overrides_every_pinned_tag_and_reaches_the_checkout_argv(monkeypatch, tmp_path, capsys):
    """`--to VERSION` must change what gets checked out, not just parse:
    a component already at the old pin must now plan as `fetch_checkout`
    to the requested version, and that version must reach the real argv."""
    from agent_tools import cli

    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(
        "[components.harness]\nrepo = \"org/harness\"\ntag = \"v1.0.0\"\nrequired = true\n\n"
        "[providers.claude-code]\nstatus = \"supported\"\n"
    )
    root = tmp_path / "root"
    monkeypatch.setattr(cli, "_checkout_facts",
                         lambda path: {"present": True, "tag": "v1.0.0", "dirty": False})
    calls = []

    def fake_run(argv, cwd):
        calls.append(argv)
        return 0, ""

    monkeypatch.setattr(cli, "_real_run", fake_run)

    rc = cli.main(["upgrade", "--root", str(root), "--manifest", str(manifest_path),
                   "--provider", "claude-code", "--to", "v2.0.0"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "fetch_checkout harness" in out
    assert any(argv[:2] == ["git", "-C"] and "v2.0.0" in argv for argv in calls)


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
