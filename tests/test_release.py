import tomllib

import pytest

from agent_tools import cli, release, release

_MANIFEST_TOML = """
[coxswain]
version = "0.1.0"

[components.harness]
repo = "org/harness"
tag = "v0.1.0"

[components.cartridges]
repo = "org/cartridges"
tag = "v0.1.0"
"""


def _no_tags(manifest):
    """Every repo component reachable with no tags — the clean case."""
    return {name: [] for name, spec in manifest["components"].items() if spec.get("repo")}


def _manifest(current="0.1.0"):
    return {"coxswain": {"version": current},
            "components": {"harness": {"repo": "org/harness", "tag": "v0.1.0"},
                            "cartridges": {"repo": "org/cartridges", "tag": "v0.1.0"}}}


@pytest.fixture(autouse=True)
def _maintainer_checkout(monkeypatch):
    """Every `cli.main(["dev", "release", ...])` below runs as if it were on
    the maintainer's machine, unless a test overrides this to prove the guard."""
    monkeypatch.setattr(cli, "_maintainer_remote_url", lambda directory: "git@github.com:ppfenning/coxswain.git")


def test_step_order_for_a_two_component_manifest():
    steps = release.release_plan(_manifest(), "0.2.0", _no_tags(_manifest()))
    assert [s["kind"] for s in steps] == ["tag", "tag", "bump_manifest", "notes", "tag_self"]
    assert steps[0] == {"kind": "tag", "component": "harness", "repo": "org/harness", "tag": "v0.2.0"}
    assert steps[1] == {"kind": "tag", "component": "cartridges", "repo": "org/cartridges", "tag": "v0.2.0"}
    assert steps[2] == {"kind": "bump_manifest", "component": "manifest", "from": "0.1.0", "to": "0.2.0"}
    assert steps[3] == {"kind": "notes", "component": "notes", "path": "releases/0.2.0.md"}
    assert steps[4] == {"kind": "tag_self", "component": "coxswain", "tag": "v0.2.0"}


def test_refuse_on_bad_semver():
    steps = release.release_plan(_manifest(), "not-a-version", {})
    assert len(steps) == 1 and steps[0]["kind"] == "refuse" and "not-a-version" in steps[0]["detail"]


def test_refuse_on_an_existing_tag_names_all_colliding_components():
    existing = {"harness": ["v0.2.0"], "cartridges": ["v0.2.0"]}
    step = release.release_plan(_manifest(), "0.2.0", existing)[0]
    assert step["kind"] == "refuse" and "harness" in step["detail"] and "cartridges" in step["detail"]


@pytest.mark.parametrize("current, version", [
    ("0.2.0", "0.1.0"),          # lesser
    ("0.1.0-beta.2", "0.1.0-beta.1"),  # beta.1 does not beat beta.2
    ("0.1.0", "0.1.0-beta.1"),   # a beta never beats its own release
])
def test_refuse_on_a_lesser_version(current, version):
    assert release.release_plan(_manifest(current), version, {})[0]["kind"] == "refuse"


@pytest.mark.parametrize("current, version", [
    ("0.1.0-beta.1", "0.1.0-beta.2"),  # beta.2 beats beta.1
    ("0.1.0-beta.1", "0.1.0"),         # a release beats its own beta
])
def test_a_strictly_greater_version_is_accepted(current, version):
    assert release.release_plan(_manifest(current), version, _no_tags(_manifest(current)))[0]["kind"] != "refuse"


def test_bumped_manifest_text_preserves_comments_and_changes_only_the_values():
    text = ('# coxswain manifest\n[coxswain]\nversion = "0.1.0"  # the released version\n\n'
            '[components.harness]\nrepo = "org/harness"\ntag = "v0.1.0"\n')
    before = tomllib.loads(text)
    after_text = release.bumped_manifest_text(text, "0.2.0")
    after = tomllib.loads(after_text)

    assert "# coxswain manifest" in after_text and "# the released version" in after_text
    assert after["coxswain"]["version"] == "0.2.0" and after["components"]["harness"]["tag"] == "v0.2.0"

    before["coxswain"]["version"] = after["coxswain"]["version"]
    before["components"]["harness"]["tag"] = after["components"]["harness"]["tag"]
    assert before == after


