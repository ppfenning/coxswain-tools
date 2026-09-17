import json
import tomllib
from pathlib import Path

import pytest

from agent_tools import cli, release, release_check, release_check_index
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

def _manifest_toml_at(version: str) -> str:
    return _MANIFEST_TOML.replace('version = "0.1.0"', f'version = "{version}"', 1)


_MANIFEST_TOML_WITH_PINNED = _MANIFEST_TOML + """
[components.crew]
repo = "org/crew"
tag = "v0.6.0"
lockstep = false
"""

_TOOLS_REPO_URL = "https://github.com/ppfenning/coxswain-tools"


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
    steps = release.release_plan(_manifest(), "0.2.0", _no_tags(_manifest()), tools_repository_url=_TOOLS_REPO_URL)
    assert [s["kind"] for s in steps] == [
        "tag", "wait_workflows", "github_release", "tag", "wait_workflows", "github_release", "notes",
        "bump_manifest", "push", "pr_create", "wait_checks", "merge", "tag_self", "wait_workflows", "github_release"]
    assert steps[0] == {"kind": "tag", "component": "harness", "repo": "org/harness", "tag": "v0.2.0"}
    assert steps[1] == {"kind": "wait_workflows", "component": "harness", "tag": "v0.2.0"}
    assert steps[2] == {"kind": "github_release", "component": "harness", "repo": "org/harness", "tag": "v0.2.0",
                         "title": "coxswain-harness 0.2.0", "notes_path": "docs/releases/0.2.0.md",
                         "heading": "## coxswain-harness", "from": "v0.1.0",
                         "link": "https://github.com/ppfenning/coxswain/releases/tag/v0.2.0"}
    assert steps[3] == {"kind": "tag", "component": "cartridges", "repo": "org/cartridges", "tag": "v0.2.0"}
    assert steps[6] == {"kind": "notes", "component": "notes", "path": "docs/releases/0.2.0.md"}
    assert steps[7] == {"kind": "bump_manifest", "component": "manifest", "from": "0.1.0", "to": "0.2.0",
                         "branch": "release/0.2.0", "commit_subject": "manifest: bump to 0.2.0 to match the tag"}
    assert steps[12] == {"kind": "tag_self", "component": "coxswain", "tag": "v0.2.0"}
    assert steps[13] == {"kind": "wait_workflows", "component": "coxswain", "tag": "v0.2.0"}
    assert steps[14] == {"kind": "github_release", "component": "coxswain", "repo": "ppfenning/coxswain",
                          "tag": "v0.2.0", "title": "coxswain 0.2.0", "notes_path": "docs/releases/0.2.0.md",
                          "heading": None}


def test_manifest_below_target_gets_its_own_bump_and_land_and_tag_sequence():
    steps = release.release_plan(_manifest(), "0.2.0", _no_tags(_manifest()))
    manifest_steps = [s for s in steps if s["component"] in ("manifest", "coxswain")]
    assert [s["kind"] for s in manifest_steps] == [
        "bump_manifest", "push", "pr_create", "wait_checks", "merge", "tag_self", "wait_workflows", "github_release"]
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
        "bump_pyproject", "push", "pr_create", "wait_checks", "merge", "tag", "wait_workflows", "github_release"]
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
    assert [s["kind"] for s in steps if s["component"] == "cartridges"] == ["tag", "wait_workflows", "github_release"]


def test_a_component_already_at_the_target_version_yields_no_bump_steps_for_it():
    manifest = _manifest("0.2.0")
    steps = release.release_plan(manifest, "0.2.0", _no_tags(manifest), {"harness": "0.2.0"})
    assert [s["kind"] for s in steps if s["component"] == "harness"] == ["tag", "wait_workflows", "github_release"]


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
    steps = release.release_plan(manifest, "0.9.0", _no_tags(manifest), pinned_commits={"crew": 3},
                                  tools_repository_url=_TOOLS_REPO_URL)
    crew_steps = [s for s in steps if s["component"] == "crew"]
    assert [s["kind"] for s in crew_steps] == ["rejoin", "wait_workflows", "github_release"]
    assert crew_steps[0] == {"kind": "rejoin", "component": "crew", "repo": "org/crew", "tag": "v0.9.0",
                              "from": "v0.7.0", "commits": 3}
    assert crew_steps[2] == {"kind": "github_release", "component": "crew", "repo": "org/crew", "tag": "v0.9.0",
                              "title": "coxswain-crew 0.9.0", "notes_path": "docs/releases/0.9.0.md",
                              "heading": "## coxswain-crew", "from": "v0.7.0",
                              "link": "https://github.com/ppfenning/coxswain/releases/tag/v0.9.0"}


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
        "tag", "wait_workflows", "github_release", "tag", "wait_workflows", "github_release",
        "notes", "tag_self", "wait_workflows", "github_release"]
    assert steps[-3] == {"kind": "tag_self", "component": "coxswain", "tag": "v0.2.0"}
    assert steps[-2] == {"kind": "wait_workflows", "component": "coxswain", "tag": "v0.2.0"}
    assert steps[-1]["kind"] == "github_release" and steps[-1]["component"] == "coxswain"


def test_equal_version_with_an_existing_tag_still_refuses():
    existing = {**_no_tags(_manifest("0.2.0")), "harness": ["v0.2.0"]}
    step = release.release_plan(_manifest("0.2.0"), "0.2.0", existing)[0]
    assert step["kind"] == "refuse" and "harness" in step["detail"]


def test_component_dir_tag_argv_and_push_argv_shape():
    assert release.component_dir("/root", "harness") == "/root/harness"
    assert release.component_dir("/root", "harness", {"harness": "/dev/harness"}) == "/dev/harness"
    assert release.tag_argv("/dev/harness", "0.2.0") == ["git", "-C", "/dev/harness", "tag", "-a", "v0.2.0", "-m", "coxswain 0.2.0"]
    assert release.push_argv("/dev/harness", "0.2.0") == ["git", "-C", "/dev/harness", "push", "origin", "v0.2.0"]


