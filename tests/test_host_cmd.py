from __future__ import annotations

import argparse
import datetime
import json
import types

from agent_tools import cli, host_cmd, run_store
from agent_tools.checkout_env import CHECKOUT_ENV_KEY
from agent_tools.lane_hosts import LaneHost

NOW = datetime.datetime(2026, 9, 26, 12, 0, tzinfo=datetime.UTC)
HOST_A = {"name": "host-a", "ssh": "host-a", "capacity": 8, "state": "active",
          "beat_at": "2026-09-26T11:58:00+00:00", "versions_json": '{"login_ok": true}'}


def test_only_active_rows_are_lane_hosts_and_the_workspace_comes_from_the_profile_host():
    rows = [HOST_A, {**HOST_A, "name": "pi", "ssh": "pi", "state": "draining"}, {**HOST_A, "name": "old", "state": "offline"}]
    assert host_cmd.host_rows_to_lane_hosts(rows, [LaneHost("host-a", "x", "/w")]) == (LaneHost("host-a", "host-a", "/w"),)


def test_the_host_list_line_has_state_capacity_beat_age_and_login():
    assert host_cmd.format_host_list([HOST_A], NOW) == ["host-a  active  cap 8  beat 2m ago  login ok"]


def test_row_weights_and_capabilities_read_active_rows_that_carry_them():
    rows = [
        {**HOST_A, "weight": 2, "capabilities": ["go"]},
        {**HOST_A, "name": "pi", "weight": 3, "capabilities": "go,rust"},
        {**HOST_A, "name": "old", "capabilities": '["go"]'},
        {**HOST_A, "name": "gone", "state": "draining", "weight": 5, "capabilities": ["go"]},
    ]
    assert host_cmd.row_weights(rows) == {"host-a": 2, "pi": 3}
    assert host_cmd.row_capabilities(rows) == {"host-a": ["go"], "pi": ["go", "rust"], "old": ["go"]}


def test_a_row_from_a_store_with_no_weight_or_capabilities_column_yields_neither():
    assert (host_cmd.row_weights([HOST_A]), host_cmd.row_capabilities([HOST_A])) == ({}, {})


def test_add_argv_with_default_weight_and_no_capabilities_matches_host_upsert_exactly():
    assert host_cmd.add_argv("host-a", "host-a", 8, 1, "", "chair") == [
        "host", "upsert", "host-a", "--ssh", "host-a", "--capacity", "8", "--by", "chair",
    ]


def test_capacity_upsert_argv_changes_only_capacity_and_never_sends_weight_capabilities_or_state():
    row = {"name": "host-a", "ssh": "host-a.tail", "state": "draining", "weight": 5, "capabilities": ["go", "rust"]}
    argv = host_cmd.capacity_upsert_argv(row, 0, "chair")
    assert argv == ["host", "upsert", "host-a", "--ssh", "host-a.tail", "--capacity", "0", "--by", "chair"]
    assert "--weight" not in argv and "--capabilities" not in argv


def test_local_host_missing_is_true_only_when_no_row_names_the_host():
    assert host_cmd.local_host_missing([], "host-b") is True
    assert host_cmd.local_host_missing([{"name": "host-b"}], "host-b") is False


def test_local_host_add_argv_uses_the_hostname_as_its_own_ssh_destination():
    assert host_cmd.local_host_add_argv("host-b", 8, "chair") == [
        "host", "upsert", "host-b", "--ssh", "host-b", "--capacity", "8", "--by", "chair",
    ]


def test_a_host_that_never_beat_reads_never_and_login_unknown():
    row = {**HOST_A, "beat_at": None, "versions_json": None}
    assert host_cmd.format_host_list([row], NOW) == ["host-a  active  cap 8  beat never  login ?"]


def test_versions_report_keeps_a_missing_claude_as_null():
    assert host_cmd.versions_report("0.20.0", "v1", "v2", None, None)["claude"] is None


