"""The published package is coxswain-tools, shipped by publish.yml on a v* tag.

No token exists anywhere: the workflow's OIDC identity is the credential, so
these checks are the only thing standing between a typo and a broken trusted
publish.
"""
import re
import tomllib

import yaml

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _publish_workflow():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "publish.yml").read_text())


def test_project_name_is_coxswain_tools():
    assert _pyproject()["project"]["name"] == "coxswain-tools"


def test_both_console_scripts_point_at_the_cli():
    scripts = _pyproject()["project"]["scripts"]
    assert scripts["cox"] == "agent_tools.cli:main"
    assert scripts["agent-tools"] == "agent_tools.cli:main"


def test_version_is_pep440_beta_shaped():
    version = _pyproject()["project"]["version"]
    assert re.match(r"^\d+\.\d+\.\d+(b\d+)?$", version)


def test_publish_triggers_on_v_tags():
    workflow = _publish_workflow()
    # PyYAML parses the bare `on:` key as the boolean True.
    trigger = workflow.get("on", workflow.get(True))
    assert "v*" in trigger["push"]["tags"]


def test_publish_uses_the_pypi_environment_and_id_token():
    job = _publish_workflow()["jobs"]["publish"]
    assert job["environment"] == "pypi"
    assert job["permissions"]["id-token"] == "write"


def test_publish_step_is_oidc_only_with_no_password():
    steps = _publish_workflow()["jobs"]["publish"]["steps"]
    publish_steps = [s for s in steps if s.get("uses", "").startswith("pypa/gh-action-pypi-publish")]
    assert len(publish_steps) == 1
    assert "password" not in publish_steps[0].get("with", {})


def test_publish_guards_the_tag_against_the_pyproject_version():
    # A tag that does not match pyproject.toml's version must fail the job
    # before uv build runs, not after PyPI has already accepted the file.
    steps = _publish_workflow()["jobs"]["publish"]["steps"]
    guard = next(s for s in steps if "GITHUB_REF_NAME" in s.get("run", ""))
    assert "version" in guard["run"]
    assert "exit 1" in guard["run"]


def test_repository_url_matches_the_registered_publisher():
    # Trusted publishing matches GitHub's own OIDC repository claim, not this
    # URL, but the pending publisher was registered for ppfenning/coxswain-tools
    # and this field should not point somewhere else once the rename lands.
    assert _pyproject()["project"]["urls"]["Repository"] == "https://github.com/ppfenning/coxswain-tools"
