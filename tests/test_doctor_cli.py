import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from agent_tools import cli
from agent_tools.cli import _run_core_probe, main


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """A chair unit installed on the test machine must not reach the doctor's `chair unit PATH` row."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("COXSWAIN_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)


def _ready_home(monkeypatch, home):
    """Makes `home` the coxswain home: it exists and COXSWAIN_HOME names it. No home record, so the machine reads as unmigrated."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("COXSWAIN_HOME", str(home))


def _write_home_record(tmp_path, home):
    record = tmp_path / "home" / ".config" / "agent-tools" / "home"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(f"{home}\n")

_STUB = """#!{python}
import json
import os
import sys
from pathlib import Path
from pathlib import Path

args = sys.argv[3:]
cartridges_dir, team, *roots = args
harness_dir = Path(__file__).resolve().parents[2]
(harness_dir / "probe_argv.json").write_text(json.dumps(args))

load = None if os.path.isdir(cartridges_dir) else f"cartridge not found: {{cartridges_dir}}"
print(json.dumps({{"import": None, "load": load, "indexed": {{root: 3 for root in roots}}, "overlay_errors": None}}))
"""

_PROVIDER = """#!{python}
print("fakeprovider 1.0.0")
"""


_GIT = '#!/bin/sh\necho "git version 2.45.0"\n'


def _write_executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    assert os.access(path, os.X_OK)


def _make_store(workspace_dir):
    conn = sqlite3.connect(workspace_dir / "runs" / "cox.db")
    conn.execute("CREATE TABLE runs (id TEXT)")
    conn.commit()
    conn.close()


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
    _write_executable(bin_dir / "git", _GIT)
    monkeypatch.setenv("PATH", str(bin_dir))

    _make_store(workspace_dir)
    _ready_home(monkeypatch, tmp_path)

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


