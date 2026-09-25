import dataclasses
import sys

import pytest

from agent_tools.lake_config import LakeConfig, LakeUnavailable, load_catalog, redact, resolve_lake, resolve_lake_at


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
