import json
import os
import sqlite3
import subprocess
from pathlib import Path

from agent_tools import cli, route_sync, route_sync_gh


class _Result:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _mirror(root):
    """Turn the mirror on for a workspace: the tracker defaults to none, and the policy file wins."""
    _write(root / "runs" / "policy.tracker.json", '{"tracker": "github-projects"}')


def _seed_workspace(root):
    _write(root / "intake" / "2026-09-05-fresh.md",
           "---\nid: fresh\ntitle: Fresh idea\nrepo: acme/widgets\n---\nbody text\n")
    checkout = root / "checkout"
    _write(checkout / ".git" / "config", '[remote "origin"]\n\turl = git@github.com:acme/widgets.git\n')
    _write(root / "work" / "init1" / "initiative.md", f"---\nrepo: {checkout}\n---\n")
    _write(root / "runs" / "run1.pid", str(os.getpid()))
    _write(root / "runs" / "run1.usage.json", json.dumps({"calls": [
        {"task_id": "task1", "cost_usd": 1.5}, {"task_id": "sibling", "cost_usd": 9.0}]}))
    _write(root / "work" / "init1" / "build" / "task1.md",
           "---\ntitle: Do the thing\nstate: ready\nattempts: [run1]\ngate: cheap\n---\nbody\n")


def test_items_from_store_reads_one_intake_and_one_work_item(tmp_path):
    _seed_workspace(tmp_path)
    items = {item.id: item for item in route_sync_gh.items_from_store(tmp_path)}
    assert items["fresh"].state == "intake" and items["fresh"].repo == "acme/widgets"
    task = items["task1"]
    assert task.state == "in_flight" and task.run == "run1" and task.cost_usd == 1.5
    assert task.repo == "acme/widgets" and task.gate == "cheap"


def test_a_reused_pid_is_not_published_as_in_flight(tmp_path):
    _seed_workspace(tmp_path)
    _write(tmp_path / "runs" / "run1.launched.json", json.dumps({"at": "1970-01-01T00:01:40+00:00"}))
    items = {item.id: item for item in route_sync_gh.items_from_store(tmp_path)}
    assert items["task1"].state == "ready"


def test_repo_of_falls_back_to_the_literal_value_when_nothing_resolves_on_disk():
    assert route_sync_gh._repo_of("acme/widgets") == "acme/widgets"
    assert route_sync_gh._repo_of("") == ""


def test_rewrite_issue_line_is_pure_and_touches_only_that_line():
    before = "---\ntitle: T\nstate: ready\n---\nbody\n"
    after = route_sync_gh._rewrite_issue_line(before, "9")
    assert after == "---\ntitle: T\nstate: ready\nissue: 9\n---\nbody\n"
    replaced = route_sync_gh._rewrite_issue_line(after, "10")
    assert replaced == "---\ntitle: T\nstate: ready\nissue: 10\n---\nbody\n"


def test_existing_parses_the_two_listings():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[:3] == ["gh", "issue", "list"]:
            return _Result(stdout=json.dumps([{"number": 5, "title": "T", "body": "B", "state": "OPEN"}]))
        return _Result(stdout=json.dumps({"items": [
            {"id": "PVTI_5", "content": {"number": 5}, "title": "T", "status": "Todo", "state": "Ready", "cost": "$1.00"}
        ]}))

    ok, (issues, project_items, item_node_ids) = route_sync_gh.existing(run, ["acme/widgets"], "acme/7")
    assert ok is True
    assert issues == {"5": {"title": "T", "body": "B", "state": "OPEN"}}
    assert project_items == {"5": {"State": "Ready", "Cost": "$1.00"}}
    assert item_node_ids == {"5": "PVTI_5"}
    assert "number,title,body,state" in calls[0]
    assert calls[0][:5] == ["gh", "issue", "list", "--repo", "acme/widgets"]
    assert calls[1][:3] == ["gh", "project", "item-list"] and "7" in calls[1]
    assert calls[0][-2:] == ["--limit", "10000"] and calls[1][-2:] == ["--limit", "10000"]


def test_existing_returns_a_detail_on_a_failed_gh_call_instead_of_an_empty_map():
    ok, detail = route_sync_gh.existing(lambda argv, **kw: _Result(returncode=1, stderr="rate limited"),
                                         ["acme/widgets"], "acme/7")
    assert ok is False and detail == "rate limited"


def test_find_project_matches_by_title_or_reports_none_or_a_failure():
    def found(argv, **kw):
        return _Result(stdout=json.dumps([{"title": "Coxswain", "number": 3}]))

    def missing(argv, **kw):
        return _Result(stdout="[]")

    def failing(argv, **kw):
        return _Result(returncode=1, stderr="boom")

    assert route_sync_gh.find_project(found, "acme") == (True, 3)
    assert route_sync_gh.find_project(missing, "acme") == (True, None)
    assert route_sync_gh.find_project(failing, "acme") == (False, "boom")