def test_a_draining_host_with_no_live_lane_reads_drained():
    row = {**HOST_A, "state": "draining"}
    assert host_cmd.format_host_list([row], NOW, {"host-a": 0}) == ["host-a  drained  cap 8  beat 2m ago  login ok"]


def test_a_draining_host_with_a_live_lane_still_reads_draining():
    row = {**HOST_A, "state": "draining"}
    assert host_cmd.format_host_list([row], NOW, {"host-a": 1}) == ["host-a  draining  cap 8  beat 2m ago  login ok"]


def test_an_active_host_is_unaffected_by_live_by_host():
    assert host_cmd.format_host_list([HOST_A], NOW, {"host-a": 0}) == ["host-a  active  cap 8  beat 2m ago  login ok"]


def test_a_draining_row_is_never_a_lane_host_no_matter_how_many_lanes_it_still_has():
    # host_rows_to_lane_hosts takes no live-lane count: a draining row is dropped on `state` alone,
    # the guarantee that already keeps a draining host from receiving new lanes.
    row = {**HOST_A, "state": "draining"}
    assert host_cmd.host_rows_to_lane_hosts([row]) == ()


def test_the_local_row_is_never_a_lane_host_candidate():
    rows = [HOST_A, {**HOST_A, "name": "host-b", "ssh": "host-b"}]
    assert host_cmd.host_rows_to_lane_hosts(rows, [LaneHost("host-a", "x", "/w")], local="host-b") == (
        LaneHost("host-a", "host-a", "/w"),
    )


def test_the_local_rows_workspace_dir_resolves_to_the_profiles_own_instead_of_refusing():
    rows = [{**HOST_A, "name": "host-b", "versions_json": None}]
    assert host_cmd.local_workspace_dir(rows, "host-b", "/srv/ws") == "/srv/ws"


def test_the_host_list_counts_this_machines_own_lanes_under_its_own_row_name():
    lanes = [run_store.Lane("r1", None, "t", "t"), run_store.Lane("r2", "host-a", "t", "t"), run_store.Lane("r3", "pi", "t", "t")]
    live = cli._live_by_host_name(lanes, "host-a")
    assert live == {"host-a": 2, "pi": 1}
    assert host_cmd.format_host_list([{**HOST_A, "state": "draining"}], NOW, live) == ["host-a  draining  cap 8  beat 2m ago  login ok"]


def test_a_gather_that_cannot_run_is_null_and_never_raises():
    def run(argv: list[str]) -> tuple[int, str]:
        raise OSError("no such tool")

    result = host_cmd.beat_versions(run, "0.20.0", "/h", None)
    host_facts = result.pop("host_facts")
    assert result == {
        "cox": "0.20.0", "graphs": None, "cartridges": None, "claude": None, "login_ok": None,
        "workspace_dir": None, "repos": [], "checkouts": {}, CHECKOUT_ENV_KEY: {},
    }
    assert isinstance(host_facts["os"], str) and host_facts["os"]
    assert isinstance(host_facts["cpu_count"], int)
    assert isinstance(host_facts["mem_total_gb"], float)


def test_the_beat_reads_login_from_claude_auth_status_json():
    def run(argv: list[str]) -> tuple[int, str]:
        if argv[:2] == ["claude", "auth"]:
            return 0, '{"loggedIn": true}'
        if "rev-parse" in argv:
            return (0, "feature-x\n") if argv[2] == "/h" else (1, "")
        if "rev-list" in argv:
            return (0, "4\n") if argv[2] == "/h" else (1, "")
        return 0, "v9\n"

    result = host_cmd.beat_versions(run, None, "/h", "/c", "/srv/ws", ["/h", "/c", "/t"])
    result.pop("host_facts")
    assert result == {
        "cox": "v9", "graphs": "v9", "cartridges": "v9", "claude": "v9", "login_ok": True,
        "workspace_dir": "/srv/ws", "repos": ["/h", "/c", "/t"],
        "checkouts": {
            "h": {"branch": "feature-x", "behind_main": 4},
            "c": {"branch": "", "behind_main": 0},
            "t": {"branch": "", "behind_main": 0},
        },
        CHECKOUT_ENV_KEY: {name: {"lock_hash": "v9", "synced_hash": "v9"} for name in ("h", "c", "t")},
    }


