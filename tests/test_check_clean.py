"""The clean check: each structural rule, a name from COXSWAIN_CLEAN_DENY, and the skipped names rule.

Fixture lines that must trip a rule carry the `clean: allow` marker, so this file passes its own check.
"""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_clean", Path(__file__).resolve().parents[1] / ".github" / "check_clean.py"
)
check_clean = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_clean)

NO_NAMES = {}
DENY = {"COXSWAIN_CLEAN_DENY": r"(?i)\bzelda\b"}


def run(tmp_path, files, environ, capsys):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text) if isinstance(text, bytes) else path.write_text(text, encoding="utf-8")
    code = check_clean.main(["check_clean.py", str(tmp_path)], environ)
    return code, capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("line", [
    "cd /home/alice/repos",  # clean: allow
    "path: /Users/alice/repos",  # clean: allow
    "ssh://box/home/alice/repos/proj",  # clean: allow
    "/Users/user/repos",  # clean: allow
])
def test_a_home_path_other_than_the_neutral_one_is_a_hit(line):
    assert check_clean.home_path(line)


def test_the_neutral_home_and_relative_paths_are_not_hits():
    assert check_clean.home_path("cd /home/user/repos && ls ~/repos and repos/home") is None


@pytest.mark.parametrize("line", ["url: https://git.lan:3000", "ssh git@Store.LAN"])  # clean: allow
def test_a_lan_host_is_a_hit(line):
    assert check_clean.lan_host(line)


def test_names_ending_in_lan_letters_are_not_lan_hosts():
    assert check_clean.lan_host("a plan.landed, the lane, git.example.internal") is None


@pytest.mark.parametrize("line", [
    "tailnet 100.64.0.1",  # clean: allow
    "host 100.127.255.254:22",  # clean: allow
    "lan 192.168.1.20",  # clean: allow
])
def test_a_cgnat_or_192_168_address_is_a_hit(line):
    assert check_clean.address(line)


@pytest.mark.parametrize("line", [
    "100.128.0.1 and 100.63.255.255 sit outside the CGNAT range",
    "10.0.0.1 and 203.0.113.7 are fine",
    "version 1.100.64.0.1 is not an address",
    "999.168.1.1 is not an address",
])
def test_addresses_outside_the_two_ranges_are_not_hits(line):
    assert check_clean.address(line) is None


def test_a_hit_prints_file_line_and_rule_and_exits_non_zero(tmp_path, capsys):
    code, out = run(tmp_path, {"docs/a.md": "fine\nserver at git.lan\n"}, NO_NAMES, capsys)  # clean: allow
    assert code == 1
    assert "docs/a.md:2: lan-host: 'git.lan'" in out  # clean: allow


def test_a_name_from_the_environment_is_a_hit(tmp_path, capsys):
    code, out = run(tmp_path, {"a.py": "x = 1\n# ask Zelda\n"}, DENY, capsys)
    assert code == 1
    assert out == ["a.py:2: names: 'Zelda'"]


def test_without_the_environment_the_names_rule_is_skipped_with_one_line(tmp_path, capsys):
    code, out = run(tmp_path, {"a.py": "# ask Zelda\n"}, NO_NAMES, capsys)
    assert code == 0
    assert out[0] == "names rule skipped: COXSWAIN_CLEAN_DENY is not set"
    assert len([line for line in out if "skipped" in line]) == 1


def test_an_unusable_deny_pattern_exits_two(tmp_path, capsys):
    code, out = run(tmp_path, {"a.py": "x\n"}, {"COXSWAIN_CLEAN_DENY": "(unclosed"}, capsys)
    assert code == 2
    assert out[0].startswith("COXSWAIN_CLEAN_DENY is not a usable regex")


def test_the_allow_marker_exempts_only_its_own_line(tmp_path, capsys):
    text = "ask Zelda  # clean: allow\nask Zelda\n"
    code, out = run(tmp_path, {"a.py": text}, DENY, capsys)
    assert (code, out) == (1, ["a.py:2: names: 'Zelda'"])


def test_git_dirs_and_binary_files_are_not_read(tmp_path, capsys):
    files = {".git/config": "url = git.lan\n", "blob.bin": b"\0git.lan"}  # clean: allow
    code, out = run(tmp_path, files, NO_NAMES, capsys)
    assert code == 0
    assert out[-1].startswith("clean:")


def test_this_repository_passes_its_structural_rules(capsys):
    root = Path(__file__).resolve().parents[1]
    assert check_clean.main(["check_clean.py", str(root)], NO_NAMES) == 0, capsys.readouterr().out


def test_skip_drops_a_fixed_rule(tmp_path, capsys):
    (tmp_path / "a.md").write_text("binds the jira tracker\n")  # clean: allow
    assert check_clean.main(["check_clean.py", str(tmp_path)], NO_NAMES) == 1
    assert check_clean.main(["check_clean.py", str(tmp_path), "--skip", "vendor"], NO_NAMES) == 0
    capsys.readouterr()


def test_skip_naming_no_rule_is_refused(tmp_path, capsys):
    assert check_clean.main(["check_clean.py", "--skip", "vendr", str(tmp_path)], NO_NAMES) == 2
    assert "vendr" in capsys.readouterr().out


def test_a_rust_target_dir_is_not_scanned(tmp_path, capsys):
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "out.txt").write_text("host.lan\n")  # clean: allow
    assert check_clean.main(["check_clean.py", str(tmp_path)], NO_NAMES) == 0
    capsys.readouterr()