def test_create_project_returns_the_number_or_a_failure_detail():
    assert route_sync_gh.create_project(lambda argv, **kw: _Result(stdout=json.dumps({"number": 4})), "acme") == (True, 4)
    assert route_sync_gh.create_project(lambda argv, **kw: _Result(returncode=1, stderr="nope"), "acme") == (False, "nope")


def test_auth_ok_reads_gh_auth_status_exit_code():
    assert route_sync_gh.auth_ok(lambda argv, **kw: _Result(returncode=0)) is True
    assert route_sync_gh.auth_ok(lambda argv, **kw: _Result(returncode=1)) is False


def test_execute_resolves_real_node_ids_and_writes_back_the_new_issue(tmp_path):
    _write(tmp_path / "work" / "init1" / "build" / "task1.md",
           "---\ntitle: Do the thing\nstate: ready\n---\nbody\n")
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[:3] == ["gh", "project", "view"]:
            return _Result(stdout=json.dumps({"id": "PVT_1"}))
        if argv[:3] == ["gh", "project", "field-list"]:
            return _Result(stdout=json.dumps({"fields": [
                {"name": "State", "id": "PVTSSF_1", "options": [{"id": "OPT_ready", "name": "Ready"}]},
                {"name": "Phase", "id": "PVTF_2"},
            ]}))
        if argv[:3] == ["gh", "issue", "create"]:
            return _Result(stdout="https://github.com/acme/widgets/issues/9")
        if argv[:3] == ["gh", "project", "item-add"]:
            return _Result(stdout=json.dumps({"id": "PVTI_9"}))
        return _Result(stdout="{}")

    steps = [
        {"kind": "issue_create", "repo": "acme/widgets", "title": "Do the thing", "body": "body", "label": "coxswain", "item_id": "task1"},
        {"kind": "project_add", "issue": None},
        {"kind": "project_set", "issue": None, "field": "State", "value": "Ready"},
        {"kind": "project_set", "issue": None, "field": "Phase", "value": "build"},
        {"kind": "writeback", "item_id": "task1", "issue": None},
    ]
    log = route_sync_gh.execute(steps, run, "acme/7", tmp_path)
    assert [(kind, ok) for kind, ok, _ in log] == [
        ("issue_create", True), ("project_add", True), ("project_set", True), ("project_set", True), ("writeback", True),
    ]
    graphql_call = calls[4]
    assert graphql_call == ["gh", "api", "graphql", "-f", f"query={route_sync_gh._STATE_MUTATION}",
                             "-f", "project=PVT_1", "-f", "item=PVTI_9", "-f", "field=PVTSSF_1", "-f", "option=OPT_ready"]
    item_edit_call = calls[5]
    assert item_edit_call == ["gh", "project", "item-edit", "--id", "PVTI_9", "--project-id", "PVT_1",
                               "--field-id", "PVTF_2", "--text", "build"]
    after = (tmp_path / "work" / "init1" / "build" / "task1.md").read_text()
    assert after.splitlines() == ["---", "title: Do the thing", "state: ready", "issue: 9", "---", "body"]


def test_execute_stops_on_a_failed_gh_call_and_never_writes_an_empty_issue(tmp_path):
    _write(tmp_path / "work" / "init1" / "build" / "task1.md",
           "---\ntitle: Do the thing\nstate: ready\n---\nbody\n")

    def run(argv, **kw):
        if argv[:3] == ["gh", "issue", "create"]:
            return _Result(returncode=1, stderr="422 label not found")
        return _Result(stdout="{}")

    steps = [
        {"kind": "issue_create", "repo": "acme/widgets", "title": "Do the thing", "body": "body", "label": "coxswain", "item_id": "task1"},
        {"kind": "project_add", "issue": None},
        {"kind": "writeback", "item_id": "task1", "issue": None},
    ]
    log = route_sync_gh.execute(steps, run, "acme/7", tmp_path)
    assert log == [("issue_create", False, "422 label not found")]
    after = (tmp_path / "work" / "init1" / "build" / "task1.md").read_text()
    assert "issue:" not in after


def test_a_refuse_step_calls_no_gh_and_does_not_stop_the_rest_of_the_run():
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return _Result(stdout="https://github.com/acme/widgets/issues/1")

    steps = [
        {"kind": "refuse", "item_id": "bad", "detail": "unknown state: 'weird'"},
        {"kind": "issue_create", "repo": "acme/widgets", "title": "T", "body": "B", "label": "coxswain", "item_id": "task1"},
    ]
    log = route_sync_gh.execute(steps, run, "acme/7", "/tmp/unused")
    assert log[0] == ("refuse", False, "unknown state: 'weird'")
    assert log[1][0] == "issue_create" and log[1][1] is True
    assert calls == [["gh", "issue", "create", "--repo", "acme/widgets", "--title", "T", "--body", "B", "--label", "coxswain"]]