class _LsRemote:
    def __init__(self, returncode, stdout):
        self.returncode, self.stdout = returncode, stdout


def test_remote_tags_parses_ls_remote_output_and_skips_peeled_tags(monkeypatch):
    stdout = "abc\trefs/tags/v0.1.0\ndef\trefs/tags/v0.1.0^{}\nghi\trefs/tags/v0.2.0\n"
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _LsRemote(0, stdout))
    assert cli._remote_tags("org/harness") == ["v0.1.0", "v0.2.0"]


def test_remote_tags_is_none_on_a_failing_git(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _LsRemote(128, ""))
    assert cli._remote_tags("org/nope") is None


def test_cli_release_dry_run_prints_every_step_and_exits_zero(tmp_path, capsys, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    rc = cli.main(["dev", "release", "0.2.0", "--dry-run", "--manifest", str(manifest_path)])
    out = capsys.readouterr().out
    assert rc == 0
    for line in ("tag harness: org/harness -> v0.2.0", "tag cartridges: org/cartridges -> v0.2.0",
                 "bump_manifest manifest: 0.1.0 -> 0.2.0", "notes notes: releases/0.2.0.md",
                 "tag_self coxswain: v0.2.0"):
        assert line in out


def test_first_cut_of_the_declared_version_yields_tag_notes_tag_self_with_no_bump():
    steps = release.release_plan(_manifest("0.2.0"), "0.2.0", _no_tags(_manifest("0.2.0")))
    assert [s["kind"] for s in steps] == ["tag", "tag", "notes", "tag_self"]
    assert steps[-1] == {"kind": "tag_self", "component": "coxswain", "tag": "v0.2.0"}


def test_equal_version_with_an_existing_tag_still_refuses():
    existing = {**_no_tags(_manifest("0.2.0")), "harness": ["v0.2.0"]}
    step = release.release_plan(_manifest("0.2.0"), "0.2.0", existing)[0]
    assert step["kind"] == "refuse" and "harness" in step["detail"]


def test_component_dir_tag_argv_and_push_argv_shape():
    assert release.component_dir("/root", "harness") == "/root/harness"
    assert release.component_dir("/root", "harness", {"harness": "/dev/harness"}) == "/dev/harness"
    assert release.tag_argv("/dev/harness", "0.2.0") == ["git", "-C", "/dev/harness", "tag", "-a", "v0.2.0", "-m", "coxswain 0.2.0"]
    assert release.push_argv("/dev/harness", "0.2.0") == ["git", "-C", "/dev/harness", "push", "origin", "v0.2.0"]


def _fake_git_run(dirty=(), fail=None, off_branch=()):
    """`fail`, when given, is `(directory, kind)` for the one call that
    should return non-zero — everything else in a clean, on-branch tree."""
    calls: list = []

    def run(argv, cwd):
        calls.append(argv)
        if fail and argv[2] == fail[0] and argv[3] == fail[1]:
            return (1, f"{fail[1]} failed")
        if argv[3] == "status":
            return (0, "M f\n") if argv[2] in dirty else (0, "")
        if argv[3] == "rev-parse":
            return (0, "feature/x\n") if argv[2] in off_branch else (0, "main\n")
        if argv[3] == "symbolic-ref":
            return (0, "refs/remotes/origin/main\n")
        return (0, "")
    return calls, run


def test_cli_release_execute_records_tag_and_push_argv_per_component_and_the_umbrella(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "releases").mkdir(parents=True)
    (umbrella_dir / "releases" / "0.1.0.md").write_text("notes")
    calls, fake_run = _fake_git_run()
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    tag_push = [c for c in calls if c[3] in ("tag", "push")]
    assert tag_push == [
        ["git", "-C", str(tmp_path / "harness"), "tag", "-a", "v0.1.0", "-m", "coxswain 0.1.0"],
        ["git", "-C", str(tmp_path / "harness"), "push", "origin", "v0.1.0"],
        ["git", "-C", str(tmp_path / "cartridges"), "tag", "-a", "v0.1.0", "-m", "coxswain 0.1.0"],
        ["git", "-C", str(tmp_path / "cartridges"), "push", "origin", "v0.1.0"],
        ["git", "-C", str(umbrella_dir), "tag", "-a", "v0.1.0", "-m", "coxswain 0.1.0"],
        ["git", "-C", str(umbrella_dir), "push", "origin", "v0.1.0"],
    ]


def test_cli_release_execute_refuses_before_any_tag_or_push_when_a_checkout_is_dirty(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    calls, fake_run = _fake_git_run(dirty={str(tmp_path / "harness")})
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 2
    assert not any(c[3] in ("tag", "push") for c in calls)


def test_cli_release_execute_refuses_a_plan_that_still_carries_a_bump_manifest_step(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    calls, fake_run = _fake_git_run()
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    rc = cli.main(["dev", "release", "0.2.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 2
    assert calls == []


def test_cli_release_execute_refuses_before_any_tag_or_push_when_the_release_note_is_missing(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    calls, fake_run = _fake_git_run()
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 2
    assert not any(c[3] in ("tag", "push") for c in calls)


def test_cli_release_execute_stops_at_the_first_failed_tag(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "releases").mkdir(parents=True)
    (umbrella_dir / "releases" / "0.1.0.md").write_text("notes")
    harness_dir = str(tmp_path / "harness")
    calls, fake_run = _fake_git_run(fail=(harness_dir, "tag"))
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 2
    tag_push = [c for c in calls if c[3] in ("tag", "push")]
    assert tag_push == [["git", "-C", harness_dir, "tag", "-a", "v0.1.0", "-m", "coxswain 0.1.0"]]


def test_cli_release_missing_manifest_fails_gracefully_not_a_traceback(tmp_path, capsys):
    rc = cli.main(["dev", "release", "0.2.0", "--dry-run", "--manifest", str(tmp_path / "manifest.toml")])
    assert rc == 2 and "refusing" in capsys.readouterr().out


def test_cli_release_exits_two_and_names_the_component_on_an_existing_tag(tmp_path, capsys, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: ["v0.2.0"] if repo == "org/harness" else [])
    rc = cli.main(["dev", "release", "0.2.0", "--dry-run", "--manifest", str(manifest_path)])
    out = capsys.readouterr().out
    assert rc == 2 and "refuse" in out and "harness" in out


def test_release_plan_refuses_when_a_remote_could_not_be_read():
    manifest = {"coxswain": {"version": "0.1.0-beta.1"}, "components": {"harness": {"repo": "org/harness", "tag": "v0.1.0-beta.1", "required": True}}}
    steps = release.release_plan(manifest, "0.1.0-beta.2", {"harness": None})
    assert [s["kind"] for s in steps] == ["refuse"] and "unknown" in steps[0]["detail"]


def test_parse_ls_remote_is_pure_and_skips_peeled_refs():
    text = "aaa\trefs/tags/v0.1.0\nbbb\trefs/tags/v0.1.0^{}\nccc\trefs/heads/main\nddd\trefs/tags/v0.2.0\n"
    assert release.parse_ls_remote(text) == ["v0.1.0", "v0.2.0"]


def test_cli_release_execute_refuses_before_any_tag_when_a_checkout_is_not_on_its_default_branch(tmp_path, monkeypatch, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    calls, fake_run = _fake_git_run(off_branch={str(tmp_path / "harness")})
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 2
    assert "is on feature/x, not main" in capsys.readouterr().out
    assert not any(c[3] in ("tag", "push") for c in calls)


def test_cox_release_alias_prints_moved_message_and_exits_two(capsys):
    rc = cli.main(["release", "0.2.0"])
    assert rc == 2
    assert capsys.readouterr().out.strip() == "moved: use cox dev release"


def test_cli_dev_release_refuses_off_a_non_maintainer_checkout(tmp_path, capsys, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    monkeypatch.setattr(cli, "_maintainer_remote_url", lambda directory: "git@github.com:someone/else.git")
    rc = cli.main(["dev", "release", "0.2.0", "--dry-run", "--manifest", str(manifest_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert len(out.strip().splitlines()) == 1 and "ppfenning/coxswain" in out


def test_cox_release_alias_prints_moved_message_for_the_flagged_form_too(capsys):
    rc = cli.main(["release", "0.4.0", "--dry-run", "--manifest", "x.toml"])
    assert rc == 2
    assert capsys.readouterr().out.strip() == "moved: use cox dev release"