def test_component_version_reads_the_project_table_version():
    text = '[project]\nname = "harness"\nversion = "0.1.0"\n'
    assert release.component_version(text) == "0.1.0"


def test_component_version_is_none_with_no_version_field():
    text = '[project]\nname = "harness"\n'
    assert release.component_version(text) is None


def test_release_index_text_appends_the_sections_rendering_to_an_empty_file():
    manifest = {"components": {"harness": {"repo": "org/harness"}, "cartridges": {"repo": "org/cartridges"}}}
    text = release.release_index_text("", "0.1.0", manifest)
    assert text == release_check_index.index_section("0.1.0", {"harness": "v0.1.0", "cartridges": "v0.1.0"}, manifest["components"]) + "\n"


def test_release_index_text_is_unchanged_when_the_versions_section_is_already_present():
    manifest = {"components": {"harness": {"repo": "org/harness"}}}
    existing = f"{release_check_index.index_section('0.1.0', {'harness': 'v0.1.0'}, manifest['components'])}\n"
    assert release.release_index_text(existing, "0.1.0", manifest) == existing


def test_release_index_text_replaces_a_stale_section_for_the_same_version_instead_of_duplicating_it():
    stale_manifest = {"components": {"crew": {"tag": "v0.6.0", "lockstep": False}}}
    existing = release.release_index_text("", "0.7.0", stale_manifest)
    rejoined_manifest = {"components": {"crew": {"tag": "v0.7.0", "lockstep": False}}}
    text = release.release_index_text(existing, "0.7.0", rejoined_manifest)
    assert text.count("## `0.7.0`") == 1
    assert text == release_check_index.index_section("0.7.0", {"crew": "v0.7.0"}, rejoined_manifest["components"]) + "\n"


def test_release_index_text_replaces_a_middle_section_in_place_leaving_the_others_positioned():
    manifest = {"components": {"harness": {"repo": "org/harness"}}}
    existing = release.release_index_text("", "0.1.0", manifest)
    existing = release.release_index_text(existing, "0.2.0", manifest)
    existing = release.release_index_text(existing, "0.3.0", manifest)
    stale_manifest = {"components": {"harness": {"tag": "v0.1.5", "lockstep": False}}}
    text = release.release_index_text(existing, "0.2.0", stale_manifest)
    assert text.index("## `0.1.0`") < text.index("## `0.2.0`") < text.index("## `0.3.0`")
    assert "v0.1.5" in text


def test_release_index_text_does_not_drop_an_adjacent_section_missing_its_blank_line():
    manifest = {"components": {"harness": {"repo": "org/harness"}}}
    hand_written = "## `0.3.0`\n\n| harness | x | `v0.3.0` | required |\n## `0.2.0`\n\n| harness | x | `v0.2.0` | required |\n"
    text = release.release_index_text(hand_written, "0.3.0", manifest)
    assert text.count("## `0.3.0`") == 1
    assert "## `0.2.0`\n\n| harness | x | `v0.2.0` | required |" in text


def _fake_git_run(dirty=(), fail=None, off_branch=(), gh_conclusion="success"):
    """`fail`, when given, is `(directory, kind)` for the one call that
    should return non-zero — everything else in a clean, on-branch tree.
    Every `gh run list` call reports one run with `gh_conclusion`. Tracks
    each directory's current branch (starting off-default for any directory
    named in `off_branch`) across `checkout` calls, exposed as
    `run.current_branch`, so a test can pin what branch a later `tag` call
    actually ran on rather than trust a runner that reports "main" no
    matter what was checked out."""
    calls: list = []
    current_branch = dict.fromkeys(off_branch, "feature/x")

    def run(argv, cwd):
        calls.append(argv)
        if argv[0] == "gh" and argv[1] == "run":
            tag = argv[argv.index("--branch") + 1]
            return (0, json.dumps([{"status": "completed", "conclusion": gh_conclusion, "name": "ci",
                                     "url": "https://x/1", "event": "push", "headBranch": tag}]))
        if argv[0] == "gh" and argv[1] == "release" and argv[2] == "view":
            return (1, "release not found")
        if argv[0] == "gh":
            return (0, "")
        if fail and argv[2] == fail[0] and argv[3] == fail[1]:
            return (1, f"{fail[1]} failed")
        if argv[3] == "status":
            return (0, "M f\n") if argv[2] in dirty else (0, "")
        if argv[3] == "checkout":
            current_branch[argv[2]] = argv[-1]
            return (0, "")
        if argv[3] == "rev-parse":
            return (0, current_branch.get(argv[2], "main") + "\n")
        if argv[3] == "symbolic-ref":
            return (0, "refs/remotes/origin/main\n")
        if argv[3] == "rev-list":
            return (0, "deadbeef\n")
        return (0, "")
    run.current_branch = current_branch
    return calls, run


def test_wait_workflows_proceeds_when_every_push_run_on_the_tag_succeeds(monkeypatch):
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
    cwds = []
    argvs = []

    def run(argv, cwd):
        argvs.append(argv)
        cwds.append(cwd)
        return (0, json.dumps([
            {"status": "completed", "conclusion": "success", "name": "CI", "url": "https://x/1",
             "event": "push", "headBranch": "v0.2.0"},
            {"status": "completed", "conclusion": "success", "name": "Publish", "url": "https://x/2",
             "event": "push", "headBranch": "v0.2.0"},
            {"status": "completed", "conclusion": "failure", "name": "main-ci", "url": "https://x/3",
             "event": "push", "headBranch": "main"}]))
    ok, detail = cli._wait_workflows("/root/harness", "v0.2.0", "harness", run)
    assert ok and "2 run" in detail
    # gh infers the repository from its working directory; the first real cut ran it from the release root
    assert cwds == ["/root/harness"]
    assert argvs[0][:5] == ["gh", "run", "list", "--branch", "v0.2.0"]


