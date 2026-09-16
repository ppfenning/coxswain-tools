import json
import tomllib

import pytest

from agent_tools import cli, release, release_check
from agent_tools.release_check import Drift

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

_MANIFEST_TOML_WITH_PINNED = _MANIFEST_TOML + """
[components.crew]
repo = "org/crew"
tag = "v0.6.0"
lockstep = false
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
    assert [s["kind"] for s in steps] == [
        "tag", "wait_workflows", "tag", "wait_workflows", "notes", "bump_manifest",
        "push", "pr_create", "wait_checks", "merge", "tag_self", "wait_workflows"]
    assert steps[0] == {"kind": "tag", "component": "harness", "repo": "org/harness", "tag": "v0.2.0"}
    assert steps[1] == {"kind": "wait_workflows", "component": "harness", "tag": "v0.2.0"}
    assert steps[2] == {"kind": "tag", "component": "cartridges", "repo": "org/cartridges", "tag": "v0.2.0"}
    assert steps[4] == {"kind": "notes", "component": "notes", "path": "docs/releases/0.2.0.md"}
    assert steps[5] == {"kind": "bump_manifest", "component": "manifest", "from": "0.1.0", "to": "0.2.0",
                         "branch": "release/0.2.0", "commit_subject": "manifest: bump to 0.2.0 to match the tag"}
    assert steps[10] == {"kind": "tag_self", "component": "coxswain", "tag": "v0.2.0"}
    assert steps[11] == {"kind": "wait_workflows", "component": "coxswain", "tag": "v0.2.0"}


def test_manifest_below_target_gets_its_own_bump_and_land_and_tag_sequence():
    steps = release.release_plan(_manifest(), "0.2.0", _no_tags(_manifest()))
    manifest_steps = [s for s in steps if s["component"] in ("manifest", "coxswain")]
    assert [s["kind"] for s in manifest_steps] == [
        "bump_manifest", "push", "pr_create", "wait_checks", "merge", "tag_self", "wait_workflows"]
    assert manifest_steps[1] == {"kind": "push", "component": "manifest", "branch": "release/0.2.0"}
    assert manifest_steps[2] == {"kind": "pr_create", "component": "manifest",
                                  "title": "manifest: bump to 0.2.0 to match the tag",
                                  "body": "Bumps manifest.toml version to 0.2.0 to match tag v0.2.0."}
    assert manifest_steps[3] == {"kind": "wait_checks", "component": "manifest"}
    assert manifest_steps[4] == {"kind": "merge", "component": "manifest"}
    assert manifest_steps[5] == {"kind": "tag_self", "component": "coxswain", "tag": "v0.2.0"}


def test_a_component_below_the_target_version_yields_bump_pyproject_then_the_land_sequence_then_tag():
    manifest = _manifest("0.2.0")
    steps = release.release_plan(manifest, "0.2.0", _no_tags(manifest), {"harness": "0.1.0"})
    harness_steps = [s for s in steps if s["component"] == "harness"]
    assert [s["kind"] for s in harness_steps] == [
        "bump_pyproject", "push", "pr_create", "wait_checks", "merge", "tag", "wait_workflows"]
    assert harness_steps[0] == {"kind": "bump_pyproject", "component": "harness", "repo": "org/harness",
                                 "branch": "release/0.2.0", "commit_subject": "pyproject: bump to 0.2.0 to match the tag",
                                 "from": "0.1.0", "to": "0.2.0"}
    assert harness_steps[1] == {"kind": "push", "component": "harness", "branch": "release/0.2.0"}
    assert harness_steps[2] == {"kind": "pr_create", "component": "harness",
                                 "title": "pyproject: bump to 0.2.0 to match the tag",
                                 "body": "Bumps harness's pyproject.toml version to 0.2.0 to match tag v0.2.0."}
    assert harness_steps[3] == {"kind": "wait_checks", "component": "harness"}
    assert harness_steps[4] == {"kind": "merge", "component": "harness"}
    assert harness_steps[5] == {"kind": "tag", "component": "harness", "repo": "org/harness", "tag": "v0.2.0"}
    assert harness_steps[6] == {"kind": "wait_workflows", "component": "harness", "tag": "v0.2.0"}
    # cartridges carries no component_versions fact, so it gets no bump.
    assert [s for s in steps if s["component"] == "cartridges"] == [
        {"kind": "tag", "component": "cartridges", "repo": "org/cartridges", "tag": "v0.2.0"},
        {"kind": "wait_workflows", "component": "cartridges", "tag": "v0.2.0"}]


def test_a_component_already_at_the_target_version_yields_no_bump_steps_for_it():
    manifest = _manifest("0.2.0")
    steps = release.release_plan(manifest, "0.2.0", _no_tags(manifest), {"harness": "0.2.0"})
    assert [s for s in steps if s["component"] == "harness"] == [
        {"kind": "tag", "component": "harness", "repo": "org/harness", "tag": "v0.2.0"},
        {"kind": "wait_workflows", "component": "harness", "tag": "v0.2.0"}]


def test_a_lockstep_false_component_gets_a_pinned_step_and_no_tag_step():
    manifest = {"coxswain": {"version": "0.1.0"},
                "components": {"harness": {"repo": "org/harness", "tag": "v0.1.0"},
                                "crew": {"repo": "org/crew", "tag": "v0.6.0", "lockstep": False}}}
    steps = release.release_plan(manifest, "0.2.0", _no_tags(manifest))
    assert [s for s in steps if s["component"] == "crew"] == [
        {"kind": "pinned", "component": "crew", "tag": "v0.6.0"}]
    assert "tag" not in [s["kind"] for s in steps if s["component"] == "crew"]
    assert {"kind": "tag", "component": "harness", "repo": "org/harness", "tag": "v0.2.0"} in steps


def test_a_pinned_components_unreadable_remote_or_colliding_tag_does_not_refuse_the_release():
    """The two preflight guards select only lockstep components: a pinned
    repository the plan will never tag cannot block the release, whether its
    remote is unreadable (None) or already carries the umbrella's next tag."""
    manifest = {"coxswain": {"version": "0.1.0"},
                "components": {"harness": {"repo": "org/harness", "tag": "v0.1.0"},
                                "crew": {"repo": "org/crew", "tag": "v0.6.0", "lockstep": False}}}
    for crew_tags in (None, ["v0.6.0", "v0.2.0"]):
        steps = release.release_plan(manifest, "0.2.0", {"harness": [], "crew": crew_tags})
        assert [s["kind"] for s in steps if s["component"] == "crew"] == ["pinned"]
        assert {"kind": "tag", "component": "harness", "repo": "org/harness", "tag": "v0.2.0"} in steps
    # A lockstep component's unknown or colliding tag still refuses, as before.
    assert release.release_plan(manifest, "0.2.0", {"harness": None, "crew": []})[0]["kind"] == "refuse"
    assert release.release_plan(manifest, "0.2.0", {"harness": ["v0.2.0"], "crew": []})[0]["kind"] == "refuse"


