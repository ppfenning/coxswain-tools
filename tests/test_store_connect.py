import pytest

from agent_tools.store_connect import connect, connect_kwargs

DSN = "postgresql://u@h/db"


def test_connect_kwargs_carries_each_keepalive_and_timeout():
    assert connect_kwargs(DSN) == {
        "conninfo": DSN,
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        "options": "-c statement_timeout=60000",
    }


def test_override_changes_only_its_own_key():
    base = connect_kwargs(DSN)
    got = connect_kwargs(DSN, statement_timeout_ms=5000)
    assert got == {**base, "options": "-c statement_timeout=5000"}


def test_connect_passes_exactly_those_kwargs(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    seen: list[tuple[tuple, dict]] = []
    sentinel = object()

    def fake(*args, **kwargs):
        seen.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(psycopg, "connect", fake)
    assert connect(DSN, connect_timeout=3) is sentinel
    assert seen == [((), connect_kwargs(DSN, connect_timeout=3))]
