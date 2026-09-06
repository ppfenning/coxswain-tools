from agent_tools.route_sync import Item, plan, render


def _item(**over):
    base = {"id": "item-1", "title": "Fix the thing", "body": "Some body.", "repo": "acme/widgets",
                "state": "ready", "phase": "core", "run": "run-7", "cost_usd": 1.5, "gate": "tests", "issue": None}
    base.update(over)
    return Item(**base)


def test_a_new_item_plans_create_add_five_sets_and_a_writeback_in_order():
    item = _item()
    steps = plan([item], issues={}, project_items={}, tracker="github-projects")
    kinds = [s["kind"] for s in steps]
    assert kinds == ["issue_create", "project_add", "project_set", "project_set",
                      "project_set", "project_set", "project_set", "writeback"]
    assert steps[0] == {"kind": "issue_create", "repo": "acme/widgets", "title": "Fix the thing",
                          "body": "Some body.", "label": "coxswain", "item_id": "item-1"}
    assert steps[1] == {"kind": "project_add", "issue": None}
    assert steps[-1] == {"kind": "writeback", "item_id": "item-1", "issue": None}


def test_two_fresh_items_keep_their_steps_grouped_per_item():
    first = _item(id="item-1", repo="acme/widgets")
    second = _item(id="item-2", repo="acme/gadgets", title="Fix another thing")
    steps = plan([first, second], issues={}, project_items={}, tracker="github-projects")
    block = ["issue_create", "project_add", "project_set", "project_set",
             "project_set", "project_set", "project_set", "writeback"]
    assert [s["kind"] for s in steps] == block + block
    first_block, second_block = steps[:8], steps[8:]
    assert first_block[0]["item_id"] == "item-1" and first_block[-1]["item_id"] == "item-1"
    assert second_block[0]["item_id"] == "item-2" and second_block[-1]["item_id"] == "item-2"


def test_an_item_whose_issue_and_fields_already_match_plans_nothing():
    item = _item(issue="acme/widgets#12")
    issues = {"acme/widgets#12": {"title": "Fix the thing", "body": "Some body.", "labels": ["coxswain"]}}
    project_items = {"acme/widgets#12": {"State": "Ready", "Phase": "core", "Run": "run-7",
                                          "Cost": "$1.50", "Gate": "tests"}}
    assert plan([item], issues, project_items, "github-projects") == []


def test_a_changed_state_plans_exactly_one_project_set():
    item = _item(issue="acme/widgets#12", state="done")
    issues = {"acme/widgets#12": {"title": "Fix the thing", "body": "Some body.", "labels": ["coxswain"]}}
    project_items = {"acme/widgets#12": {"State": "Ready", "Phase": "core", "Run": "run-7",
                                          "Cost": "$1.50", "Gate": "tests"}}
    steps = plan([item], issues, project_items, "github-projects")
    assert steps == [{"kind": "project_set", "issue": "acme/widgets#12", "field": "State", "value": "Done"}]


def test_a_changed_body_plans_one_issue_edit():
    item = _item(issue="acme/widgets#12", body="A new body.")
    issues = {"acme/widgets#12": {"title": "Fix the thing", "body": "Some body.", "labels": ["coxswain"]}}
    project_items = {"acme/widgets#12": {"State": "Ready", "Phase": "core", "Run": "run-7",
                                          "Cost": "$1.50", "Gate": "tests"}}
    steps = plan([item], issues, project_items, "github-projects")
    assert steps == [{"kind": "issue_edit", "issue": "acme/widgets#12", "title": "Fix the thing",
                        "body": "A new body."}]


def test_an_issue_missing_from_the_labelled_map_plans_no_issue_edit():
    item = _item(issue="acme/widgets#99")
    issues = {}  # #99 lost the coxswain label, or is gone; nothing on file to edit against.
    project_items = {"acme/widgets#99": {"State": "Ready", "Phase": "core", "Run": "run-7",
                                          "Cost": "$1.50", "Gate": "tests"}}
    assert plan([item], issues, project_items, "github-projects") == []


def test_an_unknown_state_plans_a_single_refuse_step():
    item = _item(state="blocked")
    steps = plan([item], issues={}, project_items={}, tracker="github-projects")
    assert steps == [{"kind": "refuse", "item_id": "item-1", "detail": "unknown state: 'blocked'"}]


def test_tracker_none_plans_nothing():
    item = _item()
    assert plan([item], issues={}, project_items={}, tracker="none") == []


def test_render_has_one_line_per_step():
    item = _item()
    steps = plan([item], issues={}, project_items={}, tracker="github-projects")
    lines = render(steps)
    assert len(lines) == len(steps)
    assert lines[0] == "issue_create acme/widgets: 'Fix the thing'"
    assert lines[1] == "project_add (new issue)"
    assert lines[-1] == "writeback item-1 -> (new issue)"


def test_render_of_a_refuse_step():
    steps = [{"kind": "refuse", "item_id": "item-1", "detail": "unknown state: 'blocked'"}]
    assert render(steps) == ["refuse item-1: unknown state: 'blocked'"]
