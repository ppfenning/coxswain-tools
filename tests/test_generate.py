from agent_tools.generate import build_env, parse_generate_file, plan_commands


def test_a_comment_line_is_dropped():
    assert parse_generate_file("# note\nmake gen") == ("make gen",)


def test_an_indented_comment_line_is_dropped():
    assert parse_generate_file("   # note\nmake gen") == ("make gen",)


def test_blank_and_whitespace_only_lines_are_dropped():
    assert parse_generate_file("\n   \n\t\nmake gen\n\n") == ("make gen",)


def test_surrounding_whitespace_is_stripped():
    assert parse_generate_file("  make gen  ") == ("make gen",)


def test_an_inline_hash_stays_in_the_command():
    assert parse_generate_file("echo hi # not a comment") == ("echo hi # not a comment",)


def test_commands_keep_their_order():
    assert parse_generate_file("b\n# c\n\na\n") == ("b", "a")


def test_empty_text_yields_no_commands():
    assert parse_generate_file("") == ()


def test_env_with_an_umbrella_carries_both_variables():
    assert build_env("/w", "/u") == {"COX_WORKTREE": "/w", "COX_UMBRELLA": "/u"}


def test_env_without_an_umbrella_omits_cox_umbrella():
    assert build_env("/w", None) == {"COX_WORKTREE": "/w"}


def test_env_returns_a_new_dict_each_call():
    assert build_env("/w", None) is not build_env("/w", None)


def test_a_command_naming_the_umbrella_is_skipped_when_it_is_unset():
    assert plan_commands(("echo $COX_UMBRELLA/x",), None) == (
        ("echo $COX_UMBRELLA/x", "skipped 'echo $COX_UMBRELLA/x': umbrella_dir is unset"),
    )


def test_a_command_naming_the_umbrella_runs_when_it_is_set():
    assert plan_commands(("echo $COX_UMBRELLA/x",), "/u") == (("echo $COX_UMBRELLA/x", None),)


def test_a_command_not_naming_the_umbrella_runs_when_it_is_unset():
    assert plan_commands(("make gen",), None) == (("make gen", None),)


def test_a_mixed_plan_keeps_order_and_pairs_each_command():
    plan = plan_commands(("make gen", "cp $COX_UMBRELLA/a .", "make lint"), None)
    assert plan == (
        ("make gen", None),
        ("cp $COX_UMBRELLA/a .", "skipped 'cp $COX_UMBRELLA/a .': umbrella_dir is unset"),
        ("make lint", None),
    )
