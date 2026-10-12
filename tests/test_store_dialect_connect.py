from pathlib import Path

import pytest

from agent_tools import chair_tick_status, run_store, store_connect
from agent_tools.store_dialect import READ_ONLY_OPTION, connect_readonly_url

psycopg = pytest.importorskip("psycopg")
dict_row = pytest.importorskip("psycopg.rows").dict_row


@pytest.fixture
def recorded(monkeypatch) -> dict:
    seen: dict = {}

    def fake_connect(*args, **kwargs):
        seen["args"] = args
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    return seen


def test_plain_url_gets_bounded_defaults(recorded):
    connect_readonly_url("postgresql://h/db")
    assert recorded["connect_timeout"] == 10
    assert recorded["keepalives"] == 1
    assert recorded["keepalives_idle"] == 30
    assert recorded["keepalives_interval"] == 10
    assert recorded["keepalives_count"] == 3
    assert recorded["autocommit"] is True
    assert recorded["row_factory"] is dict_row
    assert READ_ONLY_OPTION in recorded["options"]
    assert "-c statement_timeout=60000" in recorded["options"]


def test_url_values_win(recorded):
    connect_readonly_url("postgresql://h/db?connect_timeout=3&options=-c%20search_path%3Dx")
    assert recorded["connect_timeout"] == 3
    assert recorded["options"] == f"-c search_path=x {READ_ONLY_OPTION} -c statement_timeout=60000"


def test_url_keepalive_values_win(recorded):
    connect_readonly_url("postgresql://h/db?keepalives=0&keepalives_idle=5")
    assert "keepalives" not in recorded
    assert recorded["keepalives_idle"] == 5
    assert recorded["conninfo"] == "postgresql://h/db?keepalives=0&keepalives_idle=5"


def test_tick_status_connects_through_store_connect(monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr(run_store, "_store_url", lambda runs_dir: "postgresql://h/db")
    monkeypatch.setattr(store_connect, "connect", lambda url, **kw: urls.append(url) or object())
    conn, token = chair_tick_status._open_writable(Path("runs"))
    assert urls == ["postgresql://h/db"]
    assert token == "%s"
