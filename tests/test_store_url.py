from agent_tools.store_url import describe_store, profile_store_url, resolve_store_url, storage_url

DEFAULT = "sqlite:////r/cox.db"


def test_key_absent_or_blank_gives_the_sqlite_file_in_the_runs_dir():
    assert storage_url(None, "/r") == DEFAULT
    assert storage_url("", "/r") == DEFAULT


def test_key_a_sqlite_path_passes_through_unchanged():
    assert storage_url("/data/x.db", "/r") == "/data/x.db"


def test_key_a_postgres_url_passes_through_unchanged():
    assert storage_url("postgresql://u:p@h/db", "/r") == "postgresql://u:p@h/db"


def test_the_profile_key_is_storage_url_and_no_other():
    assert profile_store_url({"storage_url": "postgresql://h/db"}, "/r") == "postgresql://h/db"
    assert profile_store_url({"storage": "postgresql://h/db"}, "/r") == DEFAULT


def test_a_password_is_kept_out_of_the_returned_message():
    assert describe_store("postgresql://u:secret@h:5432/db") == "store: postgresql"
    assert describe_store("postgresql://h/db?user=u&password=secret") == "store: postgresql"
    assert describe_store("postgresql://u:se/cr?e#t@h/db") == "store: postgresql"
    assert describe_store("host=h password='it\\'s' dbname=d") == "store: other"
    assert describe_store("host=h password=ab&cd dbname=d") == "store: other"
    assert describe_store("host=h password=a://b") == "store: other"
    assert describe_store("SQLite:///r/cox.db") == "store: sqlite"


def test_the_edge_reads_the_key_from_the_profile_file(tmp_path):
    profile = tmp_path / "provider.yaml"
    profile.write_text("storage_url: postgresql://u:p@h/db\n", encoding="utf-8")
    assert resolve_store_url(profile, "/r") == "postgresql://u:p@h/db"


def test_the_edge_expands_a_home_relative_path(tmp_path, monkeypatch):
    (tmp_path / "provider.yaml").write_text("storage_url: /data/x.db\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_store_url("~/provider.yaml", "/r") == "/data/x.db"


def test_the_edge_falls_back_to_the_default_on_a_missing_malformed_or_non_mapping_profile(tmp_path):
    (tmp_path / "bad.yaml").write_text("storage_url: [unclosed\n", encoding="utf-8")
    (tmp_path / "list.yaml").write_text("- storage_url\n", encoding="utf-8")
    assert resolve_store_url(tmp_path / "missing.yaml", "/r") == DEFAULT
    assert resolve_store_url(tmp_path / "bad.yaml", "/r") == DEFAULT
    assert resolve_store_url(tmp_path / "list.yaml", "/r") == DEFAULT
