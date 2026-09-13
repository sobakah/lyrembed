"""Launching the user's text editor on a temporary .lrc file."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile

from . import config


class EditorError(Exception):
    """Raised when the configured editor cannot be launched."""


def editor_command() -> list[str]:
    """Split $EDITOR into argv.

    ``EDITOR="code --wait"`` must not be treated as a single filename - that
    was a hard crash before.
    """
    raw = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not raw:
        raw = str(config.get("settings", "default_editor", "nano"))
    try:
        parts = shlex.split(raw, posix=(os.name != "nt"))
    except ValueError as exc:
        raise EditorError(f"Invalid EDITOR value {raw!r}: {exc}") from exc
    if os.name == "nt":
        parts = [part.strip('"') for part in parts]
    if not parts:
        raise EditorError("No editor configured (set $EDITOR or settings.default_editor).")
    return parts


def open_editor(initial_content: str) -> str:
    """Open the editor and return the edited text (original text on failure)."""
    argv = editor_command()

    handle = tempfile.NamedTemporaryFile(suffix=".lrc", mode="w", encoding="utf-8", delete=False, newline="")
    try:
        handle.write(initial_content)
        temp_path = handle.name
    finally:
        handle.close()

    try:
        # No check=True: quitting with a non-zero status (e.g. vim's :cq) is a
        # normal user action, not a reason to abort the whole session.
        result = subprocess.run(argv + [temp_path])
        if result.returncode != 0:
            # Still read the file back; the editor may have saved before exiting.
            pass
        with open(temp_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except FileNotFoundError as exc:
        raise EditorError(
            f"Editor '{argv[0]}' not found. Set $EDITOR to an installed editor "
            f"(e.g. 'nano', 'notepad', 'code --wait')."
        ) from exc
    except OSError as exc:
        raise EditorError(f"Could not launch editor '{argv[0]}': {exc}") from exc
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass
