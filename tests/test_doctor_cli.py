import json
import os
import stat
import sys

from agent_tools.cli import main

_STUB = """#!{python}
import json
import os
import sys
from pathlib import Path

args = sys.argv[3:]
cartridges_dir, team, *roots = args
harness_dir = Path(__file__).resolve().parents[2]
(harness_dir / "probe_argv.json").write_text(json.dumps(args))

load = None if os.path.isdir(cartridges_dir) else f"cartridge not found: {{cartridges_dir}}"
print(json.dumps({{"import": None, "load": load, "indexed": {{root: 3 for root in roots}}}}))
"""

_PROVIDER = """#!{python}
print("fakeprovider 1.0.0")
"""


def _write_executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    assert os.access(path, os.X_OK)


def _good_setup(tmp_path, monkeypatch):
    cartridges_dir = tmp_path / "cartridges"; cartridges_dir.mkdir()
    skills_a = tmp_path / "skills_a"; skills_a.mkdir()
    skills_b = tmp_path / "skills_b"; skills_b.mkdir()
    harness_dir = tmp_path / "harness"
    workspace_dir = tmp_path / "workspace"
    for name in ("work", "runs", "intake"):
        (workspace_dir / name).mkdir(parents=True)
    provider_profile = tmp_path / "provider.yaml"
    provider_profile.write_text("command: fakeprovider\n")

    _write_executable(harness_dir / ".venv" / "bin" / "python", _STUB.format(python=sys.executable))
    bin_dir = tmp_path / "bin"
    _write_executable(bin_dir / "fakeprovider", _PROVIDER.format(python=sys.executable))
    monkeypatch.setenv("PATH", str(bin_dir))

    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "team: acme\n"
        f"cartridges_dir: {cartridges_dir}\n"
        f"skills_roots: [{skills_a}, {skills_b}]\n"
        f"provider_profile: {provider_profile}\n"
        f"harness_dir: {harness_dir}\n"
        f"workspace_dir: {workspace_dir}\n"
    )
    return profile, cartridges_dir, skills_a, skills_b, harness_dir


def test_all_good_gives_exit_zero_with_every_row_ok(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    rc = main(["setup", "doctor", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "FAIL" not in out


def test_probe_argv_carries_the_profiles_own_cartridges_dir_team_and_roots(tmp_path, monkeypatch, capsys):
    profile, cartridges_dir, skills_a, skills_b, harness_dir = _good_setup(tmp_path, monkeypatch)
    main(["setup", "doctor", "--profile", str(profile)])
    capsys.readouterr()
    recorded = json.loads((harness_dir / "probe_argv.json").read_text())
    assert recorded == [str(cartridges_dir), "acme", str(skills_a), str(skills_b)]


def test_a_cartridges_dir_that_was_never_created_fails_the_cartridge_row(tmp_path, monkeypatch, capsys):
    profile, cartridges_dir, *_ = _good_setup(tmp_path, monkeypatch)
    text = profile.read_text().replace(str(cartridges_dir), str(cartridges_dir / "missing"))
    profile.write_text(text)
    rc = main(["setup", "doctor", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "cartridge not found" in out


def test_a_deleted_profile_fails_and_skips_dependent_rows(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    profile.unlink()
    rc = main(["setup", "doctor", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "profile" in out and "FAIL" in out
    assert "skipped: no profile" in out


def test_a_removed_configured_path_is_named(tmp_path, monkeypatch, capsys):
    profile, cartridges_dir, *_ = _good_setup(tmp_path, monkeypatch)
    cartridges_dir.rmdir()
    rc = main(["setup", "doctor", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 1
    assert f"missing: {cartridges_dir}" in out


def test_provider_not_on_path_fails_that_row(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    empty_bin = tmp_path / "empty_bin"; empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    rc = main(["setup", "doctor", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "not on PATH" in out


def test_json_output_parses_and_ok_matches_the_exit_code(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] == (rc == 0)
    assert isinstance(doc["rows"], list) and doc["rows"]
