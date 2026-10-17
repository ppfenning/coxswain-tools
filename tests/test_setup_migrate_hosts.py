import dataclasses
import json
import shlex

from agent_tools.home import resolve
from agent_tools.setup_migrate_hosts import ObservedHost, host_migrate_plan, render_hosts_plan

LOCATIONS = resolve({}, "/home/user", "linux")

LINUX_MIGRATED = ObservedHost(
    name="linux1",
    ssh="linux1",
    workspace_dir="/home/user/.local/share/coxswain/workspaces/main",
    checkouts={"coxswain-tools": "/home/user/.local/share/coxswain/installs/coxswain-tools"},
    profile_workspace_dir="/home/user/.local/share/coxswain/workspaces/main",
    source="beat",
)
MAC_OLD = ObservedHost(
    name="mac1",
    ssh="mac1",
    workspace_dir="/Volumes/data/work",
    checkouts={"coxswain-tools": "/Volumes/data/repos/coxswain-tools"},
    profile_workspace_dir="/Volumes/data/work",
    source="beat",
)


def ops(plan):
    return [[step.op for step in group] for group in plan]


def test_linux_migrated_and_macos_not():
    plan = host_migrate_plan([LINUX_MIGRATED, MAC_OLD], LOCATIONS)
    assert ops(plan) == [["skip"], ["remote_migrate"]]
    assert plan[1][0].argv == ["ssh", "mac1", "cox", "setup", "migrate", "--apply"]


def test_host_before_and_after_beat_reports_installs():
    after = dataclasses.replace(MAC_OLD, checkouts={"coxswain-tools": "/Volumes/data/coxswain/installs/coxswain-tools"})
    assert ops(host_migrate_plan([MAC_OLD], LOCATIONS)) == [["remote_migrate"]]
    assert ops(host_migrate_plan([after], LOCATIONS)) == [["skip"]]


def test_stale_profile_entry_is_rewritten_with_beat_values():
    stale = dataclasses.replace(LINUX_MIGRATED, profile_workspace_dir="/old/workspaces")
    (step,) = host_migrate_plan([stale], LOCATIONS)[0]
    assert step.op == "rewrite_host_workspace"
    assert step.argv == []
    assert step.payload == {
        "workspace_dir": "/home/user/.local/share/coxswain/workspaces/main",
        "checkouts": {"coxswain-tools": "/home/user/.local/share/coxswain/installs/coxswain-tools"},
    }


def test_missing_profile_entry_is_rewritten():
    unset = dataclasses.replace(LINUX_MIGRATED, profile_workspace_dir=None)
    assert ops(host_migrate_plan([unset], LOCATIONS)) == [["rewrite_host_workspace"]]


def test_fallback_host_gets_print_only():
    fallback = dataclasses.replace(MAC_OLD, source="fallback")
    (step,) = host_migrate_plan([fallback], LOCATIONS)[0]
    assert (step.op, step.argv, step.payload) == ("print", [], {})
    assert "until it beats" in step.why


def test_beat_with_no_checkouts_is_not_migrated():
    empty = dataclasses.replace(LINUX_MIGRATED, checkouts={})
    assert ops(host_migrate_plan([empty], LOCATIONS)) == [["remote_migrate"]]


def test_one_checkout_outside_installs_is_not_migrated():
    mixed = dataclasses.replace(
        LINUX_MIGRATED,
        checkouts={"coxswain-tools": "/h/installs/coxswain-tools", "coxswain-graphs": "/h/repos/coxswain-graphs"},
    )
    assert ops(host_migrate_plan([mixed], LOCATIONS)) == [["remote_migrate"]]


def test_no_hosts_is_empty_plan():
    assert host_migrate_plan([], LOCATIONS) == []
    assert render_hosts_plan([]) == []


def test_weird_alias_stays_one_argv_element_and_renders_quoted():
    weird = dataclasses.replace(MAC_OLD, ssh="we ird$host;name")
    (step,) = host_migrate_plan([weird], LOCATIONS)[0]
    assert step.argv == ["ssh", "we ird$host;name", "cox", "setup", "migrate", "--apply"]
    (line,) = render_hosts_plan([[step]])
    assert shlex.join(step.argv) in line
    assert "'we ird$host;name'" in line


def test_groups_never_mix_hosts():
    hosts = [LINUX_MIGRATED, MAC_OLD, dataclasses.replace(MAC_OLD, name="mac2", source="fallback")]
    plan = host_migrate_plan(hosts, LOCATIONS)
    assert [{step.host for step in group} for group in plan] == [{"linux1"}, {"mac1"}, {"mac2"}]
    assert [line.split(":")[0] for line in render_hosts_plan(plan)] == ["linux1", "mac1", "mac2"]


def test_every_step_serialises_to_json():
    stale = dataclasses.replace(LINUX_MIGRATED, name="stale", profile_workspace_dir="/old")
    fallback = dataclasses.replace(MAC_OLD, name="fb", source="fallback")
    plan = host_migrate_plan([LINUX_MIGRATED, MAC_OLD, stale, fallback], LOCATIONS)
    decoded = [json.loads(json.dumps(dataclasses.asdict(step))) for group in plan for step in group]
    assert [d["op"] for d in decoded] == ["skip", "remote_migrate", "rewrite_host_workspace", "print"]
