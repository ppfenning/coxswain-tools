from agent_tools.leader_chat import append_line, chat_path, read_thread, unread

_A = {"at": "2026-01-01T00:00:00+00:00", "from": "operator", "text": "hello"}
_B = {"at": "2026-01-01T00:00:01+00:00", "from": "cos1", "text": "hi back"}
_C = {"at": "2026-01-01T00:00:02+00:00", "from": "operator", "text": "again"}


def test_chat_path_is_leader_chat_jsonl_under_runs_dir():
    assert chat_path("/tmp/runs").name == "leader.chat.jsonl"


def test_append_line_on_empty_text_writes_one_line():
    text = append_line("", _A)
    assert text == '{"at": "2026-01-01T00:00:00+00:00", "from": "operator", "text": "hello"}\n'


def test_append_line_on_existing_text_appends_a_second_line_and_keeps_the_first():
    once = append_line("", _A)
    twice = append_line(once, _B)
    assert twice.splitlines() == [once.strip(), '{"at": "2026-01-01T00:00:01+00:00", "from": "cos1", "text": "hi back"}']


def test_read_thread_skips_a_corrupt_line_without_raising():
    text = append_line(append_line("", _A), _B) + "{not json\n"
    assert read_thread(text, limit=10) == [_A, _B]


def test_read_thread_honours_the_limit_by_keeping_the_last_entries():
    text = append_line(append_line(append_line("", _A), _B), _C)
    assert read_thread(text, limit=2) == [_B, _C]


def test_unread_returns_entries_after_the_given_timestamp():
    thread = [_A, _B, _C]
    assert unread(thread, _A["at"]) == [_B, _C]


def test_unread_returns_the_whole_thread_when_since_is_none():
    thread = [_A, _B]
    assert unread(thread, None) == [_A, _B]


def test_append_line_succeeds_with_no_prior_reader_ever_having_existed():
    """Requirement 4: a message is never lost for want of a reader — appending
    to a thread that has never been read back still produces valid text."""
    text = append_line("", _A)
    assert read_thread(text, limit=10) == [_A]