def _env_run(lock: str | None, stamp: dict[str, str], check_code: int, calls: list[list[str]]):
    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        if "hash-object" in argv:
            return (0, f"{lock}\n") if lock else (128, "fatal: no such path")
        if argv[0] == "cat":
            return (0, stamp["v"] + "\n") if "v" in stamp else (1, "")
        if argv[:3] == ["uv", "sync", "--check"]:
            return check_code, ""
        if argv[0] == "sh":
            stamp["v"] = argv[-2]
            return 0, ""
        return 1, ""

    return run


def _env_of(run) -> dict:
    return host_cmd.beat_versions(run, None, None, None, None, ["/srv/r"])[CHECKOUT_ENV_KEY]


def _installs_nothing(calls: list[list[str]]) -> bool:
    return all("install" not in c and (c[:2] != ["uv", "sync"] or "--check" in c) for c in calls)


def test_a_repo_with_a_lockfile_and_an_equal_stamp_beats_with_both_hashes_equal():
    calls: list[list[str]] = []
    assert _env_of(_env_run("h1", {"v": "h1"}, 0, calls)) == {"r": {"lock_hash": "h1", "synced_hash": "h1"}}
    assert _installs_nothing(calls)


def test_a_failing_env_check_keeps_the_old_stamp_next_to_the_new_lock_hash():
    calls: list[list[str]] = []
    assert _env_of(_env_run("new", {"v": "old"}, 1, calls)) == {"r": {"lock_hash": "new", "synced_hash": "old"}}
    assert all(c[0] != "sh" for c in calls) and _installs_nothing(calls)


def test_a_passing_env_check_with_no_stamp_writes_it_and_reports_synced_equal_to_lock():
    calls: list[list[str]] = []
    assert _env_of(_env_run("h2", {}, 0, calls)) == {"r": {"lock_hash": "h2", "synced_hash": "h2"}}
    assert any(c[0] == "sh" and c[-1] == "/srv/r/.venv/.cox-lock-hash" for c in calls) and _installs_nothing(calls)


def test_a_repo_with_no_lockfile_reports_lock_hash_none():
    calls: list[list[str]] = []
    assert _env_of(_env_run(None, {}, 0, calls)) == {"r": {"lock_hash": None, "synced_hash": None}}
    assert all(c[0] != "sh" for c in calls)


def test_a_run_that_raises_reports_both_hashes_none_and_raises_nothing():
    def run(argv: list[str]) -> tuple[int, str]:
        raise OSError("no such tool")

    assert _env_of(run) == {"r": {"lock_hash": None, "synced_hash": None}}


def test_checkout_detail_reads_branch_and_commits_behind_main_through_run_with_no_fetch():
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return (0, "main\n") if argv[-1] == "HEAD" else (0, "2\n")

    assert host_cmd._checkout_detail(run, "/srv/coxswain-tools") == ("coxswain-tools", {"branch": "main", "behind_main": 2})
    assert all(c[:2] != ["git", "fetch"] and "fetch" not in c for c in calls)


def test_checkout_detail_reads_branch_empty_and_behind_main_zero_when_a_repo_fails():
    def run(argv: list[str]) -> tuple[int, str]:
        return 128, "fatal: not a git repository"

    assert host_cmd._checkout_detail(run, "/srv/missing") == ("missing", {"branch": "", "behind_main": 0})


def test_a_table_only_host_takes_the_workspace_its_own_beat_recorded_and_becomes_dispatchable():
    beaten = {**HOST_A, "versions_json": '{"workspace_dir": "/srv/ws"}'}
    fresh = {**HOST_A, "name": "fresh", "ssh": "fresh", "versions_json": None}
    hosts = host_cmd.host_rows_to_lane_hosts([beaten, fresh])
    assert hosts == (LaneHost("host-a", "host-a", "/srv/ws"), LaneHost("fresh", "fresh", ""))
    assert host_cmd.dispatchable(hosts) == (["host-a"], ["fresh"])


