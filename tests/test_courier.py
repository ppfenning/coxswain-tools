import json

import pytest

from agent_tools.courier import Reference, format_reference, parse_reference, resolve

_REFS = [
    Reference("run", "r1"),
    Reference("task", "work/init/p1/t1.md"),
    Reference("pr", "r1:draft_pr_create"),
    Reference("intake", "abc"),
    Reference("proposal", "r1:item_create"),
    Reference("finding", "r1"),
]


@pytest.mark.parametrize("ref", _REFS, ids=lambda r: r.kind)
def test_round_trip(ref):
    assert parse_reference(format_reference(ref)) == ref


def test_unknown_kind_returns_none():
    assert parse_reference("coxswain://widget/r1") is None


def test_malformed_reference_returns_none():
    assert parse_reference("not-a-reference") is None


def _run_record_fixture(tmp_path):
    task = tmp_path / "runs" / "r1" / "tasks" / "p1" / "t1.json"
    task.parent.mkdir(parents=True)
    record = {"run": "r1", "arbitration": {"reasoning": "the fix broke the build"}, "adversary": []}
    task.write_text(json.dumps(record), encoding="utf-8")
    return record


def test_resolve_run(tmp_path):
    record = _run_record_fixture(tmp_path)
    assert resolve(Reference("run", "r1"), tmp_path) == record


def test_resolve_finding(tmp_path):
    _run_record_fixture(tmp_path)
    assert resolve(Reference("finding", "r1"), tmp_path) == {"reasoning": "the fix broke the build"}


def test_resolve_finding_falls_back_to_adversary_why_wrong(tmp_path):
    task = tmp_path / "runs" / "r2" / "tasks" / "p1" / "t1.json"
    task.parent.mkdir(parents=True)
    task.write_text(json.dumps({"adversary": [{"why_wrong": "missed the edge case"}]}), encoding="utf-8")
    assert resolve(Reference("finding", "r2"), tmp_path) == {"reasoning": "missed the edge case"}


def _ledger_fixture(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rows = [
        {"key": "r1:draft_pr_create", "kind": "draft_pr_create", "run_id": "r1"},
        {"key": "r1:item_create", "kind": "item_create", "run_id": "r1"},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return ledger, rows


def test_resolve_pr(tmp_path):
    ledger, rows = _ledger_fixture(tmp_path)
    assert resolve(Reference("pr", "r1:draft_pr_create"), tmp_path, ledger_path=ledger) == rows[0]


def test_resolve_proposal(tmp_path):
    ledger, rows = _ledger_fixture(tmp_path)
    assert resolve(Reference("proposal", "r1:item_create"), tmp_path, ledger_path=ledger) == rows[1]


def _work_item_fixture(tmp_path, rel_path):
    path = tmp_path / rel_path
    path.parent.mkdir(parents=True)
    path.write_text('---\nid: "T1"\nstatus: "open"\n---\nBody text.\n', encoding="utf-8")
    return path


def test_resolve_task(tmp_path):
    _work_item_fixture(tmp_path, "work/init/p1/t1.md")
    assert resolve(Reference("task", "work/init/p1/t1.md"), tmp_path) == {
        "id": "T1", "status": "open", "body": "Body text.",
    }


def test_resolve_intake(tmp_path):
    """The ticket id comes from frontmatter, not the filename stem, so this fixture's filename
    deliberately differs from the `id` it resolves by."""
    intake = tmp_path / "intake" / "2026-09-16-foo.md"
    intake.parent.mkdir(parents=True)
    intake.write_text('---\nid: "abc"\ntitle: "Foo"\n---\nBody text.\n', encoding="utf-8")
    assert resolve(Reference("intake", "abc"), tmp_path) == {
        "id": "abc", "title": "Foo", "body": "Body text.",
    }


def test_resolve_missing_run_is_none(tmp_path):
    assert resolve(Reference("run", "nope"), tmp_path) is None
