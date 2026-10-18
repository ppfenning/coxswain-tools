import ast
import json
import posixpath
from dataclasses import replace
from pathlib import Path

import pytest

from agent_tools import repo_path_migrate, setup_migrate
from agent_tools.home import Locations
from agent_tools.setup_migrate import (
    CHECKOUTS,
    MARKER_NAME,
    OPS,
    ChairLease,
    FinishEdge,
    FinishFacts,
    HostRow,
    Lane,
    LeaseOutcome,
    LinkFact,
    delete_marker,
    finish,
    marker_path,
    migrate_plan,
    observe_links,
    preflight_refusals,
    removal_decisions,
    remove_link,
    render_plan,
    resolve_old_root,
)
from agent_tools.setup_migrate_hosts import ObservedHost

LOCS = Locations(
    home="/h/coxswain",
    installs="/h/coxswain/installs",
    workspaces="/h/coxswain/workspaces",
    config="/u/.config",
    state="/u/.local/state/coxswain",
    profile="/u/.config/agent-tools/profile.yaml",
    record="/u/.config/agent-tools/home",
)
NEW = "/h/coxswain/installs"
CART, GRAPHS, TOOLS = CHECKOUTS
LINK_BASE = "/u/ws/cartridges/base"
LINK_LOCAL = "/u/ws/cartridges/local"
OLD_LINKS = {
    LINK_BASE: "../../repos/coxswain-cartridges/cartridges/base",
    LINK_LOCAL: "../../repos/coxswain-cartridges/cartridges/local",
}
NEW_LINKS = {
    LINK_BASE: "../../../h/coxswain/installs/coxswain-cartridges/cartridges/base",
    LINK_LOCAL: "../../../h/coxswain/installs/coxswain-cartridges/cartridges/local",
}


def entry(name, old_kind, new_kind, old_target=None):
    return {"old": f"/u/repos/{name}", "old_kind": old_kind, "new_kind": new_kind, "old_target": old_target}


def old_entry(name):
    return entry(name, "dir", "absent")


def crashed_entry(name):
    return entry(name, "absent", "dir")


def final_entry(name):
    return entry(name, "symlink", "dir", f"{NEW}/{name}")


def bins(graphs, tools, tool_tools, tool_carts):
    return {
        GRAPHS: {"source": graphs},
        TOOLS: {"source": tools},
        f"tool:{TOOLS}": {"editable": tool_tools, "extras": ["traces", "parquet", "lake", "postgres"]},
        f"tool:{CART}": {"editable": tool_carts, "extras": []},
    }


OLD_BINS = bins(f"/u/repos/{GRAPHS}", f"/u/repos/{TOOLS}", f"/u/repos/{TOOLS}", f"/u/repos/{CART}")
FINAL_BINS = bins(f"{NEW}/{GRAPHS}", f"{NEW}/{TOOLS}", f"{NEW}/{TOOLS}", f"{NEW}/{CART}")


def layout(make_entry, settings, links, home_record, install_bins=None):
    return {
        "marketplace": f"{NEW}/{CART}",
        "marketplace_plugins": [],
        "claude_on_path": True,
        **({"install_bins": install_bins} if install_bins is not None else {}),
        "entries": {name: make_entry(name) for name in CHECKOUTS},
        "workspace": {"path": "/u/ws"},
        "settings": settings,
        "home_record": home_record,
        "cartridge_links": links,
        "worktrees": {CART: ["/u/wt/c1"], GRAPHS: ["/u/wt/g1", "/u/wt/g2"], TOOLS: []},
        "worktree_status": dict.fromkeys(CHECKOUTS, "clean") if make_entry is final_entry else {},
    }


OLD_SETTINGS = {
    "skills_roots": ["/u/repos/coxswain-cartridges/skills"],
    "provider_profile": "/u/repos/coxswain-cartridges/provider.yaml",
    "harness_dir": "/u/repos/coxswain-graphs",
    "cartridges_dir": "/u/ws/cartridges",
    "workspace_dir": "/u/ws",
    "umbrella_dir": "/u/ws",
}
NEW_SETTINGS = {
    **OLD_SETTINGS,
    "skills_roots": [f"{NEW}/coxswain-cartridges/skills"],
    "provider_profile": f"{NEW}/coxswain-cartridges/provider.yaml",
    "harness_dir": f"{NEW}/coxswain-graphs",
}


def old_layout():
    return layout(old_entry, OLD_SETTINGS, OLD_LINKS, None, OLD_BINS)


def final_layout():
    return layout(final_entry, NEW_SETTINGS, NEW_LINKS, LOCS.home, FINAL_BINS)


def ops(steps):
    return [s["op"] for s in steps]


def of(steps, op):
    return [s for s in steps if s["op"] == op]


def with_entry(observed, name, new_entry):
    return {**observed, "entries": {**observed["entries"], name: new_entry}}


def test_full_old_layout():
    steps = migrate_plan(old_layout(), LOCS)
    assert ops(steps) == (
        ["move", "symlink"] * 3
        + ["symlink"] * 2
        + ["worktree_repair", "verify"] * 3
        + ["run"] * 8
        + ["rewrite_profile_key"] * 3
        + ["rewrite_setting"]
        + ["print"] * 2
    )
    assert [s["key"] for s in of(steps, "rewrite_profile_key")] == ["skills_roots", "provider_profile", "harness_dir"]
    assert of(steps, "rewrite_profile_key")[0]["new"] == [f"{NEW}/coxswain-cartridges/skills"]
    assert of(steps, "rewrite_setting")[0] == {
        "op": "rewrite_setting",
        "key": "home_record",
        "path": LOCS.record,
        "old": None,
        "new": LOCS.home,
        "why": "The home record must name the resolved home.",
    }
    assert [(s["src"], s["dst"]) for s in of(steps, "move")] == [(f"/u/repos/{n}", f"{NEW}/{n}") for n in CHECKOUTS]


def test_cartridges_dir_and_workspace_fields_are_not_rewritten():
    keys = [s["key"] for s in of(migrate_plan(old_layout(), LOCS), "rewrite_profile_key")]
    assert keys == ["skills_roots", "provider_profile", "harness_dir"]


