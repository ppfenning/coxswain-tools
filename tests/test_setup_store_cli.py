from pathlib import Path

import pytest

from agent_tools import cli, setup_install
from agent_tools.store_probe import ProbeResult
from agent_tools.store_url import read_provider_profile

PASSWORD = "pw-test-SECRET-9f3"
ACCESS = "GKaccess123"
SECRET = "sekrit-secret-key"
ENV = {"PG_PW": PASSWORD, "S3_ID": ACCESS, "S3_SEC": SECRET}
SHARED = [
    "--store", "shared", "--host", "db.example", "--port", "5432", "--database", "graphs", "--user", "cox",
    "--bucket", "traces", "--endpoint", "http://garage.example:3900", "--region", "garage",
    "--password-env", "PG_PW", "--access-key-env", "S3_ID", "--secret-key-env", "S3_SEC",
]
GOOD = [ProbeResult("postgres", True, "accepted"), ProbeResult("object_store", True, "reachable")]
BAD = [ProbeResult("postgres", False, "refused"), ProbeResult("object_store", True, "reachable")]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGENT_TOOLS_PROFILE", raising=False)
    return tmp_path


def run(argv, **hooks):
    args = cli.build_parser().parse_args(["setup", "store", *argv])
    hooks.setdefault("env", ENV)
    hooks.setdefault("interactive", False)
    hooks.setdefault("probes", lambda profile, overlay: GOOD)
    return cli._setup_store(args, **hooks)


def settings(home: Path) -> str:
    path = home / ".config" / "agent-tools" / "profile.yaml"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def shared_profile(home: Path) -> Path:
    return home / ".config" / "agent-tools" / "shared-store.yaml"


def test_non_interactive_default_records_files_and_asks_nothing(home, capsys):
    asked = []
    assert run([], ask=asked.append) == 0
    assert "mode: files" in settings(home)
    assert not shared_profile(home).exists()
    assert asked == []


def test_files_flag(home):
    assert run(["--store", "files"]) == 0
    assert "mode: files" in settings(home)
    assert not shared_profile(home).exists()


def test_invalid_value_exits_2_naming_the_allowed_values(home, capsys):
    assert run(["--store", "cloud"]) == 2
    assert "files or shared" in capsys.readouterr().err
    assert settings(home) == ""


def test_shared_with_all_details_writes_a_readable_profile_and_records_shared(home, capsys):
    exclude = home / "exclude"
    assert run(SHARED, exclude_file=exclude) == 0
    profile = read_provider_profile(shared_profile(home))
    assert set(profile) == {"storage_url", "traces_url", "object_store"}
    assert "mode: shared" in settings(home)
    assert exclude.read_text(encoding="utf-8") == "shared-store.yaml\n"
    out = capsys.readouterr().out
    assert "postgres: ok" in out and "host db.example" in out


def test_missing_credential_variable_exits_2_naming_the_variable_only(home, capsys):
    assert run(SHARED, env={"PG_PW": PASSWORD, "S3_ID": ACCESS}) == 2
    captured = capsys.readouterr()
    assert "S3_SEC" in captured.err
    assert SECRET not in captured.out + captured.err
    assert not shared_profile(home).exists()


def test_invalid_details_exit_2_with_the_problem_sentences(home, capsys):
    assert run([*SHARED, "--endpoint", "garage.example:3900"]) == 2
    assert "s3_endpoint must start with http:// or https://." in capsys.readouterr().err
    assert not shared_profile(home).exists()


def test_non_interactive_missing_detail_exits_2_without_prompting(home, capsys):
    argv = [word for word in SHARED if word not in ("--host", "db.example")]
    assert run(argv, ask=lambda _prompt: pytest.fail("prompted")) == 2
    assert "--host" in capsys.readouterr().err


def test_interactive_prompt_chooses_shared_and_fills_missing_details(home):
    answers = iter(["shared", "db.example", "5432", "graphs", "cox", "traces", "http://garage.example:3900",
                    "garage", "PG_PW", "S3_ID", "S3_SEC"])
    assert run([], interactive=True, ask=lambda _prompt: next(answers)) == 0
    assert "mode: shared" in settings(home)
    assert shared_profile(home).exists()
    assert next(answers, None) is None


def test_interactive_enter_takes_the_default_files(home):
    prompts = []
    assert run([], interactive=True, ask=lambda prompt: prompts.append(prompt) or "") == 0
    assert len(prompts) == 1 and "[files]" in prompts[0]
    assert "mode: files" in settings(home)


def test_flag_beats_the_prompt(home):
    assert run(["--store", "files"], interactive=True, ask=lambda _prompt: pytest.fail("prompted")) == 0


def test_dry_run_writes_nothing(home, capsys):
    assert run([*SHARED, "--dry-run"], probes=lambda *_: pytest.fail("probed")) == 0
    assert run(["--store", "files", "--dry-run"]) == 0
    assert settings(home) == ""
    assert not shared_profile(home).exists()
    assert "nothing was written" in capsys.readouterr().out


def test_failing_probe_exits_1_and_keeps_the_profile(home, capsys):
    assert run(SHARED, probes=lambda *_: BAD) == 1
    assert shared_profile(home).exists()
    assert "postgres: failed" in capsys.readouterr().out


def test_failing_probe_exits_0_when_unreachable_is_allowed(home):
    assert run([*SHARED, "--allow-unreachable"], probes=lambda *_: BAD) == 0


def test_probes_get_the_resolved_keys_under_the_profile_names(home):
    seen = []
    run(SHARED, probes=lambda profile, overlay: seen.append(overlay) or GOOD)
    assert seen == [{"AWS_ACCESS_KEY_ID": ACCESS, "AWS_SECRET_ACCESS_KEY": SECRET}]


@pytest.mark.parametrize("argv,probes", [(SHARED, GOOD), (SHARED, BAD), ([*SHARED, "--dry-run"], GOOD)])
def test_output_and_error_never_carry_a_secret(home, capsys, argv, probes):
    run(argv, probes=lambda *_: probes)
    captured = capsys.readouterr()
    shown = captured.out + captured.err
    assert not any(secret in shown for secret in (PASSWORD, ACCESS, SECRET, "postgresql://"))


def test_install_plan_starts_no_service(tmp_path):
    steps = setup_install.install_plan(
        root="/r", team="t", workspace="/w", provider_profile="/r/p.yaml", skills_root="/r/s",
        uv_on_path=True, python_exists=dict.fromkeys(setup_install.REPOS, True), claude_on_path=True,
        profile_exists=False, force_profile=False, plugins=True, hook=True,
        config_dir=str(tmp_path), claude_settings_path=str(tmp_path / "settings.json"),
    )
    argvs = [" ".join(step["argv"]) for step in steps if step.get("op") == "run"]
    assert argvs
    banned = ("postgres", "garage", "docker", "systemctl", "brew services")
    assert not [argv for argv in argvs if any(word in argv for word in banned)]
