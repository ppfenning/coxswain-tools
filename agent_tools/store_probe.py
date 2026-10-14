"""Can this machine reach the configured shared store: Postgres and the S3 endpoint, with injectable connectors."""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent_tools import store_dialect, store_url

POSTGRES = "postgres"
OBJECT_STORE = "object_store"

LOCAL_STORE = "the store is local files, nothing to reach"
LOCAL_TRACES = "traces are local files, nothing to reach"
BAD_STORE_URL = "postgres storage URL could not be parsed; check its format"

# Seconds. Matches the Postgres connect_timeout default in store_connect, so neither probe can hang setup.
S3_TIMEOUT_S = 10.0
S3_MAX_ATTEMPTS = 2

_SENTENCES = {
    "refused": "{c} refused the connection; check that it is running and that the address and port are right",
    "unreachable": "{c} could not be reached; check the address, the network and that the service is running",
    "timeout": "{c} did not answer in time; check the address and the network",
    "auth": "{c} rejected the credentials; check the user, password or keys",
    "bucket": "{c} has no such bucket; check the bucket name in the traces URL",
    "missing_client": "{c} needs a client library that is not installed on this machine",
    "misconfigured": "{c} settings are incomplete; check the endpoint and the credential environment variables",
    "pg_connect": "{c} could not connect; check the address, the network, the user and the password",
    "s3_request": "{c} request failed; check the endpoint, the credentials and the bucket name",
    "generic": "{c} could not be checked; the failure type is not one this probe recognises",
}


class Unreachable(OSError):
    """The edge's re-raise for a host that cannot be resolved or a network that cannot be joined."""


_SHARED = {
    **dict.fromkeys(("ConnectionRefusedError", "ConnectionResetError", "ConnectionError"), "refused"),
    "Unreachable": "unreachable",
    **dict.fromkeys(("TimeoutError", "ConnectTimeout", "ConnectTimeoutError", "ConnectionTimeout"), "timeout"),
    **dict.fromkeys(("PermissionError", "InvalidPassword", "InvalidAuthorizationSpecification"), "auth"),
    **dict.fromkeys(("ImportError", "ModuleNotFoundError", "TracesUnavailable"), "missing_client"),
}

# psycopg3 raises OperationalError for a refused connection and a rejected password alike; pyarrow's S3
# filesystem raises a plain OSError for access denied, a bad endpoint and a missing bucket. The edges narrow
# those by message where they can; these entries cover what is left.
_KINDS = {
    POSTGRES: {**_SHARED, "OperationalError": "pg_connect"},
    OBJECT_STORE: {
        **_SHARED,
        **dict.fromkeys(("NoSuchBucket", "FileNotFoundError"), "bucket"),
        "ObjectStoreMisconfigured": "misconfigured",
        "OSError": "s3_request",
    },
}

# First match wins: auth before timeout, since a rejected login can also mention the connection.
_MESSAGE_CAUSES = (
    ("password authentication failed", "auth"),
    ("no password supplied", "auth"),
    ("authentication failed", "auth"),
    ("access_denied", "auth"),
    ("access denied", "auth"),
    ("invalidaccesskeyid", "auth"),
    ("invalid_access_key_id", "auth"),
    ("signaturedoesnotmatch", "auth"),
    ("signature_does_not_match", "auth"),
    ("forbidden", "auth"),
    ("no_such_bucket", "bucket"),
    ("nosuchbucket", "bucket"),
    ("connection refused", "refused"),
    ("timeout expired", "timeout"),
    ("timed out", "timeout"),
    ("could not translate host name", "unreachable"),
    ("name or service not known", "unreachable"),
    ("nodename nor servname", "unreachable"),
    ("couldn't resolve host", "unreachable"),
    ("network_connection", "unreachable"),
    ("needs psycopg", "missing_client"),
)

_CAUSE_TYPES: dict[str, type[Exception]] = {
    "auth": PermissionError,
    "bucket": FileNotFoundError,
    "refused": ConnectionRefusedError,
    "timeout": TimeoutError,
    "unreachable": Unreachable,
    "missing_client": ModuleNotFoundError,
}


@dataclass(frozen=True)
class ProbeResult:
    """One component's verdict. `detail` is plain words and never holds a URL, user, password or key."""

    component: str
    ok: bool
    detail: str


