"""Listing, closing and branch deletion of review PRs: the github functions and the forge dispatchers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from agent_tools import forge, forge_github, forge_local

REPO = Path("/repo")


def _fake(monkeypatch, replies):
    """Record (argv, timeout, cwd) per call; `replies` maps an argv[1:3] pair to (code, stdout, stderr)."""
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs.get("timeout"), kwargs.get("cwd")))
        code, out, err = replies.get(tuple(argv[1:3]), (0, "", ""))
        return subprocess.CompletedProcess(argv, code, out, err)

    monkeypatch.setattr(forge_github.subprocess, "run", run)
    return calls


def _rows(heads):
    return json.dumps([{"number": i + 1, "headRefName": h} for i, h in enumerate(heads)])


def test_listing_keeps_open_prs_with_the_prefix(monkeypatch):
    calls = _fake(monkeypatch, {("pr", "list"): (0, _rows(["rv/a-1", "main", "rv/b-2", "other/rv/c"]), "")})
    assert forge_github.list_open_prs_by_prefix(REPO, "rv/") == [
        {"number": 1, "head": "rv/a-1"},
        {"number": 3, "head": "rv/b-2"},
    ]
    argv, timeout, cwd = calls[0]
    assert argv[argv.index("--state") + 1] == "open"
    assert (timeout, cwd) == (30, REPO)


def test_listing_at_the_limit_gives_a_reason(monkeypatch):
    limit = forge_github.PR_LIST_LIMIT
    calls = _fake(monkeypatch, {("pr", "list"): (0, _rows(["rv/x"] * limit), "")})
    result = forge_github.list_open_prs_by_prefix(REPO, "rv/")
    assert isinstance(result, str) and "limit" in result
    assert calls[0][0][calls[0][0].index("--limit") + 1] == str(limit)


def test_listing_just_under_the_limit_is_a_list(monkeypatch):
    _fake(monkeypatch, {("pr", "list"): (0, _rows(["rv/x"] * (forge_github.PR_LIST_LIMIT - 1)), "")})
    assert len(forge_github.list_open_prs_by_prefix(REPO, "rv/")) == forge_github.PR_LIST_LIMIT - 1


def test_listing_failure_gives_a_reason(monkeypatch):
    _fake(monkeypatch, {("pr", "list"): (1, "", "boom")})
    assert forge_github.list_open_prs_by_prefix(REPO, "rv/") == "could not list open pull requests: boom"


def test_close_comments_before_it_closes(monkeypatch):
    calls = _fake(monkeypatch, {("pr", "view"): (0, '{"state":"OPEN"}', "")})
    assert forge_github.close_pr(REPO, 7, "landed in abc") == (True, "closed")
    assert [c[0][1:3] for c in calls] == [["pr", "view"], ["pr", "comment"], ["pr", "close"]]
    assert calls[1][0][-2:] == ["--body", "landed in abc"]
    assert [c[1] for c in calls] == [30, 120, 120]


def test_close_of_a_closed_pr_posts_nothing(monkeypatch):
    calls = _fake(monkeypatch, {("pr", "view"): (0, '{"state":"CLOSED"}', "")})
    assert forge_github.close_pr(REPO, 7, "c") == (True, "already closed")
    assert len(calls) == 1


def test_failed_comment_does_not_close(monkeypatch):
    calls = _fake(monkeypatch, {("pr", "view"): (0, '{"state":"OPEN"}', ""), ("pr", "comment"): (1, "", "denied")})
    assert forge_github.close_pr(REPO, 7, "c") == (False, "denied")
    assert [c[0][2] for c in calls] == ["view", "comment"]


def test_delete_branch_succeeds(monkeypatch):
    calls = _fake(monkeypatch, {})
    assert forge_github.delete_branch(REPO, "rv/a") == (True, "deleted")
    assert calls[0][0][-1] == "repos/{owner}/{repo}/git/refs/heads/rv/a"
    assert (calls[0][1], calls[0][2]) == (120, REPO)


def test_delete_of_a_missing_branch_is_already_gone(monkeypatch):
    _fake(monkeypatch, {("api", "-X"): (1, "", "gh: Reference does not exist (HTTP 422)")})
    assert forge_github.delete_branch(REPO, "rv/a") == (True, "already gone")


def test_delete_on_a_404_is_a_failure_not_already_gone(monkeypatch):
    _fake(monkeypatch, {("api", "-X"): (1, "", "gh: Not Found (HTTP 404)")})
    assert forge_github.delete_branch(REPO, "rv/a") == (False, "gh: Not Found (HTTP 404)")


def test_retry_after_a_failed_close_does_not_comment_twice(monkeypatch):
    first = _fake(monkeypatch, {("pr", "view"): (0, '{"state":"OPEN","comments":[]}', ""), ("pr", "close"): (1, "", "timeout")})
    assert forge_github.close_pr(REPO, 7, "landed") == (False, "timeout")
    assert [c[0][2] for c in first] == ["view", "comment", "close"]
    retry = _fake(monkeypatch, {("pr", "view"): (0, '{"state":"OPEN","comments":[{"body":"landed"}]}', "")})
    assert forge_github.close_pr(REPO, 7, "landed") == (True, "closed")
    assert [c[0][2] for c in retry] == ["view", "close"]


def test_each_call_turns_a_timeout_into_a_reason(monkeypatch):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(forge_github.subprocess, "run", run)
    assert "timed out after 30s" in forge_github.list_open_prs_by_prefix(REPO, "rv/")
    closed = forge_github.close_pr(REPO, 7, "c")
    assert closed[0] is False and "timed out after 30s" in closed[1]
    deleted = forge_github.delete_branch(REPO, "rv/a")
    assert deleted[0] is False and "timed out after 120s" in deleted[1]


def test_delete_failure_is_reported(monkeypatch):
    _fake(monkeypatch, {("api", "-X"): (1, "", "gh: Forbidden (HTTP 403)")})
    assert forge_github.delete_branch(REPO, "rv/a") == (False, "gh: Forbidden (HTTP 403)")


def test_dispatchers_on_a_module_without_the_functions():
    bare = SimpleNamespace()
    for module in (bare, forge_local):
        assert forge.list_open_prs_by_prefix(module, REPO, "rv/") == forge.NOT_SUPPORTED
        assert forge.close_pr(module, REPO, 7, "c") == (False, forge.NOT_SUPPORTED)
        assert forge.delete_branch(module, REPO, "rv/a") == (False, forge.NOT_SUPPORTED)


def test_dispatchers_call_the_module():
    stub = SimpleNamespace(
        list_open_prs_by_prefix=lambda repo, prefix: [{"number": 1, "head": prefix}],
        close_pr=lambda repo, number, comment: (True, f"{number}:{comment}"),
        delete_branch=lambda repo, branch: (True, branch),
    )
    assert forge.list_open_prs_by_prefix(stub, REPO, "rv/") == [{"number": 1, "head": "rv/"}]
    assert forge.close_pr(stub, REPO, 7, "c") == (True, "7:c")
    assert forge.delete_branch(stub, REPO, "rv/a") == (True, "rv/a")