PARITY_HOME = "/srv/u"
REPO_MAP_OLD = {
    "o/other-a": "/srv/elsewhere/other-a",
    "o/coxswain-cartridges": f"/u/repos/{CART}",
    "o/other-b": "/u/repos/not-a-checkout",
    "o/coxswain-graphs": f"~/repos/{GRAPHS}",
    "o/other-c": "~/repos/other-c",
    "o/coxswain-tools": f"{PARITY_HOME}/repos/{TOOLS}",
}
REPO_MAP_NEW = {
    **REPO_MAP_OLD,
    "o/coxswain-cartridges": f"{NEW}/{CART}",
    "o/coxswain-graphs": f"{NEW}/{GRAPHS}",
    "o/coxswain-tools": f"{NEW}/{TOOLS}",
}


def repo_map_steps(repo_map, spellings=(PARITY_HOME,)):
    settings = OLD_SETTINGS if repo_map is None else {**OLD_SETTINGS, "repo_map": repo_map}
    steps = migrate_plan(layout(old_entry, settings, OLD_LINKS, None, OLD_BINS), LOCS, spellings)
    return [s for s in of(steps, "rewrite_profile_key") if s["key"] == "repo_map"]


def test_repo_map_rewrites_only_checkout_entries_and_keeps_order():
    given = dict(REPO_MAP_OLD)
    (step,) = repo_map_steps(given)
    assert step["new"] == REPO_MAP_NEW
    assert list(step["new"]) == list(REPO_MAP_OLD)
    assert step["old"] == REPO_MAP_OLD
    assert given == REPO_MAP_OLD


def test_repo_map_parity_spelling_needs_the_home_spelling_argument():
    (step,) = repo_map_steps(REPO_MAP_OLD, spellings=())
    assert step["new"] == {**REPO_MAP_NEW, "o/coxswain-tools": f"{PARITY_HOME}/repos/{TOOLS}"}


def test_repo_map_already_final_plans_no_step():
    assert repo_map_steps(REPO_MAP_NEW) == []


def test_repo_map_absent_plans_no_step():
    assert repo_map_steps(None) == []


def test_half_moved_layout_resumes_with_remaining_steps_only():
    half = with_entry(with_entry(old_layout(), CART, final_entry(CART)), GRAPHS, crashed_entry(GRAPHS))
    steps = migrate_plan({**half, "worktree_status": {CART: "clean"}}, LOCS)
    assert [(s["op"], s.get("src") or s.get("path")) for s in steps[:3]] == [
        ("symlink", f"/u/repos/{GRAPHS}"),
        ("move", f"/u/repos/{TOOLS}"),
        ("symlink", f"/u/repos/{TOOLS}"),
    ]
    assert [s["checkout"] for s in of(steps, "worktree_repair")] == [GRAPHS, TOOLS]
    assert CART not in json.dumps(of(steps, "move") + of(steps, "worktree_repair"))


def test_fully_moved_layout_is_a_single_skip():
    steps = migrate_plan(final_layout(), LOCS)
    assert steps == [{"op": "skip", "why": "Every checkout, link, setting and profile key is already final."}]
    assert render_plan(steps) == "1. skip - Every checkout, link, setting and profile key is already final."


def test_unfinished_settings_alone_are_not_a_skip():
    steps = migrate_plan({**final_layout(), "home_record": None}, LOCS)
    assert ops(steps)[:1] == ["rewrite_setting"]
    assert "skip" not in ops(steps)
    assert "move" not in ops(steps)


def test_destination_conflict_refuses_and_plans_nothing_else():
    steps = migrate_plan(with_entry(old_layout(), TOOLS, entry(TOOLS, "dir", "dir")), LOCS)
    assert ops(steps) == ["refuse"]
    assert steps[0]["checkout"] == TOOLS
    assert f"{NEW}/{TOOLS}" in steps[0]["why"]


@pytest.mark.parametrize("new_kind", ["dir", "file", "symlink"])
def test_new_path_beside_real_old_directory_refuses(new_kind):
    steps = migrate_plan(with_entry(old_layout(), GRAPHS, entry(GRAPHS, "dir", new_kind)), LOCS)
    assert ops(steps) == ["refuse"]
    assert GRAPHS in steps[0]["why"]


def test_checkout_absent_at_both_paths_refuses():
    steps = migrate_plan(with_entry(final_layout(), GRAPHS, entry(GRAPHS, "absent", "absent")), LOCS)
    assert ops(steps) == ["refuse"]
    assert GRAPHS in steps[0]["why"]


def test_checkout_missing_from_entries_refuses():
    observed = old_layout()
    observed["entries"] = {k: v for k, v in observed["entries"].items() if k != TOOLS}
    steps = migrate_plan(observed, LOCS)
    assert ops(steps) == ["refuse"]
    assert TOOLS in steps[0]["why"]


def test_all_checkouts_refusing_is_never_a_skip():
    observed = {**final_layout(), "entries": {n: entry(n, "absent", "absent") for n in CHECKOUTS}}
    assert ops(migrate_plan(observed, LOCS)) == ["refuse"] * 3
    assert ops(migrate_plan({**final_layout(), "entries": {}}, LOCS)) == ["refuse"] * 3


def test_old_symlink_elsewhere_or_dangling_refuses():
    elsewhere = entry(TOOLS, "symlink", "dir", "/u/other")
    dangling = entry(TOOLS, "symlink", "absent", f"{NEW}/{TOOLS}")
    for bad in (elsewhere, dangling):
        assert ops(migrate_plan(with_entry(final_layout(), TOOLS, bad), LOCS)) == ["refuse"]


def test_crash_between_move_and_link_emits_exactly_the_symlink():
    steps = migrate_plan(with_entry(final_layout(), GRAPHS, crashed_entry(GRAPHS)), LOCS)
    assert of(steps, "symlink") == [
        {
            "op": "symlink",
            "path": f"/u/repos/{GRAPHS}",
            "target": f"{NEW}/{GRAPHS}",
            "why": f"{GRAPHS} stays reachable at its old path through a link to the new one.",
        }
    ]
    assert of(steps, "move") == []
    assert [s["checkout"] for s in of(steps, "worktree_repair") + of(steps, "verify")] == [GRAPHS, GRAPHS]


def test_old_path_already_a_symlink_to_new_emits_no_step_for_it():
    linked = {**with_entry(old_layout(), GRAPHS, final_entry(GRAPHS)), "worktree_status": {GRAPHS: "clean"}}
    steps = migrate_plan(linked, LOCS)
    assert GRAPHS not in json.dumps([s for s in steps if s["op"] in ("move", "symlink", "worktree_repair", "verify")])