def test_wait_workflows_fails_naming_the_component_workflow_and_url_when_one_run_is_not_success(monkeypatch):
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)

    def run(argv, cwd):
        return (0, json.dumps([
            {"status": "completed", "conclusion": "success", "name": "CI", "url": "https://x/1",
             "event": "push", "headBranch": "v0.2.0"},
            {"status": "completed", "conclusion": "failure", "name": "Publish", "url": "https://x/2",
             "event": "push", "headBranch": "v0.2.0"}]))
    ok, detail = cli._wait_workflows("/root/harness", "v0.2.0", "harness", run)
    assert not ok
    assert "harness" in detail and "Publish" in detail and "https://x/2" in detail


def test_wait_workflows_retries_a_pending_run_then_succeeds_once_it_concludes(monkeypatch):
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
    gh_replies = [
        json.dumps([{"status": "in_progress", "conclusion": None, "name": "CI", "url": "https://x/1",
                     "event": "push", "headBranch": "v0.2.0"}]),
        json.dumps([{"status": "completed", "conclusion": "success", "name": "CI", "url": "https://x/1",
                     "event": "push", "headBranch": "v0.2.0"}]),
    ]
    sleeps = []

    def run(argv, cwd):
        return (0, gh_replies.pop(0))
    ok, detail = cli._wait_workflows("/root/harness", "v0.2.0", "harness", run,
                                      timeout_s=900, sleep=sleeps.append, now=iter([0.0, 0.0, 10.0]).__next__)
    assert ok and "1 run" in detail and sleeps == [10]


def test_wait_workflows_waits_through_two_empty_polls_then_succeeds_on_a_green_run(monkeypatch):
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
    gh_replies = [
        "[]",
        "[]",
        json.dumps([{"status": "completed", "conclusion": "success", "name": "Publish", "url": "https://x/1",
                     "event": "push", "headBranch": "v0.2.0"}]),
    ]
    sleeps = []

    def run(argv, cwd):
        return (0, gh_replies.pop(0))
    ok, detail = cli._wait_workflows("/root/harness", "v0.2.0", "harness", run, timeout_s=900,
                                      sleep=sleeps.append, now=iter([0.0, 0.0, 10.0, 20.0]).__next__)
    assert ok and "1 run" in detail and sleeps == [10, 10]


def test_wait_workflows_stays_pending_while_the_only_matching_run_is_queued(monkeypatch):
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
    sleeps = []

    def run(argv, cwd):
        return (0, json.dumps([{"status": "queued", "conclusion": None, "name": "Publish", "url": "https://x/1",
                                 "event": "push", "headBranch": "v0.2.0"}]))
    ok, detail = cli._wait_workflows("/root/harness", "v0.2.0", "harness", run, timeout_s=15,
                                      sleep=sleeps.append, now=iter([0.0, 0.0, 10.0, 20.0]).__next__)
    assert sleeps == [10, 10]
    assert not ok and "Publish" in detail


def test_wait_workflows_fails_after_timeout_on_zero_runs_only_when_a_workflow_declares_a_tag_trigger(tmp_path):
    def run(argv, cwd):
        return (0, "[]")
    triggered = tmp_path / "with_trigger"
    (triggered / ".github" / "workflows").mkdir(parents=True)
    (triggered / ".github" / "workflows" / "publish.yml").write_text('on:\n  push:\n    tags: ["v*"]\n')
    ok, detail = cli._wait_workflows(str(triggered), "v0.2.0", "harness", run, timeout_s=0)
    assert not ok and "harness" in detail

    untriggered = tmp_path / "without_trigger"
    untriggered.mkdir()
    ok, detail = cli._wait_workflows(str(untriggered), "v0.2.0", "harness", run, timeout_s=0)
    assert ok


def test_wait_workflows_returns_immediately_with_no_polling_for_a_component_with_no_tag_trigger(tmp_path):
    calls = []
    sleeps = []

    def run(argv, cwd):
        calls.append(argv)
        return (0, "[]")
    no_trigger = tmp_path / "no_trigger"
    no_trigger.mkdir()
    ok, detail = cli._wait_workflows(str(no_trigger), "v0.2.0", "harness", run, sleep=sleeps.append)
    assert ok and "no tag-triggered workflow" in detail
    assert calls == [] and sleeps == []