def test_a_lockstep_false_component_with_commits_past_its_tag_gets_a_rejoin_step():
    manifest = {"coxswain": {"version": "0.1.0"},
                "components": {"harness": {"repo": "org/harness", "tag": "v0.1.0"},
                                "crew": {"repo": "org/crew", "tag": "v0.7.0", "lockstep": False}}}
    steps = release.release_plan(manifest, "0.9.0", _no_tags(manifest), pinned_commits={"crew": 3})
    assert [s for s in steps if s["component"] == "crew" and s["kind"] != "wait_workflows"] == [
        {"kind": "rejoin", "component": "crew", "repo": "org/crew", "tag": "v0.9.0", "from": "v0.7.0", "commits": 3}]
    assert {"kind": "wait_workflows", "component": "crew", "tag": "v0.9.0"} in steps


def test_a_lockstep_false_component_with_zero_or_no_pinned_commits_still_gets_pinned():
    manifest = {"coxswain": {"version": "0.1.0"},
                "components": {"harness": {"repo": "org/harness", "tag": "v0.1.0"},
                                "crew": {"repo": "org/crew", "tag": "v0.7.0", "lockstep": False}}}
    for pinned_commits in ({"crew": 0}, None):
        steps = release.release_plan(manifest, "0.9.0", _no_tags(manifest), pinned_commits=pinned_commits)
        assert [s for s in steps if s["component"] == "crew"] == [
            {"kind": "pinned", "component": "crew", "tag": "v0.7.0"}]