def test_workspace_by_location_or_under_the_root_is_never_moved():
    for path, phrase in (
        ("/u/ws", "where it is registered"),
        (f"{LOCS.workspaces}/main", "under the workspaces folder"),
    ):
        steps = migrate_plan({**old_layout(), "workspace": {"path": path}}, LOCS)
        note = of(steps, "print")[-1]
        assert note["path"] == path
        assert phrase in note["why"]
        assert all(s.get("src") != path and s.get("dst") != path for s in of(steps, "move"))
    assert not any(
        s.get("item") == "workspace" for s in of(migrate_plan({**old_layout(), "workspace": None}, LOCS), "print")
    )


def test_workspace_symlinks_are_repointed_only_when_stale():
    steps = migrate_plan(old_layout(), LOCS)
    links = [s for s in of(steps, "symlink") if s["path"] in (LINK_BASE, LINK_LOCAL)]
    assert [(s["path"], s["target"], s["old_target"]) for s in links] == [
        (LINK_BASE, NEW_LINKS[LINK_BASE], OLD_LINKS[LINK_BASE]),
        (LINK_LOCAL, NEW_LINKS[LINK_LOCAL], OLD_LINKS[LINK_LOCAL]),
    ]
    fresh = {**old_layout(), "cartridge_links": {LINK_BASE: NEW_LINKS[LINK_BASE], LINK_LOCAL: OLD_LINKS[LINK_LOCAL]}}
    assert [s["path"] for s in of(migrate_plan(fresh, LOCS), "symlink") if s["path"].startswith("/u/ws")] == [
        LINK_LOCAL
    ]


def test_workspace_links_keep_the_cartridges_subfolder():
    observed = {
        **old_layout(),
        "cartridge_links": {
            LINK_BASE: "/u/repos/coxswain-cartridges/cartridges/base",
            LINK_LOCAL: "/u/repos/coxswain-cartridges/cartridges/local",
        },
    }
    links = [s for s in of(migrate_plan(observed, LOCS), "symlink") if s["path"] in (LINK_BASE, LINK_LOCAL)]
    assert [(s["path"], s["target"]) for s in links] == [
        (LINK_BASE, "../../../h/coxswain/installs/coxswain-cartridges/cartridges/base"),
        (LINK_LOCAL, "../../../h/coxswain/installs/coxswain-cartridges/cartridges/local"),
    ]
    assert links[0]["target"].endswith("coxswain-cartridges/cartridges/base")
    assert links[1]["target"].endswith("coxswain-cartridges/cartridges/local")


def test_links_at_the_checkout_root_are_repointed_at_the_subfolders():
    root = "../../../h/coxswain/installs/coxswain-cartridges"
    observed = {**final_layout(), "cartridge_links": {LINK_BASE: root, LINK_LOCAL: root}}
    links = of(migrate_plan(observed, LOCS), "symlink")
    assert [(s["path"], s["target"]) for s in links] == [(p, NEW_LINKS[p]) for p in (LINK_BASE, LINK_LOCAL)]


def test_worktree_repair_and_verify_argv():
    steps = migrate_plan(old_layout(), LOCS)
    repairs = {s["checkout"]: s for s in of(steps, "worktree_repair")}
    verifies = {s["checkout"]: s for s in of(steps, "verify")}
    assert repairs[GRAPHS]["argv"] == ["git", "-C", f"{NEW}/{GRAPHS}", "worktree", "repair", "/u/wt/g1", "/u/wt/g2"]
    assert repairs[TOOLS]["argv"] == ["git", "-C", f"{NEW}/{TOOLS}", "worktree", "repair"]
    for name in CHECKOUTS:
        assert verifies[name]["argv"] == ["git", "-C", f"{NEW}/{name}", "worktree", "list", "--porcelain"]
        assert verifies[name]["expect"] == "no worktree names a missing path"
        assert steps.index(repairs[name]) < steps.index(verifies[name])
    last_link = max(i for i, s in enumerate(steps) if s["op"] in ("move", "symlink"))
    assert min(i for i, s in enumerate(steps) if s["op"] in ("worktree_repair", "verify")) > last_link


def test_every_step_has_a_known_op_and_a_why_and_no_delete_fields():
    for observed in (old_layout(), final_layout(), with_entry(old_layout(), TOOLS, entry(TOOLS, "dir", "dir"))):
        for step in migrate_plan(observed, LOCS):
            assert step["op"] in OPS
            assert step["why"].endswith(".")
            assert not {"digest", "replaces"} & set(step)


def test_unobserved_cartridge_links_refuse_instead_of_skipping():
    observed = {k: v for k, v in final_layout().items() if k != "cartridge_links"}
    steps = migrate_plan(observed, LOCS)
    assert ops(steps) == ["refuse"]
    assert "cartridge links" in steps[0]["why"]


def test_follow_on_prints_name_each_installer_item_and_prefix():
    notes = {s["item"]: s for s in of(migrate_plan(old_layout(), LOCS), "print")}
    assert set(notes) == {"repo paths", "workspace"}
    assert notes["repo paths"]["checkouts"][TOOLS] == {"old": f"/u/repos/{TOOLS}", "new": f"{NEW}/{TOOLS}"}
    assert not of(migrate_plan(final_layout(), LOCS), "print")


def test_repo_paths_line_points_at_finish():
    note = next(s for s in of(migrate_plan(old_layout(), LOCS), "print") if s["item"] == "repo paths")
    assert note["why"] == "run cox setup migrate --finish once every host has migrated."
    assert "repo paths - run cox setup migrate --finish once every host has migrated." in render_plan([note])


UHOME = "/u"
FINST = f"{UHOME}/coxswain/installs"
FOLD = f"{UHOME}/repos"
MARKER = json.dumps({"old_root": FOLD, "new_root": FINST})
OK_ROWS = [{"check": "home root", "ok": True, "detail": ""}]
BEAT = ObservedHost("m", "m", "/w", {TOOLS: "/m/x/coxswain/installs/coxswain-tools"}, None, "beat")
OLD_ROW = {"kind": "task", "initiative": "i", "task_id": "t", "extra": {"repo": f"~/repos/{TOOLS}"}, "surfaces": []}
OLD_FILE = {"work/i/p/t.md": f"---\nrepo: ~/repos/{GRAPHS}\n---\nbody\n"}
RIGHT = LinkFact(TOOLS, f"{FOLD}/{TOOLS}", "symlink", f"{FINST}/{TOOLS}")