def test_cli_release_execute_records_tag_and_push_argv_per_component_and_the_umbrella(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("notes")
    calls, fake_run = _fake_git_run()
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
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
    run_list_calls = [c for c in gh_calls if c[1] == "run"]
    assert run_list_calls == [["gh", "run", "list", "--branch", "v0.1.0", "--json",
                               "status,conclusion,name,url,event,headBranch"]] * 3
    release_calls = [(c[2], c[3]) for c in gh_calls if c[1] == "release"]
    assert release_calls == [("view", "v0.1.0"), ("create", "v0.1.0")] * 3


def test_cli_release_execute_appends_the_index_section_and_a_second_run_leaves_it_unchanged(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("notes")
    index_path = umbrella_dir / "docs" / "releases" / "index.md"
    index_path.write_text("## `0.0.1`\n\n| harness | x | `v0.0.1` | required |\n")
    calls, fake_run = _fake_git_run()
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    after_first = index_path.read_text()
    assert "## `0.1.0`" in after_first and "| harness |" in after_first and "`v0.1.0`" in after_first
    assert "## `0.0.1`\n\n| harness | x | `v0.0.1` | required |" in after_first
    rc2 = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc2 == 0
    assert index_path.read_text() == after_first


def test_cli_release_execute_runs_a_pinned_component_to_success_with_no_tag_or_push_for_it(tmp_path, monkeypatch, capsys):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML_WITH_PINNED)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("notes")
    calls, fake_run = _fake_git_run()
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
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
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
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
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
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


def test_cli_release_execute_runs_the_bump_manifest_land_sequence_and_leaves_the_files_bumped(tmp_path, monkeypatch):
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    manifest_path = umbrella_dir / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    (umbrella_dir / "docs" / "releases" / "0.2.0.md").write_text("notes")
    (umbrella_dir / "pyproject.toml").write_text('[project]\nname = "coxswain"\nversion = "0.1.0"\n')
    (umbrella_dir / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "colorama"\nversion = "0.4.6"\n\n'
        '[[package]]\nname = "coxswain"\nversion = "0.1.0"\n')
    calls, fake_run = _fake_git_run()
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
    rc = cli.main(["dev", "release", "0.2.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    umbrella_calls = [c for c in calls if c[0] == "git" and c[2] == str(umbrella_dir)]
    kinds = [c[3] for c in umbrella_calls]
    # Same ordering guarantee as the component case: after `merge`, the
    # executor switches back to the default branch and pulls it before the
    # pre-existing `tag_self` step tags HEAD, so it tags the squash-merged
    # commit rather than the stale pre-merge commit on release/0.2.0.
    assert kinds == ["status", "rev-parse", "symbolic-ref", "checkout", "add", "commit", "push",
                      "symbolic-ref", "checkout", "pull", "tag", "push"]
    assert umbrella_calls[8] == release.checkout_ref_argv(str(umbrella_dir), "main")
    assert umbrella_calls[9] == release.pull_argv(str(umbrella_dir), "main")
    assert ["gh", "pr", "create", "--title", "manifest: bump to 0.2.0 to match the tag",
            "--body", "Bumps manifest.toml version to 0.2.0 to match tag v0.2.0."] in calls
    assert ["gh", "pr", "merge", "--squash", "--delete-branch"] in calls
    assert 'version = "0.2.0"' in (umbrella_dir / "manifest.toml").read_text()
    assert 'version = "0.2.0"' in (umbrella_dir / "pyproject.toml").read_text()
    assert 'name = "coxswain"\nversion = "0.2.0"' in (umbrella_dir / "uv.lock").read_text()
    assert fake_run.current_branch[str(umbrella_dir)] == "main"
    assert cli._checkout_ready(str(umbrella_dir), fake_run) == (True, "")


def test_release_execute_bump_pyproject_no_ops_and_skips_its_land_steps_when_already_at_the_target(tmp_path):
    # Simulates a rerun after a previous attempt already committed and merged
    # harness's bump: the plan still carries the full land sequence, but the
    # checkout's pyproject.toml already reads the target version.
    harness_dir = tmp_path / "harness"
    harness_dir.mkdir()
    (harness_dir / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.2.0.md").write_text("notes")
    calls, fake_run = _fake_git_run()
    steps = [
        {"kind": "bump_pyproject", "component": "harness", "repo": "org/harness", "branch": "release/0.2.0",
         "commit_subject": "pyproject: bump to 0.2.0 to match the tag", "from": "0.1.0", "to": "0.2.0"},
        {"kind": "push", "component": "harness", "branch": "release/0.2.0"},
        {"kind": "pr_create", "component": "harness", "title": "x", "body": "y"},
        {"kind": "wait_checks", "component": "harness"},
        {"kind": "merge", "component": "harness"},
        {"kind": "notes", "component": "notes", "path": "docs/releases/0.2.0.md"},
    ]
    rc = cli._release_execute(steps, "0.2.0", str(tmp_path), {}, str(umbrella_dir), fake_run,
                               {"components": {}}, str(umbrella_dir / "manifest.toml"))
    assert rc == 0
    assert calls == []


def test_cli_release_execute_runs_the_bump_pyproject_land_sequence_in_order_and_leaves_the_file_bumped(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_manifest_toml_at("0.2.0"))
    harness_dir = tmp_path / "harness"
    harness_dir.mkdir()
    (harness_dir / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.2.0.md").write_text("notes")
    calls, fake_run = _fake_git_run()
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    monkeypatch.setattr(cli, "_component_declares_tag_trigger", lambda directory: True)
    rc = cli.main(["dev", "release", "0.2.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    harness_calls = [c for c in calls if c[0] == "git" and c[2] == str(harness_dir)]
    kinds = [c[3] for c in harness_calls]
    # After `merge`, the executor switches the local checkout back to the
    # default branch and pulls it before the pre-existing `tag` step runs —
    # otherwise `tag` (which tags HEAD with no ref) would tag the stale
    # pre-squash commit still checked out on release/0.2.0.
    assert kinds == ["status", "rev-parse", "symbolic-ref", "checkout", "add", "commit", "push",
                      "symbolic-ref", "checkout", "pull", "tag", "push"]
    assert harness_calls[3] == release.checkout_branch_argv(str(harness_dir), "release/0.2.0")
    assert harness_calls[5] == release.commit_argv(
        str(harness_dir), "pyproject: bump to 0.2.0 to match the tag", "pyproject.toml")
    assert harness_calls[8] == release.checkout_ref_argv(str(harness_dir), "main")
    assert harness_calls[9] == release.pull_argv(str(harness_dir), "main")
    assert ["gh", "pr", "create", "--title", "pyproject: bump to 0.2.0 to match the tag",
            "--body", "Bumps harness's pyproject.toml version to 0.2.0 to match tag v0.2.0."] in calls
    assert ["gh", "pr", "merge", "--squash", "--delete-branch"] in calls
    assert (harness_dir / "pyproject.toml").read_text() == '[project]\nversion = "0.2.0"\n'
    # The checkout is back on main by the time `tag` runs (and stays there),
    # so a second release invocation's `_checkout_ready` on this same
    # directory would not be refused.
    assert fake_run.current_branch[str(harness_dir)] == "main"
    assert cli._checkout_ready(str(harness_dir), fake_run) == (True, "")


def test_release_execute_bump_pyproject_leaves_the_file_and_default_branch_untouched_when_checkout_fails(tmp_path):
    # A failed `checkout -b` must never leave the default branch dirty with
    # an uncommitted bump no branch owns — the version line is only ever
    # rewritten once the branch cut itself has succeeded.
    harness_dir = tmp_path / "harness"
    harness_dir.mkdir()
    original = '[project]\nversion = "0.1.0"\n'
    (harness_dir / "pyproject.toml").write_text(original)
    umbrella_dir = tmp_path / "coxswain"
    calls, fake_run = _fake_git_run(fail=(str(harness_dir), "checkout"))
    steps = [
        {"kind": "bump_pyproject", "component": "harness", "repo": "org/harness", "branch": "release/0.2.0",
         "commit_subject": "pyproject: bump to 0.2.0 to match the tag", "from": "0.1.0", "to": "0.2.0"},
    ]
    rc = cli._release_execute(steps, "0.2.0", str(tmp_path), {}, str(umbrella_dir), fake_run,
                               {"components": {}}, str(umbrella_dir / "manifest.toml"))
    assert rc == 2
    assert (harness_dir / "pyproject.toml").read_text() == original
    assert not any(c[0] == "git" and c[3] in ("add", "commit") for c in calls)


def test_release_execute_bump_manifest_leaves_the_files_untouched_when_checkout_fails(tmp_path):
    umbrella_dir = tmp_path / "coxswain"
    umbrella_dir.mkdir()
    manifest_path = umbrella_dir / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    (umbrella_dir / "pyproject.toml").write_text('[project]\nname = "coxswain"\nversion = "0.1.0"\n')
    (umbrella_dir / "uv.lock").write_text(
        '[[package]]\nname = "coxswain"\nversion = "0.1.0"\n')
    calls, fake_run = _fake_git_run(fail=(str(umbrella_dir), "checkout"))
    steps = [
        {"kind": "bump_manifest", "component": "manifest", "from": "0.1.0", "to": "0.2.0",
         "branch": "release/0.2.0", "commit_subject": "manifest: bump to 0.2.0 to match the tag"},
    ]
    rc = cli._release_execute(steps, "0.2.0", str(tmp_path), {}, str(umbrella_dir), fake_run,
                               {"components": {}}, str(manifest_path))
    assert rc == 2
    assert manifest_path.read_text() == _MANIFEST_TOML
    assert (umbrella_dir / "pyproject.toml").read_text() == '[project]\nname = "coxswain"\nversion = "0.1.0"\n'
    assert (umbrella_dir / "uv.lock").read_text() == '[[package]]\nname = "coxswain"\nversion = "0.1.0"\n'
    assert not any(c[0] == "git" and c[3] in ("add", "commit") for c in calls)


def test_cli_release_dry_run_still_prints_every_step_and_touches_no_file(tmp_path, capsys, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    rc = cli.main(["dev", "release", "0.2.0", "--dry-run", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "bump_manifest manifest: 0.1.0 -> 0.2.0" in out
    assert "tag_self coxswain: v0.2.0" in out
    assert not (tmp_path / "coxswain").exists()


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
         "detail": "versions: manifest.toml <-> cox/pyproject.toml — cox pyproject.toml is 0.1.0, "
                   "manifest wants 0.2.0 — bump it by hand"}
    ]


def test_gate_a_behind_pyproject_with_a_planned_bump_pyproject_passes_the_gate():
    drifts = [Drift("versions", "manifest.toml", None, "cartridges/pyproject.toml", None,
                     "cartridges pyproject.toml is 0.10.0, manifest wants 0.11.0 "
                     "(cox dev release 0.11.0 performs the bump)")]
    plan_steps = [{"kind": "bump_pyproject", "component": "cartridges", "to": "0.11.0"}]
    assert release.gate(drifts, None, plan_steps) == [
        {"kind": "note", "component": "versions",
         "detail": "versions: manifest.toml <-> cartridges/pyproject.toml — cartridges pyproject.toml is 0.10.0, "
                   "manifest wants 0.11.0 — the release bumps it"}
    ]


def test_gate_a_behind_pyproject_with_no_planned_bump_refuses():
    drifts = [Drift("versions", "manifest.toml", None, "cartridges/pyproject.toml", None,
                     "cartridges pyproject.toml is 0.10.0, manifest wants 0.11.0 "
                     "(cox dev release 0.11.0 performs the bump)")]
    assert release.gate(drifts, None, []) == [
        {"kind": "refuse", "component": "versions",
         "detail": "versions: manifest.toml <-> cartridges/pyproject.toml — cartridges pyproject.toml is 0.10.0, "
                   "manifest wants 0.11.0 — bump it by hand"}
    ]


def test_gate_a_manifest_one_version_behind_with_a_planned_bump_manifest_passes():
    drifts = [Drift("versions", "manifest.toml", None, "coxswain/pyproject.toml", None,
                     "umbrella pyproject.toml is 0.10.0, manifest wants 0.11.0 "
                     "(cox dev release 0.11.0 performs the bump)")]
    plan_steps = [{"kind": "bump_manifest", "component": "manifest", "from": "0.10.0", "to": "0.11.0"}]
    assert release.gate(drifts, None, plan_steps) == [
        {"kind": "note", "component": "versions",
         "detail": "versions: manifest.toml <-> coxswain/pyproject.toml — umbrella pyproject.toml is 0.10.0, "
                   "manifest wants 0.11.0 — the release bumps it"}
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


def test_extract_release_notes_returns_the_section_up_to_the_next_heading(tmp_path):
    notes = tmp_path / "0.2.0.md"
    notes.write_text("# Release 0.2.0\n\n## coxswain-harness\nharness notes\n\n## coxswain-cartridges\ncartridges notes\n")
    assert release.extract_release_notes(str(notes), "## coxswain-harness") == "## coxswain-harness\nharness notes\n\n"


def test_extract_release_notes_returns_none_for_a_missing_heading_or_file(tmp_path):
    notes = tmp_path / "0.2.0.md"
    notes.write_text("## coxswain-harness\nharness notes\n")
    assert release.extract_release_notes(str(notes), "## coxswain-crew") is None
    assert release.extract_release_notes(str(tmp_path / "missing.md"), "## coxswain-harness") is None


def test_github_release_argv_shapes():
    assert release.github_release_view_argv("v0.2.0") == ["gh", "release", "view", "v0.2.0"]
    assert release.github_release_create_argv("v0.2.0", "coxswain 0.2.0", "docs/releases/0.2.0.md") == [
        "gh", "release", "create", "v0.2.0", "--verify-tag", "--title", "coxswain 0.2.0",
        "--notes-file", "docs/releases/0.2.0.md"]
    assert release.github_release_edit_argv("v0.2.0", "docs/releases/0.2.0.md") == [
        "gh", "release", "edit", "v0.2.0", "--notes-file", "docs/releases/0.2.0.md"]


def test_umbrella_release_slug_prefers_the_manifests_own_repo_entry():
    manifest = {"coxswain": {"version": "0.1.0", "repo": "ppfenning/coxswain"}}
    assert release.umbrella_release_slug(manifest) == "ppfenning/coxswain"


def test_umbrella_release_slug_falls_back_to_the_tools_repository_url_minus_tools():
    manifest = {"coxswain": {"version": "0.1.0"}}
    assert release.umbrella_release_slug(manifest, "https://github.com/ppfenning/coxswain-tools") == "ppfenning/coxswain"


def test_umbrella_release_slug_is_none_with_no_manifest_repo_and_no_url_and_reads_no_file():
    """Neither source given: `None`, not a `pyproject.toml` read off disk —
    this pure function never touches the filesystem."""
    assert release.umbrella_release_slug({"coxswain": {"version": "0.1.0"}}) is None
    assert release.umbrella_release_slug({"coxswain": {"version": "0.1.0"}}, None) is None


def test_tools_repository_url_reads_installed_package_metadata_not_a_checkout_path():
    """No monkeypatch: proves the lookup survives being installed (editable
    counts), unlike a `pyproject.toml` path that only exists in a checkout."""
    assert cli._tools_repository_url() == _TOOLS_REPO_URL


def test_cli_release_dry_run_prints_the_umbrella_github_release_command(tmp_path, capsys, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_tools_repository_url", lambda: _TOOLS_REPO_URL)
    rc = cli.main(["dev", "release", "0.2.0", "--dry-run", "--manifest", str(manifest_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert ("github_release coxswain: ppfenning/coxswain -> gh release create v0.2.0 --verify-tag "
            "--title coxswain 0.2.0 --notes-file docs/releases/0.2.0.md") in out


def test_cli_release_execute_edits_an_existing_github_release_instead_of_creating_one(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("## coxswain-harness\nharness notes\n")
    calls, fake_run = _fake_git_run()

    def run(argv, cwd):
        if argv[:3] == ["gh", "release", "view"]:
            calls.append(argv)
            return (0, "")
        return fake_run(argv, cwd)
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    release_calls = [(c[2], c[3]) for c in calls if c[0] == "gh" and c[1] == "release"]
    assert release_calls == [("view", "v0.1.0"), ("edit", "v0.1.0")] * 3


def test_cli_release_execute_writes_a_components_section_and_link_line_to_its_notes_file(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text(
        "## coxswain-harness\nharness notes\n\n## coxswain-cartridges\ncartridges notes\n")
    calls, fake_run = _fake_git_run()
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", fake_run)
    monkeypatch.setattr(cli, "_tools_repository_url", lambda: _TOOLS_REPO_URL)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    create_calls = [c for c in calls if c[0] == "gh" and c[1] == "release" and c[2] == "create"]
    harness_create = next(c for c in create_calls if c[6] == "coxswain-harness 0.1.0")
    content = Path(harness_create[-1]).read_text()
    assert content.startswith("## coxswain-harness\nharness notes\n")
    assert "https://github.com/ppfenning/coxswain/releases/tag/v0.1.0" in content


def test_cli_release_execute_falls_back_to_unchanged_since_when_crews_section_is_absent(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML_WITH_PINNED)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("## coxswain-harness\nharness notes\n")
    calls, fake_run = _fake_git_run()

    def run(argv, cwd):
        return (0, "3\n") if argv[3] == "rev-list" and "--count" in argv else fake_run(argv, cwd)
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    create_calls = [c for c in calls if c[0] == "gh" and c[1] == "release" and c[2] == "create"]
    crew_create = next(c for c in create_calls if c[6] == "coxswain-crew 0.1.0")
    assert Path(crew_create[-1]).read_text().startswith("unchanged since v0.6.0")


def test_cli_release_execute_names_the_previous_release_tag_not_the_one_being_cut(tmp_path, monkeypatch):
    manifest_v1 = _MANIFEST_TOML
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML.replace('version = "0.1.0"', 'version = "0.2.0"').replace(
        'tag = "v0.1.0"', 'tag = "v0.2.0"'))
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("## coxswain-harness\nharness notes\n")
    (umbrella_dir / "docs" / "releases" / "0.2.0.md").write_text("## coxswain-harness\nharness notes v2\n")
    calls, fake_run = _fake_git_run()

    def run(argv, cwd):
        if argv[0] == "git" and argv[3] == "show":
            return (0, manifest_v1)
        return (0, "3\n") if argv[3] == "rev-list" and "--count" in argv else fake_run(argv, cwd)
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "release", "0.2.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    create_calls = [c for c in calls if c[0] == "gh" and c[1] == "release" and c[2] == "create"]
    cartridges_create = next(c for c in create_calls if c[6] == "coxswain-cartridges 0.2.0")
    assert Path(cartridges_create[-1]).read_text().startswith("unchanged since v0.1.0")


def test_cli_release_execute_names_first_release_when_no_earlier_release_notes_exist(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text(_MANIFEST_TOML_WITH_PINNED)
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("## coxswain-harness\nharness notes\n")
    calls, fake_run = _fake_git_run()

    def run(argv, cwd):
        return (0, "3\n") if argv[3] == "rev-list" and "--count" in argv else fake_run(argv, cwd)
    monkeypatch.setattr(cli, "_remote_tags", lambda repo: [])
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "release", "0.1.0", "--manifest", str(manifest_path), "--root", str(tmp_path)])
    assert rc == 0
    create_calls = [c for c in calls if c[0] == "gh" and c[1] == "release" and c[2] == "create"]
    cartridges_create = next(c for c in create_calls if c[6] == "coxswain-cartridges 0.1.0")
    body = Path(cartridges_create[-1]).read_text()
    assert body.startswith("first release")
    assert "unchanged since" not in body


_BACKFILL_MANIFEST = """
[coxswain]
version = "0.1.0"
repo = "org/coxswain"

[components.harness]
repo = "org/harness"
tag = "v0.1.0"
"""

_BACKFILL_HARNESS_BODY = ("## coxswain-harness\nharness notes\n"
                           "\nSee the full release notes: https://github.com/org/coxswain/releases/tag/v0.1.0\n")


def _backfill_umbrella(tmp_path, manifest_toml=_BACKFILL_MANIFEST, notes="## coxswain-harness\nharness notes\n"):
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text(notes)
    for name in ("harness", "crew", "cartridges", "graphs", "tools", "route", "cox"):
        (tmp_path / name).mkdir(exist_ok=True)  # a component with no checkout is skipped, so give them one
    return umbrella_dir, {"0.1.0": manifest_toml}


def test_backfill_skips_a_component_with_no_checkout_instead_of_crashing(tmp_path, monkeypatch, capsys):
    """The 0.2.0 manifest names `hud`, which has no checkout here: the first live
    backfill died on it with FileNotFoundError before reaching later versions."""
    _backfill_umbrella(tmp_path, manifest_toml=_BACKFILL_MANIFEST + '\n[components.hud]\nrepo = "org/hud"\ntag = "v0.1.0"\n')
    calls, run = _fake_backfill_run({"0.1.0": _BACKFILL_MANIFEST + '\n[components.hud]\nrepo = "org/hud"\ntag = "v0.1.0"\n'})
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped org/hud v0.1.0: no checkout at" in out
    assert "would create org/harness v0.1.0" in out
    assert not [c for c in calls if c[:3] == ["gh", "release", "view"] and c[3] == "v0.1.0" and "hud" in " ".join(c)]


def _fake_backfill_run(manifest_by_version, view_bodies=None):
    calls: list = []
    view_bodies = view_bodies or {}

    def run(argv, cwd):
        calls.append(argv)
        if argv[0] == "git" and argv[3] == "show":
            version = argv[4].split(":")[0][1:]
            return (0, manifest_by_version[version])
        if argv[:3] == ["gh", "release", "view"]:
            key = (cwd, argv[3])
            return (0, view_bodies[key]) if key in view_bodies else (1, "release not found")
        return (0, "")
    return calls, run


def test_backfill_prints_created_when_no_release_exists_for_a_tag(tmp_path, monkeypatch):
    _backfill_umbrella(tmp_path)
    calls, run = _fake_backfill_run({"0.1.0": _BACKFILL_MANIFEST})
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path)])
    assert rc == 0
    create_calls = [c for c in calls if c[:3] == ["gh", "release", "create"]]
    assert {c[3] for c in create_calls} == {"v0.1.0"}


def test_backfill_prints_created_edited_and_already_current(tmp_path, monkeypatch, capsys):
    umbrella_dir, _ = _backfill_umbrella(tmp_path)
    harness_dir = str(tmp_path / "harness")
    calls, run = _fake_backfill_run(
        {"0.1.0": _BACKFILL_MANIFEST},
        view_bodies={(harness_dir, "v0.1.0"): _BACKFILL_HARNESS_BODY,
                      (str(umbrella_dir), "v0.1.0"): "## coxswain-harness\nharness notes\n"})
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "already-current org/harness v0.1.0" in out
    assert "already-current org/coxswain v0.1.0" in out


def test_backfill_prints_edited_when_the_release_body_has_drifted(tmp_path, monkeypatch, capsys):
    _backfill_umbrella(tmp_path)
    harness_dir = str(tmp_path / "harness")
    calls, run = _fake_backfill_run(
        {"0.1.0": _BACKFILL_MANIFEST},
        view_bodies={(harness_dir, "v0.1.0"): "a stale body\n"})
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "edited org/harness v0.1.0" in out
    edit_calls = [c for c in calls if c[:3] == ["gh", "release", "edit"]]
    assert {c[3] for c in edit_calls} == {"v0.1.0"}


def test_backfill_created_body_ends_with_the_link_sentence(tmp_path, monkeypatch):
    _backfill_umbrella(tmp_path)
    calls, run = _fake_backfill_run({"0.1.0": _BACKFILL_MANIFEST})
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path)])
    assert rc == 0
    harness_create = next(c for c in calls if c[:3] == ["gh", "release", "create"] and c[6] == "coxswain-harness 0.1.0")
    assert Path(harness_create[-1]).read_text().endswith(
        "See the full release notes: https://github.com/org/coxswain/releases/tag/v0.1.0\n")


def test_backfill_already_current_reads_the_body_through_json_body_not_plain_view(tmp_path, monkeypatch):
    _backfill_umbrella(tmp_path)
    harness_dir = str(tmp_path / "harness")
    calls, run = _fake_backfill_run(
        {"0.1.0": _BACKFILL_MANIFEST},
        view_bodies={(harness_dir, "v0.1.0"): _BACKFILL_HARNESS_BODY})
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path)])
    assert rc == 0
    view_calls = [c for c in calls if c[:3] == ["gh", "release", "view"]]
    assert view_calls and all(c[-4:] == ["--json", "body", "-q", ".body"] for c in view_calls)


_BACKFILL_MANIFEST_PINNED_AT_0_4_0 = """
[coxswain]
version = "0.4.0"
repo = "org/coxswain"

[components.harness]
repo = "org/harness"
tag = "v0.4.0"

[components.crew]
repo = "org/crew"
tag = "v0.4.0"
lockstep = false
"""

_BACKFILL_MANIFEST_PINNED_STILL_AT_0_6_0 = """
[coxswain]
version = "0.6.0"
repo = "org/coxswain"

[components.harness]
repo = "org/harness"
tag = "v0.6.0"

[components.crew]
repo = "org/crew"
tag = "v0.4.0"
lockstep = false
"""


def test_backfill_a_pinned_component_is_only_processed_at_the_version_it_was_tagged(tmp_path, monkeypatch):
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    for name in ("harness", "crew"):
        (tmp_path / name).mkdir(exist_ok=True)
    (umbrella_dir / "docs" / "releases" / "0.4.0.md").write_text(
        "## coxswain-harness\nharness notes\n\n## coxswain-crew\ncrew notes\n")
    (umbrella_dir / "docs" / "releases" / "0.6.0.md").write_text("## coxswain-harness\nharness notes v6\n")
    calls, run = _fake_backfill_run({"0.4.0": _BACKFILL_MANIFEST_PINNED_AT_0_4_0,
                                      "0.6.0": _BACKFILL_MANIFEST_PINNED_STILL_AT_0_6_0})
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path)])
    assert rc == 0
    create_calls = [c for c in calls if c[:3] == ["gh", "release", "create"]]
    crew_creates = [c for c in create_calls if c[6].startswith("coxswain-crew")]
    assert [c[6] for c in crew_creates] == ["coxswain-crew 0.4.0"]
    crew_body = Path(crew_creates[0][-1]).read_text()
    assert crew_body.startswith("## coxswain-crew\ncrew notes\n")
    assert crew_body.endswith("https://github.com/org/coxswain/releases/tag/v0.4.0\n")
    harness_titles = {c[6] for c in create_calls if c[6].startswith("coxswain-harness")}
    assert harness_titles == {"coxswain-harness 0.4.0", "coxswain-harness 0.6.0"}


def test_backfill_unchanged_since_fallback_names_the_prior_tag_not_the_one_being_created(tmp_path, monkeypatch):
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    for name in ("harness", "crew"):
        (tmp_path / name).mkdir(exist_ok=True)
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("## coxswain-harness\nharness notes\n")
    (umbrella_dir / "docs" / "releases" / "0.2.0.md").write_text("no component sections this cut\n")
    manifest_v2 = _BACKFILL_MANIFEST.replace('version = "0.1.0"', 'version = "0.2.0"').replace(
        'tag = "v0.1.0"', 'tag = "v0.2.0"')
    calls, run = _fake_backfill_run({"0.1.0": _BACKFILL_MANIFEST, "0.2.0": manifest_v2})
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path)])
    assert rc == 0
    create_calls = [c for c in calls if c[:3] == ["gh", "release", "create"]]
    harness_v2 = next(c for c in create_calls if c[6] == "coxswain-harness 0.2.0")
    assert Path(harness_v2[-1]).read_text().startswith("unchanged since v0.1.0")


