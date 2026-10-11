"""Check suites for a commit: the parser on literal JSON, and the gh calls of the edge."""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_tools import forge_github
from agent_tools.forge import ForgeError

KEYS = {"workflow", "conclusion", "url"}


def _suite(id, conclusion, status="completed", app="GitHub Actions"):
    return {"id": id, "status": status, "conclusion": conclusion, "app": {"name": app},
            "url": f"https://api.github.com/suites/{id}", "head_sha": "abc", "repository": {"html_url": "https://github.com/o/r"}}


def _run_row(suite_id, name, conclusion):
    return {"check_suite_id": suite_id, "name": name, "conclusion": conclusion, "html_url": f"https://github.com/o/r/actions/runs/{suite_id}"}


def _parse(suites, runs):
    return forge_github.parse_check_suites({"check_suites": suites}, {"workflow_runs": runs})


def test_startup_failure_suite():
    assert _parse([_suite(1, "startup_failure")], [_run_row(1, "ci", "startup_failure")]) == (
        {"workflow": "ci", "conclusion": "startup_failure", "url": "https://github.com/o/r/actions/runs/1"},
    )


def test_failure_suite():
    (row,) = _parse([_suite(2, "failure")], [_run_row(2, "lint", "failure")])
    assert (set(row), row["workflow"], row["conclusion"]) == (KEYS, "lint", "failure")


def test_success_suite():
    (row,) = _parse([_suite(3, "success")], [_run_row(3, "test", "success")])
    assert (row["workflow"], row["conclusion"]) == ("test", "success")


def test_in_progress_suite_has_none_conclusion():
    (row,) = _parse([_suite(4, None, status="in_progress")], [_run_row(4, "deploy", None)])
    assert (row["workflow"], row["conclusion"]) == ("deploy", None)


def test_empty_suite_list():
    assert _parse([], []) == ()
    assert forge_github.parse_check_suites({"total_count": 0, "check_suites": []}, {"workflow_runs": []}) == ()


def test_suite_without_a_run_falls_back_to_app_name_and_checks_page():
    assert _parse([_suite(5, "success", app="Codecov")], []) == (
        {"workflow": "Codecov", "conclusion": "success", "url": "https://github.com/o/r/commit/abc/checks?check_suite_id=5"},
    )


def test_unusable_bodies_give_no_rows():
    assert forge_github.parse_check_suites(None, None) == ()


def test_rows_missing_ids_are_not_matched_to_each_other():
    suite = {"conclusion": "failure", "app": {"name": "GitHub Actions"}}
    assert _parse([suite], [{"name": "ci", "html_url": "https://x"}]) == (
        {"workflow": "GitHub Actions", "conclusion": "failure", "url": None},
    )


def _fake_gh(monkeypatch, suites_out, runs_out="", runs_rc=0):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs.get("timeout"), kwargs.get("cwd")))
        out, rc = (suites_out, 0) if "check-suites" in argv[-1] else (runs_out, runs_rc)
        return subprocess.CompletedProcess(argv, rc, out, "HTTP 403" if rc else "")

    monkeypatch.setattr(forge_github.subprocess, "run", run)
    return calls


def test_edge_reads_suites_and_runs_with_the_read_bound(monkeypatch, tmp_path):
    calls = _fake_gh(monkeypatch, json.dumps({"check_suites": [_suite(1, "failure")]}),
                     json.dumps({"workflow_runs": [_run_row(1, "ci", "failure")]}))
    rows = forge_github.read_check_suites(tmp_path, "abc123")
    assert rows == ({"workflow": "ci", "conclusion": "failure", "url": "https://github.com/o/r/actions/runs/1"},)
    assert calls == [
        (["gh", "api", "--paginate", "repos/{owner}/{repo}/commits/abc123/check-suites?per_page=100"], 30, tmp_path),
        (["gh", "api", "--paginate", "repos/{owner}/{repo}/actions/runs?head_sha=abc123&per_page=100"], 30, tmp_path),
    ]


def test_edge_reads_every_page(monkeypatch, tmp_path):
    pages = json.dumps({"check_suites": [_suite(1, "success")]}) + json.dumps({"check_suites": [_suite(2, "startup_failure")]})
    runs = json.dumps({"workflow_runs": [_run_row(1, "ci", "success")]}) + json.dumps({"workflow_runs": [_run_row(2, "lint", "startup_failure")]})
    _fake_gh(monkeypatch, pages, runs)
    rows = forge_github.read_check_suites(tmp_path, "abc")
    assert [(r["workflow"], r["conclusion"]) for r in rows] == [("ci", "success"), ("lint", "startup_failure")]


def test_edge_keeps_suites_when_runs_cannot_be_read(monkeypatch, tmp_path):
    _fake_gh(monkeypatch, json.dumps({"check_suites": [_suite(7, "startup_failure")]}), runs_rc=1)
    assert forge_github.read_check_suites(tmp_path, "abc") == (
        {"workflow": "GitHub Actions", "conclusion": "startup_failure", "url": "https://github.com/o/r/commit/abc/checks?check_suite_id=7"},
    )


def test_edge_raises_forge_error_on_a_failed_call(monkeypatch, tmp_path):
    monkeypatch.setattr(forge_github.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "HTTP 404"))
    with pytest.raises(ForgeError, match="HTTP 404"):
        forge_github.read_check_suites(tmp_path, "abc123")


def test_edge_raises_forge_error_on_unreadable_output(monkeypatch, tmp_path):
    monkeypatch.setattr(forge_github.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "not json", ""))
    with pytest.raises(ForgeError, match="unreadable"):
        forge_github.read_check_suites(tmp_path, "abc123")