def summarize(results: list[ProbeResult]) -> str:
    """One `component: ok|failed - detail` line per result, in the order given."""
    return "\n".join(f"{r.component}: {'ok' if r.ok else 'failed'} - {r.detail}" for r in results)


def classify_failure(exc_type_name: str, component: str) -> str:
    """A plain sentence for a failure type name. Takes no exception text, which can carry a URL with a password."""
    return _SENTENCES[_KINDS.get(component, _SHARED).get(exc_type_name, "generic")].format(c=component)


def cause_from_message(message: str) -> str | None:
    """The failure kind a client's message names, or None. Only the kind leaves; the message never does."""
    lowered = message.lower()
    return next((kind for needle, kind in _MESSAGE_CAUSES if needle in lowered), None)


def bucket_of(url: str) -> str:
    """The bucket named by an `s3://bucket/prefix` URL, `""` when none."""
    return url.partition("://")[2].partition("/")[0]


def _narrowed(exc: Exception) -> Exception:
    """Edge: a bare exception of the type the message names, else `exc` itself. The new one carries no text."""
    cause = cause_from_message(str(exc))
    return _CAUSE_TYPES[cause]() if cause else exc


def _ping_postgres(url: str) -> None:
    """Edge, the default `connect`: a read-only connection, `SELECT 1`, close. Raises on any failure."""
    try:
        conn = store_dialect.connect_readonly_url(url)
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
    except Exception as exc:
        raise _narrowed(exc) from None


def _head_bucket(object_store: Mapping[str, Any], bucket: str) -> None:
    """Edge, the default `head_bucket`: the traces reader's pyarrow S3 filesystem, bounded in time, asked for the bucket."""
    from agent_tools import run_store  # deferred: run_store is large and pyarrow is an optional extra

    pafs, _ = run_store._import_pyarrow()
    try:
        options = run_store.s3_options(object_store, os.environ) if object_store else {}
    except ValueError:
        # The ValueError text names an env var or a secret key; a fixed message keeps that out of every caller.
        raise run_store.ObjectStoreMisconfigured("object_store settings are incomplete") from None
    bounded = {
        **options,
        "connect_timeout": S3_TIMEOUT_S,
        "request_timeout": S3_TIMEOUT_S,
        "retry_strategy": pafs.AwsStandardS3RetryStrategy(max_attempts=S3_MAX_ATTEMPTS),
    }
    try:
        info = pafs.S3FileSystem(**bounded).get_file_info(bucket)
    except OSError as exc:
        raise _narrowed(exc) from None
    if info.type == pafs.FileType.NotFound:
        raise FileNotFoundError(bucket)


def probe_postgres(storage_url: str, connect: Callable[[str], object] | None = None) -> ProbeResult:
    """Edge. `connect(url)` opens, checks and closes, raising on failure. A sqlite or path URL never calls it."""
    try:
        remote = store_dialect.is_postgres(storage_url)
    except ValueError:
        return ProbeResult(POSTGRES, False, BAD_STORE_URL)
    if not remote:
        return ProbeResult(POSTGRES, True, LOCAL_STORE)
    try:
        (connect or _ping_postgres)(storage_url)
    except Exception as exc:
        return ProbeResult(POSTGRES, False, classify_failure(type(exc).__name__, POSTGRES))
    return ProbeResult(POSTGRES, True, "postgres accepted a read-only connection")


def probe_object_store(
    object_store: Mapping[str, Any],
    traces_url: str,
    head_bucket: Callable[[Mapping[str, Any], str], object] | None = None,
) -> ProbeResult:
    """Edge. `head_bucket(object_store, bucket)` raises on failure. A local traces directory never calls it."""
    root = store_url.traces_root(traces_url, ".", object_store)
    if not root.remote:
        return ProbeResult(OBJECT_STORE, True, LOCAL_TRACES)
    bucket = bucket_of(root.url)
    if not bucket:
        return ProbeResult(OBJECT_STORE, False, "object_store traces URL names no bucket")
    try:
        (head_bucket or _head_bucket)(root.object_store or {}, bucket)
    except Exception as exc:
        return ProbeResult(OBJECT_STORE, False, classify_failure(type(exc).__name__, OBJECT_STORE))
    return ProbeResult(OBJECT_STORE, True, "object_store bucket is reachable")