def test_cli_dry_run_prints_the_rendered_steps_and_exits_0(tmp_path, capsys, monkeypatch):
    _mirror(tmp_path)
    _write(tmp_path / "intake" / "2026-09-05-fresh.md",
           "---\nid: fresh\ntitle: Fresh idea\nrepo: acme/widgets\n---\n")

    def run(argv, **kw):
        if argv[:3] == ["gh", "auth", "status"]:
            return _Result(returncode=0)
        if argv[:3] == ["gh", "issue", "list"]:
            return _Result(stdout="[]")
        return _Result(stdout=json.dumps({"items": []}))

    monkeypatch.setattr(subprocess, "run", run)
    rc = cli.main(["route", "sync", "--dry-run", "--project", "acme/7", "--workspace", str(tmp_path)])
    assert rc == 0
    assert "issue_create acme/widgets" in capsys.readouterr().out


def test_cli_dry_run_without_a_project_makes_no_mutating_gh_calls(tmp_path, monkeypatch):
    _mirror(tmp_path)
    _write(tmp_path / "intake" / "2026-09-05-fresh.md",
           "---\nid: fresh\ntitle: Fresh idea\nrepo: acme/widgets\n---\n")
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[:3] == ["gh", "auth", "status"]:
            return _Result(returncode=0)
        return _Result(stdout="[]")

    monkeypatch.setattr(subprocess, "run", run)
    profile_path = tmp_path / "profile.yaml"
    rc = cli.main(["route", "sync", "--dry-run", "--profile", str(profile_path), "--workspace", str(tmp_path)])
    assert rc == 0
    assert not any(c[:3] == ["gh", "project", "create"] for c in calls)
    assert not Path(str(profile_path) + ".route-sync-project").exists()


def test_cli_exits_2_when_no_project_is_given_and_no_repo_can_derive_an_owner(tmp_path, monkeypatch):
    _mirror(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: _Result(returncode=0) if argv[:3] == ["gh", "auth", "status"] else _Result(stdout="[]"))
    rc = cli.main(["route", "sync", "--profile", str(tmp_path / "profile.yaml"), "--workspace", str(tmp_path)])
    assert rc == 2


def test_cli_exits_2_when_gh_is_not_authenticated(tmp_path, monkeypatch):
    _mirror(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: _Result(returncode=1))
    rc = cli.main(["route", "sync", "--dry-run", "--workspace", str(tmp_path)])
    assert rc == 2


def test_a_failed_set_after_create_still_records_the_issue_and_an_empty_value_clears(tmp_path):
    _write(tmp_path / "intake" / "i1.md", "---\nid: i1\ntitle: T\n---\nbody\n")
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[:3] == ["gh", "project", "view"]:
            return _Result(stdout=json.dumps({"id": "PVT_1"}))
        if argv[:3] == ["gh", "project", "field-list"]:
            return _Result(stdout=json.dumps({"fields": [{"name": "Phase", "id": "PVTF_2"}, {"name": "Run", "id": "PVTF_3"}]}))
        if argv[:3] == ["gh", "issue", "create"]:
            return _Result(stdout="https://github.com/acme/widgets/issues/9")
        if argv[:3] == ["gh", "project", "item-add"]:
            return _Result(stdout=json.dumps({"id": "PVTI_9"}))
        if "--text" in argv:
            return _Result(returncode=1, stderr="boom")
        return _Result(stdout="{}")

    steps = [
        {"kind": "issue_create", "repo": "acme/widgets", "title": "T", "body": "body", "label": "coxswain", "item_id": "i1"},
        {"kind": "project_add", "issue": None},
        {"kind": "project_set", "issue": None, "field": "Phase", "value": ""},
        {"kind": "project_set", "issue": None, "field": "Run", "value": "run-1"},
        {"kind": "writeback", "item_id": "i1", "issue": None},
    ]
    log = route_sync_gh.execute(steps, run, "acme/7", tmp_path)
    assert [(kind, ok) for kind, ok, _ in log] == [
        ("issue_create", True), ("project_add", True), ("project_set", True), ("project_set", False),
    ]
    assert calls[-2][-1] == "--clear"
    assert "issue: 9" in (tmp_path / "intake" / "i1.md").read_text()


