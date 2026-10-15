"""Terminal presentation: colours, prompts, previews and path completion."""

from __future__ import annotations

import glob
import os
import shutil
import sys
import unicodedata
from typing import NoReturn

from . import config

# --- readline shim -----------------------------------------------------------
# The stdlib module is POSIX-only. On Windows we fall back to pyreadline3 and,
# if that is missing too, to a no-op stub so the program still runs.
try:  # pragma: no cover - platform dependent
    import readline as _readline
except ImportError:  # pragma: no cover
    try:
        import pyreadline3 as _readline  # type: ignore[no-redef]
    except ImportError:
        _readline = None

HAS_READLINE = _readline is not None


def _rl(name: str):
    """Return a readline callable, or None when unavailable."""
    return getattr(_readline, name, None) if _readline else None


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
def badge(text: str, color: str) -> str:
    return f"{color}{StyleUI.BOLD}[{text}]{StyleUI.RESET}"


def choice_key(key: str, color: str, selected: bool = False) -> str:
    """A choice such as ``[2]``. The selected one - the entry Enter would pick,
    whether chosen or preselected - gets bold yellow brackets, so the current
    choice stands out without reading the "Enter = ..." hint."""
    if selected:
        bracket = f"{StyleUI.YELLOW}{StyleUI.BOLD}"
        return f"{bracket}[{StyleUI.RESET}{color}{StyleUI.BOLD}{key}{StyleUI.RESET}{bracket}]{StyleUI.RESET}"
    return f"[{color}{key}{StyleUI.RESET}]"


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


class QuitRequested(Exception):
    """Raised to end the program from deep inside a menu.

    Handled once in ``cli.main``, so cleanup runs and the exit code is decided
    in one place instead of by ``sys.exit`` calls scattered through the menus.
    """

    def __init__(self, code: int = 0) -> None:
        super().__init__(code)
        self.code = code


def safe_input(prompt: str = "", default_text: str = "") -> str:
    """input() with an optional prefilled answer.

    Ctrl+C propagates as KeyboardInterrupt (exit code 130 in ``cli.main``, the
    same as anywhere else); end of input (Ctrl+D, an exhausted pipe) quits.
    """
    set_hook = _rl("set_startup_hook")
    insert_text = _rl("insert_text")
    try:
        if default_text and set_hook and insert_text:
            set_hook(lambda: insert_text(default_text))
        elif default_text:
            # No readline: show the prefill so the user can retype or accept it.
            print(f"{StyleUI.GRAY}(current: {default_text}){StyleUI.RESET}")
        return input(prompt)
    except EOFError:
        print()
        exit_script()
    finally:
        if set_hook:
            set_hook(None)


def confirm(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = safe_input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes", "j", "ja")


def exit_script(code: int = 0) -> NoReturn:
    print(f"\n{StyleUI.YELLOW}Program terminated.{StyleUI.RESET}")
    raise QuitRequested(code)


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


def preview_text(text: str, max_lines: int | None = None) -> None:
    if max_lines is None:
        max_lines = int(config.get("settings", "preview_lines", 16))
    lines = text.splitlines()
    body_width = max(20, terminal_width() - 4)

    print(f"\n{StyleUI.GRAY}┌─── PREVIEW ({min(len(lines), max_lines)}/{len(lines)} lines) ───{StyleUI.RESET}")
    for line in lines[:max_lines]:
        print(f"{StyleUI.GRAY}│{StyleUI.RESET} {_truncate(line, body_width)}")
    if len(lines) > max_lines:
        print(f"{StyleUI.GRAY}│ ... ({len(lines) - max_lines} more lines){StyleUI.RESET}")
    print(f"{StyleUI.GRAY}└{'─' * 42}{StyleUI.RESET}")


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


# --- menus -------------------------------------------------------------------
def print_menu(groups: list[tuple[str, list[tuple[str, str]]]], lead: str | None = None) -> None:
    """Render keyboard options as labelled groups in aligned columns.

    *groups* is a list of (group title, [(key, label), ...]). Column count and
    width adapt to the terminal, so a long option list stays readable instead
    of running into one wall of text.
    """
    entries = [entry for _, items in groups for entry in items]
    if not entries:
        return

    key_width = max(len(key) for key, _ in entries)
    label_width = max(len(label) for _, label in entries)
    cell_width = key_width + label_width + 4  # "[k] label"
    gap = 3
    usable = max(20, terminal_width() - 2)
    columns = max(1, (usable + gap) // (cell_width + gap))

    if lead:
        print(f"\n{lead}")
    else:
        print()

    for title, items in groups:
        if title:
            print(f"{StyleUI.GRAY}{title}{StyleUI.RESET}")
        for start in range(0, len(items), columns):
            row = items[start:start + columns]
            cells = []
            for position, (key, label) in enumerate(row):
                colored = f"[{_key_color(key)}{key}{StyleUI.RESET}]{' ' * (key_width - len(key))} {label}"
                padding = cell_width - (key_width + len(label) + 4)
                last = position == len(row) - 1
                cells.append(colored + ("" if last else " " * (padding + gap)))
            print("  " + "".join(cells).rstrip())


_DESTRUCTIVE_KEYS = {"q", "d"}
_NAVIGATION_KEYS = {"p", "s", "t", "b", "c"}


def _key_color(key: str) -> str:
    lowered = key.lower()
    if lowered in _DESTRUCTIVE_KEYS:
        return StyleUI.RED
    if lowered in _NAVIGATION_KEYS:
        return StyleUI.YELLOW
    return StyleUI.GREEN


def print_primary_action(key: str, description: str) -> None:
    """Highlight the action Enter would take, above the regular menu."""
    print(f"\n  [{StyleUI.CYAN}{StyleUI.BOLD}{key}{StyleUI.RESET}] {StyleUI.BOLD}{description}{StyleUI.RESET}")


# --- path completion ---------------------------------------------------------
_completions: tuple[str, list[str]] = ("", [])


def complete_path(text: str, state: int):
    """Readline completer. Readline calls it with state 0, 1, 2, ... until it
    returns None; the matches are listed once per Tab, not once per call,
    which made a large folder cost quadratically many file system lookups."""
    global _completions
    if state == 0 or _completions[0] != text:
        # The prompt strips quotes, so a quoted path completes as well.
        expanded = os.path.expanduser(text.lstrip("'\""))
        # Music folders are routinely named "Album [FLAC]" or "Live [2019]";
        # the brackets must not be read as a glob character class.
        matches = sorted(glob.glob(glob.escape(expanded) + "*"))
        _completions = (text, [m + (os.sep if os.path.isdir(m) else " ") for m in matches])
    results = _completions[1]
    return results[state] if state < len(results) else None


def enable_path_completion() -> None:
    set_delims = _rl("set_completer_delims")
    parse_bind = _rl("parse_and_bind")
    set_completer = _rl("set_completer")
    if not (set_delims and parse_bind and set_completer):
        return
    # The prompt takes exactly one path, so spaces belong to it: with a space
    # as delimiter, readline passed only "Album/" of "My Album/" to the
    # completer, and folders with spaces could not be completed into.
    set_delims("\t\n")
    parse_bind("tab: complete")
    set_completer(complete_path)


def disable_path_completion() -> None:
    set_completer = _rl("set_completer")
    if set_completer:
        set_completer(None)
