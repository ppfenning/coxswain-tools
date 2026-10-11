"""The one place the chair loop builds psycopg connection options: bounded connect, keepalive, statement timeout."""

from agent_tools.store_dialect import MISSING_PSYCOPG

CONNECT_TIMEOUT_S = 10
KEEPALIVES = 1
KEEPALIVES_IDLE_S = 30
KEEPALIVES_INTERVAL_S = 10
KEEPALIVES_COUNT = 3
STATEMENT_TIMEOUT_MS = 60000


def connect_kwargs(
    conninfo: str,
    *,
    connect_timeout: int = CONNECT_TIMEOUT_S,
    keepalives_idle: int = KEEPALIVES_IDLE_S,
    keepalives_interval: int = KEEPALIVES_INTERVAL_S,
    keepalives_count: int = KEEPALIVES_COUNT,
    statement_timeout_ms: int = STATEMENT_TIMEOUT_MS,
) -> dict[str, object]:
    """Kwargs for `psycopg.connect`; the DSN passes through untouched and this helper owns `options`."""
    return {
        "conninfo": conninfo,
        "connect_timeout": connect_timeout,
        "keepalives": KEEPALIVES,
        "keepalives_idle": keepalives_idle,
        "keepalives_interval": keepalives_interval,
        "keepalives_count": keepalives_count,
        "options": f"-c statement_timeout={statement_timeout_ms}",
    }


def connect(conninfo: str, **overrides: int):
    """Edge. Open a Postgres connection with the bounded defaults; keyword overrides match `connect_kwargs`."""
    try:
        import psycopg
    except ImportError:
        raise RuntimeError(MISSING_PSYCOPG) from None
    return psycopg.connect(**connect_kwargs(conninfo, **overrides))
