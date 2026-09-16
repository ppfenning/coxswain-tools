from pathlib import Path

from agent_tools.release_check import Drift, facts_plan
from agent_tools.release_check_manifest import check_manifest, gather_manifest_facts, versions_in


def test_versions_in_finds_every_v_prefixed_semver_token():
    assert versions_in("see v0.1.0-beta.1 and v0.2.0 here") == {"v0.1.0-beta.1", "v0.2.0"}


def test_check_manifest_flags_a_component_with_no_docs_page():
    facts = {
        "manifest": {"coxswain": {"version": "0.2.0"}, "components": {"cox": {"tag": "v0.2.0"}}},
        "manifest_path": "/repo/manifest.toml",
        "component_docs": {"cox": "/repo/coxswain/docs/components/cox.md"},
        "release_notes": "/repo/coxswain/docs/releases/0.2.0.md",
        "component_pages": {},
        "notes_page": "cox landed in this release",
    }
    assert check_manifest(facts) == [
        Drift(
            "manifest", "/repo/manifest.toml", None,
            "/repo/coxswain/docs/components/cox.md", None,
            "add /repo/coxswain/docs/components/cox.md for cox",
        ),
    ]


def test_check_manifest_flags_a_page_that_cites_the_wrong_version():
    facts = {
        "manifest": {"coxswain": {"version": "0.2.0"}, "components": {"cox": {"tag": "v0.2.0"}}},
        "manifest_path": "/repo/manifest.toml",
        "component_docs": {"cox": "/repo/coxswain/docs/components/cox.md"},
        "release_notes": "/repo/coxswain/docs/releases/0.2.0.md",
        "component_pages": {"cox": "cox is at v0.1.0"},
        "notes_page": "cox landed in this release",
    }
    assert check_manifest(facts) == [
        Drift(
            "manifest", "/repo/manifest.toml", None,
            "/repo/coxswain/docs/components/cox.md", 1,
            "update /repo/coxswain/docs/components/cox.md to v0.2.0",
        ),
    ]


def test_check_manifest_compares_a_page_against_its_own_component_tag_not_the_umbrella_version():
    facts = {
        "manifest": {"coxswain": {"version": "0.2.0"}, "components": {"cox": {"tag": "v0.1.4"}}},
        "manifest_path": "/repo/manifest.toml",
        "component_docs": {"cox": "/repo/coxswain/docs/components/cox.md"},
        "release_notes": "/repo/coxswain/docs/releases/0.2.0.md",
        "component_pages": {"cox": "cox is at v0.1.4"},
        "notes_page": "cox landed in this release",
    }
    assert check_manifest(facts) == []


def test_check_manifest_flags_a_component_absent_from_the_release_notes():
    facts = {
        "manifest": {"coxswain": {"version": "0.2.0"}, "components": {"cox": {"tag": "v0.2.0"}}},
        "manifest_path": "/repo/manifest.toml",
        "component_docs": {"cox": "/repo/coxswain/docs/components/cox.md"},
        "release_notes": "/repo/coxswain/docs/releases/0.2.0.md",
        "component_pages": {"cox": "cox is at v0.2.0"},
        "notes_page": "nothing about it here",
    }
    assert check_manifest(facts) == [
        Drift(
            "manifest", "/repo/manifest.toml", None,
            "/repo/coxswain/docs/releases/0.2.0.md", None,
            "mention cox in /repo/coxswain/docs/releases/0.2.0.md",
        ),
    ]


def test_check_manifest_flags_an_absent_release_notes_page_once_not_per_component():
    facts = {
        "manifest": {
            "coxswain": {"version": "0.2.0"},
            "components": {"cox": {"tag": "v0.2.0"}, "route": {"tag": "v0.2.0"}},
        },
        "manifest_path": "/repo/manifest.toml",
        "component_docs": {
            "cox": "/repo/coxswain/docs/components/cox.md",
            "route": "/repo/coxswain/docs/components/route.md",
        },
        "release_notes": "/repo/coxswain/docs/releases/0.2.0.md",
        "component_pages": {
            "cox": "cox is at v0.2.0",
            "route": "route is at v0.2.0",
        },
        "notes_page": None,
    }
    assert check_manifest(facts) == [
        Drift(
            "manifest", "/repo/manifest.toml", None,
            "/repo/coxswain/docs/releases/0.2.0.md", None,
            "add /repo/coxswain/docs/releases/0.2.0.md",
        ),
    ]


def test_check_manifest_with_agreeing_facts_has_no_drift():
    facts = {
        "manifest": {"coxswain": {"version": "0.2.0"}, "components": {"cox": {"tag": "v0.2.0"}}},
        "manifest_path": "/repo/manifest.toml",
        "component_docs": {"cox": "/repo/coxswain/docs/components/cox.md"},
        "release_notes": "/repo/coxswain/docs/releases/0.2.0.md",
        "component_pages": {"cox": "cox is at v0.2.0"},
        "notes_page": "cox landed in this release",
    }
    assert check_manifest(facts) == []