def facts(**over):
    base = FinishFacts(
        FOLD, MARKER, FINST, UHOME, [{"op": "skip", "why": "x."}], OK_ROWS, [BEAT], [OLD_ROW], OLD_FILE, [RIGHT]
    )
    return base._replace(**over)


class Spy:
    """Records edge calls in order. `write` rewrites the rows it holds; `stuck` makes it a no-op."""

    def __init__(self, rows, files, stuck=False):
        self.rows, self.files, self.stuck, self.events = list(rows), dict(files), stuck, []

    def edge(self):
        return FinishEdge(self.write, self.rescan, self.remove, self.delete_marker)

    def write(self, moves):
        self.events.append("write")
        if not self.stuck:
            for old, new in moves:
                self.rows = [
                    m or r for r in self.rows for m in [repo_path_migrate.migrate_row(r, UHOME, move=(old, new))]
                ]
                self.files = {
                    k: repo_path_migrate.migrate_text(t, UHOME, move=(old, new)) or t for k, t in self.files.items()
                }

    def rescan(self):
        self.events.append("rescan")
        return self.rows, self.files

    def remove(self, path):
        self.events.append(f"remove {path}")

    def delete_marker(self):
        self.events.append("delete_marker")


def run_finish(f=None, apply=True, stuck=False):
    f = f or facts()
    spy = Spy(f.rows, f.files, stuck)
    return finish(f, apply, spy.edge()), spy


def test_no_marker_and_no_explicit_root_exits_two():
    result, spy = run_finish(facts(old_root=None, marker_text=None))
    assert (result.code, result.lines) == (2, ("finish: no old root known; pass --old-root",))
    assert spy.events == []


def test_no_marker_with_explicit_root_proceeds():
    result, _ = run_finish(facts(marker_text=None), apply=False)
    assert result.code == 0
    assert "finish: would delete the marker" not in result.lines


def test_marker_supplies_the_old_root_when_none_is_given():
    assert resolve_old_root(None, MARKER) == FOLD
    assert resolve_old_root("/x", MARKER) == "/x"
    assert resolve_old_root(None, "not json") is None
    assert resolve_old_root(None, "{}") is None


def test_gate_local_plan_not_single_skip():
    result, spy = run_finish(facts(plan=[{"op": "symlink", "why": "x."}]))
    assert (result.code, result.lines) == (1, ("finish: waiting on the local plan to reach a single skip step",))
    assert spy.events == []


def test_gate_failing_home_row():
    result, spy = run_finish(facts(home_rows=[{"check": "home root", "ok": False, "detail": "skipped"}]))
    assert (result.code, result.lines) == (1, ("finish: waiting on home check home root",))
    assert spy.events == []


def test_gate_host_without_a_beat():
    result, _ = run_finish(facts(hosts=[replace(BEAT, source="fallback")]))
    assert (result.code, result.lines) == (1, ("finish: waiting on host m to beat",))


def test_gate_host_not_migrated():
    old = replace(BEAT, checkouts={TOOLS: "/m/x/repos/coxswain-tools"})
    result, spy = run_finish(facts(hosts=[old]))
    assert (result.code, result.lines) == (1, ("finish: waiting on host m to migrate",))
    assert spy.events == []


def test_plan_form_lists_and_writes_nothing():
    result, spy = run_finish(apply=False)
    assert result.code == 0
    assert spy.events == []
    assert f"finish: would remove {FOLD}/{TOOLS}" in result.lines
    assert "finish: would delete the marker" in result.lines
    assert sorted(c["key"] for c in result.rewritten) == ["task:i/t", "work/i/p/t.md"]


def test_rewrite_then_clean_rescan_then_removal():
    result, spy = run_finish()
    assert result.code == 0
    assert spy.rows[0]["extra"]["repo"] == f"~/coxswain/installs/{TOOLS}"
    assert f"repo: ~/coxswain/installs/{GRAPHS}" in spy.files["work/i/p/t.md"]
    assert result.removed == (f"{FOLD}/{TOOLS}",)


def test_leftover_value_stops_before_any_removal():
    result, spy = run_finish(stuck=True)
    assert result.code == 1
    assert result.lines == (f"finish: 2 value(s) still under {FOLD}",)
    assert spy.events == ["write", "rescan"]


def test_marker_is_deleted_only_after_the_removals():
    _, spy = run_finish()
    assert spy.events == ["write", "rescan", f"remove {FOLD}/{TOOLS}", "delete_marker"]


def test_real_directory_and_wrong_link_are_kept():
    wrong = LinkFact(GRAPHS, f"{FOLD}/{GRAPHS}", "symlink", "/elsewhere")
    real = LinkFact(CART, f"{FOLD}/{CART}", "dir")
    result, spy = run_finish(facts(links=[RIGHT, wrong, real]))
    assert result.removed == (f"{FOLD}/{TOOLS}",)
    assert result.kept == (
        f"{FOLD}/{GRAPHS}: a link to /elsewhere, not {FINST}/{GRAPHS}",
        f"{FOLD}/{CART}: a real directory",
    )
    assert [e for e in spy.events if e.startswith("remove")] == [f"remove {FOLD}/{TOOLS}"]
    assert spy.events[-1] == "delete_marker"


def test_second_run_is_a_noop(tmp_path):
    installs, old = tmp_path / "installs", tmp_path / "repos"
    installs.mkdir()
    old.mkdir()
    for name in CHECKOUTS:
        (installs / name).mkdir()
        (old / name).symlink_to(installs / name)
    marker = tmp_path / MARKER_NAME
    marker.write_text(MARKER)
    links = observe_links(str(old), UHOME)
    assert [(link.kind, link.target) for link in links] == [("symlink", str(installs / n)) for n in CHECKOUTS]
    removals, kept = removal_decisions(links, str(installs))
    assert len(removals) == 3 and kept == ()
    for path in removals:
        remove_link(path)
    delete_marker(str(marker))
    assert not marker.exists() and not any(old.iterdir())
    again = facts(
        old_root=str(old),
        marker_text=None,
        installs=str(installs),
        rows=[],
        files={},
        links=observe_links(str(old), UHOME),
    )
    result, spy = run_finish(again)
    assert (result.code, result.lines) == (0, ("finish: nothing to do",))
    assert spy.events == []
    delete_marker(str(marker))


