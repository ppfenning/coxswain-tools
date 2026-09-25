import json
import os

import pytest

from agent_tools import cli, source_jira
from agent_tools.sources import Candidate, Ref, SourceConfig

HOST = "https://jira.acme.example"
REPO = "jira.acme.example/WID"
LINK = f"{HOST}/browse/WID-12"


def issue(key, project, status, labels, description="Steps to reproduce."):
    return {
        "id": key[-2:],
        "key": key,
        "self": f"{HOST}/rest/api/2/issue/{key[-2:]}",
        "fields": {
            "summary": f"Summary of {key}",
            "description": description,
            "status": {"name": status},
            "labels": labels,
            "project": {"key": project},
        },
    }


INTAKE = issue("WID-12", "WID", "Ready for Intake", ["intake"])
OTHER = issue("WID-13", "WID", "In Progress", ["bug"])
FOREIGN = issue("ZZZ-14", "ZZZ", "Ready for Intake", ["intake"])
EMPTY = issue("WID-15", "WID", "Ready for Intake", ["intake"], description=None)
SEARCH_RESPONSE = {"startAt": 0, "maxResults": 100, "total": 3, "issues": [INTAKE, OTHER, FOREIGN]}
LISTING = SEARCH_RESPONSE["issues"]


def config(filter_, token_env="JIRA_TOKEN"):
    return SourceConfig(repos=(REPO,), filter=filter_, token_env=token_env)


@pytest.fixture
def curl_args(tmp_path, monkeypatch):
    """A `curl` on PATH that records its argv, one per line, and replies with the recorded search response."""
    (tmp_path / "reply.json").write_text(json.dumps(SEARCH_RESPONSE))
    fake = tmp_path / "curl"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {tmp_path}/args\ncat {tmp_path}/reply.json\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("JIRA_TOKEN", "s3cret")
    return lambda: (tmp_path / "args").read_text().splitlines()


def test_candidates_by_status_returns_only_the_named_status_in_a_configured_project():
    assert source_jira.candidates(config("status:Ready for Intake"), LISTING) == (
        Ref(link=LINK, repo=REPO),
    )


def test_a_status_filter_matches_regardless_of_case_as_jql_does():
    assert source_jira.candidates(config("status:ready for intake"), LISTING) == (
        Ref(link=LINK, repo=REPO),
    )


def test_candidates_by_label_returns_only_the_labelled_ticket():
    assert source_jira.candidates(config("label:intake"), LISTING) == (Ref(link=LINK, repo=REPO),)


def test_candidates_with_an_unknown_filter_kind_returns_nothing():
    assert source_jira.candidates(config("assignee:pat"), LISTING) == ()


def test_read_normalises_a_ticket_and_a_null_description_becomes_empty():
    assert source_jira.read(INTAKE) == Candidate(
        title="Summary of WID-12", body="Steps to reproduce.", repo=REPO, link=LINK
    )
    assert source_jira.read(EMPTY).body == ""


def test_taken_is_true_only_when_an_intake_file_carries_the_link():
    assert source_jira.taken(LINK, frozenset({LINK}))
    assert not source_jira.taken(LINK, frozenset({f"{HOST}/browse/WID-99"}))


def test_list_argv_runs_under_the_shipped_edge_and_asks_newest_first_one_page(curl_args):
    listing = cli._fetch_listing(source_jira, config("label:intake"))
    found, problems = cli._pull_candidates(source_jira, config("label:intake"), listing)
    assert (listing, list(found), problems) == (LISTING, [LINK], [])
    assert curl_args() == [
        "-sSf",
        "-H",
        "Authorization: Bearer s3cret",
        "-G",
        f"{HOST}/rest/api/2/search",
        "--data-urlencode",
        'jql=project = "WID" AND labels = "intake" ORDER BY created DESC',
        "--data-urlencode",
        "maxResults=100",
    ]


def test_list_argv_never_carries_the_token_value(curl_args):
    assert "s3cret" not in " ".join(source_jira.list_argv(config("label:intake"), REPO))


def test_list_argv_refuses_a_token_env_mark_argv_cannot_read():
    argv = source_jira.list_argv(config("label:intake", token_env="JIRA_PAT"), REPO)
    assert argv[2].endswith("exit 2") and "curl" not in argv[2]


def test_list_argv_refuses_an_unknown_filter_kind():
    assert source_jira.list_argv(config("assignee:pat"), REPO)[2].endswith("exit 2")


def test_mark_argv_posts_one_comment_naming_the_path_and_makes_no_transition(curl_args):
    path = "intake/2026-09-24-wid-12.md"
    code, _, _ = cli._run_argv(source_jira.mark_argv(Ref(link=LINK, repo=REPO), path))
    assert code == 0
    assert curl_args() == [
        "-sSf",
        "-H",
        "Authorization: Bearer s3cret",
        "-X",
        "POST",
        "-H",
        "Content-Type: application/json",
        "--data",
        json.dumps({"body": path}),
        f"{HOST}/rest/api/2/issue/WID-12/comment",
    ]
