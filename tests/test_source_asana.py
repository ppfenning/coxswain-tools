import json

from agent_tools import cli, source_asana
from agent_tools.sources import Candidate, Ref, SourceConfig

LINK = "https://app.asana.com/0/1200/3300"

RECORDED_LISTING = {
    "data": [
        {
            "gid": "3300",
            "name": "Flaky upload retry",
            "notes": "Uploads occasionally fail without a retry.",
            "permalink_url": LINK,
            "tags": [{"name": "intake"}],
            "memberships": [
                {"project": {"gid": "9999"}, "section": {"name": "Ready"}},
                {"project": {"gid": "1200"}, "section": {"name": "Backlog"}},
            ],
        },
        {
            "gid": "3301",
            "name": "Unrelated",
            "notes": "Not for intake.",
            "permalink_url": "https://app.asana.com/0/1200/3301",
            "tags": [{"name": "bug"}],
            "memberships": [{"project": {"gid": "1200"}, "section": {"name": "Ready"}}],
        },
        {
            "gid": "3302",
            "name": "No memberships requested",
            "notes": "",
            "permalink_url": "https://app.asana.com/0/1200/3302",
            "tags": [],
        },
    ]
}

CONFIG = SourceConfig(repos=("1200",), filter="tag:intake", token_env="ASANA_PAT")
LISTING = source_asana.unwrap(RECORDED_LISTING, "1200")


def test_candidates_keeps_only_the_tagged_task_in_a_configured_project():
    assert source_asana.candidates(CONFIG, LISTING) == (Ref(link=LINK, repo="1200"),)


def test_candidates_matches_a_section_only_in_the_project_the_task_was_listed_from():
    config = SourceConfig(repos=("1200",), filter="section:Ready", token_env="ASANA_PAT")
    assert source_asana.candidates(config, LISTING) == (
        Ref(link="https://app.asana.com/0/1200/3301", repo="1200"),
    )


def test_read_normalises_title_body_repo_and_link():
    assert source_asana.read(LISTING[0]) == Candidate(
        title="Flaky upload retry",
        body="Uploads occasionally fail without a retry.",
        repo="1200",
        link=LINK,
    )


def test_taken_is_true_once_an_intake_link_matches():
    assert [source_asana.taken(LINK, links) for links in (frozenset({LINK}), frozenset())] == [
        True,
        False,
    ]


def test_list_argv_reads_the_token_from_the_named_env_var_and_fails_on_http_errors():
    assert source_asana.list_argv(CONFIG, "1200")[2].startswith(
        'curl -sSf -H "Authorization: Bearer ${ASANA_PAT}" '
    )


def test_the_edge_unwraps_the_recorded_listing_into_one_candidate(monkeypatch):
    monkeypatch.setattr(cli, "_run_argv", lambda _argv: (0, json.dumps(RECORDED_LISTING), ""))
    listing = cli._fetch_listing(source_asana, CONFIG)
    found, problems = cli._pull_candidates(source_asana, CONFIG, listing)
    assert (list(found), problems) == ([LINK], [])


def test_the_edge_comments_once_with_the_configured_token_env_and_moves_nothing():
    argv = cli._mark_argv(source_asana, CONFIG, Ref(link=LINK, repo="1200"), "intake/a.md")
    assert argv == [
        "sh",
        "-c",
        'curl -sSf -H "Authorization: Bearer ${ASANA_PAT}" -X POST'
        " -H 'Content-Type: application/json'"
        """ -d '{"data": {"text": "Taken into intake: intake/a.md"}}'"""
        " https://app.asana.com/api/1.0/tasks/3300/stories",
    ]