_ITEM_RESPONSE = {"data": {"repository": {"issue": {"projectItems": {"nodes": [
    {"id": "PVTI_other", "project": {"number": 3, "owner": {"login": "acme"}},
     "fieldValues": {"nodes": [{"name": "Done", "field": {"name": "State"}}]}},
    {"id": "PVTI_5", "project": {"number": 7, "owner": {"login": "Acme"}},
     "fieldValues": {"nodes": [{}, {"name": "Ready", "field": {"name": "State"}},
                               {"text": "$1.00", "field": {"name": "Cost"}},
                               {"text": "x", "field": {"name": "Unrelated"}}]}},
]}}}}}


def test_project_item_from_response_keeps_only_the_matching_projects_item():
    assert route_sync_gh.project_item_from_response(_ITEM_RESPONSE, "acme/7") == (
        {"State": "Ready", "Cost": "$1.00"}, "PVTI_5")
    assert route_sync_gh.project_item_from_response(_ITEM_RESPONSE, "acme/9") == ({}, "")


def _item_run(calls, labels=("coxswain",)):
    def run(argv, **kw):
        calls.append(argv)
        if argv[:3] == ["gh", "issue", "view"]:
            return _Result(stdout=json.dumps({"number": 5, "title": "T", "body": "B", "state": "OPEN",
                                              "labels": [{"name": n} for n in labels]}))
        return _Result(stdout=json.dumps(_ITEM_RESPONSE))
    return run


def test_existing_item_reads_only_that_issue_and_its_project_item():
    calls = []
    ok, (issues, project_items, item_node_ids) = route_sync_gh.existing_item(
        _item_run(calls), "acme/widgets", "5", "acme/7")
    assert ok is True
    assert issues == {"5": {"title": "T", "body": "B", "state": "OPEN"}}
    assert project_items == {"5": {"State": "Ready", "Cost": "$1.00"}} and item_node_ids == {"5": "PVTI_5"}
    assert calls[0] == ["gh", "issue", "view", "5", "--repo", "acme/widgets", "--json", "number,title,body,state,labels"]
    assert calls[1][:3] == ["gh", "api", "graphql"] and "number=5" in calls[1] and len(calls) == 2


def test_existing_item_without_a_project_skips_the_project_read():
    calls = []
    ok, (issues, project_items, _) = route_sync_gh.existing_item(_item_run(calls), "acme/widgets", "5", None)
    assert ok is True and list(issues) == ["5"] and project_items == {} and len(calls) == 1


def test_existing_item_makes_no_call_for_an_item_with_no_issue_yet():
    calls = []
    assert route_sync_gh.existing_item(_item_run(calls), "acme/widgets", None, "acme/7") == (True, ({}, {}, {}))
    assert calls == []


def test_existing_item_treats_an_unlabelled_issue_as_absent_and_reports_a_failed_call():
    calls = []
    assert route_sync_gh.existing_item(_item_run(calls, labels=()), "acme/widgets", "5", "acme/7") == (True, ({}, {}, {}))
    assert len(calls) == 1
    ok, detail = route_sync_gh.existing_item(lambda argv, **kw: _Result(returncode=1, stderr="rate limited"),
                                             "acme/widgets", "5", "acme/7")
    assert (ok, detail) == (False, "rate limited")


def test_existing_item_fails_loudly_when_graphql_answers_with_errors():
    def run(argv, **kw):
        if argv[:3] == ["gh", "issue", "view"]:
            return _Result(stdout=json.dumps({"number": 5, "title": "T", "body": "B", "labels": [{"name": "coxswain"}]}))
        return _Result(stdout=json.dumps({"errors": [{"message": "Field 'x' doesn't exist"}]}))

    assert route_sync_gh.existing_item(run, "acme/widgets", "5", "acme/7") == (False, "Field 'x' doesn't exist")


def test_cli_item_sync_refuses_an_unknown_or_shared_id_without_listing_anything(tmp_path, monkeypatch, capsys):
    _mirror(tmp_path)
    _write(tmp_path / "intake" / "a.md", "---\nid: a\ntitle: A\nrepo: acme/widgets\nissue: 5\n---\nB\n")
    _write(tmp_path / "intake" / "a2.md", "---\nid: shared\ntitle: A\nrepo: acme/widgets\nissue: 5\n---\nB\n")
    _write(tmp_path / "work" / "init1" / "build" / "t.md", "---\nid: shared\ntitle: T\nstate: ready\n---\nB\n")
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return _Result(returncode=0, stdout="[]")

    monkeypatch.setattr(subprocess, "run", run)
    for wanted, count in (("typo", 0), ("shared", 2)):
        rc = cli.main(["route", "sync", "--item", wanted, "--project", "acme/7", "--workspace", str(tmp_path)])
        assert rc == 2
        assert f"matches {count} work-store items, expected exactly one" in capsys.readouterr().out
    assert calls == [["gh", "auth", "status"]] * 2


