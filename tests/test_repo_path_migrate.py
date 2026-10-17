import copy

from agent_tools import repo_path_migrate as m

HOME = "/".join(["", "home", "dev"])
OTHER_HOME = "/".join(["", "Users", "sam"])
REPO = "/".join([HOME, "repos", "coxswain"])
SURFACE = "/".join([REPO, "agent_tools", "cli.py"])
OUTSIDE = "/".join(["", "srv", "repos", "coxswain"])


def _row(repo, surfaces=(), task_id="t1"):
    return {
        "kind": "task",
        "initiative": "init",
        "task_id": task_id,
        "state": "ready",
        "surfaces": list(surfaces),
        "extra": {"id": task_id, "repo": repo},
    }


def _initiative(repo):
    return "\n".join(["---", "title: Thing", f"repo: {repo}", "---", "", "body " + REPO, ""])


def _ticket(*surfaces):
    items = [f"  - {s}" for s in surfaces]
    return "\n".join(["---", "state: ready", "title: T", "needs: []", "surfaces:", *items, "---", "", "body", ""])


def test_row_contracts_repo_and_leaves_input_unchanged():
    row = _row(REPO, [SURFACE, "agent_tools/app.py"])
    before = copy.deepcopy(row)
    out = m.migrate_row(row, HOME)
    assert out["extra"]["repo"] == "~/repos/coxswain"
    assert out["surfaces"] == ["~/repos/coxswain/agent_tools/cli.py", "agent_tools/app.py"]
    assert row == before


def test_row_with_another_machines_home_contracts():
    out = m.migrate_row(_row("/".join([OTHER_HOME, "repos", "x"])), HOME)
    assert out["extra"]["repo"] == "~/repos/x"


def test_initiative_text_changes_only_the_repo_line():
    text = _initiative(REPO)
    out = m.migrate_text(text, HOME)
    old_lines, new_lines = text.split("\n"), out.split("\n")
    assert [n for o, n in zip(old_lines, new_lines) if o != n] == ["repo: ~/repos/coxswain"]
    assert len(old_lines) == len(new_lines)


def test_quoted_repo_keeps_its_quotes():
    out = m.migrate_text(_initiative(f'"{REPO}"'), HOME)
    assert 'repo: "~/repos/coxswain"\n' in out


def test_ticket_block_surfaces_contract_only_absolute_entries():
    out = m.migrate_text(_ticket(SURFACE, "agent_tools/app.py"), HOME)
    assert out == _ticket("~/repos/coxswain/agent_tools/cli.py", "agent_tools/app.py")


def test_flow_surfaces_contract_and_keep_spacing():
    text = f"---\ntitle: T\nsurfaces: [{SURFACE},  a.py]\n---\n\nbody\n"
    assert (
        m.migrate_text(text, HOME)
        == "---\ntitle: T\nsurfaces: [~/repos/coxswain/agent_tools/cli.py,  a.py]\n---\n\nbody\n"
    )


def test_tilde_bare_name_and_outside_paths_return_none():
    assert m.migrate_row(_row("~/repos/coxswain", ["a.py"]), HOME) is None
    assert m.migrate_row(_row("coxswain"), HOME) is None
    assert m.migrate_row(_row(OUTSIDE, [OUTSIDE + "/a.py"]), HOME) is None
    assert m.migrate_text(_initiative("~/repos/coxswain"), HOME) is None
    assert m.migrate_text(_initiative(OUTSIDE), HOME) is None
    assert m.migrate_text("no header here\n", HOME) is None


def test_plan_lists_changes_in_stable_order():
    rows = [_row(REPO, task_id="b"), _row("~/x", task_id="skip"), _row(REPO, [SURFACE], task_id="a")]
    files = {"z/initiative.md": _initiative(REPO), "a/t.md": _ticket(SURFACE)}
    keys = [(c["key"], c["old"], c["new"]) for c in m.plan(rows, files, HOME)]
    assert keys == [
        ("task:init/b", REPO, "~/repos/coxswain"),
        ("task:init/a", REPO, "~/repos/coxswain"),
        ("task:init/a", SURFACE, "~/repos/coxswain/agent_tools/cli.py"),
        ("a/t.md", SURFACE, "~/repos/coxswain/agent_tools/cli.py"),
        ("z/initiative.md", REPO, "~/repos/coxswain"),
    ]


def test_plan_on_its_own_output_is_empty():
    rows = [_row(REPO, [SURFACE])]
    files = {"i/initiative.md": _initiative(REPO), "i/t.md": _ticket(SURFACE)}
    new_rows = [m.migrate_row(r, HOME) for r in rows]
    new_files = {p: m.migrate_text(t, HOME) for p, t in files.items()}
    assert m.plan(new_rows, new_files, HOME) == []