def test_with_no_profile_git_and_the_local_forge_are_still_gathered_and_the_next_step_prints(tmp_path, monkeypatch, capsys):
    _good_setup(tmp_path, monkeypatch)
    rc = main(["setup", "doctor", "--profile", str(tmp_path / "absent.yaml")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "git version 2.45.0" in out
    assert "local: plain git, no pull-request host" in out
    assert out.splitlines()[-1].startswith("next: run `cox setup`")


def test_a_machine_without_git_fails_the_git_row(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    (tmp_path / "bin" / "git").unlink()
    rc = main(["setup", "doctor", "--profile", str(profile)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "missing: install git" in out


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


# ── the probe text itself is executed against a fake `core` package ─────────

_FAKE_CORE_OK = {
    "core/__init__.py": "",
    "core/cartridge.py": (
        "class CartridgeError(Exception):\n"
        "    pass\n\n"
        "def load(team, cartridges_dir, *, skill_index):\n"
        "    return {'team': team}\n\n"
        "def layers(team, cartridges_dir, *, skill_index):\n"
        "    return [('base', {'policy': {'review_tier': '1'}}),\n"
        "            (team, {'policy': {'review_tier': '2'}})]\n"
    ),
    "core/skills.py": "def index_from_roots(roots):\n    return {f'local-skills:{i}': [r] for i, r in enumerate(roots, 1)}\n",
}
_WRAPPER = """#!/bin/sh
PYTHONPATH={pkg} exec {python} "$@"
"""


def _real_probe_setup(tmp_path, monkeypatch, fake_core):
    profile, cartridges_dir, skills_a, skills_b, harness_dir = _good_setup(tmp_path, monkeypatch)
    pkg = tmp_path / "fakepkg"
    for rel, text in fake_core.items():
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text(text)
    # A wrapper that runs the REAL interpreter with the fake package on PYTHONPATH,
    # so `_CORE_PROBE_SCRIPT` is executed rather than stubbed.
    _write_executable(harness_dir / ".venv" / "bin" / "python", _WRAPPER.format(pkg=pkg, python=sys.executable))
    return profile, cartridges_dir, skills_a, skills_b, harness_dir


def _rows(capsys):
    return {r["check"]: r for r in json.loads(capsys.readouterr().out)["rows"]}


def test_the_probe_runs_the_real_cartridge_api_and_passes(tmp_path, monkeypatch, capsys):
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, _FAKE_CORE_OK)
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    rows = _rows(capsys)
    assert rows["core importable"]["ok"] and rows["cartridge"]["ok"] and rows["skills"]["ok"], rows
    assert rc == 0


def test_the_probe_reports_the_resolved_cartridge_and_the_skill_index(tmp_path, monkeypatch, capsys):
    profile, cartridges_dir, skills_a, skills_b, harness_dir = _real_probe_setup(tmp_path, monkeypatch, _FAKE_CORE_OK)
    facts = _run_core_probe(str(harness_dir / ".venv" / "bin" / "python"), str(cartridges_dir), "acme",
                             [str(skills_a), str(skills_b)])
    assert facts["resolved"] == {"team": "acme"}
    assert facts["skill_index"] == {"local-skills:1": [str(skills_a)], "local-skills:2": [str(skills_b)]}
    # The doctor rows this fixture already produced are unaffected by the new keys.
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    rows = _rows(capsys)
    assert rows["core importable"]["ok"] and rows["cartridge"]["ok"] and rows["skills"]["ok"], rows
    assert rc == 0


def test_a_cartridge_load_error_reaches_the_cartridge_row_verbatim(tmp_path, monkeypatch, capsys):
    fake = dict(_FAKE_CORE_OK)
    fake["core/cartridge.py"] = "class CartridgeError(Exception):\n    pass\n\ndef load(team, cartridges_dir, *, skill_index):\n    raise CartridgeError(\"no cartridge for 'acme'\")\n"
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, fake)
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    rows = _rows(capsys)
    assert rows["cartridge"]["ok"] is False and "no cartridge for 'acme'" in rows["cartridge"]["detail"]
    assert rows["skills"]["ok"], "the index still worked"
    assert rc == 1


def test_a_cartridge_load_error_gives_resolved_none_but_keeps_the_skill_index(tmp_path, monkeypatch):
    fake = dict(_FAKE_CORE_OK)
    fake["core/cartridge.py"] = "class CartridgeError(Exception):\n    pass\n\ndef load(team, cartridges_dir, *, skill_index):\n    raise CartridgeError(\"no cartridge for 'acme'\")\n"
    _, cartridges_dir, skills_a, skills_b, harness_dir = _real_probe_setup(tmp_path, monkeypatch, fake)
    facts = _run_core_probe(str(harness_dir / ".venv" / "bin" / "python"), str(cartridges_dir), "acme",
                             [str(skills_a), str(skills_b)])
    assert facts["resolved"] is None
    assert facts["cartridge_load"] == "CartridgeError: no cartridge for 'acme'"
    assert facts["skill_index"] == {"local-skills:1": [str(skills_a)], "local-skills:2": [str(skills_b)]}


def test_a_failing_skill_index_fails_skills_and_names_itself_on_the_cartridge_row(tmp_path, monkeypatch, capsys):
    fake = dict(_FAKE_CORE_OK)
    fake["core/skills.py"] = "def index_from_roots(roots):\n    raise RuntimeError('bad plugin manifest')\n"
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, fake)
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    rows = _rows(capsys)
    assert rows["skills"]["ok"] is False
    assert "skill index failed" in rows["cartridge"]["detail"] and "bad plugin manifest" in rows["cartridge"]["detail"]
    assert rc == 1


def test_the_probe_reports_provenance_for_each_key_the_fixed_layers_set(tmp_path, monkeypatch):
    _, cartridges_dir, skills_a, skills_b, harness_dir = _real_probe_setup(tmp_path, monkeypatch, _FAKE_CORE_OK)
    facts = _run_core_probe(str(harness_dir / ".venv" / "bin" / "python"), str(cartridges_dir), "acme",
                             [str(skills_a), str(skills_b)])
    assert facts["provenance"] == {"policy.review_tier": "acme"}


def test_a_layers_failure_surfaces_as_provenance_error_with_the_exception_text(tmp_path, monkeypatch):
    fake = dict(_FAKE_CORE_OK)
    fake["core/cartridge.py"] = (
        "class CartridgeError(Exception):\n"
        "    pass\n\n"
        "def load(team, cartridges_dir, *, skill_index):\n"
        "    return {'team': team}\n\n"
        "def layers(team, cartridges_dir, *, skill_index):\n"
        "    raise RuntimeError('extends chain broke')\n"
    )
    _, cartridges_dir, skills_a, skills_b, harness_dir = _real_probe_setup(tmp_path, monkeypatch, fake)
    facts = _run_core_probe(str(harness_dir / ".venv" / "bin" / "python"), str(cartridges_dir), "acme",
                             [str(skills_a), str(skills_b)])
    assert "provenance" not in facts
    assert facts["provenance_error"] == "RuntimeError: extends chain broke"


def test_an_empty_layers_list_is_a_loader_contract_violation_not_a_clean_result(tmp_path, monkeypatch):
    fake = dict(_FAKE_CORE_OK)
    fake["core/cartridge.py"] = (
        "class CartridgeError(Exception):\n"
        "    pass\n\n"
        "def load(team, cartridges_dir, *, skill_index):\n"
        "    return {'team': team}\n\n"
        "def layers(team, cartridges_dir, *, skill_index):\n"
        "    return []\n"
    )
    _, cartridges_dir, skills_a, skills_b, harness_dir = _real_probe_setup(tmp_path, monkeypatch, fake)
    facts = _run_core_probe(str(harness_dir / ".venv" / "bin" / "python"), str(cartridges_dir), "acme",
                             [str(skills_a), str(skills_b)])
    assert "provenance" not in facts
    assert facts["provenance_error"] == "the loader returned no layers"


_FAKE_CORE_OVERLAY = dict(_FAKE_CORE_OK)
_FAKE_CORE_OVERLAY["core/cartridge.py"] = (
    "class CartridgeError(Exception):\n"
    "    pass\n\n"
    "def load(team, cartridges_dir, *, skill_index):\n"
    "    return {'team': team}\n\n"
    "def layers(team, cartridges_dir, *, skill_index):\n"
    "    return [('base', {'policy': {'review_tier': '1'}})]\n\n"
    "def overlay_errors(text):\n"
    "    return ['skills'] if 'skills' in text else []\n"
)


def test_an_overlay_with_a_refused_key_fails_the_project_overlay_row_naming_it(tmp_path, monkeypatch, capsys):
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, _FAKE_CORE_OVERLAY)
    repo = tmp_path / "repo"
    (repo / ".agent").mkdir(parents=True)
    (repo / ".agent" / "cartridge.yaml").write_text("skills:\n  - forbidden\n")
    rc = main(["setup", "doctor", "--profile", str(profile), "--repo", str(repo), "--json"])
    rows = _rows(capsys)
    assert rows["project overlay"]["ok"] is False
    assert rows["project overlay"]["detail"] == "skills"
    assert rc == 1


def test_no_overlay_file_leaves_every_row_ok(tmp_path, monkeypatch, capsys):
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, _FAKE_CORE_OVERLAY)
    repo = tmp_path / "repo"; repo.mkdir()
    rc = main(["setup", "doctor", "--profile", str(profile), "--repo", str(repo), "--json"])
    rows = _rows(capsys)
    assert rows["project overlay"] == {"check": "project overlay", "ok": True, "detail": "no project overlay"}
    assert rc == 0