def test_cli_item_sync_issues_only_the_per_item_gh_calls(tmp_path, monkeypatch):
    _mirror(tmp_path)
    _write(tmp_path / "intake" / "a.md", "---\nid: a\ntitle: A\nrepo: acme/widgets\nissue: 5\n---\nB\n")
    _write(tmp_path / "intake" / "b.md", "---\nid: b\ntitle: B\nrepo: acme/other\nissue: 6\n---\nB\n")
    calls = []
    inner = _item_run(calls)

    def run(argv, **kw):
        return _Result(returncode=0) if argv[:3] == ["gh", "auth", "status"] else inner(argv, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    rc = cli.main(["route", "sync", "--dry-run", "--item", "a", "--project", "acme/7", "--workspace", str(tmp_path)])
    assert rc == 0
    assert [c[:3] for c in calls] == [["gh", "issue", "view"], ["gh", "api", "graphql"]]
    assert not any("item-list" in c for c in calls)


def test_execute_closes_the_issue_with_gh_issue_close_and_reports_a_failed_close():
    calls = []
    steps = [{"kind": "issue_close", "issue": "5"}]

    def run(argv, **kw):
        calls.append(argv)
        return _Result(returncode=0)

    item = cli.route_sync.Item("a", "A", "B", "acme/widgets", "done", "", "", 0.0, "", "5")
    log = route_sync_gh.execute(steps, run, "acme/7", "/tmp/unused", [item])
    assert log == [("issue_close", True, "")]
    assert calls == [["gh", "issue", "close", "5", "--repo", "acme/widgets"]]
    failed = route_sync_gh.execute(steps, lambda argv, **kw: _Result(returncode=1, stderr="nope"), "acme/7", "/tmp/unused", [item])
    assert failed == [("issue_close", False, "nope")]


def _checkout(root):
    """A directory whose git origin resolves to `acme/widgets`, which is what a work item's repo must be."""
    _write(root / "checkout" / ".git" / "config", '[remote "origin"]\n\turl = git@github.com:acme/widgets.git\n')
    return root / "checkout"


def test_cli_item_sync_closes_the_open_issue_of_a_done_item(tmp_path, monkeypatch):
    _mirror(tmp_path)
    _write(tmp_path / "work" / "init1" / "initiative.md", f"---\nrepo: {_checkout(tmp_path)}\n---\n")
    _write(tmp_path / "work" / "init1" / "build" / "t.md",
           "---\nid: t\ntitle: T\nstate: done\nissue: 5\n---\nB\n")
    calls = []
    inner = _item_run(calls)

    def run(argv, **kw):
        if argv[:3] == ["gh", "auth", "status"]:
            return _Result()
        if argv[:3] == ["gh", "project", "view"]:
            return _Result(stdout=json.dumps({"id": "PVT_1"}))
        if argv[:3] == ["gh", "project", "field-list"]:
            names = ("State", "Phase", "Run", "Cost", "Gate")
            return _Result(stdout=json.dumps({"fields": [
                {"name": n, "id": f"F_{n}", "options": [{"id": "OPT_done", "name": "Done"}] if n == "State" else []}
                for n in names]}))
        if argv[:3] in (["gh", "issue", "view"], ["gh", "api", "graphql"]):
            return inner(argv, **kw)
        calls.append(argv)
        return _Result(stdout="{}")

    monkeypatch.setattr(subprocess, "run", run)
    rc = cli.main(["route", "sync", "--item", "t", "--project", "acme/7", "--workspace", str(tmp_path)])
    assert rc == 0
    assert calls[-1] == ["gh", "issue", "close", "5", "--repo", "acme/widgets"]
    calls.clear()
    _write(tmp_path / "work" / "init1" / "build" / "t.md", "---\nid: t\ntitle: T\nstate: done\nissue: 5\n---\nB\n")

    def closed(argv, **kw):
        out = run(argv, **kw)
        if argv[:3] == ["gh", "issue", "view"]:
            return _Result(stdout=json.dumps({"number": 5, "title": "T", "body": "B", "state": "CLOSED",
                                              "labels": [{"name": "coxswain"}]}))
        return out

    monkeypatch.setattr(subprocess, "run", closed)
    cli.main(["route", "sync", "--item", "t", "--project", "acme/7", "--workspace", str(tmp_path)])
    assert not any(c[:3] == ["gh", "issue", "close"] for c in calls)


def _file_profile(tmp_path, tracker_line="tracker: github-projects\n"):
    ws = tmp_path / "workspace"
    ws.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"team: acme\nworkspace_dir: {ws}\nharness_dir: /opt/h\ncartridges_dir: /opt/c\n"
                       f"provider_profile: /opt/p.yaml\n{tracker_line}")
    return profile, ws


