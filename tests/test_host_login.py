"""`cox host login` stores login state and never moves `beat_at`.

`FakeStore` stands in for `harness.store_cli host login`, which lives in another repo: it applies the argv
cox sends to a real SQLite `hosts` row. The tests pin what cox sends and what the store must do with it.
"""

from __future__ import annotations

import argparse
import json
import sqlite3

import pytest

from agent_tools import cli

T0 = "2026-10-11T09:00:00+00:00"


class FakeStore:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE hosts (name TEXT PRIMARY KEY, beat_at TEXT, versions_json TEXT)")
        self.conn.execute("INSERT INTO hosts VALUES ('jarvis', ?, ?)", (T0, json.dumps({"login_ok": True})))

    def run(self, argv: list[str]) -> tuple[int, str]:
        opts = dict(zip(argv[3::2], argv[4::2]))
        merged = {"login_ok": {"true": True, "false": False, "none": None}[opts["--login-ok"]],
                  "reason": opts.get("--reason"), "failures": int(opts["--failures"])}
        self.conn.execute("UPDATE hosts SET versions_json = ? WHERE name = ?", (json.dumps(merged), argv[2]))
        return 0, "{}"

    def row(self) -> tuple[str, dict]:
        beat_at, raw = self.conn.execute("SELECT beat_at, versions_json FROM hosts").fetchone()
        return beat_at, json.loads(raw)


def _login(tmp_path, monkeypatch, login_ok, reason=None, failures=0) -> FakeStore:
    (tmp_path / "ws").mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"workspace_dir: {tmp_path / 'ws'}\n", encoding="utf-8")
    store = FakeStore(tmp_path / "fake.db")
    monkeypatch.setattr(cli.store_cli, "runner", lambda runs_dir: store.run)
    args = argparse.Namespace(profile=str(profile), name="jarvis", login_ok=login_ok, reason=reason, failures=failures)
    assert cli._host_login(args) == 0
    return store


def test_a_failed_login_stores_ok_reason_and_failures_and_leaves_beat_at(tmp_path, monkeypatch):
    beat_at, stored = _login(tmp_path, monkeypatch, False, "not logged in", 3).row()
    assert beat_at == T0
    assert stored == {"login_ok": False, "reason": "not logged in", "failures": 3}


def test_a_login_of_none_stores_none_not_a_string(tmp_path, monkeypatch):
    assert _login(tmp_path, monkeypatch, None).row()[1]["login_ok"] is None


def test_the_argv_names_the_login_verb_and_never_beat():
    assert cli._host_login_argv("jarvis", False, "expired", 3, "chair") == [
        "host", "login", "jarvis", "--login-ok", "false", "--reason", "expired", "--failures", "3", "--by", "chair",
    ]
    assert "--reason" not in cli._host_login_argv("jarvis", None, None, 0, "chair")


@pytest.mark.parametrize("text, expected", [("true", True), ("False", False), ("none", None)])
def test_login_ok_reads_true_false_and_none(text, expected):
    assert cli._parse_login_ok(text) is expected
    ns = cli.build_parser().parse_args(["host", "login", "jarvis", text])
    assert ns.login_ok is expected


def test_login_ok_rejects_anything_else():
    with pytest.raises(argparse.ArgumentTypeError):
        cli._parse_login_ok("maybe")
