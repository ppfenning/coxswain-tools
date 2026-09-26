from pathlib import Path

from agent_tools import land, store_cli

APPROVED = "---\nid: t1\nstate: approved\n---\nbody\n"
DONE = "---\nid: t1\nstate: done\n---\nbody\n"


def test_store_mode_reads_the_store_state():
    assert land.approved_state("store", "approved", "ready") == "approved"


def test_store_mode_without_a_row_reads_the_file_state():
    assert land.approved_state("store", None, "approved") == "approved"


def test_files_mode_ignores_the_store():
    assert land.approved_state("files", "done", "approved") == "approved"


def test_a_set_state_success_continues():
    assert land.set_state_stop(store_cli.StateSet({"state": "done"})) is None


def test_a_refused_precondition_names_another_machine():
    reason = land.set_state_stop(store_cli.StateRefused("state is done", "done"))
    assert "another machine landed this task" in reason


def test_a_failure_stops_with_its_exit_and_detail():
    assert land.set_state_stop(store_cli.Failed(2, "boom")) == "land: store set-state failed: exit 2: boom"


def test_not_available_stops():
    assert "store not available" in land.set_state_stop(store_cli.NotAvailable())


class _Fake:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, expected):
        self.calls.append(expected)
        return self.result


def _drive(tmp_path: Path, mode: str, result):
    """The caller's shape: write the file only when new text comes back."""
    item = tmp_path / "t1.md"
    item.write_text(APPROVED, encoding="utf-8")
    fake = _Fake(result)
    new_text, message = land.close_to_done(item.read_text(encoding="utf-8"), mode=mode, merged=True, set_state=fake)
    if new_text is not None:
        item.write_text(new_text, encoding="utf-8")
    return item.read_text(encoding="utf-8"), message, fake.calls


def test_store_success_rewrites_the_file_after_an_expect_approved_call(tmp_path):
    text, message, calls = _drive(tmp_path, "store", store_cli.parse_set_state(0, '{"state": "done"}'))
    assert (text, message, calls) == (DONE, None, ["approved"])


def test_store_exit_3_stops_and_leaves_the_file(tmp_path):
    text, message, calls = _drive(tmp_path, "store", store_cli.parse_set_state(3, '{"state": "done"}'))
    assert text == APPROVED and "another machine landed this task" in message and calls == ["approved"]


def test_store_exit_2_stops_and_leaves_the_file(tmp_path):
    text, message, calls = _drive(tmp_path, "store", store_cli.parse_set_state(2, "", "bad"))
    assert text == APPROVED and "exit 2: bad" in message and calls == ["approved"]


def test_files_mode_calls_no_set_state_and_matches_approve_to_done(tmp_path):
    text, message, calls = _drive(tmp_path, "files", store_cli.StateSet({}))
    assert (text, message, calls) == (DONE, None, [])
    assert land.approve_to_done(APPROVED, merged=True) == (DONE, None)


def test_store_mode_skips_the_store_when_the_item_is_already_done():
    fake = _Fake(store_cli.StateSet({}))
    assert land.close_to_done(DONE, mode="store", merged=True, set_state=fake) == (None, None)
    assert fake.calls == []
