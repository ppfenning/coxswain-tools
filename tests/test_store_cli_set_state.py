import subprocess
from pathlib import Path

from agent_tools import store_cli
from agent_tools.store_cli import Failed, NotAvailable, StateRefused, StateSet

PY = "/h/.venv/bin/python"
HEAD = [PY, "-m", "harness.store_cli"]


def _harness(monkeypatch, code=None, out="", raises=None):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")

    monkeypatch.setattr(store_cli, "_harness_python", lambda: Path(PY))
    monkeypatch.setattr(store_cli.subprocess, "run", fake_run)
    return seen


def test_set_state_argv():
    assert store_cli.set_state_argv(PY, "init", "t1", "done", "me", "sqlite:///s.db") == [
        *HEAD, "set-state", "init", "t1", "done", "--by", "me", "--store-url", "sqlite:///s.db"]


def test_set_state_argv_without_a_store_url_omits_the_flag():
    assert store_cli.set_state_argv(PY, "init", "t1", "done", "me") == [*HEAD, "set-state", "init", "t1", "done", "--by", "me"]


def test_parse_exit_0_with_a_json_object_is_the_record():
    assert store_cli.parse_set_state(0, '{"task": "t1", "state": "done"}', "") == StateSet({"task": "t1", "state": "done"})


def test_parse_exit_0_without_a_json_object_is_a_failure():
    assert store_cli.parse_set_state(0, "", "") == Failed(0, "no JSON object on stdout")
    assert store_cli.parse_set_state(0, "[1]", "") == Failed(0, "[1]")


def test_parse_exit_3_is_a_refusal_carrying_the_detail():
    assert store_cli.parse_set_state(3, '{"error": "not ready"}', "") == StateRefused('{"error": "not ready"}')
    assert store_cli.parse_set_state(3, "", "blocked") == StateRefused("blocked")


def test_parse_exit_2_is_a_failure_carrying_stderr_when_stdout_is_empty():
    assert store_cli.parse_set_state(2, "", "bad args\n") == Failed(2, "bad args")


def test_parse_any_other_nonzero_code_is_a_failure():
    assert store_cli.parse_set_state(1, "boom", "") == Failed(1, "boom")


def test_edge_exit_0_returns_the_record_and_passes_the_store_url(monkeypatch, tmp_path):
    seen = _harness(monkeypatch, 0, '{"state": "done"}')
    assert store_cli.set_state(tmp_path, "init", "t1", "done", "me") == StateSet({"state": "done"})
    assert seen == [[*HEAD, "set-state", "init", "t1", "done", "--by", "me", "--store-url", store_cli._store_url(tmp_path)]]
    assert store_cli.mirror_state(tmp_path, "init", "t1", "done", "me") is None


def test_edge_exit_3_is_a_refusal_and_mirrors_a_warning(monkeypatch, tmp_path):
    _harness(monkeypatch, 3, '{"error": "no such task"}')
    assert store_cli.set_state(tmp_path, "init", "t1", "done", "me") == StateRefused('{"error": "no such task"}')
    assert store_cli.mirror_state(tmp_path, "init", "t1", "done", "me") == (
        'warning: store did not record init/t1 as done: refused: {"error": "no such task"}')


def test_edge_exit_2_is_a_failure_and_mirrors_a_one_line_warning(monkeypatch, tmp_path):
    _harness(monkeypatch, 2, "line one\nline two")
    assert store_cli.set_state(tmp_path, "init", "t1", "done", "me") == Failed(2, "line one\nline two")
    assert store_cli.mirror_state(tmp_path, "init", "t1", "done", "me") == (
        "warning: store did not record init/t1 as done: exit 2: line one line two")


def test_edge_oserror_is_a_failure_and_mirrors_a_warning(monkeypatch, tmp_path):
    _harness(monkeypatch, raises=OSError("no exec"))
    assert store_cli.set_state(tmp_path, "init", "t1", "done", "me") == Failed(-1, "no exec")
    assert store_cli.mirror_state(tmp_path, "init", "t1", "done", "me") == (
        "warning: store did not record init/t1 as done: exit -1: no exec")


def test_a_missing_harness_is_not_available_and_mirrors_none(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(store_cli.subprocess, "run", boom)
    assert store_cli.set_state(tmp_path, "init", "t1", "done", "me") == NotAvailable()
    assert store_cli.mirror_state(tmp_path, "init", "t1", "done", "me") is None


def test_mirror_state_turns_a_raise_into_a_warning(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(store_cli, "set_state", boom)
    assert store_cli.mirror_state(tmp_path, "init", "t1", "done", "me") == (
        "warning: store did not record init/t1 as done: RuntimeError: kaboom")
