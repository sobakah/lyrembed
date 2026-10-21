"""An app-menu entry for Linux and BSD desktops (freedesktop.org).

pipx and pip cannot install desktop files, so lyrembed writes its own entry
on request, pointing at the lyrembed that wrote it: a pipx launcher, or the
Python and run.py of a checkout. Run it again after moving to another
installation.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ICON_SOURCE = Path(__file__).with_name("static") / "icon.svg"
ENTRY_NAME = "lyrembed.desktop"

# Characters that make an Exec argument need quotes (Desktop Entry spec,
# "The Exec key"); inside quotes, these four need a backslash.
_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")
_ESCAPED_IN_QUOTES = re.compile(r'(["`$\\])')


class DesktopError(Exception):
    """The entry could not be written or removed."""


def supported() -> bool:
    return sys.platform.startswith(("linux", "freebsd", "openbsd", "netbsd"))


def _data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def entry_path() -> Path:
    return _data_home() / "applications" / ENTRY_NAME


def icon_path() -> Path:
    return _data_home() / "icons" / "hicolor" / "scalable" / "apps" / "lyrembed.svg"


def launch_command() -> list[str]:
    """How the running lyrembed was started, so the menu starts the same one."""
    started = Path(sys.argv[0]).resolve()
    if started.name == "__main__.py":
        # python -m lyrembed: the module is only found from its checkout.
        launcher = started.parent.parent / "run.py"
        return [sys.executable, str(launcher)] if launcher.is_file() else [sys.executable, "-m", "lyrembed"]
    if started.suffix == ".py":
        return [sys.executable, str(started)]
    return [str(started)]


def exec_argument(argument: str) -> str:
    """*argument* as the Exec key needs it: quoted when it has reserved
    characters, then with the string escapes every desktop-file value has.
    The spec applies the string escape first when reading, so a literal
    backslash in a quoted argument ends up as four."""
    if any(c in _RESERVED for c in argument):
        argument = '"' + _ESCAPED_IN_QUOTES.sub(r"\\\1", argument) + '"'
    return argument.replace("\\", "\\\\").replace("%", "%%")


def entry_text(command: list[str], icon: Path) -> str:
    exec_line = " ".join(exec_argument(a) for a in command) + " %f"
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=lyrembed",
        "GenericName=Lyrics editor",
        "Comment=Search, check and embed synchronized lyrics in your music files",
        f"Exec={exec_line}",
        f"Icon={icon}",
        # The page opens in the browser; a terminal would only show its address.
        "Terminal=false",
        # The browser's window is not lyrembed's, so a startup spinner would
        # keep turning until it times out.
        "StartupNotify=false",
        "Categories=AudioVideo;Audio;",
        "Keywords=lyrics;lrc;karaoke;romanization;synced;",
        "",
    ])


def _refresh(applications: Path) -> None:
    """Some menus only notice a new entry after this; none needs it to work."""
    tool = shutil.which("update-desktop-database")
    if tool:
        try:
            subprocess.run([tool, str(applications)], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass


def install() -> tuple[Path, Path, list[str]]:
    """Write the icon and the entry; returns their paths and the command."""
    if not supported():
        raise DesktopError("App-menu entries are written for Linux and BSD desktops only.")
    command = launch_command()
    entry, icon = entry_path(), icon_path()
    try:
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ICON_SOURCE, icon)
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text(entry_text(command, icon), encoding="utf-8")
    except OSError as exc:
        raise DesktopError(f"Could not write the app-menu entry: {exc}") from exc
    _refresh(entry.parent)
    return entry, icon, command


def remove() -> list[Path]:
    """Delete the entry and the icon; returns what was there."""
    removed = []
    for path in (entry_path(), icon_path()):
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DesktopError(f"Could not remove '{path}': {exc}") from exc
    if removed:
        _refresh(entry_path().parent)
    return removed