def test_pinned_commits_for_other_components_does_not_affect_a_lockstep_component():
    manifest = {"coxswain": {"version": "0.1.0"},
                "components": {"harness": {"repo": "org/harness", "tag": "v0.1.0"},
                                "crew": {"repo": "org/crew", "tag": "v0.7.0", "lockstep": False}}}
    steps = release.release_plan(manifest, "0.9.0", _no_tags(manifest), pinned_commits={"harness": 5})
    assert {"kind": "tag", "component": "harness", "repo": "org/harness", "tag": "v0.9.0"} in steps
    assert [s["kind"] for s in steps if s["component"] == "crew"] == ["pinned"]


def test_cli_release_dry_run_prints_a_rejoin_line_for_a_changed_pinned_component(tmp_path, capsys, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML_WITH_PINNED.replace('tag = "v0.6.0"', 'tag = "v0.7.0"'))
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])

    def run(argv, cwd):
        return (0, "3\n") if argv[3] == "rev-list" else (0, "")
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "release", "0.9.0", "--dry-run", "--manifest", str(manifest_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "rejoin crew: v0.7.0 -> v0.9.0 (3 commits)" in out


def test_a_manifest_with_no_lockstep_key_tags_every_component_as_before():
    manifest = _manifest()
    steps = release.release_plan(manifest, "0.2.0", _no_tags(manifest))
    assert [s["kind"] for s in steps if s["component"] in ("harness", "cartridges") and s["kind"] == "tag"] == ["tag", "tag"]


def test_wait_workflows_is_inserted_only_after_tag_and_tag_self_steps():
    manifest = _manifest()
    steps = release.release_plan(manifest, "0.2.0", _no_tags(manifest), {"harness": "0.1.0"})
    kinds = [s["kind"] for s in steps]
    assert "bump_pyproject" in kinds and "push" in kinds and "pr_create" in kinds
    assert "wait_checks" in kinds and "merge" in kinds and "notes" in kinds and "bump_manifest" in kinds
    for i, kind in enumerate(kinds[:-1]):
        if kind in ("tag", "tag_self"):
            assert kinds[i + 1] == "wait_workflows"
        else:
            assert kinds[i + 1] != "wait_workflows"


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


def test_bumped_manifest_text_leaves_a_lockstep_false_components_tag_untouched():
    text = ('[coxswain]\nversion = "0.1.0"\n\n'
            '[components.harness]\nrepo = "org/harness"\ntag = "v0.1.0"\n\n'
            '[components.crew]\nrepo = "org/crew"\ntag = "v0.6.0"\nlockstep = false\n')
    after = tomllib.loads(release.bumped_manifest_text(text, "0.2.0"))
    assert after["coxswain"]["version"] == "0.2.0"
    assert after["components"]["harness"]["tag"] == "v0.2.0"
    assert after["components"]["crew"]["tag"] == "v0.6.0"


def test_bumped_manifest_text_rewrites_a_rejoining_components_tag_but_keeps_lockstep_false():
    text = ('[coxswain]\nversion = "0.1.0"\n\n'
            '[components.harness]\nrepo = "org/harness"\ntag = "v0.1.0"\n\n'
            '[components.crew]\nrepo = "org/crew"\ntag = "v0.7.0"\nlockstep = false\n')
    after_text = release.bumped_manifest_text(text, "0.9.0", rejoining={"crew"})
    after = tomllib.loads(after_text)
    assert after["components"]["crew"]["tag"] == "v0.9.0"
    assert after["components"]["crew"]["lockstep"] is False


def test_rejoined_names_the_components_release_plan_gave_a_rejoin_step():
    manifest = {"coxswain": {"version": "0.1.0"},
                "components": {"harness": {"repo": "org/harness", "tag": "v0.1.0"},
                                "crew": {"repo": "org/crew", "tag": "v0.7.0", "lockstep": False}}}
    steps = release.release_plan(manifest, "0.9.0", _no_tags(manifest), pinned_commits={"crew": 3})
    assert release.rejoined(steps) == {"crew"}
    assert release.rejoined(release.release_plan(manifest, "0.9.0", _no_tags(manifest))) == set()


def test_a_release_that_bumps_the_manifest_also_rejoins_a_pinned_component_in_one_pass():
    text = ('[coxswain]\nversion = "0.1.0"\n\n'
            '[components.harness]\nrepo = "org/harness"\ntag = "v0.1.0"\n\n'
            '[components.crew]\nrepo = "org/crew"\ntag = "v0.7.0"\nlockstep = false\n')
    manifest = tomllib.loads(text)
    steps = release.release_plan(manifest, "0.9.0", _no_tags(manifest), pinned_commits={"crew": 3})
    assert "bump_manifest" in [s["kind"] for s in steps]
    after = tomllib.loads(release.bumped_manifest_text(text, "0.9.0", rejoining=release.rejoined(steps)))
    assert after["coxswain"]["version"] == "0.9.0"
    assert after["components"]["crew"]["tag"] == "v0.9.0"
    assert after["components"]["crew"]["lockstep"] is False


def test_declares_tag_trigger_reads_the_dict_form_and_skips_the_list_form():
    assert release.declares_tag_trigger('on:\n  push:\n    tags: ["v*"]\n') is True
    assert release.declares_tag_trigger("on: [push, pull_request]\n") is False


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
                 "bump_manifest manifest: 0.1.0 -> 0.2.0", "notes notes: docs/releases/0.2.0.md",
                 "tag_self coxswain: v0.2.0"):
        assert line in out