def test_backfill_a_failed_create_call_prints_failed_and_stops(tmp_path, monkeypatch, capsys):
    _backfill_umbrella(tmp_path)
    _, base_run = _fake_backfill_run({"0.1.0": _BACKFILL_MANIFEST})

    def run(argv, cwd):
        if argv[:3] == ["gh", "release", "create"]:
            return (1, "boom")
        return base_run(argv, cwd)
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "FAILED org/coxswain v0.1.0: boom" in out
    assert "org/harness" not in out


def test_backfill_dry_run_prints_would_and_makes_no_create_or_edit_calls(tmp_path, monkeypatch, capsys):
    _backfill_umbrella(tmp_path)
    calls, run = _fake_backfill_run({"0.1.0": _BACKFILL_MANIFEST})
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "would create org/harness v0.1.0" in out
    assert "would create org/coxswain v0.1.0" in out
    write_calls = [c for c in calls if c[:3] in (["gh", "release", "create"], ["gh", "release", "edit"])]
    assert write_calls == []


def test_backfill_processes_multiple_versions_oldest_first(tmp_path, monkeypatch, capsys):
    umbrella_dir = tmp_path / "coxswain"
    (umbrella_dir / "docs" / "releases").mkdir(parents=True)
    for name in ("harness", "crew"):
        (tmp_path / name).mkdir(exist_ok=True)
    (umbrella_dir / "docs" / "releases" / "0.2.0.md").write_text("## coxswain-harness\nharness notes v2\n")
    (umbrella_dir / "docs" / "releases" / "0.1.0.md").write_text("## coxswain-harness\nharness notes v1\n")
    calls, run = _fake_backfill_run({"0.1.0": _BACKFILL_MANIFEST, "0.2.0": _BACKFILL_MANIFEST})
    monkeypatch.setattr(cli, "_real_run", run)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    lines = [line for line in out.splitlines() if line.startswith("created")]
    assert lines.index("created org/coxswain v0.1.0") < lines.index("created org/coxswain v0.2.0")
    assert lines.index("created org/harness v0.1.0") < lines.index("created org/harness v0.2.0")


_BACKFILL_MANIFEST_NO_UMBRELLA_REPO = """
[coxswain]
version = "0.1.0"

[components.harness]
repo = "org/harness"
tag = "v0.1.0"
"""


def test_backfill_umbrella_repo_matches_umbrella_release_slugs_own_fallback(tmp_path, monkeypatch, capsys):
    _backfill_umbrella(tmp_path, manifest_toml=_BACKFILL_MANIFEST_NO_UMBRELLA_REPO)
    calls, run = _fake_backfill_run({"0.1.0": _BACKFILL_MANIFEST_NO_UMBRELLA_REPO})
    monkeypatch.setattr(cli, "_real_run", run)
    monkeypatch.setattr(cli, "_tools_repository_url", lambda: _TOOLS_REPO_URL)
    rc = cli.main(["dev", "backfill-github-releases", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    expected_slug = release.umbrella_release_slug(tomllib.loads(_BACKFILL_MANIFEST_NO_UMBRELLA_REPO), _TOOLS_REPO_URL)
    assert f"created {expected_slug} v0.1.0" in out