def test_a_core_import_failure_leaves_project_overlay_not_checked_even_with_an_overlay_present(tmp_path, monkeypatch, capsys):
    fake = {"core/__init__.py": "", "core/cartridge.py": _FAKE_CORE_OVERLAY["core/cartridge.py"]}
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, fake)
    repo = tmp_path / "repo"
    (repo / ".agent").mkdir(parents=True)
    (repo / ".agent" / "cartridge.yaml").write_text("skills:\n  - forbidden\n")
    rc = main(["setup", "doctor", "--profile", str(profile), "--repo", str(repo), "--json"])
    rows = _rows(capsys)
    assert rows["project overlay"] == {"check": "project overlay", "ok": False, "detail": "not checked"}
    assert rc == 1


def test_a_trailing_yaml_comment_on_the_provider_command_is_not_part_of_the_command(tmp_path, monkeypatch, capsys):
    """The shipped provider profile reads `command: claude   # the binary; must be on PATH`."""
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    (tmp_path / "provider.yaml").write_text("command: fakeprovider            # the binary; must be on PATH and logged in\n")
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    rows = _rows(capsys)
    assert rows["provider"]["ok"], rows["provider"]
    assert rc == 0


# ── schema_versions is gathered through a real, in-process `import core` /
# `import harness`, using the same write-a-fake-package technique as the probe. ─

