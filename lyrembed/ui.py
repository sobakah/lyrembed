"""Terminal output for batch mode and the server: colours, messages, status lines."""

from __future__ import annotations

import os
import shutil
import sys
import unicodedata

from . import config

# --- colours -----------------------------------------------------------------
_ANSI = {
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
    "RED": "\033[31m",
    "GREEN": "\033[32m",
    "YELLOW": "\033[33m",
    "BLUE": "\033[34m",
    "MAGENTA": "\033[35m",
    "CYAN": "\033[36m",
    "WHITE": "\033[37m",
    "GRAY": "\033[90m",
}


class StyleUI:
    RESET = BOLD = DIM = RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = GRAY = ""


def colors_enabled() -> bool:
    mode = str(config.get("settings", "color", "auto")).lower()
    if mode in ("never", "off", "false", "0"):
        return False
    if mode in ("always", "on", "true", "1"):
        return True
    if os.environ.get("NO_COLOR"):  # no-color.org: only a non-empty value counts
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return sys.stdout.isatty()


def apply_color_settings() -> None:
    """(Re)configure StyleUI according to config and terminal capabilities."""
    enabled = colors_enabled()
    if enabled and os.name == "nt":  # pragma: no cover - platform dependent
        enabled = _enable_windows_ansi()
    for name, code in _ANSI.items():
        setattr(StyleUI, name, code if enabled else "")


def _enable_windows_ansi() -> bool:  # pragma: no cover - platform dependent
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


apply_color_settings()


# --- primitives --------------------------------------------------------------
def print_banner(text: str) -> None:
    width = min(terminal_width(), 72)
    print(f"\n{StyleUI.CYAN}{StyleUI.BOLD}{'─' * width}{StyleUI.RESET}")
    print(f"{StyleUI.CYAN}{StyleUI.BOLD} {text}{StyleUI.RESET}")
    print(f"{StyleUI.CYAN}{StyleUI.BOLD}{'─' * width}{StyleUI.RESET}")


def info(msg: str) -> None:
    print(f"{StyleUI.CYAN}{msg}{StyleUI.RESET}")


def success(msg: str) -> None:
    print(f"{StyleUI.GREEN}{msg}{StyleUI.RESET}")


def warn(msg: str) -> None:
    print(f"{StyleUI.YELLOW}{msg}{StyleUI.RESET}")


def error(msg: str) -> None:
    print(f"{StyleUI.RED}{msg}{StyleUI.RESET}")


def terminal_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:  # pragma: no cover
        return default


def char_width(char: str) -> int:
    """Terminal columns *char* occupies: CJK and full-width forms take two."""
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def display_width(text: str) -> int:
    return sum(char_width(char) for char in text)


def _truncate(line: str, width: int) -> str:
    """Cut *line* to *width* terminal columns, not characters - lyrics are
    often CJK, where counting characters overflows the line by half."""
    if display_width(line) <= width:
        return line
    out, used = "", 0
    for char in line:
        step = char_width(char)
        if used + step > max(1, width - 1):
            break
        out += char
        used += step
    return out + "…"


def transient(msg: str):
    """Context manager printing a status line that is cleared afterwards."""

    # A status line that wraps cannot be cleared with a carriage return.
    shown = _truncate(msg, max(10, terminal_width() - 1))

    class _Transient:
        def __enter__(self):
            if sys.stdout.isatty():
                print(f"{StyleUI.GRAY}{shown}{StyleUI.RESET}", end="\r", flush=True)
            return self

        def __exit__(self, *exc):
            if sys.stdout.isatty():
                print(" " * display_width(shown), end="\r", flush=True)
            return False

    return _Transient()
