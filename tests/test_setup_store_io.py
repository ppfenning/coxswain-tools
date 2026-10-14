import dataclasses
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from agent_tools import route, setup_store_io
from agent_tools.setup_store import SharedConnection, build_shared_profile
from agent_tools.setup_store_io import (
    PROFILE_FILE_NAME,
    apply_files_choice,
    apply_shared_choice,
    credentials_from_env,
    default_profile_path,
    record_store_mode,
    write_shared_profile,
)
from agent_tools.store_url import read_provider_profile

PASSWORD = "pw-test-SECRET-9f3"
CONN = SharedConnection(
    pg_host="db.example",
    pg_port=5432,
    pg_database="graphs",
    pg_user="cox",
    pg_password=PASSWORD,
    s3_endpoint="http://garage.example:3900",
    s3_bucket="traces",
    s3_region="garage",
    s3_access_key_id="GKaccess123",
    s3_secret_access_key="sekrit-secret-key",
)


def test_credentials_resolved_and_unset():
    env = {"PG_PW": PASSWORD, "S3_KEY": ""}
    names = {"pg_password": "PG_PW", "s3_access_key_id": "S3_KEY", "s3_secret_access_key": "S3_SEC"}
    got = credentials_from_env(env, names)
    assert got == {"values": {"pg_password": PASSWORD}, "unset": ["S3_KEY", "S3_SEC"]}


def test_profile_reads_back_with_three_keys(tmp_path):
    path = write_shared_profile(tmp_path / "conf" / "deep" / PROFILE_FILE_NAME, build_shared_profile(CONN), None)
    assert path.is_file()
    assert set(read_provider_profile(path)) == {"storage_url", "traces_url", "object_store"}


def test_profile_mode_is_owner_only_even_over_a_wider_file(tmp_path):
    path = tmp_path / PROFILE_FILE_NAME
    path.write_text("old: 1\n", encoding="utf-8")
    path.chmod(0o644)
    write_shared_profile(path, build_shared_profile(CONN), None)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_wider_existing_file_is_never_written_while_wide(tmp_path, monkeypatch):
    path = tmp_path / PROFILE_FILE_NAME
    path.write_text("old: 1\n", encoding="utf-8")
    path.chmod(0o644)
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((stat.S_IMODE(os.stat(src).st_mode), Path(dst).read_text(encoding="utf-8")))
        real_replace(src, dst)

    monkeypatch.setattr(setup_store_io.os, "replace", spy)
    write_shared_profile(path, build_shared_profile(CONN), None)
    assert seen == [(0o600, "old: 1\n")]
    assert PASSWORD in path.read_text(encoding="utf-8")


def test_failed_write_keeps_the_old_profile_and_leaves_no_temp(tmp_path, monkeypatch):
    path = tmp_path / PROFILE_FILE_NAME
    path.write_text("old: 1\n", encoding="utf-8")

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(setup_store_io.os, "replace", broken)
    with pytest.raises(OSError):
        write_shared_profile(path, build_shared_profile(CONN), None)
    assert path.read_text(encoding="utf-8") == "old: 1\n"
    assert [p.name for p in tmp_path.iterdir()] == [PROFILE_FILE_NAME]


def test_symlink_is_refused_and_its_target_untouched(tmp_path):
    target = tmp_path / "elsewhere.yaml"
    target.write_text("keep: 1\n", encoding="utf-8")
    link = tmp_path / PROFILE_FILE_NAME
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        write_shared_profile(link, build_shared_profile(CONN), None)
    assert target.read_text(encoding="utf-8") == "keep: 1\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_tracked_file_is_refused(tmp_path):
    path = tmp_path / PROFILE_FILE_NAME
    path.write_text("committed: 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", PROFILE_FILE_NAME], cwd=tmp_path, check=True)
    with pytest.raises(ValueError, match="tracked by git"):
        write_shared_profile(path, build_shared_profile(CONN), None)
    assert path.read_text(encoding="utf-8") == "committed: 1\n"


def test_profile_missing_a_provider_key_is_refused(tmp_path):
    with pytest.raises(ValueError, match="lacks storage_url, traces_url, object_store"):
        write_shared_profile(tmp_path / PROFILE_FILE_NAME, {}, None)
    assert list(tmp_path.iterdir()) == []