def test_first_cut_of_the_declared_version_yields_tag_notes_tag_self_with_no_bump():
    steps = release.release_plan(_manifest("0.2.0"), "0.2.0", _no_tags(_manifest("0.2.0")))
    assert [s["kind"] for s in steps] == [
        "tag", "wait_workflows", "tag", "wait_workflows", "notes", "tag_self", "wait_workflows"]
    assert steps[-2] == {"kind": "tag_self", "component": "coxswain", "tag": "v0.2.0"}
    assert steps[-1] == {"kind": "wait_workflows", "component": "coxswain", "tag": "v0.2.0"}


def test_equal_version_with_an_existing_tag_still_refuses():
    existing = {**_no_tags(_manifest("0.2.0")), "harness": ["v0.2.0"]}
    step = release.release_plan(_manifest("0.2.0"), "0.2.0", existing)[0]
    assert step["kind"] == "refuse" and "harness" in step["detail"]


def test_component_dir_tag_argv_and_push_argv_shape():
    assert release.component_dir("/root", "harness") == "/root/harness"
    assert release.component_dir("/root", "harness", {"harness": "/dev/harness"}) == "/dev/harness"
    assert release.tag_argv("/dev/harness", "0.2.0") == ["git", "-C", "/dev/harness", "tag", "-a", "v0.2.0", "-m", "coxswain 0.2.0"]
    assert release.push_argv("/dev/harness", "0.2.0") == ["git", "-C", "/dev/harness", "push", "origin", "v0.2.0"]


def _fake_git_run(dirty=(), fail=None, off_branch=(), gh_conclusion="success"):
    """`fail`, when given, is `(directory, kind)` for the one call that
    should return non-zero — everything else in a clean, on-branch tree.
    Every `gh run list` call reports one run with `gh_conclusion`."""
    calls: list = []

    def run(argv, cwd):
        calls.append(argv)
        if argv[0] == "gh":
            return (0, json.dumps([{"status": "completed", "conclusion": gh_conclusion, "name": "ci", "url": "https://x/1"}]))
        if fail and argv[2] == fail[0] and argv[3] == fail[1]:
            return (1, f"{fail[1]} failed")
        if argv[3] == "status":
            return (0, "M f\n") if argv[2] in dirty else (0, "")
        if argv[3] == "rev-parse":
            return (0, "feature/x\n") if argv[2] in off_branch else (0, "main\n")
        if argv[3] == "symbolic-ref":
            return (0, "refs/remotes/origin/main\n")
        if argv[3] == "rev-list":
            return (0, "deadbeef\n")
        return (0, "")
    return calls, run


