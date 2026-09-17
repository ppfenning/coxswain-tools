from agent_tools.release_check import Drift
from agent_tools.release_check_index import check_release_index, gather_release_index_facts, index_section


def test_index_section_renders_the_pages_table_shape():
    out = index_section("0.2.0", {"cox": "v0.2.0", "route": "v0.2.0"}, {"cox": {"repo": "o/cox", "required": True, "provides": "cox"}})
    assert out.startswith("## `0.2.0`\n\n| Component | Repository or path | Tag | Required or flag |")
    assert "| cox | `o/cox` | `v0.2.0` | required, provides `cox` |" in out
    assert out.endswith("See the [0.2.0 release notes](0.2.0.md) for what landed in each component.")


def test_check_release_index_has_no_drift_when_the_section_is_present_verbatim():
    section = index_section("0.2.0", {"cox": "v0.2.0"})
    facts = {
        "release_versions": {"0.2.0"},
        "releases_index": f"{section}\n",
        "manifest": {"components": {"cox": {"tag": "v0.2.0"}}},
    }
    assert check_release_index(facts) == []


def test_check_release_index_flags_a_version_missing_its_section_entirely():
    facts = {
        "release_versions": {"0.2.0"},
        "releases_index": "nothing about any release here\n",
        "manifest": {"components": {"cox": {"tag": "v0.2.0"}}},
    }
    assert check_release_index(facts) == [
        Drift("release_index", "docs/releases/index.md", None,
              "docs/releases/index.md", None, "add its section for 0.2.0"),
    ]


def test_check_release_index_flags_a_section_missing_one_components_line():
    facts = {
        "release_versions": {"0.2.0"},
        "releases_index": "## `0.2.0`\n\n| cox | `o/cox` | `v0.2.0` | required |\n",
        "manifest": {"components": {"cox": {"tag": "v0.2.0"}, "route": {"tag": "v0.2.0"}}},
    }
    assert check_release_index(facts) == [
        Drift("release_index", "docs/releases/index.md", None,
              "docs/releases/index.md", None, "add its section for 0.2.0"),
    ]


def test_gather_release_index_facts_reads_versions_from_page_filenames_and_the_index_text(tmp_path):
    releases = tmp_path / "docs" / "releases"
    releases.mkdir(parents=True)
    (releases / "0.2.0.md").write_text("cox landed")
    (releases / "index.md").write_text("## `0.2.0`\n\n| cox | x | `v0.2.0` | required |\n")
    facts = gather_release_index_facts(str(tmp_path))
    assert facts == {
        "release_versions": {"0.2.0"},
        "releases_index": "## `0.2.0`\n\n| cox | x | `v0.2.0` | required |\n",
        "releases_index_path": str(releases / "index.md"),
    }


def test_gather_release_index_facts_with_no_releases_directory_yields_empty_facts(tmp_path):
    facts = gather_release_index_facts(str(tmp_path))
    assert facts == {
        "release_versions": set(),
        "releases_index": "",
        "releases_index_path": str(tmp_path / "docs" / "releases" / "index.md"),
    }