def test_second_write_changes_nothing(tmp_path):
    path, exclude = tmp_path / PROFILE_FILE_NAME, tmp_path / "git" / "info" / "exclude"
    profile = build_shared_profile(CONN)
    write_shared_profile(path, profile, exclude)
    before = (path.read_bytes(), path.stat().st_mtime_ns, exclude.read_bytes(), exclude.stat().st_mtime_ns)
    write_shared_profile(path, profile, exclude)
    assert (path.read_bytes(), path.stat().st_mtime_ns, exclude.read_bytes(), exclude.stat().st_mtime_ns) == before


def test_exclude_line_appears_once_after_three_runs(tmp_path):
    path, exclude = tmp_path / PROFILE_FILE_NAME, tmp_path / "exclude"
    exclude.write_text("*.pyc", encoding="utf-8")
    for _ in range(3):
        write_shared_profile(path, build_shared_profile(CONN), exclude)
    assert exclude.read_text(encoding="utf-8").splitlines() == ["*.pyc", PROFILE_FILE_NAME]


def test_default_profile_path_is_beside_settings(tmp_path):
    assert default_profile_path(tmp_path / "settings.yaml") == tmp_path / PROFILE_FILE_NAME


def _mode(settings: Path) -> str:
    return route.parse_profile(settings.read_text(encoding="utf-8"))["store"]["mode"]


def test_record_store_mode_round_trips_and_keeps_other_keys(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text("team: acme\noperator: pat\n", encoding="utf-8")
    record_store_mode("shared", settings)
    assert _mode(settings) == "shared"
    record_store_mode("files", settings)
    parsed = route.parse_profile(settings.read_text(encoding="utf-8"))
    assert (parsed["store"]["mode"], parsed["team"], parsed["operator"]) == ("files", "acme", "pat")


def test_record_store_mode_refusal_gives_the_reason(tmp_path):
    settings = tmp_path / "settings.yaml"
    with pytest.raises(ValueError, match=r"store\.mode 'bogus'.*store\.mode must be one of files, shared"):
        record_store_mode("bogus", settings)
    assert not settings.exists()


def test_record_store_mode_reasons_differ_for_unwritable_and_malformed(tmp_path):
    (tmp_path / "dir.yaml").mkdir()
    with pytest.raises(ValueError, match="cannot write"):
        record_store_mode("files", tmp_path / "dir.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not parse"):
        record_store_mode("files", bad)


def test_apply_files_choice_writes_no_profile(tmp_path):
    settings = tmp_path / "settings.yaml"
    settings.write_text("team: acme\n", encoding="utf-8")
    apply_files_choice(settings)
    assert _mode(settings) == "files"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.yaml"]


def test_apply_shared_choice_validates_before_writing_anything(tmp_path):
    settings = tmp_path / "settings.yaml"
    with pytest.raises(ValueError, match="pg_password is missing") as refused:
        apply_shared_choice(dataclasses.replace(CONN, pg_password=""), settings, tmp_path / "exclude")
    assert "pg_host" not in str(refused.value)
    assert list(tmp_path.iterdir()) == []


def test_apply_shared_choice_writes_profile_excludes_it_and_records_mode(tmp_path):
    settings, exclude = tmp_path / "settings.yaml", tmp_path / "exclude"
    message = apply_shared_choice(CONN, settings, exclude)
    profile = default_profile_path(settings)
    assert message == f"wrote {profile}; store: postgresql, host db.example, port 5432, bucket traces"
    assert set(read_provider_profile(profile)) == {"storage_url", "traces_url", "object_store"}
    assert exclude.read_text(encoding="utf-8") == PROFILE_FILE_NAME + "\n"
    assert _mode(settings) == "shared"


def test_nothing_shown_contains_the_password(tmp_path, capsys):
    settings = tmp_path / "settings.yaml"
    message = apply_shared_choice(CONN, settings, tmp_path / "exclude")
    apply_files_choice(settings)
    with pytest.raises(ValueError) as refused:
        record_store_mode("bogus", settings)
    with pytest.raises(ValueError) as invalid:
        apply_shared_choice(dataclasses.replace(CONN, s3_bucket=""), settings)
    shown = capsys.readouterr()
    assert PASSWORD not in message + str(refused.value) + str(invalid.value) + shown.out + shown.err
    assert str(default_profile_path(settings)) in message