def test_wait_workflows_proceeds_when_every_run_for_the_tag_sha_succeeds():
    cwds = []

    def run(argv, cwd):
        if argv[3] == "rev-list":
            return (0, "abc1234\n")
        cwds.append(cwd)
        return (0, json.dumps([
            {"status": "completed", "conclusion": "success", "name": "CI", "url": "https://x/1"},
            {"status": "completed", "conclusion": "success", "name": "Publish", "url": "https://x/2"}]))
    ok, detail = cli._wait_workflows("/root/harness", "v0.2.0", "harness", run)
    assert ok and "2 run" in detail
    # gh infers the repository from its working directory; the first real cut ran it from the release root
    assert cwds == ["/root/harness"]


def test_wait_workflows_fails_naming_the_component_workflow_and_url_when_one_run_is_not_success():
    def run(argv, cwd):
        if argv[3] == "rev-list":
            return (0, "abc1234\n")
        return (0, json.dumps([
            {"status": "completed", "conclusion": "success", "name": "CI", "url": "https://x/1"},
            {"status": "completed", "conclusion": "failure", "name": "Publish", "url": "https://x/2"}]))
    ok, detail = cli._wait_workflows("/root/harness", "v0.2.0", "harness", run)
    assert not ok
    assert "harness" in detail and "Publish" in detail and "https://x/2" in detail


def test_wait_workflows_retries_a_pending_run_then_succeeds_once_it_concludes():
    gh_replies = [
        json.dumps([{"status": "in_progress", "conclusion": None, "name": "CI", "url": "https://x/1"}]),
        json.dumps([{"status": "completed", "conclusion": "success", "name": "CI", "url": "https://x/1"}]),
    ]
    sleeps = []

    def run(argv, cwd):
        if argv[3] == "rev-list":
            return (0, "abc1234\n")
        return (0, gh_replies.pop(0))
    ok, detail = cli._wait_workflows("/root/harness", "v0.2.0", "harness", run,
                                      timeout_s=900, sleep=sleeps.append, now=iter([0.0, 0.0, 10.0]).__next__)
    assert ok and "1 run" in detail and sleeps == [10]


def test_wait_workflows_fails_after_timeout_on_zero_runs_only_when_a_workflow_declares_a_tag_trigger(tmp_path):
    def run(argv, cwd):
        return (0, "abc1234\n") if argv[3] == "rev-list" else (0, "[]")
    triggered = tmp_path / "with_trigger"
    (triggered / ".github" / "workflows").mkdir(parents=True)
    (triggered / ".github" / "workflows" / "publish.yml").write_text('on:\n  push:\n    tags: ["v*"]\n')
    ok, detail = cli._wait_workflows(str(triggered), "v0.2.0", "harness", run, timeout_s=0)
    assert not ok and "harness" in detail

    untriggered = tmp_path / "without_trigger"
    untriggered.mkdir()
    ok, detail = cli._wait_workflows(str(untriggered), "v0.2.0", "harness", run, timeout_s=0)
    assert ok


