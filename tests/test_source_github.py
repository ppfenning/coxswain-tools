from agent_tools import source_github
from agent_tools.sources import Candidate, Ref, SourceConfig

RAW_ISSUE = {
    "number": 12,
    "title": "Flaky upload retry",
    "body": "Uploads occasionally fail without a retry.",
    "labels": [{"name": "intake"}],
    "url": "https://github.com/acme/widgets/issues/12",
    "repository": {"nameWithOwner": "acme/widgets"},
}

RAW_ISSUE_NO_LABEL = {
    "number": 13,
    "title": "Unrelated bug",
    "body": "Not for intake.",
    "labels": [{"name": "bug"}],
    "url": "https://github.com/acme/widgets/issues/13",
    "repository": {"nameWithOwner": "acme/widgets"},
}

RAW_ISSUE_OTHER_REPO = {
    "number": 14,
    "title": "Wrong repo",
    "body": "Also intake-labelled, but not a configured repo.",
    "labels": [{"name": "intake"}],
    "url": "https://github.com/other/repo/issues/14",
    "repository": {"nameWithOwner": "other/repo"},
}

RAW_ISSUE_TRIAGE = {
    "number": 15,
    "title": "Needs triage",
    "body": "Labelled for triage, not intake.",
    "labels": [{"name": "triage"}],
    "url": "https://github.com/acme/widgets/issues/15",
    "repository": {"nameWithOwner": "acme/widgets"},
}


def test_candidates_returns_only_the_intake_labelled_issue():
    config = SourceConfig(repos=("acme/widgets",), filter="label:intake", token_env="GH_TOKEN")
    listing = [RAW_ISSUE, RAW_ISSUE_NO_LABEL]
    assert source_github.candidates(config, listing) == (
        Ref(link="https://github.com/acme/widgets/issues/12", repo="acme/widgets"),
    )


def test_candidates_excludes_an_issue_outside_the_configured_repos():
    config = SourceConfig(repos=("acme/widgets",), filter="label:intake", token_env="GH_TOKEN")
    assert source_github.candidates(config, [RAW_ISSUE_OTHER_REPO]) == ()


def test_candidates_matches_the_label_the_config_filter_names_not_a_hardcoded_one():
    config = SourceConfig(repos=("acme/widgets",), filter="label:triage", token_env="GH_TOKEN")
    listing = [RAW_ISSUE, RAW_ISSUE_TRIAGE]
    assert source_github.candidates(config, listing) == (
        Ref(link="https://github.com/acme/widgets/issues/15", repo="acme/widgets"),
    )


def test_read_normalises_title_body_repo_and_link():
    assert source_github.read(RAW_ISSUE) == Candidate(
        title="Flaky upload retry",
        body="Uploads occasionally fail without a retry.",
        repo="acme/widgets",
        link="https://github.com/acme/widgets/issues/12",
    )


def test_taken_is_true_once_an_intake_link_matches():
    link = "https://github.com/acme/widgets/issues/12"
    assert source_github.taken(link, frozenset({link})) is True
    assert source_github.taken(link, frozenset()) is False


def test_list_argv_builds_the_gh_issue_list_command():
    config = SourceConfig(repos=("acme/widgets",), filter="label:intake", token_env="GH_TOKEN")
    assert source_github.list_argv(config, "acme/widgets") == [
        "gh",
        "issue",
        "list",
        "--repo",
        "acme/widgets",
        "--label",
        "intake",
        "--json",
        "number,title,body,labels,url,repository",
    ]


def test_list_argv_honors_the_configured_filter_label():
    config = SourceConfig(repos=("acme/widgets",), filter="label:triage", token_env="GH_TOKEN")
    assert source_github.list_argv(config, "acme/widgets")[5:7] == ["--label", "triage"]


def test_mark_argv_returns_one_argv_that_swaps_the_label_then_comments():
    ref = Ref(link="https://github.com/acme/widgets/issues/12", repo="acme/widgets")
    assert source_github.mark_argv(ref, "work/intake/acme-widgets-12.md") == [
        "sh",
        "-c",
        "gh issue edit 12 --repo acme/widgets --remove-label intake --add-label intake:taken"
        " && gh issue comment 12 --repo acme/widgets --body work/intake/acme-widgets-12.md",
    ]