def _fake_schema_package(tmp_path, monkeypatch, *, cartridges, graphs):
    pkg = tmp_path / "fakeschema"
    (pkg / "core").mkdir(parents=True)
    (pkg / "core" / "__init__.py").write_text(f"SCHEMA_VERSION = {cartridges!r}\n")
    (pkg / "harness").mkdir(parents=True)
    (pkg / "harness" / "__init__.py").write_text(f"CORE_SCHEMA = {graphs!r}\n")
    monkeypatch.syspath_prepend(str(pkg))
    monkeypatch.delitem(sys.modules, "core", raising=False)
    monkeypatch.delitem(sys.modules, "harness", raising=False)


def test_a_fresh_install_passes_the_store_row_with_no_runs_yet(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    (tmp_path / "workspace" / "runs" / "cox.db").unlink()
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    assert _rows(capsys)["store"] == {"check": "store", "ok": True, "detail": "sqlite, no runs yet (the first run creates it)"}
    assert rc == 0


def test_a_malformed_storage_url_fails_the_store_row_without_echoing_it(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    (tmp_path / "provider.yaml").write_text("command: fakeprovider\nstorage_url: postgresql://u:secret@[bad/db\n")
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    assert _rows(capsys)["store"] == {"check": "store", "ok": False, "detail": "storage_url is not a valid URL"}
    assert rc == 1


def test_schema_versions_are_gathered_and_agree_ok(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    _fake_schema_package(tmp_path, monkeypatch, cartridges="1.0", graphs="1.0")
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    rows = _rows(capsys)
    assert rows["schema"] == {"check": "schema", "ok": True, "detail": "cartridges 1.0, graphs 1.0, tools 1.0"}
    assert rc == 0


def test_a_differing_major_gathered_through_import_warns_the_schema_row(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    _fake_schema_package(tmp_path, monkeypatch, cartridges="1.0", graphs="2.0")
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    rows = _rows(capsys)
    assert rows["schema"] == {"check": "schema", "ok": False, "detail": "graphs 2.0"}
    assert rc == 1


def test_schema_row_reads_cartridges_from_the_checkout_above_provider_profile(tmp_path, monkeypatch, capsys):
    """`provider_profile` sits under `<checkout>/providers/`, per a real
    install; the profile's own `cartridges_dir` names an empty workspace
    directory the reader must never consult."""
    checkout = tmp_path / "checkout"
    (checkout / "core").mkdir(parents=True)
    (checkout / "core" / "__init__.py").write_text('SCHEMA_VERSION = "1.0"\n')
    provider_dir = checkout / "providers"
    provider_dir.mkdir()

    cartridges_dir = tmp_path / "workspace" / "cartridges"; cartridges_dir.mkdir(parents=True)
    skills_a = tmp_path / "skills_a"; skills_a.mkdir()
    skills_b = tmp_path / "skills_b"; skills_b.mkdir()
    harness_dir = tmp_path / "harness"
    workspace_dir = tmp_path / "workspace"
    for name in ("work", "runs", "intake"):
        (workspace_dir / name).mkdir(parents=True, exist_ok=True)
    _make_store(workspace_dir)
    provider_profile = provider_dir / "provider.yaml"
    provider_profile.write_text("command: fakeprovider\n")

    _write_executable(harness_dir / ".venv" / "bin" / "python", _STUB.format(python=sys.executable))
    (harness_dir / "harness").mkdir()
    (harness_dir / "harness" / "__init__.py").write_text('CORE_SCHEMA = "1.0"\n')
    bin_dir = tmp_path / "bin"
    _write_executable(bin_dir / "fakeprovider", _PROVIDER.format(python=sys.executable))
    _write_executable(bin_dir / "git", _GIT)
    monkeypatch.setenv("PATH", str(bin_dir))
    _ready_home(monkeypatch, tmp_path)

    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "team: acme\n"
        f"cartridges_dir: {cartridges_dir}\n"
        f"skills_roots: [{skills_a}, {skills_b}]\n"
        f"provider_profile: {provider_profile}\n"
        f"harness_dir: {harness_dir}\n"
        f"workspace_dir: {workspace_dir}\n"
    )

    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    rows = _rows(capsys)
    assert rows["schema"] == {"check": "schema", "ok": True, "detail": "cartridges 1.0, graphs 1.0, tools 1.0"}
    assert rc == 0


def _unit_row_line(out):
    return next(line for line in out.splitlines() if "chair unit PATH" in line)


def test_no_installed_unit_reads_not_installed(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    main(["setup", "doctor", "--profile", str(profile)])
    assert "not installed" in _unit_row_line(capsys.readouterr().out)


def test_an_installed_unit_is_judged_by_the_executables_on_its_path(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    unit_bin = tmp_path / "unit-bin"
    _write_executable(unit_bin / "cox", "#!/bin/sh\n")
    unit = tmp_path / "home" / ".config" / "systemd" / "user" / "coxswain-chair.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(f"[Service]\nEnvironment=PATH={unit_bin}\n")
    rc = main(["setup", "doctor", "--profile", str(profile)])
    line = _unit_row_line(capsys.readouterr().out)
    assert rc == 1
    assert "FAIL" in line and "pytest" in line
    _write_executable(unit_bin / "pytest", "#!/bin/sh\n")
    _write_executable(unit_bin / "claude", "#!/bin/sh\n")
    _write_executable(unit_bin / "uv", "#!/bin/sh\n")
    assert main(["setup", "doctor", "--profile", str(profile)]) == 0


# ── the model freshness check runs inside the probe, against a stub core ────

def _fresh_core(warnings_src, *, profile_src="return {'p': 1}", catalog_src="return {'c': 1}"):
    return {
        **_FAKE_CORE_OK,
        "core/catalog.py": f"def load_catalog(path):\n    {catalog_src}\n",
        "core/profile.py": f"def load_profile(provider_profile, catalog_path):\n    {profile_src}\n",
        "core/model_freshness.py": f"def freshness_warnings(profile, catalog):\n    {warnings_src}\n",
    }


def _freshness_rows(rows):
    return [r for r in rows.values() if r["check"] == "model freshness"]


def test_a_freshness_check_with_nothing_to_report_adds_no_row(tmp_path, monkeypatch, capsys):
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, _fresh_core("return []"))
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    assert _freshness_rows(_rows(capsys)) == []
    assert rc == 0


def test_a_freshness_warning_prints_one_warn_line_and_leaves_the_exit_code(tmp_path, monkeypatch, capsys):
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, _fresh_core("return ['opus-4 is 120 days old']"))
    rc = main(["setup", "doctor", "--profile", str(profile)])
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("model freshness")]
    assert len(lines) == 1 and "warn" in lines[0] and "opus-4 is 120 days old" in lines[0]
    assert rc == 0


def test_a_core_without_model_freshness_gives_the_one_skip_line(tmp_path, monkeypatch, capsys):
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, _FAKE_CORE_OK)
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    (row,) = _freshness_rows(_rows(capsys))
    assert row["ok"] is True and row["warn"] is True and row["detail"].startswith("skipped:")
    assert rc == 0


def test_a_profile_loader_error_list_becomes_a_not_checked_line(tmp_path, monkeypatch, capsys):
    core = _fresh_core("return []", profile_src="return ['no such tier', 'second']")
    profile, *_ = _real_probe_setup(tmp_path, monkeypatch, core)
    rc = main(["setup", "doctor", "--profile", str(profile), "--json"])
    (row,) = _freshness_rows(_rows(capsys))
    assert row["detail"] == "model freshness not checked: no such tier"
    assert rc == 0


# ── the coxswain home rows ───────────────────────────────────────────────────

_HOME_CHECKS = {
    "home root", "marketplace registered", "marketplace location", "old layout", "home paths", "home recorded",
    "cartridge links",
}


def _checks(capsys):
    return [r["check"] for r in json.loads(capsys.readouterr().out)["rows"]]


def test_with_no_profile_every_home_row_is_skipped(tmp_path, monkeypatch, capsys):
    _good_setup(tmp_path, monkeypatch)
    main(["setup", "doctor", "--profile", str(tmp_path / "absent.yaml"), "--json"])
    home = [r for r in json.loads(capsys.readouterr().out)["rows"] if r["check"] in _HOME_CHECKS]
    assert {r["check"] for r in home} == _HOME_CHECKS
    assert {r["detail"] for r in home} == {"skipped: no profile"}


def test_a_profile_with_paths_under_the_home_installs_passes_the_home_paths_row(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    home = tmp_path / "coxswain"
    installs = home / "installs"
    _ready_home(monkeypatch, home)
    _write_home_record(tmp_path, home)
    profile.write_text(
        "team: acme\n"
        f"skills_roots: [{installs}/coxswain-cartridges/skills-plugins]\n"
        f"provider_profile: {installs}/coxswain-cartridges/providers/claude-code.yaml\n"
        f"harness_dir: {installs}/coxswain-graphs\n"
        f"workspace_dir: {home}/workspaces/default\n"
    )
    main(["setup", "doctor", "--profile", str(profile), "--json"])
    rows = _rows(capsys)
    assert rows["home paths"]["ok"] is True and "warn" not in rows["home paths"]
    assert rows["home recorded"]["ok"] is True
    assert rows["home root"]["ok"] is True


def test_home_rows_sit_after_the_doctor_checks_and_before_the_store_rows(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    main(["setup", "doctor", "--profile", str(profile), "--json"])
    checks = _checks(capsys)
    home = [i for i, c in enumerate(checks) if c in _HOME_CHECKS]
    assert home == list(range(home[0], home[0] + len(home)))
    assert checks.index("chair unit PATH") < home[0]
    assert home[-1] < checks.index("shared store")


def test_a_failing_home_row_exits_nonzero_and_a_warn_row_does_not(tmp_path, monkeypatch, capsys):
    profile, *_ = _good_setup(tmp_path, monkeypatch)
    assert main(["setup", "doctor", "--profile", str(profile)]) == 0
    capsys.readouterr()
    monkeypatch.setenv("COXSWAIN_HOME", str(tmp_path / "elsewhere"))
    record = Path(cli.home_paths.resolve_here(None).record)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(str(tmp_path / "elsewhere"), encoding="utf-8")  # a migrated machine: a missing home fails
    assert main(["setup", "doctor", "--profile", str(profile), "--json"]) == 1
    assert _rows(capsys)["home root"]["ok"] is False