def test_check_manifest_does_not_read_lockstep_a_pages_own_tag_agreeing_with_it_is_not_a_drift():
    """`check_manifest` never reads `spec["lockstep"]`; a `lockstep = False`
    component's page is still compared only against the component's own
    manifest `tag`, so a tag that differs from `coxswain.version` is not
    itself a drift — this is pre-existing tag-vs-page behavior, unchanged by
    the `lockstep` key's presence."""
    facts = {
        "manifest": {"coxswain": {"version": "0.6.0"},
                     "components": {"crew": {"tag": "v0.4.0", "lockstep": False}}},
        "manifest_path": "/repo/manifest.toml",
        "component_docs": {"crew": "/repo/coxswain/docs/components/crew.md"},
        "release_notes": "/repo/coxswain/docs/releases/0.6.0.md",
        "component_pages": {"crew": "crew is at v0.4.0"},
        "notes_page": "crew landed in this release",
    }
    assert check_manifest(facts) == []


def test_check_manifest_flags_a_stale_page_the_same_way_whether_or_not_lockstep_is_set():
    """A page that disagrees with its component's own manifest `tag` is a
    drift regardless of `lockstep`; the flag plays no part in this
    comparison, since `check_manifest` does not compare a component's tag
    against `coxswain.version` at all — only `check_versions`
    (`agent_tools/release_check.py`) touches `coxswain.version`."""
    facts = {
        "manifest": {"coxswain": {"version": "0.6.0"},
                     "components": {"cox": {"tag": "v0.6.0", "lockstep": True}}},
        "manifest_path": "/repo/manifest.toml",
        "component_docs": {"cox": "/repo/coxswain/docs/components/cox.md"},
        "release_notes": "/repo/coxswain/docs/releases/0.6.0.md",
        "component_pages": {"cox": "cox is at v0.4.0"},
        "notes_page": "cox landed in this release",
    }
    assert check_manifest(facts) == [
        Drift(
            "manifest", "/repo/manifest.toml", None,
            "/repo/coxswain/docs/components/cox.md", 1,
            "update /repo/coxswain/docs/components/cox.md to v0.6.0",
        ),
    ]


def test_gather_manifest_facts_reads_the_pages_and_notes_named_by_facts_plan(tmp_path):
    manifest = {"coxswain": {"version": "0.2.0"}, "components": {"cox": {"tag": "v0.2.0"}}}
    manifest_path = tmp_path / "manifest.toml"
    paths = facts_plan(str(tmp_path), manifest)
    Path(paths["component_docs"]["cox"]).parent.mkdir(parents=True)
    Path(paths["component_docs"]["cox"]).write_text("cox is at v0.2.0")
    Path(paths["release_notes"]).parent.mkdir(parents=True)
    Path(paths["release_notes"]).write_text("cox landed")
    facts = gather_manifest_facts(manifest, str(manifest_path), paths["component_docs"], paths["release_notes"])
    assert facts == {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "component_pages": {"cox": "cox is at v0.2.0"},
        "notes_page": "cox landed",
    }


def test_versions_in_is_empty_without_a_version_token():
    assert versions_in("no versions here") == set()


def test_check_manifest_does_not_drift_on_a_page_with_no_version_string():
    facts = {
        "manifest": {"coxswain": {"version": "0.2.0"}, "components": {"cox": {"tag": "v0.2.0"}}},
        "manifest_path": "/repo/manifest.toml",
        "component_docs": {"cox": "/repo/coxswain/docs/components/cox.md"},
        "release_notes": "/repo/coxswain/docs/releases/0.2.0.md",
        "component_pages": {"cox": "cox is a rowing coxswain agent, no version mentioned here"},
        "notes_page": "cox landed in this release",
    }
    assert check_manifest(facts) == []


def test_check_manifest_flags_a_page_whose_stated_version_disagrees_with_its_own_component_tag():
    facts = {
        "manifest": {"coxswain": {"version": "0.6.0"}, "components": {"cox": {"tag": "v0.4.0"}}},
        "manifest_path": "/repo/manifest.toml",
        "component_docs": {"cox": "/repo/coxswain/docs/components/cox.md"},
        "release_notes": "/repo/coxswain/docs/releases/0.6.0.md",
        "component_pages": {"cox": "cox is at v0.6.0"},
        "notes_page": "cox landed in this release",
    }
    assert check_manifest(facts) == [
        Drift(
            "manifest", "/repo/manifest.toml", None,
            "/repo/coxswain/docs/components/cox.md", 1,
            "update /repo/coxswain/docs/components/cox.md to v0.4.0",
        ),
    ]


def test_check_manifest_mirroring_the_umbrella_layout_has_zero_drift(tmp_path):
    manifest = {"coxswain": {"version": "0.2.0"}, "components": {"cox": {"tag": "v0.2.0"}}}
    manifest_path = tmp_path / "manifest.toml"
    plan = facts_plan(str(tmp_path), manifest)
    cox_page = Path(plan["component_docs"]["cox"])
    cox_page.parent.mkdir(parents=True)
    cox_page.write_text("cox is a rowing coxswain agent")
    notes = Path(plan["release_notes"])
    notes.parent.mkdir(parents=True)
    notes.write_text("cox landed in this release")
    facts = {
        **plan,
        **gather_manifest_facts(manifest, str(manifest_path), plan["component_docs"], plan["release_notes"]),
    }
    assert check_manifest(facts) == []
