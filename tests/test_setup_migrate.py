import ast
import json
import posixpath
from pathlib import Path

import pytest

from agent_tools import setup_migrate
from agent_tools.home import Locations
from agent_tools.setup_migrate import CHECKOUTS, OPS, migrate_plan, render_plan

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
OLD_LINK = "../../repos/coxswain-cartridges"
NEW_LINK = "../../../h/coxswain/installs/coxswain-cartridges"


def entry(name, old_kind, new_kind, old_target=None):
    return {"old": f"/u/repos/{name}", "old_kind": old_kind, "new_kind": new_kind, "old_target": old_target}


def old_entry(name):
    return entry(name, "dir", "absent")


def crashed_entry(name):
    return entry(name, "absent", "dir")


def final_entry(name):
    return entry(name, "symlink", "dir", f"{NEW}/{name}")


def layout(make_entry, settings, links, home_record):
    return {
        "entries": {name: make_entry(name) for name in CHECKOUTS},
        "workspace": {"path": "/u/ws"},
        "settings": settings,
        "home_record": home_record,
        "cartridge_links": {LINK_BASE: links, LINK_LOCAL: links},
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
    return layout(old_entry, OLD_SETTINGS, OLD_LINK, None)


def final_layout():
    return layout(final_entry, NEW_SETTINGS, NEW_LINK, LOCS.home)


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
        + ["rewrite_profile_key"] * 3
        + ["rewrite_setting"]
        + ["print"] * 4
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
        (LINK_BASE, NEW_LINK, OLD_LINK),
        (LINK_LOCAL, NEW_LINK, OLD_LINK),
    ]
    fresh = {**old_layout(), "cartridge_links": {LINK_BASE: NEW_LINK, LINK_LOCAL: OLD_LINK}}
    assert [s["path"] for s in of(migrate_plan(fresh, LOCS), "symlink") if s["path"].startswith("/u/ws")] == [
        LINK_LOCAL
    ]


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
    assert set(notes) == {"editable installs", "marketplace", "repo paths", "workspace"}
    assert notes["marketplace"]["checkouts"] == {CART: {"old": f"/u/repos/{CART}", "new": f"{NEW}/{CART}"}}
    assert notes["repo paths"]["checkouts"][TOOLS] == {"old": f"/u/repos/{TOOLS}", "new": f"{NEW}/{TOOLS}"}
    assert not of(migrate_plan(final_layout(), LOCS), "print")


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

    def resolves(path, hops=0):
        node = fs.get(path)
        return node is not None and (node[0] == "dir" or (hops < 5 and resolves(node[1], hops + 1)))

    def within(path):
        return resolves(path) or (path not in ("/", "") and within(posixpath.dirname(path)))

    fs = dict(fs)
    for step in steps:
        if step["op"] == "move":
            assert fs[step["src"]][0] == "dir"
            assert step["dst"] not in fs
            fs[step["dst"]] = fs.pop(step["src"])
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
        new = f"{NEW}/{posixpath.basename(e['old'])}"
        if e["old_kind"] == "dir":
            fs[e["old"]] = ("dir",)
        elif e["old_kind"] == "symlink":
            fs[e["old"]] = ("symlink", e["old_target"])
        if e["new_kind"] == "dir":
            fs[new] = ("dir",)
    for path, target in observed["cartridge_links"].items():
        fs[path] = ("symlink", posixpath.normpath(posixpath.join(posixpath.dirname(path), target)))
    return fs


@pytest.mark.parametrize("make", [old_layout, final_layout])
def test_applying_the_plan_leaves_no_dangling_link_or_early_repoint(make):
    observed = make()
    fs, resolves = simulate(migrate_plan(observed, LOCS), model(observed))
    assert all(resolves(path) for path, node in fs.items() if node[0] == "symlink")


def test_dangle_check_over_a_layout_whose_links_were_removed():
    observed = layout(crashed_entry, OLD_SETTINGS, OLD_LINK, None)
    steps = migrate_plan(observed, LOCS)
    assert [s["path"] for s in of(steps, "symlink")[:3]] == [f"/u/repos/{n}" for n in CHECKOUTS]
    fs, resolves = simulate(steps, model(observed))
    assert all(resolves(path) for path, node in fs.items() if node[0] == "symlink")
    assert fs[LINK_BASE] == ("symlink", f"{NEW}/{CART}")


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
    assert imported <= {"__future__", "posixpath", "typing", "agent_tools"}
    assert "agent_tools.home" in {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
