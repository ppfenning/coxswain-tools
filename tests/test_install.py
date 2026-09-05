import subprocess

from agent_tools import cli, install


def _manifest():
    return {
        "coxswain": {"version": "0.1.0"},
        "components": {
            "harness": {"repo": "org/harness", "tag": "v1.0.0", "required": True},
            "cartridges": {"repo": "org/cartridges", "tag": "v1.0.0", "required": True},
            "hud": {"repo": "org/hud", "tag": "v1.0.0", "required": False, "flag": "hud"},
            "desktop-app": {"repo": "org/desktop-app", "tag": "v1.0.0", "required": False,
                            "flag": "desktop", "path": "/Applications/Coxswain.app"},
        },
        "providers": {
            "claude-code": {"status": "supported"},
            "other-cli": {"status": "planned"},
        },
    }


def _facts(checkouts=None, provider_cli_on_path=True):
    return {"root": "/root", "checkouts": checkouts or {}, "provider_cli_on_path": provider_cli_on_path}


def _options(provider="claude-code", with_=None, team="pat", workspace="/ws", root="/root"):
    return {"provider": provider, "with": with_ or [], "root": root, "team": team, "workspace": workspace}


def _kinds(steps):
    return [(s["kind"], s["component"]) for s in steps]


def test_fresh_root_clones_required_only():
    steps = install.plan(_manifest(), _facts(), _options())
    kinds = _kinds(steps)
    assert ("clone", "harness") in kinds
    assert ("clone", "cartridges") in kinds
    assert not any(c == "hud" for _, c in kinds)
    assert not any(c == "desktop-app" for _, c in kinds)
    assert ("setup_install", "setup_install") in kinds
    assert ("doctor", "doctor") in kinds


def test_with_hud_adds_hud():
    steps = install.plan(_manifest(), _facts(), _options(with_=["hud"]))
    assert ("clone", "hud") in _kinds(steps)


def test_present_at_tag_skips():
    checkouts = {"harness": {"present": True, "tag": "v1.0.0", "dirty": False},
                 "cartridges": {"present": True, "tag": "v1.0.0", "dirty": False}}
    steps = install.plan(_manifest(), _facts(checkouts), _options())
    kinds = _kinds(steps)
    assert ("skip", "harness") in kinds
    assert ("skip", "cartridges") in kinds


def test_tag_drift_fetches():
    checkouts = {"harness": {"present": True, "tag": "v0.9.0", "dirty": False},
                 "cartridges": {"present": True, "tag": "v1.0.0", "dirty": False}}
    steps = install.plan(_manifest(), _facts(checkouts), _options())
    assert ("fetch_checkout", "harness") in _kinds(steps)
    assert ("skip", "cartridges") in _kinds(steps)


def test_dirty_checkout_refuses():
    checkouts = {"harness": {"present": True, "tag": "v1.0.0", "dirty": True},
                 "cartridges": {"present": True, "tag": "v1.0.0", "dirty": False}}
    steps = install.plan(_manifest(), _facts(checkouts), _options())
    refused = [s["component"] for s in steps if s["kind"] == "refuse"]
    assert refused == ["harness"]


def test_unknown_with_flag_yields_a_single_refuse():
    steps = install.plan(_manifest(), _facts(), _options(with_=["bogus"]))
    assert len(steps) == 1 and steps[0]["kind"] == "refuse" and steps[0]["component"] == "bogus"


def test_with_desktop_adds_a_desktop_step_after_doctor():
    steps = install.plan(_manifest(), _facts(), _options(with_=["desktop"]))
    kinds = _kinds(steps)
    assert ("desktop", "desktop-app") in kinds
    assert kinds.index(("doctor", "doctor")) < kinds.index(("desktop", "desktop-app"))


def test_unplanned_provider_yields_a_single_refuse():
    steps = install.plan(_manifest(), _facts(), _options(provider="other-cli"))
    assert len(steps) == 1 and steps[0]["kind"] == "refuse" and steps[0]["component"] == "other-cli"


def test_rows_statuses_ok_drift_missing_extra():
    checkouts = {
        "harness": {"present": True, "tag": "v1.0.0", "dirty": False},
        "cartridges": {"present": True, "tag": "v0.9.0", "dirty": False},
        "extra-thing": {"present": True, "tag": "v9.9.9", "dirty": False},
    }
    by_name = {r[0]: r for r in install.rows(_manifest(), _facts(checkouts))}
    assert by_name["harness"] == ("harness", "v1.0.0", "v1.0.0", "ok")
    assert by_name["cartridges"] == ("cartridges", "v1.0.0", "v0.9.0", "drift")
    assert by_name["hud"] == ("hud", "v1.0.0", None, "missing")
    assert by_name["desktop-app"] == ("desktop-app", "v1.0.0", None, "missing")
    assert by_name["extra-thing"] == ("extra-thing", None, "v9.9.9", "extra")


def test_doctor_step_names_provider_cli_on_path_from_facts_in_both_directions():
    for on_path in (True, False):
        steps = install.plan(_manifest(), _facts(provider_cli_on_path=on_path), _options())
        doctor_step = next(s for s in steps if s["kind"] == "doctor")
        assert str(on_path) in doctor_step["detail"]


