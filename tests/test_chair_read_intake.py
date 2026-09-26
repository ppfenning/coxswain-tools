import os

from agent_tools import chair_read_intake as cri


def test_two_undecomposed_files_sort_oldest_first():
    entries = [
        ({"path": "intake/new.md", "done": False}, 200.0),
        ({"path": "intake/old.md", "done": False}, 100.0),
    ]
    assert cri.undecomposed_oldest_first(entries) == ["intake/old.md", "intake/new.md"]


def test_a_decomposed_file_is_dropped():
    entries = [
        ({"path": "intake/a.md", "done": True}, 100.0),
        ({"path": "intake/b.md", "done": False}, 200.0),
    ]
    assert cri.undecomposed_oldest_first(entries) == ["intake/b.md"]


def test_a_profile_with_one_source_is_configured():
    assert cri.has_sources(["github"]) is True


def test_a_profile_with_no_sources_is_not_configured():
    assert cri.has_sources([]) is False


def test_the_edge_lists_intake_by_mtime_and_skips_done(tmp_path):
    (tmp_path / "intake" / "done").mkdir(parents=True)
    for rel, mtime in (("new.md", 200), ("old.md", 100), ("done/gone.md", 50)):
        p = tmp_path / "intake" / rel
        p.write_text("body\n", encoding="utf-8")
        os.utime(p, (mtime, mtime))
    assert cri.read_intake(tmp_path) == ["intake/old.md", "intake/new.md"]
    assert cri.read_intake(tmp_path / "missing") == []


def test_the_edge_reads_sources_from_the_profile(tmp_path):
    profile = tmp_path / "profile.yaml"
    assert cri.read_sources_configured(profile) is False
    profile.write_text('sources: {"github": {"repos": ["a/b"]}}\n', encoding="utf-8")
    assert cri.read_sources_configured(profile) is True
    profile.write_text("sources: {}\n", encoding="utf-8")
    assert cri.read_sources_configured(profile) is False
