import json
import os
import subprocess
from pathlib import Path

from agent_tools import cli, route_sync_gh


class _Result:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _seed_workspace(root):
    _write(root / "intake" / "2026-09-05-fresh.md",
           "---\nid: fresh\ntitle: Fresh idea\nrepo: acme/widgets\n---\nbody text\n")
    checkout = root / "checkout"
    _write(checkout / ".git" / "config", '[remote "origin"]\n\turl = git@github.com:acme/widgets.git\n')
    _write(root / "work" / "init1" / "initiative.md", f"---\nrepo: {checkout}\n---\n")
    _write(root / "runs" / "run1.pid", str(os.getpid()))
    _write(root / "runs" / "run1.usage.json", json.dumps({"calls": [{"cost_usd": 1.5}]}))
    _write(root / "work" / "init1" / "build" / "task1.md",
           "---\ntitle: Do the thing\nstate: ready\nattempts: [run1]\ngate: cheap\n---\nbody\n")


def test_items_from_store_reads_one_intake_and_one_work_item(tmp_path):
    _seed_workspace(tmp_path)
    items = {item.id: item for item in route_sync_gh.items_from_store(tmp_path)}
    assert items["fresh"].state == "intake" and items["fresh"].repo == "acme/widgets"
    task = items["task1"]
    assert task.state == "in_flight" and task.run == "run1" and task.cost_usd == 1.5
    assert task.repo == "acme/widgets" and task.gate == "cheap"


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
            return _Result(stdout=json.dumps([{"number": 5, "title": "T", "body": "B"}]))
        return _Result(stdout=json.dumps({"items": [
            {"id": "PVTI_5", "content": {"number": 5}, "title": "T", "status": "Todo", "state": "Ready", "cost": "$1.00"}
        ]}))

    ok, (issues, project_items, item_node_ids) = route_sync_gh.existing(run, ["acme/widgets"], "acme/7")
    assert ok is True
    assert issues == {"5": {"title": "T", "body": "B"}}
    assert project_items == {"5": {"State": "Ready", "Cost": "$1.00"}}
    assert item_node_ids == {"5": "PVTI_5"}
    assert calls[0][:5] == ["gh", "issue", "list", "--repo", "acme/widgets"]
    assert calls[1][:3] == ["gh", "project", "item-list"] and "7" in calls[1]


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
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: _Result(returncode=0) if argv[:3] == ["gh", "auth", "status"] else _Result(stdout="[]"))
    rc = cli.main(["route", "sync", "--profile", str(tmp_path / "profile.yaml"), "--workspace", str(tmp_path)])
    assert rc == 2


def test_cli_exits_2_when_gh_is_not_authenticated(tmp_path, monkeypatch):
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

