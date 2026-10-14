import pytest
from test_doctor_cli import _fresh_core, _freshness_rows, _real_probe_setup, _rows

from agent_tools.cli import _run_core_probe, main

# The stub loader calls `.read_text()` on its first argument, as the real one does, so a str reproduces the crash.
_READS_THE_FILE = "return {'text': provider_profile.read_text()}"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AGENT_TOOLS_PROVIDER_PROFILE", raising=False)


def _probe(tmp_path, monkeypatch, provider_profile, **core_kwargs):
    profile, cartridges_dir, skills_a, skills_b, harness_dir = _real_probe_setup(
        tmp_path, monkeypatch, _fresh_core("return []", **core_kwargs))
    facts = _run_core_probe(str(harness_dir / ".venv" / "bin" / "python"), str(cartridges_dir), "acme",
                            [str(skills_a), str(skills_b)], provider_profile=provider_profile)
    return profile, facts


def test_a_string_path_to_a_real_profile_runs_the_check(tmp_path, monkeypatch):
    real = tmp_path / "real-provider.yaml"
    real.write_text("command: fakeprovider\n")
    _, facts = _probe(tmp_path, monkeypatch, str(real), profile_src=_READS_THE_FILE)
    assert facts["freshness_lines"] == []


def test_the_doctor_row_for_a_real_profile_is_absent_and_the_exit_code_is_zero(tmp_path, monkeypatch, capsys):
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, _fresh_core("return []", profile_src=_READS_THE_FILE))
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    assert _freshness_rows(_rows(capsys)) == []
    assert rc == 0


def test_a_missing_profile_path_is_named_in_a_warn_row(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "nowhere" / "provider.yaml"
    _, facts = _probe(tmp_path, monkeypatch, str(missing), profile_src=_READS_THE_FILE)
    (line,) = facts["freshness_lines"]
    assert line == f"model freshness not checked: provider profile not found at {missing}"
    assert "AttributeError" not in line


def test_the_doctor_shows_a_missing_profile_as_a_warn_row(tmp_path, monkeypatch, capsys):
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, _fresh_core("return []", profile_src=_READS_THE_FILE))
    (tmp_path / "provider.yaml").unlink()
    main(["setup", "doctor", "--profile", str(profile), "--json"])
    (row,) = _freshness_rows(_rows(capsys))
    assert row["ok"] is True and row["warn"] is True
    assert str(tmp_path / "provider.yaml") in row["detail"] and "AttributeError" not in row["detail"]


def test_an_unset_profile_is_stated_as_unset(tmp_path, monkeypatch):
    _, facts = _probe(tmp_path, monkeypatch, None, profile_src=_READS_THE_FILE)
    assert facts["freshness_lines"] == ["model freshness not checked: AGENT_TOOLS_PROVIDER_PROFILE is unset"]


def test_an_unexpected_loader_exception_shows_no_class_name_or_interpreter_message(tmp_path, monkeypatch):
    real = tmp_path / "real-provider.yaml"
    real.write_text("command: fakeprovider\n")
    _, facts = _probe(tmp_path, monkeypatch, str(real), profile_src="raise RuntimeError('boom')")
    (line,) = facts["freshness_lines"]
    assert line == f"model freshness not checked: could not evaluate the provider profile at {real}"
    assert "RuntimeError" not in line and "boom" not in line