def _gh_for_file(calls, create_fails=False):
    def run(argv, **kw):
        calls.append(argv)
        if argv[:3] == ["gh", "issue", "create"]:
            return (_Result(returncode=1, stderr="rate limited") if create_fails
                    else _Result(stdout="https://github.com/acme/widgets/issues/9"))
        if argv[:3] == ["gh", "project", "list"]:
            return _Result(stdout=json.dumps({"projects": [{"title": "Coxswain", "number": 7}]}))
        if argv[:3] == ["gh", "project", "field-list"]:
            return _Result(stdout=json.dumps({"fields": [
                {"name": n, "id": f"F_{n}", "options": [{"id": "O", "name": "Ready"}, {"id": "I", "name": "Intake"}]}
                for n in ("State", "Phase", "Run", "Cost", "Gate")]}))
        if argv[:3] == ["gh", "project", "item-add"]:
            return _Result(stdout=json.dumps({"id": "PVTI_9"}))
        return _Result(stdout="{}")
    return run


def _creates(calls):
    return [c for c in calls if c[:3] == ["gh", "issue", "create"]]


def test_route_file_creates_the_items_issue_at_birth_and_writes_it_back(tmp_path, monkeypatch, capsys):
    profile, ws = _file_profile(tmp_path)
    calls = []
    monkeypatch.setattr(subprocess, "run", _gh_for_file(calls))
    rc = cli.main(["route", "file", "--profile", str(profile), "--repo", str(_checkout(tmp_path)), "--title", "Fix the thing"])
    assert rc == 0
    assert [c[c.index("--title") + 1] for c in _creates(calls)] == ["Fix the thing"]
    assert "issue: 9" in (ws / "work" / "fix-the-thing" / "build" / "fix-the-thing.md").read_text()
    assert "synced fix-the-thing" in capsys.readouterr().out


def test_route_file_under_tracker_none_says_not_synced_and_calls_no_gh(tmp_path, monkeypatch, capsys):
    profile, ws = _file_profile(tmp_path, tracker_line="")
    calls = []
    monkeypatch.setattr(subprocess, "run", _gh_for_file(calls))
    rc = cli.main(["route", "file", "--profile", str(profile), "--repo", str(_checkout(tmp_path)), "--title", "Fix the thing"])
    out = capsys.readouterr().out
    assert rc == 0
    assert (ws / "work" / "fix-the-thing" / "build" / "fix-the-thing.md").exists()
    assert "route file: wrote fix-the-thing; not synced: tracker is none" in out.splitlines()
    assert "synced fix-the-thing" not in out.splitlines()
    assert not any(c[:1] == ["gh"] for c in calls)


def test_route_file_intake_syncs_the_intake_item_too(tmp_path, monkeypatch):
    profile, ws = _file_profile(tmp_path)
    calls = []
    monkeypatch.setattr(subprocess, "run", _gh_for_file(calls))
    rc = cli.main(["route", "file", "--profile", str(profile), "--repo", "acme/widgets", "--title", "Fix the thing", "--intake"])
    assert rc == 0 and len(_creates(calls)) == 1
    [written] = list((ws / "intake").glob("*.md"))
    assert "issue: 9" in written.read_text()


def test_route_file_from_intake_syncs_only_the_new_work_item(tmp_path, monkeypatch):
    profile, ws = _file_profile(tmp_path)
    _write(ws / "intake" / "2026-09-05-fresh.md",
           f"---\nid: fresh\ntitle: Fresh idea\nrepo: {_checkout(tmp_path)}\n---\nbody text\n")
    calls = []
    monkeypatch.setattr(subprocess, "run", _gh_for_file(calls))
    rc = cli.main(["route", "file", "--profile", str(profile), "--from-intake", str(ws / "intake" / "2026-09-05-fresh.md")])
    assert rc == 0
    assert [c[c.index("--title") + 1] for c in _creates(calls)] == ["Fresh idea"]
    assert "issue: 9" in (ws / "work" / "fresh-idea" / "build" / "fresh-idea.md").read_text()
    assert not (ws / "intake" / "2026-09-05-fresh.md").exists()


def test_route_file_still_writes_the_item_and_says_so_in_one_line_when_the_sync_fails(tmp_path, monkeypatch, capsys):
    profile, ws = _file_profile(tmp_path)
    calls = []
    monkeypatch.setattr(subprocess, "run", _gh_for_file(calls, create_fails=True))
    rc = cli.main(["route", "file", "--profile", str(profile), "--repo", str(_checkout(tmp_path)), "--title", "Fix the thing"])
    out = capsys.readouterr().out
    assert rc == 0
    assert (ws / "work" / "fix-the-thing" / "build" / "fix-the-thing.md").exists()
    failures = [line for line in out.splitlines() if "issue sync failed" in line]
    assert failures == ["route file: wrote fix-the-thing; issue sync failed (exit 1; issue_create: rate limited); "
                        "run `route sync --item fix-the-thing`"]
    assert "issue_create:" not in out.replace(failures[0], "")


