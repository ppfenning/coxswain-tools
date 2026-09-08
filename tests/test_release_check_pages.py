from agent_tools.release_check_pages import (
    alias_sentences,
    check_pages,
    description_ok,
    readme_h1,
)

GOOD_PKG_INFO = "Metadata-Version: 2.1\nDescription-Content-Type: text/markdown\n\n# cox\n\nBody.\n"


def _package(**overrides) -> dict:
    base = {
        "name": "cox",
        "pyproject": {"project": {"readme": "README.md", "description": "One sentence under 160 characters."}},
        "readme": "# cox\n\nSome body.\n",
        "pkg_info": GOOD_PKG_INFO,
    }
    return {**base, **overrides}


def test_readme_h1_returns_the_first_h1_line():
    assert readme_h1("# cox\n\nbody\n") == "cox"


def test_readme_h1_is_none_with_no_h1():
    assert readme_h1("just a body\n") is None


def test_alias_sentences_finds_the_sentence_containing_the_alias():
    text = "First sentence. agent-tools is kept as an alias for one release. Last one."
    assert alias_sentences(text, ("agent-tools", "cast")) == [(1, "agent-tools is kept as an alias for one release.")]


def test_alias_sentences_is_empty_with_no_alias():
    assert alias_sentences("Nothing to see here.", ("agent-tools", "cast")) == []


def test_description_ok_is_true_for_one_short_sentence():
    assert description_ok("One sentence under 160 characters.") is True


def test_description_ok_is_false_over_160_characters():
    assert description_ok("x" * 161 + ".") is False


def test_no_drift_when_the_package_facts_agree():
    assert check_pages({"packages": [_package()]}) == []


def test_missing_readme_field_in_pyproject_is_a_drift():
    pkg = _package(pyproject={"project": {"description": "One sentence under 160 characters."}})
    drifts = check_pages({"packages": [pkg]})
    assert any(d.correction == "add readme to [project] in cox/pyproject.toml" for d in drifts)


def test_pkg_info_missing_a_markdown_description_is_a_drift():
    pkg = _package(pkg_info="Metadata-Version: 2.1\n\n")
    drifts = check_pages({"packages": [pkg]})
    assert any(d.b_file == "cox/PKG-INFO" for d in drifts)


def test_readme_h1_not_matching_the_component_name_is_a_drift():
    pkg = _package(readme="# agent-tools\n\nbody\n")
    drifts = check_pages({"packages": [pkg]})
    assert any(d.correction == "set the README H1 to cox" for d in drifts)


def test_alias_in_description_with_no_deprecation_marker_is_a_drift():
    pkg = _package(pyproject={"project": {"readme": "README.md", "description": "agent-tools is kept as an alias for one release."}})
    drifts = check_pages({"packages": [pkg]})
    assert any(d.correction == "state the alias is deprecated in description or drop it" for d in drifts)


def test_description_too_long_is_a_drift():
    pkg = _package(pyproject={"project": {"readme": "README.md", "description": "x" * 200}})
    drifts = check_pages({"packages": [pkg]})
    assert any(d.correction == "make description one sentence under 160 characters" for d in drifts)
