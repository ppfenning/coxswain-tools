import json

from agent_tools import cli, run_store, source_github
from agent_tools.cli import main
from agent_tools.sources import Candidate, SourceConfig

CONFIG = SourceConfig(repos=("acme/widgets",), filter="label:intake", token_env="GH_TOKEN")


def _item(item_id: str, url: str, kind: str = "ISSUE", linked_prs: int = 0, state: str = "OPEN") -> dict:
    return {
        "id": item_id,
        "type": kind,
        "title": "Card",
        "body": "Body",
        "url": url,
        "state": state,
        "linked_prs": linked_prs,
    }


ITEM_A = _item("PVTI_a", "https://github.com/acme/widgets/issues/21")


def test_an_issue_item_in_a_configured_repo_gives_one_candidate_with_its_item_id():
    assert source_github.project_candidates([ITEM_A], CONFIG) == (
        Candidate(
            title="Card",
            body="Body",
            repo="acme/widgets",
            link="https://github.com/acme/widgets/issues/21",
            kind="issue",
            project_item_id="PVTI_a",
        ),
    )


def test_an_item_in_an_unconfigured_repo_gives_none():
    other = _item("PVTI_b", "https://github.com/other/repo/issues/3")
    assert source_github.project_candidates([other], CONFIG) == ()


def test_an_item_linked_to_a_pull_request_is_left_out():
    assert source_github.project_candidates([_item("PVTI_c", ITEM_A["url"], linked_prs=1)], CONFIG) == ()


def test_a_closed_issue_item_is_left_out():
    closed = _item("PVTI_f", ITEM_A["url"], state="CLOSED")
    assert source_github.project_candidates([closed], CONFIG) == ()


def test_a_pull_request_item_and_a_draft_without_a_url_are_left_out():
    pull = _item("PVTI_d", "https://github.com/acme/widgets/pull/9", kind="PULL_REQUEST")
    draft = {**_item("PVTI_e", ""), "type": "DRAFT_ISSUE", "url": None}
    assert source_github.project_candidates([pull, draft], CONFIG) == ()


def test_project_list_argv_names_the_owner_and_name_and_flattens_with_jq():
    argv = source_github.project_list_argv(CONFIG, "acme/widgets")
    assert argv[:3] == ["gh", "api", "graphql"]
    assert {"owner=acme", "name=widgets"} <= set(argv)
    assert argv[argv.index("--jq") + 1].startswith("[.data.repository.projectsV2")


class _Adapter:
    candidates = staticmethod(source_github.candidates)
    read = staticmethod(source_github.read)
    project_candidates = staticmethod(source_github.project_candidates)


def test_a_tagged_project_entry_reaches_found_keyed_by_its_url():
    found, problems = cli._pull_candidates(_Adapter, CONFIG, [{**ITEM_A, "_listing": "project"}])
    assert problems == []
    assert found[ITEM_A["url"]].project_item_id == "PVTI_a"


def test_an_item_whose_id_matches_an_existing_row_is_taken_so_it_is_not_planned_again():
    found, _ = cli._pull_candidates(_Adapter, CONFIG, [{**ITEM_A, "_listing": "project"}])
    assert cli._taken_by_project_item(found, [{"project_item_id": "PVTI_a"}]) == frozenset({ITEM_A["url"]})
    assert cli._taken_by_project_item(found, [{"project_item_id": "PVTI_z"}]) == frozenset()


def _pull_profile(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "team: acme\n"
        f"workspace_dir: {ws}\n"
        "harness_dir: /opt/coxswain-graphs\n"
        "cartridges_dir: /opt/cartridges\n"
        "provider_profile: /opt/providers/acme.yaml\n"
        'sources: {"github": {"repos": ["acme/widgets"]}}\n'
        'repo_map: {"acme/widgets": "tools"}\n'
    )
    return profile, ws


def _patch_gh(monkeypatch, project_items):
    ran: list[list[str]] = []

    def run(argv):
        ran.append(argv)
        return 0, json.dumps(project_items) if argv[:3] == ["gh", "api", "graphql"] else "[]", ""

    monkeypatch.setattr(cli, "_run_argv", run)
    return ran


def test_fetch_listing_runs_the_project_argv_once_per_repo_and_tags_each_entry(monkeypatch):
    ran = _patch_gh(monkeypatch, [ITEM_A])
    listing = cli._fetch_listing(source_github, CONFIG)
    assert [argv[:3] for argv in ran].count(["gh", "api", "graphql"]) == 1
    assert listing == [{**ITEM_A, "_listing": "project"}]


def test_route_pull_files_a_project_item_with_no_label_as_one_intake(tmp_path, monkeypatch):
    profile, ws = _pull_profile(tmp_path)
    _patch_gh(monkeypatch, [ITEM_A])
    assert main(["route", "pull", "--profile", str(profile)]) == 0
    [written] = (ws / "intake").glob("*.md")
    assert 'link: "https://github.com/acme/widgets/issues/21"' in written.read_text(encoding="utf-8")


def test_route_pull_over_a_project_item_a_store_row_already_holds_writes_nothing(tmp_path, monkeypatch, capsys):
    profile, ws = _pull_profile(tmp_path)
    _patch_gh(monkeypatch, [ITEM_A])
    monkeypatch.setattr(run_store, "read_queue", lambda _runs: [{"project_item_id": "PVTI_a"}])
    assert main(["route", "pull", "--profile", str(profile)]) == 0
    assert capsys.readouterr().out == "routing: pull wrote nothing: no eligible candidates\n"
    assert not (ws / "intake").exists()
