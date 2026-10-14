import dataclasses
from pathlib import Path

import pytest

from agent_tools.run_store import s3_options
from agent_tools.setup_store import (
    ACCESS_KEY_ENV,
    SECRET_KEY_ENV,
    SharedConnection,
    build_shared_profile,
    default_store_choice,
    parse_store_choice,
    redacted_summary,
    validate_shared,
)
from agent_tools.store_url import describe_store, profile_store_url, profile_traces_root

CONN = SharedConnection(
    pg_host="db.example",
    pg_port=5432,
    pg_database="graphs",
    pg_user="cox",
    pg_password="p@ss/word",
    s3_endpoint="http://garage.example:3900",
    s3_bucket="traces",
    s3_region="garage",
    s3_access_key_id="GKaccess123",
    s3_secret_access_key="sekrit-secret-key",
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("files", "files"),
        ("shared", "shared"),
        ("FILES", "files"),
        (" Shared ", "shared"),
        ("", None),
        ("postgres", None),
        ("sqlite", None),
        (None, None),
    ],
)
def test_parse_store_choice(value, expected):
    assert parse_store_choice(value) == expected


def test_default_store_choice_is_files():
    assert default_store_choice() == "files"


def test_valid_connection_has_no_problems():
    assert validate_shared(CONN) == []


def test_connection_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        CONN.pg_host = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("pg_host", ""),
        ("s3_bucket", ""),
        ("pg_port", 0),
        ("pg_port", 65536),
        ("s3_endpoint", "ftp-garage.example:3900"),
        ("pg_password", ""),
        ("s3_access_key_id", ""),
        ("s3_secret_access_key", ""),
    ],
)
def test_invalid_field_is_named(field, bad):
    problems = validate_shared(dataclasses.replace(CONN, **{field: bad}))
    assert len(problems) == 1
    assert field in problems[0]


def test_problems_do_not_echo_values():
    bad = dataclasses.replace(CONN, s3_endpoint="garage.example:3900", pg_port=70000, pg_host="")
    text = " ".join(validate_shared(bad))
    assert "garage.example" not in text
    assert "70000" not in text


def test_build_shared_profile_has_three_keys_and_encodes_password():
    profile = build_shared_profile(CONN)
    assert set(profile) == {"storage_url", "traces_url", "object_store"}
    assert profile["storage_url"] == "postgresql://cox:p%40ss%2Fword@db.example:5432/graphs"
    assert profile["traces_url"] == "s3://traces"


def test_profile_holds_env_names_not_credential_values():
    block = build_shared_profile(CONN)["object_store"]
    assert block["access_key_env"] == ACCESS_KEY_ENV
    assert block["secret_key_env"] == SECRET_KEY_ENV
    assert not {"access_key", "secret_key", "secret"} & set(block)
    assert CONN.s3_secret_access_key not in repr(block)
    assert CONN.s3_access_key_id not in repr(block)


def test_profile_is_the_shape_readers_accept():
    profile = build_shared_profile(CONN)
    runs_dir = Path("unused")
    assert profile_store_url(profile, runs_dir) == profile["storage_url"]
    assert describe_store(profile["storage_url"]) == "store: postgresql"
    root = profile_traces_root(profile, runs_dir)
    assert (root.remote, root.url, root.object_store) == (True, "s3://traces", profile["object_store"])
    env = {ACCESS_KEY_ENV: "a", SECRET_KEY_ENV: "s"}
    assert s3_options(profile["object_store"], env) == {
        "scheme": "http",
        "endpoint_override": "garage.example:3900",
        "region": "garage",
        "access_key": "a",
        "secret_key": "s",
        "force_virtual_addressing": False,
    }


def test_redacted_summary_names_host_port_bucket_only():
    summary = redacted_summary(CONN)
    assert summary == "store: postgresql, host db.example, port 5432, bucket traces"
    for secret in (CONN.pg_password, CONN.pg_user, CONN.s3_secret_access_key, CONN.s3_access_key_id):
        assert secret not in summary