def _task_record(root, run, task, landed):
    _write(root / "runs" / run / "tasks" / "build" / f"{task}.json",
           json.dumps({"ticket": task, "phase": "build", "landed": landed}))


def _two_task_store(root):
    _write(root / "work" / "init1" / "initiative.md", "---\nrepo: acme/widgets\n---\nInitiative body\n")
    for task in ("tA", "tB"):
        _write(root / "work" / "init1" / "build" / f"{task}.md",
               f"---\ntitle: {task}\nstate: done\nattempts: [run1]\n---\nb\n")


def _by_id(root):
    return {item.id: item for item in route_sync_gh.items_from_store(root)}


def test_run_is_the_run_whose_task_record_landed_the_item(tmp_path):
    _two_task_store(tmp_path)
    _task_record(tmp_path, "run1", "tA", False)
    _task_record(tmp_path, "run2", "tA", True)
    items = _by_id(tmp_path)
    assert items["tA"].run == "run2"
    assert items["tB"].run == "run1"


def test_cost_is_only_this_tasks_calls_across_two_runs_and_never_a_run_total(tmp_path):
    _two_task_store(tmp_path)
    _write(tmp_path / "runs" / "run1.usage.json", json.dumps({"calls": [
        {"task_id": "tA", "cost_usd": 1.0}, {"task_id": "tB", "cost_usd": 10.0},
        {"role": "scope_epic", "task_id": None, "cost_usd": 100.0}]}))
    _write(tmp_path / "runs" / "run2.usage.json", json.dumps({"calls": [
        {"task_id": "tA", "cost_usd": 0.25}, {"task_id": "tB", "cost_usd": 20.0}]}))
    items = _by_id(tmp_path)
    assert items["tA"].cost_usd == 1.25
    assert items["tB"].cost_usd == 30.0


def test_a_call_with_a_composite_task_id_or_none_is_no_tasks_cost(tmp_path):
    _two_task_store(tmp_path)
    _write(tmp_path / "runs" / "run1.usage.json", json.dumps({"calls": [
        {"task_id": "run1:build:tA", "cost_usd": 2.0}, {"cost_usd": 50.0}, {"task_id": "tA", "cost_usd": "x"}]}))
    assert _by_id(tmp_path)["tA"].cost_usd == 0.0


def test_a_store_only_run_costs_its_task_from_the_run_store(tmp_path):
    _two_task_store(tmp_path)
    (tmp_path / "runs").mkdir()
    conn = sqlite3.connect(tmp_path / "runs" / "cox.db")
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, launched_at TEXT, ended_at TEXT)")
    conn.execute(
        "CREATE TABLE node_calls (call_id TEXT, run_id TEXT, seq INTEGER, task_id TEXT, cost_usd REAL, ts TEXT, "
        "model_alias TEXT, ok BOOL, decision_json TEXT, role TEXT, tier TEXT, ceiling_usd REAL, ceiling_source TEXT, "
        "turns INTEGER, duration_ms INTEGER, input_tokens INTEGER, cache_read_tokens INTEGER, "
        "cache_creation_tokens INTEGER, input_total INTEGER, output_tokens INTEGER)"
    )
    conn.execute("INSERT INTO runs VALUES ('run1', '2026-09-25T04:00:00+00:00', '2026-09-25T05:00:00+00:00')")
    conn.executemany(
        "INSERT INTO node_calls (call_id, run_id, seq, task_id, cost_usd, ts, ok) VALUES (?, 'run1', ?, ?, ?, ?, 1)",
        [("c1", 1, "tA", 0.5, "2026-09-25T04:10:00+00:00"), ("c2", 2, "tB", 4.0, "2026-09-25T04:20:00+00:00"),
         ("c3", 3, None, 100.0, "2026-09-25T04:30:00+00:00")],
    )
    conn.commit()
    conn.close()
    assert not list((tmp_path / "runs").glob("*.usage.json"))
    items = _by_id(tmp_path)
    assert (items["tA"].cost_usd, items["tB"].cost_usd) == (0.5, 4.0)


def test_an_unreadable_usage_file_costs_a_task_nothing(tmp_path):
    _two_task_store(tmp_path)
    _write(tmp_path / "runs" / "run1.usage.json", "{not json")
    assert _by_id(tmp_path)["tA"].cost_usd == 0.0