def test_a_postgres_row_with_jsonb_versions_and_a_datetime_beat_reads_like_a_sqlite_one():
    row = {**HOST_A, "beat_at": datetime.datetime(2026, 9, 26, 11, 58, tzinfo=datetime.UTC), "versions_json": {"login_ok": True}}
    assert host_cmd.format_host_list([row], NOW) == ["host-a  active  cap 8  beat 2m ago  login ok"]


def test_profile_hosts_the_table_does_not_name_are_shadowed():
    assert host_cmd.shadowed([LaneHost("host-a", "j", "/w"), LaneHost("pi", "p", "/w")], ["host-a"]) == ["pi"]


def test_sync_uses_the_paths_the_hosts_own_beat_recorded():
    assert host_cmd.recorded_repos({"versions_json": '{"repos": ["/srv/graphs", "rel", 3]}'}) == ["/srv/graphs"]
    assert host_cmd.recorded_repos(None) == []


def test_sync_names_a_checkout_missing_on_the_host_apart_from_a_failed_pull():
    def run(argv: list[str]) -> tuple[int, str]:
        return 128, "fatal: cannot change to '/x/graphs': No such file or directory"

    assert host_cmd.sync_host("host-a", ["/x/graphs"], run) == (
        1, ["graphs  not updated (no checkout at /x/graphs on the host): fatal: cannot change to '/x/graphs': No such file or directory"],
    )


def test_sync_reports_a_pull_that_is_not_a_fast_forward_and_does_not_force():
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return (128, "fatal: Not possible to fast-forward, aborting.") if "/h" in argv[-1] else (0, "abc1234\n")

    assert host_cmd.sync_host("host-a", ["/h", "/c"], run) == (
        1, ["h  not updated (not a fast-forward): fatal: Not possible to fast-forward, aborting.", "c  abc1234"],
    )
    assert all("--force" not in " ".join(c) for c in calls)


def test_cox_host_add_runs_store_cli_host_upsert_with_the_holder_label(tmp_path, monkeypatch, capsys):
    (tmp_path / "ws").mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path / 'ws'}\n", encoding="utf-8")
    seen: list[list[str]] = []

    def fake_runner(runs_dir):
        def run(argv: list[str]) -> tuple[int, str]:
            seen.append(argv)
            return 0, '{"name": "host-a"}'

        return run

    monkeypatch.setattr(cli.store_cli, "runner", fake_runner)
    monkeypatch.setenv("COX_SESSION_LABEL", "chair")
    args = argparse.Namespace(profile=str(profile), name="host-a", ssh="host-a", capacity=8, weight=1, capabilities="")
    assert cli._host_add(args) == 0
    assert seen == [["host", "upsert", "host-a", "--ssh", "host-a", "--capacity", "8", "--by", "chair"]]
    assert capsys.readouterr().out == '{"name": "host-a"}\n'


def test_beat_name_refusal_allows_an_omitted_name():
    assert host_cmd.beat_name_refusal(None, "mac.local") is None


def test_beat_name_refusal_allows_the_hostname_itself():
    assert host_cmd.beat_name_refusal("mac.local", "mac.local") is None


def test_beat_name_refusal_allows_the_short_form_of_a_dotted_hostname():
    assert host_cmd.beat_name_refusal("mac", "mac.local") is None


def test_beat_name_refusal_refuses_a_different_name_with_both_names_in_the_text():
    assert host_cmd.beat_name_refusal("builder", "mac.local") == (
        "refusing: cox host beat records this machine (mac.local), not builder; run cox host beat on builder itself"
    )


def test_cox_host_beat_for_another_machine_exits_2_and_never_calls_the_store(monkeypatch, capsys):
    seen: list[list[str]] = []
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "mac.local")
    monkeypatch.setattr(cli.store_cli, "runner", lambda runs_dir: (lambda argv: seen.append(argv) or (0, "{}")))
    assert cli._host_beat(argparse.Namespace(name="builder")) == 2
    assert capsys.readouterr().out == (
        "refusing: cox host beat records this machine (mac.local), not builder; run cox host beat on builder itself\n"
    )
    assert seen == []


