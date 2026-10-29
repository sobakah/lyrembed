"""A newer lyrembed: found by the release tags on GitHub, installed by pipx.

Built after picktag's update check (core/selfupdate.py there, 2026-10-27),
like lyrembed's.
The repository is private, so the check asks git (``git ls-remote``), which
uses the same access as ``pipx install`` (``gh auth setup-git``); it never
prompts for a password. A pipx install is updated by pipx: ``pipx upgrade``
for an install from the repository, ``pipx install --force`` with the new tag
for one pinned to a release (it stays pinned). Any other install gets the
command to run instead. Only the page uses this; batch mode never looks.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import __version__

NAME = "lyrembed"
REPO = "https://github.com/sobakah/lyrembed.git"
RELEASES = "https://github.com/sobakah/lyrembed/releases"
_TAG = re.compile(r"refs/tags/(\d+)\.(\d+)\.(\d+)$")


class UpdateError(Exception):
    """GitHub cannot be asked (no git, no network, no access)."""


def parse(version: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    return tuple(int(n) for n in match.groups()) if match else None


def _git_env() -> dict[str, str]:
    """No password prompt, neither in the terminal nor in a window."""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_ASKPASS", "SSH_ASKPASS")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def latest(timeout: float = 30) -> str:
    """The newest release tag (x.y.z) of lyrembed's repository."""
    git = shutil.which("git")
    if git is None:
        raise UpdateError("git isn't installed")
    try:
        result = subprocess.run([git, "-c", "core.askPass=", "ls-remote", "--tags", "--refs", REPO],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                                env=_git_env(), start_new_session=True)
    except subprocess.TimeoutExpired:
        raise UpdateError("GitHub didn't answer") from None
    except OSError as exc:
        raise UpdateError(f"git didn't start: {exc}") from None
    if result.returncode != 0:
        last = (result.stderr.strip().splitlines() or [f"git ended with code {result.returncode}"])[-1]
        raise UpdateError(last.removeprefix("fatal: "))
    tags = [tuple(int(n) for n in m.groups()) for line in result.stdout.splitlines() if (m := _TAG.search(line))]
    if not tags:
        raise UpdateError("no releases found")
    return ".".join(map(str, max(tags)))


def newer(version: str, than: str = __version__) -> bool:
    a, b = parse(version), parse(than)
    return a is not None and b is not None and a > b


@dataclass
class Install:
    """How this lyrembed was installed and how to update it: argv is run by
    lyrembed (a pipx install), command is shown either way."""
    kind: str                     # "pipx", "checkout" or "other"
    command: str
    argv: list[str] | None = None


def pipx_metadata(prefix: str | Path | None = None) -> dict | None:
    try:
        return json.loads((Path(prefix or sys.prefix) / "pipx_metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def how_installed(version: str, prefix: str | Path | None = None,
                  package_dir: Path = Path(__file__).resolve().parent) -> Install:
    """The way to version for this install (prefix: its venv, sys.prefix)."""
    meta = pipx_metadata(prefix)
    if meta is not None:
        main = meta.get("main_package") or {}
        name = main.get("package") or NAME
        url = main.get("package_or_url") or ""
        pipx = shutil.which("pipx") or "pipx"
        pinned = re.match(r"(.*git\+.+?\.git)@[^/]+$", url)
        if pinned:  # a release named in the address: the new one, still pinned
            argv = [pipx, "install", "--force", *meta.get("venv_args", []),
                    *(["--pip-args", shlex.join(main["pip_args"])] if main.get("pip_args") else []),
                    f"{pinned.group(1)}@{version}"]
        else:
            argv = [pipx, "upgrade", name]
        return Install("pipx", shlex.join(["pipx", *argv[1:]]), argv)
    top = package_dir.parent
    if (top / ".git").exists():
        return Install("checkout", f"git -C {shlex.quote(str(top))} pull")
    return Install("other", f"pipx install --force git+{REPO}@{version}")


def state_dir() -> Path:
    """Where the update's output is kept."""
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / NAME


def install(argv: list[str], log: Path, timeout: float = 900) -> tuple[int, str]:
    """Run the update (pipx clones and builds: about a minute); its exit code
    and the end of its output, which is also kept in log."""
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=timeout, env=_git_env(), start_new_session=True)
    except subprocess.TimeoutExpired:
        return 124, f"stopped after {timeout:.0f} seconds"
    except OSError as exc:
        return 127, str(exc)
    out = (result.stdout + result.stderr).strip()
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"$ {shlex.join(argv)}\n{out}\n", encoding="utf-8")
    except OSError as exc:
        out += f"\n(could not keep this in {log}: {exc})"
    return result.returncode, "\n".join(out.splitlines()[-6:])


def restart_argv() -> list[str]:
    """The command lyrembed was started with, to start it again after an update."""
    first = Path(sys.argv[0])
    if first.name == NAME and os.access(first, os.X_OK):
        return [str(first), *sys.argv[1:]]
    if first.name == "__main__.py":  # python -m lyrembed
        return [sys.executable, "-m", NAME, *sys.argv[1:]]
    return [sys.executable, *sys.argv]