def test_observe_links_expands_only_a_tilde_root():
    tilde = observe_links("~/repos", UHOME)
    assert [(link.path, link.kind) for link in tilde] == [(f"/u/repos/{n}", "absent") for n in CHECKOUTS]
    absolute = observe_links("/nowhere/repos", UHOME)
    assert [link.path for link in absolute] == [f"/nowhere/repos/{n}" for n in CHECKOUTS]


def test_marker_path_sits_in_the_state_folder():
    assert marker_path(LOCS) == "/u/.local/state/coxswain/migrate.json"


def claude_argvs(observed):
    return [s["argv"] for s in installs_runs(observed) if s["argv"][0] == "claude"]


def test_old_registered_path_yields_remove_add_install_after_the_profile_rewrites():
    observed = {**old_layout(), "marketplace": f"/u/repos/{CART}"}
    steps = migrate_plan(observed, LOCS)
    tail = [
        s["argv"]
        for s in steps[max(i for i, s in enumerate(steps) if s["op"].startswith("rewrite")) + 1 :]
        if s["op"] == "run"
    ]
    assert tail == [
        ["claude", "plugin", "marketplace", "remove", "coxswain-cartridges"],
        ["claude", "plugin", "marketplace", "add", f"{NEW}/{CART}"],
        ["claude", "plugin", "install", "local-skills@coxswain-cartridges"],
    ]


def test_each_observed_plugin_is_reinstalled_in_order():
    observed = {
        **old_layout(),
        "marketplace": f"/u/repos/{CART}",
        "marketplace_plugins": ["b@coxswain-cartridges", "a@coxswain-cartridges"],
    }
    assert claude_argvs(observed)[2:] == [
        ["claude", "plugin", "install", "b@coxswain-cartridges"],
        ["claude", "plugin", "install", "a@coxswain-cartridges"],
    ]


def test_absent_registration_adds_without_a_remove():
    assert claude_argvs({**old_layout(), "marketplace": None}) == [
        ["claude", "plugin", "marketplace", "add", f"{NEW}/{CART}"],
        ["claude", "plugin", "install", "local-skills@coxswain-cartridges"],
    ]


def test_registered_path_equal_to_the_final_path_yields_no_marketplace_step():
    assert claude_argvs(old_layout()) == []
    assert not [s for s in migrate_plan(old_layout(), LOCS) if s["op"] == "skip"]


def test_claude_absent_yields_one_not_checked_print_and_no_claude_run_step():
    observed = {**old_layout(), "marketplace": f"/u/repos/{CART}", "claude_on_path": False}
    steps = migrate_plan(observed, LOCS)
    checked = [s for s in of(steps, "print") if s["item"] == "marketplace not checked"]
    assert [s["why"] for s in checked] == ["The Claude marketplace was not checked because claude is not on PATH."]
    assert claude_argvs(observed) == []
    assert "marketplace" not in {s["item"] for s in of(steps, "print")}


def installs_runs(observed):
    return of(migrate_plan(observed, LOCS), "run")


def test_old_layout_emits_eight_run_steps_in_order():
    runs = installs_runs(old_layout())
    assert [(s["argv"], s.get("cwd")) for s in runs] == [
        (["uv", "venv", "--clear", "-q"], f"{NEW}/{TOOLS}"),
        (["uv", "pip", "install", "-q", "-e", ".[dev]"], f"{NEW}/{TOOLS}"),
        (["uv", "venv", "--clear", "-q"], f"{NEW}/{GRAPHS}"),
        (["uv", "pip", "install", "-q", "-e", ".[dev]", "-e", f"{NEW}/{CART}"], f"{NEW}/{GRAPHS}"),
        (["uv", "venv", "--clear", "-q"], f"{NEW}/{CART}"),
        (["uv", "pip", "install", "-q", "-e", ".[dev]"], f"{NEW}/{CART}"),
        (["uv", "tool", "install", "--force", "-q", "-e", f"{NEW}/{TOOLS}[traces,parquet,lake,postgres]"], None),
        (["uv", "tool", "install", "--force", "-q", "-e", f"{NEW}/{CART}"], None),
    ]


def test_run_steps_follow_the_worktree_steps_and_precede_the_settings():
    kinds = ops(migrate_plan(old_layout(), LOCS))
    assert kinds.index("run") > max(i for i, k in enumerate(kinds) if k in ("worktree_repair", "verify"))
    assert kinds.index("run") < kinds.index("rewrite_profile_key")


def test_final_layout_emits_no_run_step_and_no_print():
    steps = migrate_plan(final_layout(), LOCS)
    assert not of(steps, "run")
    assert not of(steps, "print")


def test_matching_sources_emit_no_run_step_when_other_work_remains():
    steps = migrate_plan(layout(old_entry, OLD_SETTINGS, OLD_LINKS, None, FINAL_BINS), LOCS)
    assert not of(steps, "run")
    assert "editable installs" not in {s["item"] for s in of(steps, "print")}


def test_unobserved_install_bins_emit_no_run_step():
    assert not installs_runs({k: v for k, v in old_layout().items() if k != "install_bins"})


@pytest.mark.parametrize(
    "stale",
    [
        bins(None, f"{NEW}/{TOOLS}", f"{NEW}/{TOOLS}", f"{NEW}/{CART}"),
        bins(f"{NEW}/{GRAPHS}", None, f"{NEW}/{TOOLS}", f"{NEW}/{CART}"),
        bins(f"{NEW}/{GRAPHS}", f"{NEW}/{TOOLS}", None, f"{NEW}/{CART}"),
        bins(f"{NEW}/{GRAPHS}", f"{NEW}/{TOOLS}", f"{NEW}/{TOOLS}", None),
        bins(f"{NEW}/{GRAPHS}", f"{NEW}/{TOOLS}", f"{NEW}/{TOOLS}", f"/u/repos/{CART}"),
    ],
)
def test_one_absent_or_differing_source_emits_all_eight(stale):
    assert len(installs_runs({**final_layout(), "install_bins": stale})) == 8


