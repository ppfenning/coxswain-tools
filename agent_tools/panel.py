"""Pure box-drawing and meter rendering for cox home btop panels."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    text: str
    role: str = "plain"


Line = tuple[Span, ...]


@dataclass(frozen=True)
class Borders:
    top_left: str
    horizontal: str
    top_right: str
    vertical: str
    bottom_left: str
    bottom_right: str


BORDERS = Borders("╭", "─", "╮", "│", "╰", "╯")

MIN_SIZE = 2

_PARTIALS = "▏▎▍▌▋▊▉"


def _clip_line(line: Line, inner: int) -> Line:
    if inner <= 0:
        return ()
    kept: list[Span] = []
    remaining = inner - 1
    for span in line:
        if remaining <= 0:
            break
        text = span.text[:remaining]
        if text:
            kept.append(Span(text, span.role))
        remaining -= len(text)
    last_role = kept[-1].role if kept else line[0].role
    kept.append(Span("…", last_role))
    return tuple(kept)


def _fit_line(line: Line, inner: int) -> Line:
    total = sum(len(span.text) for span in line)
    if total > inner:
        return _clip_line(line, inner)
    if total < inner:
        return line + (Span(" " * (inner - total)),)
    return line


def _fit_border(content: Line, inner: int) -> Line:
    b = BORDERS
    total = sum(len(span.text) for span in content)
    if total > inner:
        return _clip_line(content, inner)
    if total < inner:
        return content + (Span(b.horizontal * (inner - total), "border"),)
    return content


def _top_border(title: str, inner: int) -> Line:
    b = BORDERS
    content = (
        (Span(b.horizontal + " ", "border"), Span(title, "title"), Span(" ", "border"))
        if title
        else (Span(b.horizontal * inner, "border"),)
    )
    middle = _fit_border(content, inner)
    return (Span(b.top_left, "border"),) + middle + (Span(b.top_right, "border"),)


def _bottom_border(inner: int) -> Line:
    b = BORDERS
    return (
        Span(b.bottom_left, "border"),
        Span(b.horizontal * inner, "border"),
        Span(b.bottom_right, "border"),
    )


def _body_row(line: Line, inner: int) -> Line:
    b = BORDERS
    return (Span(b.vertical, "border"),) + _fit_line(line, inner) + (Span(b.vertical, "border"),)


def box(title: str, body: tuple[Line, ...], width: int, height: int) -> tuple[Line, ...]:
    if width < MIN_SIZE or height < MIN_SIZE:
        return ()
    inner = width - 2
    inner_height = height - 2
    rows = tuple(_body_row(line, inner) for line in body[:inner_height])
    blank = (Span(" " * inner),)
    rows += tuple(_body_row(blank, inner) for _ in range(inner_height - len(rows)))
    return (_top_border(title, inner),) + rows + (_bottom_border(inner),)


def meter(value: float, width: int, role: str = "meter") -> Line:
    if width <= 0:
        return ()
    clamped = min(max(value, 0.0), 1.0)
    eighths = round(clamped * width * 8)
    full, remainder = divmod(eighths, 8)
    text = "█" * full
    if remainder:
        text += _PARTIALS[remainder - 1]
        full += 1
    text += " " * (width - full)
    return (Span(text, role),)