def test_an_initiative_is_a_card_listed_before_its_tasks_and_done_only_when_every_task_is(tmp_path):
    _two_task_store(tmp_path)
    items = route_sync_gh.items_from_store(tmp_path)
    assert [item.id for item in items] == ["initiative:init1", "tA", "tB"]
    assert items[0].state == "done" and items[0].initiative and items[1].parent == "initiative:init1"
    _write(tmp_path / "work" / "init1" / "build" / "tB.md", "---\ntitle: tB\nstate: ready\n---\nb\n")
    assert route_sync_gh.items_from_store(tmp_path)[0].state == "ready"


def test_an_initiative_with_no_task_is_not_done(tmp_path):
    _write(tmp_path / "work" / "empty" / "initiative.md", "---\nrepo: acme/widgets\n---\n")
    assert [(i.id, i.state) for i in route_sync_gh.items_from_store(tmp_path)] == [("initiative:empty", "ready")]


def _sub_issue_gh(calls, parent_of_child=None):
    def run(argv, **kw):
        calls.append(argv)
        if argv[:3] == ["gh", "issue", "create"]:
            number = 10 if argv[argv.index("--title") + 1] == "init1" else 11
            return _Result(stdout=f"https://github.com/acme/widgets/issues/{number}")
        if argv[2].startswith("repos/"):
            return _Result(stdout="NODE_" + argv[2].rsplit("/", 1)[-1] + "\n")
        if "$id:ID!" in " ".join(argv):
            parent = {"id": parent_of_child} if parent_of_child else None
            return _Result(stdout=json.dumps({"data": {"node": {"parent": parent}}}))
        return _Result(stdout=json.dumps({"data": {"addSubIssue": {"issue": {"id": "NODE_10"}}}}))
    return run


def test_the_initiative_gets_its_own_issue_and_each_task_is_linked_under_it(tmp_path):
    checkout = tmp_path / "checkout"
    _write(checkout / ".git" / "config", '[remote "origin"]\n\turl = git@github.com:acme/widgets.git\n')
    _write(tmp_path / "work" / "init1" / "initiative.md", f"---\nrepo: {checkout}\n---\nInitiative body\n")
    _write(tmp_path / "work" / "init1" / "build" / "t1.md", "---\ntitle: T1\nstate: done\n---\nb\n")
    items = route_sync_gh.items_from_store(tmp_path)
    steps = [s for s in route_sync.plan(items, {}, {}, "github-projects")
             if s["kind"] in ("issue_create", "writeback", "sub_issue_link")]
    calls = []
    log = route_sync_gh.execute(steps, _sub_issue_gh(calls), "", tmp_path, items)
    assert [(kind, ok) for kind, ok, _ in log] == [
        ("issue_create", True), ("writeback", True), ("issue_create", True), ("writeback", True),
        ("sub_issue_link", True)]
    creates = [c for c in calls if c[:3] == ["gh", "issue", "create"]]
    assert [(c[c.index("--repo") + 1], c[c.index("--title") + 1]) for c in creates] == [
        ("acme/widgets", "init1"), ("acme/widgets", "T1")]
    assert calls[-1] == ["gh", "api", "graphql", "-f", f"query={route_sync_gh._SUB_ISSUE_MUTATION}",
                         "-f", "parent=NODE_10", "-f", "child=NODE_11"]
    assert "issue: 10" in (tmp_path / "work" / "init1" / "initiative.md").read_text()
    assert "issue: 11" in (tmp_path / "work" / "init1" / "build" / "t1.md").read_text()


def _link_step():
    return {"kind": "sub_issue_link", "repo": "acme/widgets", "parent_item": "initiative:init1",
            "parent_issue": "10", "child_item": "t1", "child_issue": "11"}


def test_a_child_already_under_its_parent_issues_no_mutation(tmp_path):
    calls = []
    log = route_sync_gh.execute([_link_step()], _sub_issue_gh(calls, parent_of_child="NODE_10"), "", tmp_path)
    assert log == [("sub_issue_link", True, "11 already under 10")]
    assert not any("addSubIssue" in " ".join(c) for c in calls)


def test_a_failed_add_sub_issue_is_reported(tmp_path):
    def run(argv, **kw):
        if argv[2].startswith("repos/"):
            return _Result(stdout="NODE\n")
        if "$id:ID!" in " ".join(argv):
            return _Result(stdout=json.dumps({"data": {"node": {"parent": None}}}))
        return _Result(stdout=json.dumps({"errors": [{"message": "sub-issues disabled"}]}))
    assert route_sync_gh.execute([_link_step()], run, "", tmp_path) == [("sub_issue_link", False, "sub-issues disabled")]