def test_observed_extras_are_carried_into_each_tool_install():
    stale = {
        **OLD_BINS,
        f"tool:{TOOLS}": {"editable": None, "extras": []},
        f"tool:{CART}": {"editable": None, "extras": ["x", "y"]},
    }
    installs = installs_runs({**old_layout(), "install_bins": stale})[-2:]
    assert [s["argv"][-1] for s in installs] == [f"{NEW}/{TOOLS}", f"{NEW}/{CART}[x,y]"]


def test_venv_extras_returns_only_fully_installed_groups():
    traces = {"traces": ["pyarrow", "Duck_DB"]}
    assert setup_migrate.venv_extras(traces, {"pyarrow", "duck.db"}) == ["dev", "traces"]
    assert setup_migrate.venv_extras(traces, {"pyarrow"}) == ["dev"]
    assert setup_migrate.venv_extras({"postgres": ["psycopg"]}, set()) == ["dev"]


def test_unreadable_venv_prints_the_unknown_line_and_defaults_to_dev(tmp_path, capsys):
    assert setup_migrate.read_venv_extras(GRAPHS, str(tmp_path / "missing")) == ["dev"]
    assert capsys.readouterr().out == "migrate: coxswain-graphs venv extras unknown; installed .[dev] only\n"


def locs_at(installs):
    return Locations(
        home=LOCS.home,
        installs=str(installs),
        workspaces=LOCS.workspaces,
        config=LOCS.config,
        state=LOCS.state,
        profile=LOCS.profile,
        record=LOCS.record,
    )


def write_dist(root, checkout, dist, url):
    folder = root / checkout / ".venv/lib/python3.14/site-packages" / f"{dist}-0.1.0.dist-info"
    folder.mkdir(parents=True)
    (folder / "direct_url.json").write_text(json.dumps({"url": url, "dir_info": {"editable": True}}))


def write_receipt(tool_dir, tool, text):
    (tool_dir / tool).mkdir(parents=True)
    (tool_dir / tool / "uv-receipt.toml").write_text(text)


def test_observe_install_bins_reads_sources_and_receipts(tmp_path):
    installs, tool_dir = tmp_path / "installs", tmp_path / "tools"
    write_dist(installs, GRAPHS, "agent_graphs", "file:///src/graphs")
    write_dist(installs, TOOLS, "coxswain_tools", "file:///src/tools")
    write_receipt(
        tool_dir,
        "coxswain-tools",
        '[tool]\nrequirements = [{ name = "coxswain-tools", editable = "/src/tools", '
        'extras = ["traces", "parquet", "lake", "postgres"] }]\n',
    )
    write_receipt(
        tool_dir,
        "agent-cartridges",
        '[tool]\nrequirements = [{ name = "agent-cartridges", editable = "/src/carts" }]\n',
    )
    assert setup_migrate.observe_install_bins(locs_at(installs), str(tool_dir)) == {
        GRAPHS: {"source": "/src/graphs"},
        TOOLS: {"source": "/src/tools"},
        f"tool:{TOOLS}": {"editable": "/src/tools", "extras": ["traces", "parquet", "lake", "postgres"]},
        f"tool:{CART}": {"editable": "/src/carts", "extras": []},
    }


def test_observe_install_bins_records_missing_and_broken_files_as_absent(tmp_path):
    write_receipt(tmp_path / "tools", "coxswain-tools", "not [valid toml")
    absent = {"editable": None, "extras": []}
    assert setup_migrate.observe_install_bins(locs_at(tmp_path / "installs"), str(tmp_path / "tools")) == {
        GRAPHS: {"source": None},
        TOOLS: {"source": None},
        f"tool:{TOOLS}": absent,
        f"tool:{CART}": absent,
    }


def unit_text(root):
    return f"[Service]\nEnvironment=PATH={root}/coxswain-tools/.venv/bin:/usr/bin\nExecStart=/usr/bin/cox chair run\n"


def test_unit_naming_the_old_root_yields_one_last_chair_unit_step():
    baseline = migrate_plan(old_layout(), LOCS)
    steps = migrate_plan({**old_layout(), "chair_unit": unit_text("/u/repos")}, LOCS)
    assert len(of(steps, "chair_unit")) == 1
    assert steps[-1]["op"] == "chair_unit"
    assert steps[:-1] == baseline


def test_unit_regenerated_to_the_new_root_yields_no_chair_unit_step():
    steps = migrate_plan({**old_layout(), "chair_unit": unit_text(NEW)}, LOCS)
    assert not of(steps, "chair_unit")
    assert steps == migrate_plan(old_layout(), LOCS)


def test_absent_chair_unit_yields_no_chair_unit_step():
    steps = migrate_plan({**old_layout(), "chair_unit": None}, LOCS)
    assert not of(steps, "chair_unit")
    assert steps == migrate_plan(old_layout(), LOCS)


BARE_UNIT = "[Service]\nExecStart=/usr/bin/cox chair run\n"
DROP_IN = "[Service]\nEnvironment=PATH=/u/repos/coxswain-tools/.venv/bin:/u/repos/coxswain-graphs/.venv/bin:/usr/bin\n"


def dropin_observed(**extra):
    return {**old_layout(), "chair_unit": BARE_UNIT, "chair_unit_drop_ins": [("10-path.conf", DROP_IN)], **extra}


def test_pure_rewrite_changes_only_the_old_folders_on_path_lines():
    text = (
        '[Service]\nEnvironment="OTHER=/u/repos/coxswain-tools"\n'
        "Environment=PATH=/u/repos/coxswain-tools/.venv/bin:/u/repos/coxswain-tools-x/bin:/usr/bin\n"
    )
    pairs = (("/u/repos/coxswain-tools", f"{NEW}/coxswain-tools"),)
    assert setup_migrate.rewrite_dropin_path(text, pairs) == (
        '[Service]\nEnvironment="OTHER=/u/repos/coxswain-tools"\n'
        f"Environment=PATH={NEW}/coxswain-tools/.venv/bin:/u/repos/coxswain-tools-x/bin:/usr/bin\n"
    )


def test_dropin_only_path_with_old_folders_plans_a_rewrite_targeting_the_drop_in():
    steps = migrate_plan(dropin_observed(), LOCS)
    assert not of(steps, "chair_unit")
    (step,) = of(steps, "chair_unit_dropin")
    assert steps[-1] is step
    assert step["dropin"] == "10-path.conf"
    assert step["restart"] is False
    assert ["/u/repos/coxswain-tools", f"{NEW}/coxswain-tools"] in step["pairs"]
    assert steps[:-1] == migrate_plan(old_layout(), LOCS)


