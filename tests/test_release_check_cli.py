from agent_tools.release_check import Drift
from agent_tools.release_check_cli import check_cli_surface, commands_in_doc, walk_help

_HELP = {
    "cox": (
        "usage: cox [-h] {dev,runs} ...\n\n"
        "positional arguments:\n"
        "  {dev,runs}\n"
        "    dev                 maintainer commands\n"
        "    runs                inspect runs\n"
    ),
    "cox dev": (
        "usage: cox dev [-h] {release-check} ...\n\n"
        "positional arguments:\n"
        "  {release-check}\n"
        "    release-check       gather facts and print drifts\n"
    ),
    "cox dev release-check": "usage: cox dev release-check [-h]\n",
    "cox runs": (
        "usage: cox runs [-h] {top} ...\n\n"
        "positional arguments:\n"
        "  {top}\n"
        "    top                 live table of runs in flight\n"
    ),
    "cox runs top": "usage: cox runs top [-h]\n",
}

_HELP_WITH_A_PLAIN_CHOICE = {
}


def test_walk_help_returns_only_leaf_commands_from_a_subparsers_choices_line():
    assert walk_help(_HELP) == {"cox dev release-check", "cox runs top"}


def test_walk_help_does_not_read_a_flags_or_a_plain_positionals_choice_list_as_a_subcommand():
    assert walk_help(_HELP_WITH_A_PLAIN_CHOICE) == set()


def test_commands_in_doc_matches_a_backticked_cox_mention_and_drops_a_trailing_flag():
    assert commands_in_doc("Run `cox dev release-check --json` first.") == {"cox dev release-check"}


def test_check_cli_surface_flags_a_command_missing_from_the_umbrella_docs():
    facts = {
        "cli_commands": {"cox dev release-check"},
        "doc_commands": {"docs/reference/cli/runs.md": set()},
        "readme_commands": {"dev": {"cox dev release-check"}},
    }
    assert check_cli_surface(facts) == [
        Drift("cli_surface", "cox --help", None, "docs/reference/cli/dev.md", None,
              "add cox dev release-check to docs/reference/cli/dev.md"),
    ]


def test_check_cli_surface_flags_a_command_missing_from_its_provider_readme():
    facts = {
        "cli_commands": {"cox dev release-check"},
        "doc_commands": {"docs/reference/cli/dev.md": {"cox dev release-check"}},
        "readme_commands": {"dev": set()},
    }
    assert check_cli_surface(facts) == [
        Drift("cli_surface", "cox --help", None, "dev/README.md", None,
              "add cox dev release-check to dev/README.md"),
    ]


def test_check_cli_surface_flags_a_documented_command_the_cli_no_longer_has():
    facts = {
        "cli_commands": set(),
        "doc_commands": {"docs/reference/cli/dev.md": {"cox dev retired"}},
        "readme_commands": {"dev": {"cox dev retired"}},
    }
    assert check_cli_surface(facts) == [
        Drift("cli_surface", "docs/reference/cli/dev.md", None, "cox --help", None,
              "remove cox dev retired from docs/reference/cli/dev.md"),
        Drift("cli_surface", "dev/README.md", None, "cox --help", None,
              "remove cox dev retired from dev/README.md"),
    ]


def test_check_cli_surface_has_no_readme_to_check_when_no_readme_names_the_group():
    facts = {
        "cli_commands": {"cox dev release-check"},
        "doc_commands": {"docs/reference/cli/dev.md": {"cox dev release-check"}},
        "readme_commands": {},
    }
    assert check_cli_surface(facts) == []


def test_check_cli_surface_reports_no_drift_when_the_facts_agree():
    facts = {
        "cli_commands": {"cox dev release-check"},
        "doc_commands": {"docs/reference/cli/dev.md": {"cox dev release-check"}},
        "readme_commands": {"dev": {"cox dev release-check"}},
    }
    assert check_cli_surface(facts) == []
