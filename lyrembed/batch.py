"""Batch mode (--auto): the confident match for every track, printed as it goes.

It may run from cron, so it never asks anything; everything interactive
happens on the browser page (web.py).
"""

from __future__ import annotations

import time
from pathlib import Path

from . import config, core
from .tagging import MetadataCache
from .ui import StyleUI, error, info, print_banner, success, transient, warn


def open_music_directory(path: Path) -> tuple[Path, list[Path]] | None:
    """The resolved folder and its music files, or None after saying why it
    cannot be used."""
    resolved = path.expanduser().resolve()
    files, problem = core.check_music_directory(resolved)
    if problem is not None:
        (error if problem.severe else warn)(f"\n{problem.message}")
        return None
    return resolved, files


# --- batch mode --------------------------------------------------------------
def _print_steps(steps, tally: core.Tally) -> None:
    """Print a batch or bulk run as it goes and count its outcomes.

    A search shows a status line while it runs: it happens inside the
    generator when the next step is asked for, so that request is what the
    status line wraps.
    """
    steps = iter(steps)
    step = None
    while True:
        try:
            if isinstance(step, core.Searching):
                with transient(f"{step.prefix}: searching..."):
                    step = next(steps)
            else:
                step = next(steps)
        except StopIteration:
            return
        if isinstance(step, core.Pause):
            if step.limited:
                print(f"{StyleUI.GRAY}{step.prefix}: {', '.join(step.limited)} is limiting requests; "
                      f"waiting {step.seconds:g}s, then trying again.{StyleUI.RESET}")
            time.sleep(step.seconds)
        elif isinstance(step, core.Notice):
            warn(step.message)
        elif isinstance(step, core.Outcome):
            tally.add(step)
            for message in step.warnings:
                warn(f"{step.prefix}: {message}")
            line = f"{step.prefix}: {step.message}"
            if step.result == "done":
                success(line)
            elif step.result == "planned":
                print(f"{StyleUI.CYAN}{line}{StyleUI.RESET}")
            elif step.result == "failed":
                error(line)
            elif step.notable:
                warn(line)
            else:
                print(f"{StyleUI.GRAY}{line}{StyleUI.RESET}")


def run_batch(files: list[Path], base_dir: Path, cache: MetadataCache,
              overwrite: bool, dry_run: bool) -> int:
    romanize = bool(config.get("settings", "auto_romanize", False))
    print_banner(f"Batch mode ({len(files)} files){', romanizing' if romanize else ''}"
                 f"{' - dry run' if dry_run else ''}")
    backup_root = core.backup_directory() if overwrite and not dry_run else None
    tally = core.Tally()
    _print_steps(core.batch(files, base_dir, cache, overwrite, dry_run, backup_root, romanize), tally)

    print_banner("Batch summary")
    print(f"  {StyleUI.GREEN}Applied: {tally.done}{StyleUI.RESET}   "
          f"{StyleUI.YELLOW}Skipped: {tally.skipped}{StyleUI.RESET}   "
          f"{StyleUI.RED}Failed: {tally.failed}{StyleUI.RESET}")
    if tally.backed_up and backup_root is not None:
        info(f"  Replaced lyrics were backed up to {backup_root}")
    return 1 if tally.failed else 0
