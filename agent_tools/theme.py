from types import MappingProxyType

ROLES = ("plain", "dim", "title", "border", "ok", "warn", "alert", "accent", "meter")

Theme = MappingProxyType[str, tuple[int, int]]


def _theme(**roles: tuple[int, int]) -> Theme:
    return MappingProxyType(roles)


default = _theme(
    plain=(15, -1),
    dim=(244, -1),
    title=(47, -1),
    border=(22, -1),
    ok=(46, -1),
    warn=(214, -1),
    alert=(196, -1),
    accent=(33, -1),
    meter=(40, -1),
)

gruvbox = _theme(
    plain=(223, -1),
    dim=(245, -1),
    title=(214, -1),
    border=(237, -1),
    ok=(142, -1),
    warn=(214, -1),
    alert=(167, -1),
    accent=(109, -1),
    meter=(175, -1),
)

nord = _theme(
    plain=(188, -1),
    dim=(59, -1),
    title=(110, -1),
    border=(60, -1),
    ok=(108, -1),
    warn=(179, -1),
    alert=(167, -1),
    accent=(110, -1),
    meter=(114, -1),
)

THEMES: MappingProxyType[str, Theme] = MappingProxyType(
    {"default": default, "gruvbox": gruvbox, "nord": nord}
)


def resolve(name: str) -> dict[str, tuple[int, int]]:
    return dict(THEMES.get(name, default))


def pair_numbers(theme: Theme) -> dict[str, int]:
    return {role: n for n, role in enumerate(sorted(theme), start=1)}


def install(theme: Theme) -> dict[str, int]:
    import curses

    numbers = pair_numbers(theme)
    if not curses.has_colors():
        return dict.fromkeys(theme, 0)
    curses.start_color()
    curses.use_default_colors()
    for role, n in numbers.items():
        fg, bg = theme[role]
        curses.init_pair(n, fg, bg)
    return numbers
