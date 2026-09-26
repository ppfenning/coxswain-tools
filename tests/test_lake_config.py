import dataclasses
import sys

import pytest

from agent_tools.lake_config import (
    LakeConfig,
    LakeUnavailable,
    iceberg_properties,
    load_catalog,
    redact,
    resolve_lake,
    resolve_lake_at,
)


def test_both_keys_absent_gives_a_sqlite_catalog_and_a_file_warehouse_in_the_runs_dir():
    assert resolve_lake({}, "/runs") == LakeConfig("sqlite:////runs/lake-catalog.db", "file:///runs/lake")


def test_the_catalog_key_wins_when_set():
    assert resolve_lake({"lake_catalog_url": "sqlite:////x/c.db"}, "/runs").catalog_uri == "sqlite:////x/c.db"


def test_the_warehouse_key_wins_when_it_is_an_s3_url():
    assert resolve_lake({"lake_url": "s3://bucket/lake"}, "/runs").warehouse == "s3://bucket/lake"


def test_a_postgres_catalog_url_passes_through_unchanged():
    url = "postgresql+psycopg://u:pw@h:5432/lake"
    assert resolve_lake({"lake_catalog_url": url}, "/runs").catalog_uri == url


def test_a_blank_string_counts_as_absent():
    assert resolve_lake({"lake_catalog_url": "", "lake_url": ""}, "/runs") == resolve_lake({}, "/runs")


def test_redact_hides_a_password_and_keeps_the_rest():
    assert redact("postgresql://u:secret@h:5432/db") == "postgresql://u:***@h:5432/db"


def test_redact_leaves_a_url_without_a_password_alone():
    assert redact("s3://bucket/p") == "s3://bucket/p"


def test_the_config_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        resolve_lake({}, "/runs").warehouse = "x"  # type: ignore[misc]


def test_resolve_lake_at_reads_the_profile_file(tmp_path):
    profile = tmp_path / "p.yaml"
    profile.write_text("lake_url: s3://b/l\n", encoding="utf-8")
    assert resolve_lake_at(profile, "/runs").warehouse == "s3://b/l"


def test_load_catalog_without_pyiceberg_names_the_lake_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyiceberg.catalog.sql", None)
    with pytest.raises(LakeUnavailable, match="lake"):
        load_catalog(LakeConfig("sqlite:///:memory:", "file:///w"))


def test_load_catalog_returns_a_coxswain_sql_catalog_on_a_temporary_sqlite_file(tmp_path):
    pytest.importorskip("pyiceberg")
    pytest.importorskip("sqlalchemy")
    uri = f"sqlite:///{tmp_path / 'lake-catalog.db'}"
    catalog = load_catalog(resolve_lake({"lake_catalog_url": uri, "lake_url": f"file://{tmp_path / 'lake'}"}, tmp_path))
    assert (catalog.name, catalog.properties["uri"]) == ("coxswain", uri)


BLOCK = {"endpoint": "http://minio:9000", "region": "us-east-1", "access_key_env": "LAKE_KEY", "secret_key_env": "LAKE_SECRET"}


def test_a_block_gives_exactly_the_pyiceberg_s3_properties():
    assert iceberg_properties(BLOCK, {"LAKE_KEY": "k", "LAKE_SECRET": "s"}) == {
        "s3.endpoint": "http://minio:9000",
        "s3.region": "us-east-1",
        "s3.access-key-id": "k",
        "s3.secret-access-key": "s",
        "s3.force-virtual-addressing": "false",
    }


def test_a_named_env_var_that_is_unset_raises_naming_it():
    with pytest.raises(ValueError, match="LAKE_SECRET"):
        iceberg_properties(BLOCK, {"LAKE_KEY": "k"})


def test_a_literal_secret_in_the_block_is_refused():
    with pytest.raises(ValueError, match="secret_key"):
        iceberg_properties({**BLOCK, "secret_key": "hunter2"}, {"LAKE_KEY": "k", "LAKE_SECRET": "s"})


def test_no_block_gives_no_properties_and_the_default_local_warehouse_is_unchanged():
    config = resolve_lake({}, "/runs")
    assert (iceberg_properties(config.object_store, {}), config.warehouse) == ({}, "file:///runs/lake")


def test_the_block_is_carried_as_given_on_the_config():
    assert resolve_lake({"object_store": BLOCK}, "/runs").object_store == BLOCK
