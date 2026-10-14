"""`claude auth status` output survives the startup noise a login shell prints around it."""

from agent_tools.chair_login_check import _stdout_verdict
from agent_tools.remote_argv import json_object_in
from agent_tools.remote_doctor import auth_verdict

STATUS = '{\n  "loggedIn": true,\n  "authMethod": "claude.ai"\n}\n'
NOISE_BEFORE = "(eval):1: can't change option: zle\n[ERROR]: gitstatus failed to initialize.\n"
NOISE_AFTER = "logout\n"


def test_plain_json_object_is_found():
    assert json_object_in(STATUS) == {"loggedIn": True, "authMethod": "claude.ai"}


def test_noise_before_and_after_is_ignored():
    assert json_object_in(NOISE_BEFORE + STATUS + NOISE_AFTER)["loggedIn"] is True


def test_brace_inside_a_noise_line_is_not_a_start():
    assert json_object_in("note {x} here\n" + STATUS)["loggedIn"] is True


def test_no_object_is_none():
    assert json_object_in("not json at all\n") is None
    assert json_object_in("") is None


def test_doctor_verdict_reads_logged_in_through_noise():
    assert auth_verdict(NOISE_BEFORE + STATUS) is None


def test_doctor_verdict_still_refuses_logged_out_and_unreadable():
    assert "not logged in" in auth_verdict('{"loggedIn": false}')
    assert "unreadable" in auth_verdict("gitstatus failed\n")


def test_login_check_verdict_reads_through_noise():
    assert _stdout_verdict(NOISE_BEFORE + STATUS) is True
    assert _stdout_verdict('{"loggedIn": false}') is False
    assert _stdout_verdict("no json") is None
