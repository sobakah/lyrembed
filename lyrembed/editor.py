"""Launching the user's text editor on a temporary .lrc file."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time

from . import config


# An editor that exits this quickly without changing anything has most likely
# handed the file to an already running GUI instance and returned at once.
_DETACHED_EDITOR_SECONDS = 1.0


class EditorError(Exception):
    """Raised when the configured editor cannot be launched."""


class EditorReturnedAtOnce(EditorError):
    """The editor closed within a second without changes: a terminal editor
    quit right away, or a GUI editor that did not wait for the file. Not an
    error in the first case, so callers show it as a note."""


def editor_command() -> list[str]:
    """Split $VISUAL or $EDITOR into argv.

    $VISUAL comes first by convention: it names the full-screen editor, $EDITOR
    the line-mode fallback. ``EDITOR="code --wait"`` must not be treated as a
    single filename - that was a hard crash before.
    """
    raw = os.environ.get("VISUAL") or os.environ.get("EDITOR")
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
    """Open the editor and return the edited text.

    Raises EditorError when the editor cannot be launched, or when it returned
    at once without changes - a GUI editor started without its wait flag, whose
    edits would otherwise be lost because the file is deleted right away.
    """
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
        started = time.monotonic()
        # The return code is ignored on purpose: the editor may have saved
        # before exiting non-zero, so the file is read back either way.
        subprocess.run(argv + [temp_path])
        elapsed = time.monotonic() - started
        with open(temp_path, "r", encoding="utf-8", errors="replace") as fh:
            edited = fh.read()
        if edited == initial_content and elapsed < _DETACHED_EDITOR_SECONDS:
            raise EditorReturnedAtOnce(
                f"No changes (the editor '{argv[0]}' closed after {elapsed:.1f}s). "
                f"If it is a GUI editor, make it wait for the file to close "
                f"(e.g. EDITOR='code --wait' or 'subl -w')."
            )
        return edited
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
