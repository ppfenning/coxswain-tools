import re

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Removes SGR escape sequences, so a help-text assertion reads the same
    whether or not argparse's colorizer is active (Python 3.14 colours help
    output whenever FORCE_COLOR is set, as the cox-launched session does)."""
    return _ANSI.sub("", text)