def test_active_service_plans_the_restart_and_inactive_does_not():
    assert of(migrate_plan(dropin_observed(chair_active=True), LOCS), "chair_unit_dropin")[0]["restart"] is True
    assert of(migrate_plan(dropin_observed(chair_active=False), LOCS), "chair_unit_dropin")[0]["restart"] is False


def test_unit_only_plan_is_unchanged_when_drop_ins_are_present_but_do_not_set_path():
    observed = {**old_layout(), "chair_unit": unit_text("/u/repos"), "chair_unit_drop_ins": [("a.conf", "[Service]\n")]}
    assert migrate_plan(observed, LOCS) == migrate_plan({**old_layout(), "chair_unit": unit_text("/u/repos")}, LOCS)
    assert of(migrate_plan(observed, LOCS), "chair_unit")[0] == {
        "op": "chair_unit",
        "why": "The chair systemd unit still names the old checkout root in its PATH, so it is regenerated.",
    }


def test_already_migrated_dropin_path_plans_nothing():
    migrated = DROP_IN.replace("/u/repos", NEW)
    observed = dropin_observed(chair_unit_drop_ins=[("10-path.conf", migrated)])
    assert migrate_plan(observed, LOCS) == migrate_plan(old_layout(), LOCS)


def test_a_migrated_machine_with_its_path_in_a_drop_in_plans_only_the_skip():
    # every setting already names the installs folder, so each checkout is observed at its new path
    migrated = DROP_IN.replace("/u/repos", NEW)
    at_new = {name: {**final_entry(name), "old": f"{NEW}/{name}"} for name in CHECKOUTS}
    observed = {**final_layout(), "entries": at_new, "chair_unit": BARE_UNIT, "chair_unit_drop_ins": [("10-path.conf", migrated)]}
    assert [step["op"] for step in migrate_plan(observed, LOCS)] == ["skip"]


def test_a_later_dropin_overrides_an_old_unit_path_so_the_unit_is_not_regenerated():
    observed = {
        **old_layout(),
        "chair_unit": unit_text("/u/repos"),
        "chair_unit_drop_ins": [("10-path.conf", unit_text(NEW))],
    }
    assert migrate_plan(observed, LOCS) == migrate_plan(old_layout(), LOCS)


def test_checkout_linked_but_not_repaired_resumes_with_repair_and_verify_not_a_skip():
    stale = {**final_layout(), "worktree_status": {CART: "clean", GRAPHS: "stale", TOOLS: "clean"}}
    steps = migrate_plan(stale, LOCS)
    assert [(s["op"], s["checkout"]) for s in steps[:2]] == [("worktree_repair", GRAPHS), ("verify", GRAPHS)]
    assert not {"skip", "move", "symlink"} & set(ops(steps))
    unknown = {k: v for k, v in final_layout().items() if k != "worktree_status"}
    assert [s["checkout"] for s in of(migrate_plan(unknown, LOCS), "worktree_repair")] == list(CHECKOUTS)


def test_unobserved_worktrees_of_a_checkout_needing_repair_refuse():
    observed = old_layout()
    observed["worktrees"] = {k: v for k, v in observed["worktrees"].items() if k != GRAPHS}
    steps = migrate_plan(observed, LOCS)
    assert ops(steps) == ["refuse"]
    assert GRAPHS in steps[0]["why"]
    assert ops(migrate_plan({**final_layout(), "worktrees": {}}, LOCS)) == ["skip"]


def test_tilde_form_settings_are_expanded_before_comparing():
    tilde = {
        **OLD_SETTINGS,
        "harness_dir": "~/repos/coxswain-graphs",
        "skills_roots": ["~/repos/coxswain-cartridges/skills"],
    }
    steps = migrate_plan({**final_layout(), "settings": tilde}, LOCS)
    rewrites = {s["key"]: s for s in of(steps, "rewrite_profile_key")}
    assert rewrites["harness_dir"]["old"] == "~/repos/coxswain-graphs"
    assert rewrites["harness_dir"]["new"] == f"{NEW}/coxswain-graphs"
    assert rewrites["skills_roots"]["new"] == [f"{NEW}/coxswain-cartridges/skills"]
    assert "skip" not in ops(steps)


def test_simulator_rejects_a_link_to_a_missing_entry():
    with pytest.raises(AssertionError):
        simulate([{"op": "symlink", "path": "/u/x", "target": f"{NEW}/missing"}], {f"{NEW}/{CART}": ("dir",)})


def test_steps_are_json_serialisable():
    for observed in (old_layout(), final_layout()):
        steps = migrate_plan(observed, LOCS)
        assert json.loads(json.dumps(steps)) == steps


def test_render_plan_is_one_line_per_step():
    steps = migrate_plan(old_layout(), LOCS)
    lines = render_plan(steps).split("\n")
    assert len(lines) == len(steps)
    assert (
        lines[0] == f"1. move /u/repos/{CART} -> {NEW}/{CART} - {CART} belongs under the installs folder of the home."
    )
    refuse = render_plan(migrate_plan(with_entry(old_layout(), TOOLS, entry(TOOLS, "dir", "dir")), LOCS))
    assert refuse.startswith(f"1. refuse {TOOLS} - {TOOLS} has a real directory")
    assert render_plan([]) == ""


def simulate(steps, fs):
    """Apply steps to a dict model of the filesystem, failing on any dangling link, replaced dir or early re-point."""

    def real(path, hops=0):
        """The physical path with every symlinked ancestor substituted, or None when links loop."""
        parent = posixpath.dirname(path)
        base = path if parent == path else real(parent, hops + 1) if hops < 8 else None
        full = None if base is None else path if parent == path else posixpath.join(base, posixpath.basename(path))
        node = fs.get(full)
        return real(node[1], hops + 1) if node and node[0] == "symlink" and hops < 8 else full

    def resolves(path):
        return fs.get(real(path), (None,))[0] == "dir"

    def within(path):
        return resolves(path) or (path not in ("/", "") and within(posixpath.dirname(path)))

    fs = dict(fs)
    for step in steps:
        if step["op"] == "move":
            assert fs[step["src"]][0] == "dir"
            assert step["dst"] not in fs
            inside = [k for k in fs if k == step["src"] or k.startswith(step["src"] + "/")]
            fs.update({step["dst"] + k[len(step["src"]) :]: fs.pop(k) for k in inside})
        elif step["op"] == "symlink":
            assert fs.get(step["path"], ("symlink",))[0] == "symlink"
            target = posixpath.normpath(posixpath.join(posixpath.dirname(step["path"]), step["target"]))
            assert resolves(target), step
            fs[step["path"]] = ("symlink", target)
        elif step["op"] in ("rewrite_profile_key", "rewrite_setting") and step["key"] != "home_record":
            new = step["new"] if isinstance(step["new"], list) else [step["new"]]
            assert all(within(path) for path in new), step
    return fs, resolves


