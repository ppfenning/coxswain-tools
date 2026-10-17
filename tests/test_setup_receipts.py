from agent_tools.setup_receipts import default_tool_dir, parse_receipt_extras, read_tool_extras

RECEIPT = """\
[tool]
requirements = [{ name = "coxswain-tools", editable = "/h/installs/coxswain-tools", extras = ["traces", "parquet", "lake", "postgres"] }]
entrypoints = [{ name = "cox", install-path = "/h/.local/bin/cox" }]
"""


def test_parser_returns_the_listed_extras_in_order():
    assert parse_receipt_extras(RECEIPT) == ("traces", "parquet", "lake", "postgres")


def test_parser_gives_empty_for_no_extras_no_requirements_or_bad_text():
    no_extras = '[tool]\nrequirements = [{ name = "coxswain-tools" }]\n'
    assert parse_receipt_extras(no_extras) == ()
    assert parse_receipt_extras("[tool]\nrequirements = []\n") == ()
    assert parse_receipt_extras("[tool]\nentrypoints = []\n") == ()
    assert parse_receipt_extras("not = [valid toml") == ()
    assert parse_receipt_extras("") == ()


def test_parser_ignores_a_non_list_extras_value():
    assert parse_receipt_extras('[tool]\nrequirements = [{ name = "x", extras = "traces" }]\n') == ()


def test_tool_dir_follows_uv_precedence():
    assert default_tool_dir({"UV_TOOL_DIR": "/t", "XDG_DATA_HOME": "/x"}, "/h") == "/t"
    assert default_tool_dir({"XDG_DATA_HOME": "/x"}, "/h") == "/x/uv/tools"
    assert default_tool_dir({"UV_TOOL_DIR": ""}, "/h") == "/h/.local/share/uv/tools"


def test_edge_gives_empty_when_the_receipt_file_is_missing(tmp_path):
    assert read_tool_extras(str(tmp_path), "coxswain-tools") == ()


def test_edge_reads_the_receipt_under_the_package_directory(tmp_path):
    folder = tmp_path / "coxswain-tools"
    folder.mkdir()
    (folder / "uv-receipt.toml").write_text(RECEIPT)
    assert read_tool_extras(str(tmp_path), "coxswain-tools") == ("traces", "parquet", "lake", "postgres")
    assert read_tool_extras(str(tmp_path), "agent-cartridges") == ()
