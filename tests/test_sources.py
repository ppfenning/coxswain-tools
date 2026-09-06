from agent_tools import sources


def test_source_config_reads_the_named_block():
    profile = {
        "sources": {
            "github": {"repos": ["a/b"], "filter": "label:intake", "token_env": "GH_TOKEN"}
        }
    }
    assert sources.source_config(profile, "github") == sources.SourceConfig(
        repos=("a/b",), filter="label:intake", token_env="GH_TOKEN"
    )


def test_source_config_is_none_when_the_name_is_absent():
    assert sources.source_config({"sources": {}}, "github") is None


def test_intake_links_collects_link_from_frontmatter():
    files = {"a.md": "---\nlink: https://x/1\n---\nbody"}
    assert sources.intake_links(files) == frozenset({"https://x/1"})


def test_intake_links_skips_a_file_with_no_link_field():
    files = {"a.md": "---\ntitle: x\n---\nbody"}
    assert sources.intake_links(files) == frozenset()


def test_adapter_for_is_none_for_a_name_with_no_module():
    assert sources.adapter_for("no-such-source") is None


def test_fake_adapter_candidates_filters_by_config_repos():
    listing = [{"link": "l1", "repo": "a/b"}, {"link": "l2", "repo": "c/d"}]
    adapter = sources.FakeAdapter(listing)
    config = sources.SourceConfig(repos=("a/b",), filter="", token_env="")
    assert adapter.candidates(config, listing) == (sources.Ref(link="l1", repo="a/b"),)


def test_fake_adapter_read_returns_a_candidate():
    adapter = sources.FakeAdapter([])
    raw = {"title": "t", "body": "b", "repo": "a/b", "link": "l1"}
    assert adapter.read(raw) == sources.Candidate(title="t", body="b", repo="a/b", link="l1")


def test_fake_adapter_taken_checks_membership():
    adapter = sources.FakeAdapter([])
    assert adapter.taken("l1", frozenset({"l1"})) is True
    assert adapter.taken("l2", frozenset({"l1"})) is False


def test_fake_adapter_mark_argv_is_a_plain_list():
    adapter = sources.FakeAdapter([])
    ref = sources.Ref(link="l1", repo="a/b")
    assert adapter.mark_argv(ref, "work/intake/x.md") == [
        "echo",
        "marked",
        "l1",
        "work/intake/x.md",
    ]


def test_fake_adapter_satisfies_the_source_adapter_protocol():
    assert isinstance(sources.FakeAdapter([]), sources.SourceAdapter)
