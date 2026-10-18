# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

"""Terminal colors for cdpc output.

Color only reinforces meaning: every report also marks its lines with ASCII
symbols (`+` added, `-` missing or omitted, `~` retyped, `!` ignored,
`?` undocumented, `==` section), so output stays readable without color, in
logs and when piped. Color is off unless `configure` turns it on, which keeps
formatter output plain in tests and for library callers.

`auto` follows the NO_COLOR and FORCE_COLOR conventions and enables color
only for an interactive terminal that is not TERM=dumb.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
from typing import Optional, TextIO

MODES = ("auto", "always", "never")
# Longest line cdpc prints; narrower terminals shrink it.
MAX_WIDTH = 100
MIN_WIDTH = 40
# Narrower than this after the prefix, text stays on one line instead of
# folding into a thin column.
MIN_TEXT_WIDTH = 24
ANSI_RE = re.compile(r"\033\[[0-9;]*m")
# Tokens colored inside free text. Paths and CDP names contain no space, so
# wrapping, which breaks only at spaces, never splits them; `code` split across
# lines is just left uncolored.
_EXTENSIONS = r"(?:yaml|yml|json|cc|h|py|gn|gni|md|mm|m|java|kt|ets|txt)"
TOKEN_RE = re.compile(
    r"(?P<path>(?<![\w/.-])(?:[\w.<>-]+/)+[\w.<>-]+(?::\d+)?"
    rf"|(?<![\w/.-])[\w-]+\.{_EXTENSIONS}(?::\d+)?(?![\w.]))"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<cdp>(?<![\w.])[A-Za-z][A-Za-z0-9]+(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\]|\.<key>)+(?![\w(]))"
)
TOKEN_STYLES = {"path": ("blue",), "code": ("bold",), "cdp": ("cyan",)}

_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
}
# What each report symbol means, and its color.
CHANGE_STYLE = {
    "added": ("+", "green"),
    "omitted": ("-", "red"),
    "retyped": ("~", "yellow"),
    "ignored": ("!", "magenta"),
}

_enabled = False
_width = MAX_WIDTH


def configure(
    mode: str = "auto", stream: Optional[TextIO] = None, width: Optional[int] = None
) -> bool:
    """Set color and line width for this process; returns whether color is on.

    Without `width`, lines are at most MAX_WIDTH, or the terminal width when
    `stream` is a narrower terminal.
    """

    global _enabled, _width
    stream = stream or sys.stdout
    if width is not None:
        _width = max(width, MIN_WIDTH)
    elif _isatty(stream):
        columns = shutil.get_terminal_size((MAX_WIDTH, 24)).columns
        _width = max(min(MAX_WIDTH, columns), MIN_WIDTH)
    else:
        _width = MAX_WIDTH
    if mode == "always":
        _enabled = True
    elif mode == "never":
        _enabled = False
    else:
        _enabled = _auto(stream)
    if _enabled and os.name == "nt":
        _enable_windows_vt()
    return _enabled


def enabled() -> bool:
    return _enabled


def width() -> int:
    return _width


def visible_len(text: str) -> int:
    return len(ANSI_RE.sub("", text))


def wrap(text: str, prefix: str = "", hang: Optional[int] = None, *styles: str) -> str:
    """Wrap plain `text` after `prefix` to the line width, between words only.

    `prefix` may be colored. Continuation lines are indented by `hang` columns,
    the visible width of `prefix` by default, so they align with the text.
    `styles` color the wrapped text; without them, CDP names, file paths and
    `code` in it are highlighted. A single word longer than the line is
    kept whole. When the prefix leaves fewer than MIN_TEXT_WIDTH columns, the
    text moves below it instead of folding into a thin column.
    """

    lead = visible_len(prefix)
    hang = lead if hang is None else hang
    if lead + len(text) <= _width:
        return prefix + _body(text, styles)
    if _width - max(lead, hang) < MIN_TEXT_WIDTH:
        # A table row whose last column does not fit: move that column to
        # its own lines, indented a step past the row.
        step = len(prefix) - len(prefix.lstrip(" ")) + 4
        return prefix.rstrip(" ") + "\n" + wrap(text, " " * step, step, *styles)
    lines = textwrap.wrap(
        text,
        width=_width,
        initial_indent=" " * lead,
        subsequent_indent=" " * hang,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [" " * lead]
    out = []
    for index, line in enumerate(lines):
        indent = lead if index == 0 else hang
        body = _body(line[indent:], styles)
        out.append((prefix if index == 0 else " " * hang) + body)
    return "\n".join(out)


def highlight(text: str) -> str:
    """Color CDP names, file paths and `code` in plain text."""

    if not _enabled:
        return text
    return TOKEN_RE.sub(
        lambda match: paint(match.group(0), *TOKEN_STYLES[match.lastgroup]), text
    )


def _body(text: str, styles: tuple) -> str:
    return paint(text, *styles) if styles else highlight(text)


def wrap_message(text: str, prefix: str = "") -> str:
    """Wrap a possibly multi-line message, keeping each line's own indent.

    The first line follows `prefix`; later lines such as "  item" keep their
    leading spaces and hang two columns further.
    """

    first, *rest = text.split("\n")
    lines = [wrap(first, prefix)]
    for line in rest:
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        lines.append(wrap(stripped, " " * indent, indent + 2))
    return "\n".join(lines)


def paint(text: str, *styles: str) -> str:
    if not _enabled or not styles or not text:
        return text
    codes = ";".join(_CODES[style] for style in styles)
    return f"\033[{codes}m{text}\033[0m"


def bold(text: str) -> str:
    return paint(text, "bold")


def dim(text: str) -> str:
    return paint(text, "dim")


def header(text: str) -> str:
    """A section title such as `== DOM ==`."""

    return paint(f"== {text} ==", "bold", "cyan")


def change(kind: str, text: str) -> str:
    """`text` prefixed with the symbol of a field change kind."""

    symbol, color = CHANGE_STYLE.get(kind, ("*", ""))
    return paint(f"{symbol} {text}", color) if color else f"{symbol} {text}"


def error(text: str) -> str:
    return wrap_message(text, f"{paint('error:', 'bold', 'red')} ")


def warning(text: str) -> str:
    return wrap_message(text, f"{paint('warning:', 'bold', 'yellow')} ")


def item(symbol: str, text: str, *styles: str, indent: int = 2) -> str:
    """A list item such as `  x message`, wrapped under its text."""

    return wrap(text, " " * indent + paint(symbol, *styles) + " ")


def _auto(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    return _isatty(stream)


def _isatty(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _enable_windows_vt() -> None:
    """Let legacy Windows consoles interpret ANSI codes; best effort."""

    global _enabled
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if not kernel32.SetConsoleMode(handle, mode.value | 0x0004):
            _enabled = False
    except (AttributeError, OSError):
        _enabled = False