def test_cli_release_execute_records_tag_and_push_argv_per_component_and_the_umbrella(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("notes")
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
    gh_calls = [c for c in calls if c[0] == "gh"]
    assert gh_calls == [["gh", "run", "list", "--commit", "deadbeef", "--json", "status,conclusion,name,url"]] * 3


def test_cli_release_execute_runs_a_pinned_component_to_success_with_no_tag_or_push_for_it(tmp_path, monkeypatch, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML_WITH_PINNED)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("notes")
    calls, fake_run = _fake_git_run()
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    assert str(tmp_path / "crew") not in {c[2] for c in calls if c[0] == "git" and c[3] in ("tag", "push")}
    assert "pinned crew: v0.6.0" in capsys.readouterr().out


def test_cli_release_execute_tags_and_pushes_a_rejoined_pinned_component_in_argv_order(tmp_path, monkeypatch, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML_WITH_PINNED)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("notes")
    calls, fake_run = _fake_git_run()

    def run(argv, cwd):
        return (0, "3\n") if argv[3] == "rev-list" and "--count" in argv else fake_run(argv, cwd)
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    crew_dir = str(tmp_path / "crew")
    tag_push = [c for c in calls if c[2] == crew_dir and c[3] in ("tag", "push")]
    assert tag_push == [release.tag_argv(crew_dir, "0.1.0"), release.push_argv(crew_dir, "0.1.0")]
    assert "rejoin crew: v0.1.0" in capsys.readouterr().out


def test_cli_release_execute_fails_the_release_when_a_wait_workflows_run_is_not_success(tmp_path, monkeypatch, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("notes")
    calls, fake_run = _fake_git_run(gh_conclusion="failure")
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "FAILED wait_workflows harness: harness: ci did not succeed (https://x/1)" in out
    # the release stops at harness's wait_workflows: cartridges and the umbrella are never tagged.
    tag_push = [c for c in calls if c[3] in ("tag", "push")]
    assert tag_push == [
        ["git", "-C", str(tmp_path / "harness"), "tag", "-a", "v0.1.0", "-m", "coxswain 0.1.0"],
        ["git", "-C", str(tmp_path / "harness"), "push", "origin", "v0.1.0"],
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
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("notes")
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


def test_gate_refuses_one_step_per_drift_when_no_reason_is_given():
    drifts = [Drift("cli-surface", "docs/reference/cli/x.md", None, "cli.py", 42, "add cox x to the docs")]
    assert release.gate(drifts, None) == [
        {"kind": "refuse", "component": "cli-surface",
         "detail": "cli-surface: docs/reference/cli/x.md <-> cli.py:42 — add cox x to the docs"}
    ]


def test_gate_notes_the_reason_and_drift_count_when_one_is_given():
    drifts = [Drift("cli-surface", "a", None, "b", None, "fix a"), Drift("manifest", "a", None, "b", None, "fix b")]
    assert release.gate(drifts, "docs land next sprint") == [
        {"kind": "note", "component": "release-check", "detail": "docs land next sprint (2 drifts allowed)"}
    ]


def test_gate_is_a_no_op_with_no_drifts():
    assert release.gate([], None) == []
    assert release.gate([], "any reason") == []


def test_gate_refuses_a_versions_drift_even_when_a_reason_is_given():
    drifts = [Drift("versions", "manifest.toml", None, "cox/pyproject.toml", None,
                     "cox pyproject.toml is 0.1.0, manifest wants 0.2.0")]
    assert release.gate(drifts, "docs land next sprint") == [
        {"kind": "refuse", "component": "versions",
         "detail": "versions: manifest.toml <-> cox/pyproject.toml — cox pyproject.toml is 0.1.0, manifest wants 0.2.0"}
    ]


def test_cli_release_refuses_on_a_real_versions_drift_even_with_allow_doc_drift(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness" / "pyproject.toml").write_text('[project]\nversion = "0.0.9"\n')
    rc = cli.main(["dev", "release", "0.2.0", "--dry-run", "--manifest", str(manifest_path),
                   "--root", str(tmp_path), "--allow-doc-drift", "shipping anyway"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "refuse versions" in out and "harness" in out


def test_cli_release_refuses_before_tagging_when_a_drift_stands(tmp_path, capsys, monkeypatch):
    def stub(facts):
        return [Drift("cli-surface", "docs/x.md", None, "cli.py", 10, "add x")]

    monkeypatch.setattr(release_check, "CHECKS", (stub,))
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    rc = cli.main(["dev", "release", "0.2.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "refuse cli-surface: cli-surface: docs/x.md <-> cli.py:10 — add x" in out
    assert "tag " not in out


def test_cli_release_with_allow_doc_drift_notes_the_reason_and_proceeds(tmp_path, capsys, monkeypatch):
    def stub(facts):
        return [Drift("cli-surface", "docs/x.md", None, "cli.py", 10, "add x")]

    monkeypatch.setattr(release_check, "CHECKS", (stub,))
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    rc = cli.main(["dev", "release", "0.2.0", "--dry-run", "--manifest", str(manifest_path),
                   "--root", str(tmp_path), "--allow-doc-drift", "docs land next sprint"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "note release-check: docs land next sprint (1 drift allowed)" in out
    assert "tag harness: org/harness -> v0.2.0" in out


def test_component_dir_falls_back_to_the_coxswain_prefixed_checkout(tmp_path):
    (tmp_path / "coxswain-graphs").mkdir()
    assert release.component_dir(str(tmp_path), "graphs") == str(tmp_path / "coxswain-graphs")
    (tmp_path / "tools").mkdir()
    assert release.component_dir(str(tmp_path), "tools") == str(tmp_path / "tools")
    assert release.component_dir(str(tmp_path), "crew") == str(tmp_path / "crew")