def test_cox_host_add_refuses_a_non_default_weight_before_calling_the_store(tmp_path, monkeypatch, capsys):
    (tmp_path / "ws").mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path / 'ws'}\n", encoding="utf-8")
    seen: list[list[str]] = []
    monkeypatch.setattr(cli.store_cli, "runner", lambda runs_dir: (lambda argv: seen.append(argv) or (0, "{}")))
    args = argparse.Namespace(profile=str(profile), name="host-a", ssh="host-a", capacity=8, weight=2, capabilities="")
    assert cli._host_add(args) == 1
    assert seen == []
    assert capsys.readouterr().out == "refused host-a: the store has no place for weight or capabilities yet\n"


def test_cox_host_add_names_the_profile_lane_hosts_the_table_now_overrides(tmp_path, monkeypatch, capsys):
    (tmp_path / "ws").mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        f"workspace_dir: {tmp_path / 'ws'}\nlane_hosts:\n  - name: pi\n    ssh: pi\n    workspace_dir: /w\n", encoding="utf-8",
    )
    monkeypatch.setattr(cli.store_cli, "runner", lambda runs_dir: lambda argv: (0, "{}"))
    args = argparse.Namespace(profile=str(profile), name="host-a", ssh="host-a", capacity=8, weight=1, capabilities="")
    assert cli._host_add(args) == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        "note: profile lane_hosts not in the hosts table are not lane hosts while it has rows: pi; add them with `cox host add`"
    )


def test_cox_host_capacity_builds_the_upsert_argv_from_the_stores_own_row(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("COX_SESSION_LABEL", raising=False)
    (tmp_path / "ws").mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path / 'ws'}\n", encoding="utf-8")
    row = {"name": "host-a", "ssh": "host-a", "capacity": 8, "state": "active", "weight": 1, "capabilities": None}
    monkeypatch.setattr(cli.run_store, "hosts", lambda runs_dir: [row])
    calls: list[list[str]] = []

    def fake_runner(runs_dir):
        def run(argv: list[str]) -> tuple[int, str]:
            calls.append(argv)
            return 0, "{}"

        return run

    monkeypatch.setattr(cli.store_cli, "runner", fake_runner)
    args = argparse.Namespace(profile=str(profile), name="host-a", n=0, json=False)

    assert cli._host_capacity(args) == 0
    assert calls == [host_cmd.capacity_upsert_argv(row, 0, "unlabeled")]
    assert "--capacity" in calls[0] and calls[0][calls[0].index("--capacity") + 1] == "0"
    assert "--ssh" in calls[0] and calls[0][calls[0].index("--ssh") + 1] == "host-a"


def test_cox_host_capacity_refuses_a_host_absent_from_the_table(tmp_path, monkeypatch, capsys):
    (tmp_path / "ws").mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path / 'ws'}\n", encoding="utf-8")
    monkeypatch.setattr(cli.run_store, "hosts", lambda runs_dir: [])

    def _refuse_runner(runs_dir):
        raise AssertionError("store_cli must not run for a host absent from the table")

    monkeypatch.setattr(cli.store_cli, "runner", _refuse_runner)
    args = argparse.Namespace(profile=str(profile), name="ghost", n=4, json=False)

    code = cli._host_capacity(args)

    assert code == 1
    assert capsys.readouterr().out.strip() == "refused ghost: no such host"


