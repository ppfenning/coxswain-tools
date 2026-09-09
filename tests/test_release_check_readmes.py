from pathlib import Path

from agent_tools.release_check import Drift, facts_plan
from agent_tools.release_check_readmes import check_readmes, gather_readmes_facts, resolve_docs_base


def test_a_readme_naming_a_dead_repo_drifts_with_its_line_and_the_rename_correction():
    facts = {
        "readmes": {"graphs": "graphs used to be called agent-graphs"},
        "repo_names": {"org/coxswain-graphs"},
        "docs_base": "latest",
    }
    assert check_readmes(facts) == [
        Drift("readmes", "graphs/README.md", 1, "graphs/README.md", 1, "rename agent-graphs to coxswain-graphs"),
    ]


def test_the_same_dead_name_inside_an_alias_sentence_does_not_drift():
    facts = {
        "readmes": {"graphs": "This project was formerly agent-graphs, an alias kept for search."},
        "repo_names": {"org/coxswain-graphs"},
        "docs_base": "latest",
    }
    assert check_readmes(facts) == []


def test_a_plain_mention_after_an_aliased_one_still_drifts_at_its_own_line():
    text = (
        "This repo was formerly agent-graphs, an alias kept for history.\n"
        "Nothing to see here.\n"
        "But agent-graphs still appears in this line too.\n"
    )
    facts = {
        "readmes": {"graphs": text},
        "repo_names": {"org/coxswain-graphs"},
        "docs_base": "latest",
    }
    assert check_readmes(facts) == [
        Drift("readmes", "graphs/README.md", 3, "graphs/README.md", 3, "rename agent-graphs to coxswain-graphs"),
    ]


def test_two_dead_names_on_one_line_are_judged_separately():
    text = "agent-cast is the old name. Formerly agent-graphs, an alias kept for continuity."
    facts = {
        "readmes": {"mixed": text},
        "repo_names": {"org/coxswain-cast", "org/coxswain-graphs"},
        "docs_base": "latest",
    }
    assert check_readmes(facts) == [
        Drift("readmes", "mixed/README.md", 1, "mixed/README.md", 1, "rename agent-cast to coxswain-cast"),
    ]


def test_a_doc_link_missing_the_version_segment_drifts():
    facts = {
        "readmes": {"graphs": "See https://ppfenning.github.io/coxswain/graphs/ for details."},
        "repo_names": set(),
        "docs_base": "latest",
    }
    assert check_readmes(facts) == [
        Drift("readmes", "graphs/README.md", 1, "graphs/README.md", 1, "add the version segment"),
    ]


def test_a_doc_link_carrying_the_version_segment_does_not_drift():
    facts = {
        "readmes": {"graphs": "See https://ppfenning.github.io/coxswain/latest/graphs/ for details."},
        "repo_names": set(),
        "docs_base": "latest",
    }
    assert check_readmes(facts) == []


def test_a_repo_not_yet_renamed_does_not_drift_on_its_own_current_name():
    facts = {
        "readmes": {"foo": "this is agent-foo, a handy tool."},
        "repo_names": {"org/agent-foo"},
        "docs_base": "latest",
    }
    assert check_readmes(facts) == []


def test_a_clean_readme_yields_no_drift():
    facts = {
        "readmes": {"graphs": "# graphs\n\ngraphs is coxswain-graphs, part of coxswain."},
        "repo_names": {"org/coxswain-graphs"},
        "docs_base": "latest",
    }
    assert check_readmes(facts) == []


def test_gather_readmes_facts_reads_the_readmes_named_by_facts_plan(tmp_path):
    manifest = {"coxswain": {"version": "0.2.0"}, "components": {"cox": {"repo": "org/coxswain-cox"}}}
    paths = facts_plan(str(tmp_path), manifest)
    Path(paths["readmes"]["cox"]).parent.mkdir(parents=True)
    Path(paths["readmes"]["cox"]).write_text("cox is coxswain-cox")
    facts = gather_readmes_facts(paths["readmes"], manifest, "latest")
    assert facts == {
        "readmes": {"cox": "cox is coxswain-cox"},
        "repo_names": {"org/coxswain-cox"},
        "docs_base": "latest",
    }


def test_resolve_docs_base_reads_the_version_segment_from_site_url(tmp_path):
    mkdocs_path = tmp_path / "mkdocs.yml"
    mkdocs_path.write_text("site_name: coxswain\nsite_url: https://ppfenning.github.io/coxswain/0.3/\n")
    assert resolve_docs_base(str(mkdocs_path)) == "0.3"


def test_resolve_docs_base_defaults_to_latest_when_mkdocs_yml_is_absent(tmp_path):
    assert resolve_docs_base(str(tmp_path / "mkdocs.yml")) == "latest"