def model(observed):
    fs = {}
    for e in observed["entries"].values():
        name = posixpath.basename(e["old"])
        new = f"{NEW}/{name}"
        folders = (
            (lambda root: {f"{root}/cartridges/{n}": ("dir",) for n in ("base", "local")})
            if name == CART
            else (lambda root: {})
        )
        if e["old_kind"] == "dir":
            fs.update({e["old"]: ("dir",), **folders(e["old"])})
        elif e["old_kind"] == "symlink":
            fs[e["old"]] = ("symlink", e["old_target"])
        if e["new_kind"] == "dir":
            fs.update({new: ("dir",), **folders(new)})
    for path, target in observed["cartridge_links"].items():
        fs[path] = ("symlink", posixpath.normpath(posixpath.join(posixpath.dirname(path), target)))
    return fs


@pytest.mark.parametrize("make", [old_layout, final_layout])
def test_applying_the_plan_leaves_no_dangling_link_or_early_repoint(make):
    observed = make()
    fs, resolves = simulate(migrate_plan(observed, LOCS), model(observed))
    assert all(resolves(path) for path, node in fs.items() if node[0] == "symlink")


def test_dangle_check_over_a_layout_whose_links_were_removed():
    observed = layout(crashed_entry, OLD_SETTINGS, OLD_LINKS, None)
    steps = migrate_plan(observed, LOCS)
    assert [s["path"] for s in of(steps, "symlink")[:3]] == [f"/u/repos/{n}" for n in CHECKOUTS]
    fs, resolves = simulate(steps, model(observed))
    assert all(resolves(path) for path, node in fs.items() if node[0] == "symlink")
    assert fs[LINK_BASE] == ("symlink", f"{NEW}/{CART}/cartridges/base")


def test_no_symlink_dangles_after_any_step_but_a_move_and_rewrites_come_last():
    observed = old_layout()
    steps = migrate_plan(observed, LOCS)
    for k, step in enumerate(steps, 1):
        if step["op"] != "move":
            fs, resolves = simulate(steps[:k], model(observed))
            assert all(resolves(path) for path, node in fs.items() if node[0] == "symlink"), step
    first_rewrite = min(i for i, s in enumerate(steps) if s["op"].startswith("rewrite"))
    last_move = max(i for i, s in enumerate(steps) if s["op"] == "move")
    assert last_move < first_rewrite


def test_module_imports_only_standard_library_and_home():
    tree = ast.parse(Path(setup_migrate.__file__).read_text())
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }
    assert imported <= {"__future__", "collections", "json", "pathlib", "posixpath", "re", "tomllib", "typing", "agent_tools"}
    assert "agent_tools.home" in {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}


ME = "box1"
FREE_CHAIR = ChairLease(released=True, stale=False, host=ME)


def refusals(leases=(), lanes=(), chair=FREE_CHAIR, hosts=()):
    return preflight_refusals(tuple(leases), tuple(lanes), ME, chair, tuple(hosts))


def test_preflight_clean_returns_empty_list():
    granted = LeaseOutcome("/u/repos/coxswain-tools", granted=True)
    assert refusals(leases=[granted]) == []


def test_preflight_refused_lease_names_path_and_holder():
    lease = LeaseOutcome("/u/repos/coxswain-tools", granted=False, holder="run-7")
    assert refusals(leases=[lease]) == ["migrate: refusing, run-7 holds /u/repos/coxswain-tools"]


def test_preflight_errored_lease_names_path():
    lease = LeaseOutcome("/u/repos/coxswain-graphs", granted=False, error="timeout")
    assert refusals(leases=[lease]) == [
        "migrate: refusing, an unknown holder (lease error: timeout) holds /u/repos/coxswain-graphs"
    ]


def test_preflight_local_live_lane_counts():
    assert refusals(lanes=[Lane("lane-a", ME)]) == ["migrate: refusing, live lane lane-a holds a run on this machine"]


def test_preflight_lane_with_no_host_counts():
    assert refusals(lanes=[Lane("lane-b", None)]) == ["migrate: refusing, live lane lane-b holds a run on this machine"]


def test_preflight_remote_lane_is_ignored():
    assert refusals(lanes=[Lane("lane-c", "box2")]) == []


def test_preflight_chair_lease_held_here_counts():
    held = ChairLease(released=False, stale=False, host=ME)
    assert refusals(chair=held) == ["migrate: refusing, the chair holds its lease on box1"]


@pytest.mark.parametrize(
    "chair",
    [
        ChairLease(released=True, stale=False, host=ME),
        ChairLease(released=False, stale=True, host=ME),
        ChairLease(released=False, stale=False, host="box2"),
        ChairLease(released=False, stale=False, host=None),
    ],
)
def test_preflight_chair_lease_not_held_here_is_ignored(chair):
    assert refusals(chair=chair) == []


def test_preflight_active_host_row_asks_for_drain():
    assert refusals(hosts=[HostRow(ME, "active")]) == [
        "migrate: refusing, host box1 holds active state, run cox host drain box1 first"
    ]


@pytest.mark.parametrize("row", [HostRow(ME, "draining"), HostRow(ME, "drained"), HostRow("box2", "active")])
def test_preflight_other_host_rows_are_ignored(row):
    assert refusals(hosts=[row]) == []


def test_preflight_orders_leases_lanes_chair_host_row():
    lines = refusals(
        leases=[LeaseOutcome("/p", granted=False, holder="h")],
        lanes=[Lane("l", None)],
        chair=ChairLease(released=False, stale=False, host=ME),
        hosts=[HostRow(ME, "active")],
    )
    assert [line.split(", ", 1)[1].split(" holds")[0] for line in lines] == [
        "h",
        "live lane l",
        "the chair",
        "host box1",
    ]