def _on(tmp_path, versions_json):
    import sqlite3

    (tmp_path / "ws" / "runs").mkdir(parents=True)
    conn = sqlite3.connect(tmp_path / "ws" / "runs" / "cox.db")
    conn.execute("CREATE TABLE hosts (name TEXT, ssh TEXT, capacity INTEGER, state TEXT, versions_json TEXT)")
    conn.execute("INSERT INTO hosts VALUES ('host-a', 'host-a', 8, 'active', ?)", (versions_json,))
    conn.commit()
    conn.close()
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path / 'ws'}\n", encoding="utf-8")
    args = argparse.Namespace(profile=str(profile), on="host-a", tier_ceiling=None, effort_ceiling=None, fix_attempts=None)
    return cli._lane_host_or_refuse(args, {"workspace_dir": str(tmp_path / "ws")})


def test_route_launch_on_accepts_a_table_only_host_once_it_has_beaten_with_its_workspace(tmp_path):
    assert _on(tmp_path, '{"workspace_dir": "/srv/ws"}') == (LaneHost("host-a", "host-a", "/srv/ws"), None)


def test_route_launch_on_refuses_a_table_only_host_that_never_beat_and_says_how_to_fix_it(tmp_path, capsys):
    assert _on(tmp_path, None) == (None, 2)
    assert capsys.readouterr().out == (
        "routing: host host-a has no workspace_dir: run `cox host beat host-a` on it, or list it under lane_hosts in the profile\n"
    )


def test_run_store_hosts_reads_rows_by_name_and_is_empty_with_no_store_or_table(tmp_path):
    import sqlite3

    from agent_tools import run_store

    assert run_store.hosts(tmp_path) == []
    conn = sqlite3.connect(tmp_path / "cox.db")
    conn.execute("CREATE TABLE runs (run_id TEXT)")
    assert run_store.hosts(tmp_path) == []
    conn.execute("CREATE TABLE hosts (name TEXT, ssh TEXT, capacity INTEGER, state TEXT)")
    conn.executemany("INSERT INTO hosts VALUES (?, ?, ?, ?)", [("pi", "pi", 2, "active"), ("host-a", "host-a", 8, "active")])
    conn.commit()
    conn.close()
    assert [r["name"] for r in run_store.hosts(tmp_path)] == ["host-a", "pi"]


def _remove_args(tmp_path, monkeypatch, state, lanes, dry_run=False):
    (tmp_path / "ws").mkdir(exist_ok=True)
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path / 'ws'}\n", encoding="utf-8")
    monkeypatch.delenv("COX_SESSION_LABEL", raising=False)
    monkeypatch.setattr(cli.run_store, "hosts", lambda runs_dir: [{**HOST_A, "state": state}])
    monkeypatch.setattr(cli.run_store, "live_lanes", lambda runs_dir, now: lanes)
    seen: list[list[str]] = []
    monkeypatch.setattr(cli.store_cli, "runner", lambda runs_dir: lambda argv: seen.append(argv) or (0, "{}"))
    return argparse.Namespace(profile=str(profile), name="host-a", dry_run=dry_run), seen


def test_cox_host_remove_deletes_a_draining_idle_host_and_prints_the_row(tmp_path, monkeypatch, capsys):
    args, seen = _remove_args(tmp_path, monkeypatch, "draining", [])
    assert cli._host_remove(args) == 0
    assert seen == [["host", "delete", "host-a", "--by", "unlabeled"]]
    assert capsys.readouterr().out.splitlines()[-2:] == [
        'removed host-a: {"beat_at": "2026-09-26T11:58:00+00:00", "capacity": 8, "name": "host-a", "ssh": "host-a", '
        '"state": "draining", "versions_json": "{\\"login_ok\\": true}"}',
        "  restore: cox host add host-a --ssh host-a --capacity 8",
    ]


def test_cox_host_remove_refuses_an_active_host_and_names_host_drain(tmp_path, monkeypatch, capsys):
    args, seen = _remove_args(tmp_path, monkeypatch, "active", [])
    assert cli._host_remove(args) == 1
    assert seen == []
    assert capsys.readouterr().out == "refused host-a: active; run `cox host drain host-a` first\n"


