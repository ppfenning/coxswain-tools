import ast
import json
import posixpath
from pathlib import Path

import pytest

from agent_tools import setup_migrate
from agent_tools.home import Locations
from agent_tools.setup_migrate import (
    CHECKOUTS,
    OPS,
    ChairLease,
    HostRow,
    Lane,
    LeaseOutcome,
    migrate_plan,
    preflight_refusals,
    render_plan,
)

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
        + ["print"] * 3
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
    assert set(notes) == {"marketplace", "repo paths", "workspace"}
    assert notes["marketplace"]["checkouts"] == {CART: {"old": f"/u/repos/{CART}", "new": f"{NEW}/{CART}"}}
    assert notes["repo paths"]["checkouts"][TOOLS] == {"old": f"/u/repos/{TOOLS}", "new": f"{NEW}/{TOOLS}"}
    assert not of(migrate_plan(final_layout(), LOCS), "print")


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
    assert imported <= {"__future__", "json", "pathlib", "posixpath", "tomllib", "typing", "agent_tools"}
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