_MANIFEST_TOML = """
[coxswain]
version = "0.1.0"

[components.harness]
repo = "org/harness"
tag = "v1.0.0"
required = true

[components.cartridges]
repo = "org/cartridges"
tag = "v1.0.0"
required = true

[providers.claude-code]
status = "supported"
"""


def _git_repo(path, tag=None, dirty=False):
    """A real checkout at `path`: init, one commit, an optional exact tag,
    and an optional uncommitted edit afterward."""
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *argv: subprocess.run(argv, cwd=path, check=True, capture_output=True, text=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "a@b.c")
    run("git", "config", "user.name", "t")
    (path / "f").write_text("1")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "x")
    if tag:
        run("git", "tag", tag)
    if dirty:
        (path / "f").write_text("2")
    return path


def test_cli_install_dry_run_lists_clones_and_exits_zero(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    rc = cli.main(["install", "--dry-run", "--root", str(tmp_path), "--manifest", str(manifest_path),
                   "--provider", "claude-code", "--team", "pat", "--workspace", str(tmp_path / "ws")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "clone harness" in out and "clone cartridges" in out and "doctor" in out


def test_cli_install_without_dry_run_refuses_and_exits_two(tmp_path, capsys):
    rc = cli.main(["install", "--root", str(tmp_path), "--manifest", str(tmp_path / "manifest.toml"),
                   "--provider", "claude-code"])
    out = capsys.readouterr().out
    assert rc == 2
    assert out.strip() == "executing steps is not implemented yet; use --dry-run"


def test_cli_install_unknown_with_flag_exits_two(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    rc = cli.main(["install", "--dry-run", "--root", str(tmp_path), "--manifest", str(manifest_path),
                   "--provider", "claude-code", "--with", "bogus"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "refuse bogus" in out


def test_cli_install_missing_manifest_fails_gracefully_not_a_traceback(tmp_path, capsys):
    rc = cli.main(["install", "--dry-run", "--root", str(tmp_path), "--provider", "claude-code"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "refusing" in out


def test_cli_versions_missing_manifest_fails_gracefully_not_a_traceback(tmp_path, capsys):
    rc = cli.main(["versions", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "refusing" in out


def test_cli_install_does_not_borrow_the_enclosing_repos_tag_or_dirty_state(tmp_path, capsys):
    # tmp_path itself is a real, dirty checkout tagged v9.9.9; harness is a
    # plain directory under it with no .git of its own. `git -C harness ...`
    # would otherwise walk up and report tmp_path's own tag and dirty state.
    _git_repo(tmp_path, tag="v9.9.9", dirty=True)
    (tmp_path / "harness").mkdir()
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    rc = cli.main(["install", "--dry-run", "--root", str(tmp_path), "--manifest", str(manifest_path),
                   "--provider", "claude-code"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "clone harness" in out
    assert "refuse harness" not in out and "skip harness" not in out and "fetch_checkout harness" not in out


def test_cli_install_exits_two_when_a_refuse_is_mixed_among_other_steps(tmp_path, capsys):
    _git_repo(tmp_path / "harness", tag="v1.0.0", dirty=True)
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    rc = cli.main(["install", "--dry-run", "--root", str(tmp_path), "--manifest", str(manifest_path),
                   "--provider", "claude-code"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "refuse harness" in out
    assert "clone cartridges" in out
    assert "doctor" in out


def test_cli_install_resolves_provider_cli_from_the_manifests_command_not_the_provider_key(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML.replace(
        '[providers.claude-code]\nstatus = "supported"',
        '[providers.claude-code]\nstatus = "supported"\ncommand = "totally-fake-cli-xyz"'))
    seen = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: seen.append(name) or None)
    rc = cli.main(["install", "--dry-run", "--root", str(tmp_path), "--manifest", str(manifest_path),
                   "--provider", "claude-code"])
    assert rc == 0
    assert seen == ["totally-fake-cli-xyz"]


def test_cli_versions_reports_ok_missing_and_extra_from_real_checkouts(tmp_path, capsys):
    manifest_dir = tmp_path / "coxswain"
    manifest_dir.mkdir()
    manifest_path = manifest_dir / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    _git_repo(tmp_path / "harness", tag="v1.0.0")
    _git_repo(tmp_path / "extra-thing", tag="v9.9.9")
    rc = cli.main(["versions", "--manifest", str(manifest_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "harness" in out and "ok" in out
    assert "cartridges" in out and "missing" in out
    assert "extra-thing" in out and "extra" in out


def test_cli_versions_root_flag_overrides_the_manifests_own_directory(tmp_path, capsys):
    manifest_path = tmp_path / "elsewhere" / "manifest.toml"
    manifest_path.parent.mkdir()
    manifest_path.write_text(_MANIFEST_TOML)
    real_root = tmp_path / "real-root"
    _git_repo(real_root / "harness", tag="v1.0.0")
    rc = cli.main(["versions", "--manifest", str(manifest_path), "--root", str(real_root)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "harness" in out and "ok" in out