def test_cox_host_remove_refuses_a_draining_host_with_a_live_lane(tmp_path, monkeypatch, capsys):
    args, seen = _remove_args(tmp_path, monkeypatch, "draining", [run_store.Lane("r1", "host-a", "t", "t")])
    assert cli._host_remove(args) == 1
    assert seen == []
    assert capsys.readouterr().out == "refused host-a: live run r1 still on it; wait for it to finish\n"


def test_cox_host_remove_dry_run_deletes_nothing(tmp_path, monkeypatch, capsys):
    args, seen = _remove_args(tmp_path, monkeypatch, "draining", [], dry_run=True)
    assert cli._host_remove(args) == 0
    assert seen == []
    assert capsys.readouterr().out.splitlines()[-1] == "  restore: cox host add host-a --ssh host-a --capacity 8"


def test_removed_line_restores_a_null_ssh_row_with_its_name():
    row = {"name": "pi", "ssh": None, "capacity": 2, "state": "draining"}
    assert host_cmd.removed_line(row).splitlines()[-1] == "  restore: cox host add pi --ssh pi --capacity 2"


def _env_host(record: dict | None) -> dict:
    versions = {} if record is None else {CHECKOUT_ENV_KEY: record}
    return {**HOST_A, "versions_json": json.dumps(versions)}


FRESH = {"lock_hash": "a", "synced_hash": "a"}
STALE = {"lock_hash": "b", "synced_hash": "a"}
NEVER = {"lock_hash": "b", "synced_hash": None}


def test_env_rows_print_a_fresh_repo_as_fresh_without_an_instruction():
    assert host_cmd.env_rows(_env_host({"repo-x": FRESH})) == ["checkout env: repo-x fresh"]


def test_env_rows_print_a_stale_repo_with_the_sync_instruction():
    assert host_cmd.env_rows(_env_host({"repo-x": STALE})) == [
        "checkout env: repo-x stale  on host-a, in repo-x, run: uv sync --frozen --all-extras"
    ]


def test_env_rows_print_a_never_synced_repo_with_the_sync_instruction():
    assert host_cmd.env_rows(_env_host({"repo-x": NEVER})) == [
        "checkout env: repo-x never_synced  on host-a, in repo-x, run: uv sync --frozen --all-extras"
    ]


def test_env_rows_print_one_row_per_repo():
    assert host_cmd.env_rows(_env_host({"repo-x": FRESH, "repo-y": STALE})) == [
        "checkout env: repo-x fresh",
        "checkout env: repo-y stale  on host-a, in repo-y, run: uv sync --frozen --all-extras",
    ]


def test_env_rows_print_unknown_when_the_host_has_no_record():
    assert host_cmd.env_rows(_env_host(None)) == ["checkout env: unknown"]


def test_env_rows_print_unknown_for_unparseable_versions_json_and_raise_nothing():
    assert host_cmd.env_rows({**HOST_A, "versions_json": "{not json"}) == ["checkout env: unknown"]


def test_doctor_line_puts_the_env_rows_after_the_table_line():
    lines = host_cmd.doctor_line(_env_host({"repo-x": FRESH}), NOW).splitlines()
    assert lines[0].startswith("table: active") and lines[1:] == ["checkout env: repo-x fresh"]


def test_host_doctor_exits_the_same_for_a_stale_host_as_for_a_fresh_one(monkeypatch, capsys):
    def code_for(record: dict) -> int:
        monkeypatch.setattr(cli, "_leader_runs_dir_or_refuse", lambda a: ({}, "runs", None))
        monkeypatch.setattr(cli.run_store, "hosts", lambda runs_dir: [_env_host({"repo-x": record})])
        monkeypatch.setattr(cli, "_host_ssh", lambda a, profile, runs_dir: LaneHost("host-a", "host-a", ""))
        doctor = types.SimpleNamespace(doctor_on_host=lambda host, run, argv_for=None: (0, ["ok"]))
        monkeypatch.setattr(cli, "remote_doctor", doctor)
        return cli._host_doctor(argparse.Namespace(name="host-a"))

    assert code_for(STALE) == code_for(FRESH) == 0
    assert "checkout env: repo-x stale" in capsys.readouterr().out
