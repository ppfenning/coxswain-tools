"""Pure core for the setup store choice: parse the choice, validate a shared connection, build the machine-local profile content.

No I/O, no clock, no environment reads. The caller resolves credentials from its environment and passes them in.
The profile never holds a credential value: the readers refuse literal keys, so `object_store` names the env vars
(ACCESS_KEY_ENV, SECRET_KEY_ENV) and the caller exports the real values under those names."""

from dataclasses import dataclass
from urllib.parse import quote

from agent_tools.store_url import describe_store

ACCESS_KEY_ENV = "AWS_ACCESS_KEY_ID"
SECRET_KEY_ENV = "AWS_SECRET_ACCESS_KEY"
_CHOICES = frozenset({"files", "shared"})


def default_store_choice() -> str:
    return "files"


def parse_store_choice(value: str | None) -> str | None:
    """`files` or `shared`, case-insensitive and trimmed; None for anything else, and for None input."""
    choice = value.strip().lower() if value is not None else None
    return choice if choice in _CHOICES else None


@dataclass(frozen=True)
class SharedConnection:
    """Postgres and S3 settings for the shared store. Credentials are plain strings, already resolved by the caller."""

    pg_host: str
    pg_port: int
    pg_database: str
    pg_user: str
    pg_password: str
    s3_endpoint: str
    s3_bucket: str
    s3_region: str
    s3_access_key_id: str
    s3_secret_access_key: str


def validate_shared(conn: SharedConnection) -> list[str]:
    """Problems as plain sentences naming the field, never its value. Empty means valid."""
    checks = (
        (not conn.pg_host.strip(), "pg_host is empty."),
        (not 1 <= conn.pg_port <= 65535, "pg_port must be between 1 and 65535."),
        (not conn.s3_bucket.strip(), "s3_bucket is empty."),
        (
            not conn.s3_endpoint.lower().startswith(("http://", "https://")),
            "s3_endpoint must start with http:// or https://.",
        ),
        (not conn.pg_password, "pg_password is missing."),
        (not conn.s3_access_key_id, "s3_access_key_id is missing."),
        (not conn.s3_secret_access_key, "s3_secret_access_key is missing."),
    )
    return [sentence for failed, sentence in checks if failed]


def build_shared_profile(conn: SharedConnection) -> dict:
    """The provider profile keys `storage_url`, `traces_url` and `object_store`, in the shape store_url and run_store read."""
    user, password = quote(conn.pg_user, safe=""), quote(conn.pg_password, safe="")
    return {
        "storage_url": f"postgresql://{user}:{password}@{conn.pg_host}:{conn.pg_port}/{quote(conn.pg_database, safe='')}",
        "traces_url": f"s3://{conn.s3_bucket}",
        "object_store": {
            "endpoint": conn.s3_endpoint,
            "region": conn.s3_region,
            "path_style": True,
            "access_key_env": ACCESS_KEY_ENV,
            "secret_key_env": SECRET_KEY_ENV,
        },
    }


def redacted_summary(conn: SharedConnection) -> str:
    """Store scheme, host, port and bucket; no user, password or keys."""
    scheme = describe_store(build_shared_profile(conn)["storage_url"])
    return f"{scheme}, host {conn.pg_host}, port {conn.pg_port}, bucket {conn.s3_bucket}"