OLD = "~/repos/coxswain-tools"
NEW = "~/.local/share/coxswain/installs/coxswain-tools"
TOOLS_FILE = "agent_tools/cli.py"
ABS_TOOLS = "/".join([HOME, "repos", "coxswain-tools"])


def test_move_swaps_tilde_repo_and_file_beneath_it():
    row = _row(OLD, [OLD + "/" + TOOLS_FILE])
    out = m.migrate_row(row, HOME, move=(OLD, NEW))
    assert out["extra"]["repo"] == NEW
    assert out["surfaces"] == [NEW + "/" + TOOLS_FILE]


def test_move_leaves_sibling_prefix_and_relative_surfaces():
    row = _row(OLD + "-old", [OLD + "-old/" + TOOLS_FILE, TOOLS_FILE])
    assert m.migrate_row(row, HOME, move=(OLD, NEW)) is None
    mixed = m.migrate_row(_row(OLD, [TOOLS_FILE, OLD]), HOME, move=(OLD, NEW))
    assert mixed["surfaces"] == [TOOLS_FILE, NEW]


def test_move_contracts_an_absolute_linux_home_row_then_moves_it():
    out = m.migrate_row(_row(ABS_TOOLS, [ABS_TOOLS + "/" + TOOLS_FILE]), HOME, move=(OLD, NEW))
    assert out["extra"]["repo"] == NEW
    assert out["surfaces"] == [NEW + "/" + TOOLS_FILE]


def test_move_touches_only_repo_and_surfaces():
    row = {**_row(OLD, [OLD]), "title": OLD}
    out = m.migrate_row(row, HOME, move=(OLD, NEW))
    assert out["title"] == OLD
    assert out["state"] == "ready"


def test_move_swaps_flow_style_surfaces_in_a_work_file():
    line = "surfaces: [" + OLD + "/" + TOOLS_FILE + ", " + TOOLS_FILE + "]"
    text = "\n".join(["---", "state: ready", line, "---", "", "body", ""])
    out = m.migrate_text(text, HOME, move=(OLD, NEW))
    assert [n for o, n in zip(text.split("\n"), out.split("\n")) if o != n] == [
        "surfaces: [" + NEW + "/" + TOOLS_FILE + ", " + TOOLS_FILE + "]"
    ]


def test_move_swaps_block_style_surfaces_in_a_work_file():
    text = _ticket(OLD + "/" + TOOLS_FILE, TOOLS_FILE, OLD + "-old/" + TOOLS_FILE)
    out = m.migrate_text(text, HOME, move=(OLD, NEW))
    assert out.split("\n")[5:8] == [
        "  - " + NEW + "/" + TOOLS_FILE,
        "  - " + TOOLS_FILE,
        "  - " + OLD + "-old/" + TOOLS_FILE,
    ]


def test_move_swaps_an_absolute_repo_line_in_a_work_file():
    out = m.migrate_text(_initiative(ABS_TOOLS), HOME, move=(OLD, NEW))
    assert "repo: " + NEW + "\n" in out


def test_move_changes_equals_plan_with_move():
    rows = [_row(ABS_TOOLS, [ABS_TOOLS + "/" + TOOLS_FILE])]
    files = {"i/initiative.md": _initiative(OLD), "i/t.md": _ticket(OLD + "/" + TOOLS_FILE)}
    out = m.move_changes(rows, files, HOME, OLD, NEW)
    assert out == m.plan(rows, files, HOME, move=(OLD, NEW))
    assert [(c["key"], c["old"], c["new"]) for c in out] == [
        ("task:init/t1", ABS_TOOLS, NEW),
        ("task:init/t1", ABS_TOOLS + "/" + TOOLS_FILE, NEW + "/" + TOOLS_FILE),
        ("i/initiative.md", OLD, NEW),
        ("i/t.md", OLD + "/" + TOOLS_FILE, NEW + "/" + TOOLS_FILE),
    ]


def test_calls_without_move_return_the_same_changes():
    rows = [_row(OLD, [OLD + "/" + TOOLS_FILE]), _row(ABS_TOOLS)]
    files = {"i/initiative.md": _initiative(OLD)}
    expected = [{"key": "task:init/t1", "old": ABS_TOOLS, "new": OLD}]
    assert m.plan(rows, files, HOME) == expected
    assert m.plan(rows, files, HOME, move=None) == expected
