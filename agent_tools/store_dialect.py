"""The one seam between the run store and a database driver: placeholder token and read-only open."""

import sqlite3
from pathlib import Path
from urllib.parse import urlparse

POSTGRES_SCHEMES = ("postgres", "postgresql")
READ_ONLY_OPTION = "-c default_transaction_read_only=on"
MISSING_PSYCOPG = "a postgres store needs psycopg: pip install 'coxswain-tools[postgres]'"


def is_postgres(url: str) -> bool:
    return urlparse(url).scheme in POSTGRES_SCHEMES


def placeholder(url: str) -> str:
    """The bind-parameter token for the store at `url`: `%s` for Postgres, `?` for SQLite."""
    return "%s" if is_postgres(url) else "?"


def connect_readonly_url(url: str):
    """Edge. A read-only connection whose rows are addressable by column name; a path or `sqlite:` URL opens `mode=ro`."""
    if not is_postgres(url):
        path = Path(url.removeprefix("sqlite:///") if url.startswith("sqlite:") else url).resolve()
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    try:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row
    except ImportError:
        raise RuntimeError(MISSING_PSYCOPG) from None
    # Autocommit: a non-autocommit connection opens a transaction on its first SELECT and holds it, with its snapshot
    # and AccessShare locks, until close; a slow or stalled reader (the notifier, 2026-10-12) then sits "idle in
    # transaction" and blocks VACUUM and DDL. `conn.read_only` only shapes the BEGINs psycopg issues, so autocommit
    # enforces read-only on the server for every implicit transaction instead.
    options = " ".join(filter(None, (conninfo_to_dict(url).get("options"), READ_ONLY_OPTION)))
    return psycopg.connect(url, row_factory=dict_row, autocommit=True, options=options)
