"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import batch, config, core, providers, tagging, ui
from .config import __version__
from .tagging import MetadataCache


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyrembed",
        description="Search, check, romanize and embed synchronized and plain lyrics "
                    "in FLAC, MP3, OGG Vorbis, Opus and M4A files. Opens a page in your browser "
                    "(only this computer can reach it); --auto works without one.",
        epilog="lyrembed romanize --stdio romanizes lyrics for display in another program "
               "(see lyrembed romanize --help).",
    )
    parser.add_argument("path", nargs="?", help="music directory (default: current directory)")
    parser.add_argument("--version", action="version", version=f"lyrembed {__version__}")
    parser.add_argument("-c", "--config", type=Path, metavar="FILE", help="use this config file instead of the search paths")
    parser.add_argument("--list-aliases", action="store_true",
                        help="print the artist alias groups in effect and where they come from, then exit")
    parser.add_argument("--max-files", type=int, metavar="N",
                        help="scan at most N files (0 = unlimited; default: settings.max_file_count)")

    browser = parser.add_argument_group("browser")
    browser.add_argument("--no-browser", action="store_true",
                         help="print the page's address instead of opening it")
    browser.add_argument("--return-url", metavar="URL",
                         help="a 'Done' button on the page ends lyrembed and goes back to this local address "
                              "(another program's page, such as cdimport's)")
    browser.add_argument("--return-name", metavar="NAME", default="",
                         help="with --return-url, the name on that button (default: 'the previous page')")
    desktop_entry = browser.add_mutually_exclusive_group()
    desktop_entry.add_argument("--install-desktop-entry", action="store_true",
                               help="add lyrembed to the app menu (Linux/BSD), starting this lyrembed, then exit")
    desktop_entry.add_argument("--remove-desktop-entry", action="store_true",
                               help="remove lyrembed from the app menu, then exit")

    batch = parser.add_argument_group("batch mode")
    batch.add_argument("--auto", action="store_true", help="tag every track with a confident match, without prompting")
    batch.add_argument("--overwrite", action="store_true", help="in --auto mode, also replace existing lyrics (backed up first)")
    batch.add_argument("--dry-run", action="store_true", help="in --auto mode, report what would change and write nothing")
    romanize = batch.add_mutually_exclusive_group()
    romanize.add_argument("--romanize", action="store_true", default=None,
                          help="in --auto mode, romanize the lyrics it embeds (default: settings.auto_romanize)")
    romanize.add_argument("--no-romanize", dest="romanize", action="store_false",
                          help="in --auto mode, embed the lyrics as found")

    output = parser.add_argument_group("output")
    output.add_argument("--color", choices=("auto", "always", "never"), help="override colour handling")
    output.add_argument("-v", "--verbose", action="count", default=0, help="increase log verbosity (repeatable)")
    sidecar = output.add_mutually_exclusive_group()
    sidecar.add_argument("--sidecar", action="store_true", help="also write a companion .lrc file next to each track")
    sidecar.add_argument("--no-sidecar", action="store_true", help="never write companion .lrc files")

    return parser


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Silently ignoring these would let a user believe --overwrite took effect.
    if not args.auto:
        given = [flag for flag, on in (("--overwrite", args.overwrite), ("--dry-run", args.dry_run),
                                       ("--romanize" if args.romanize else "--no-romanize",
                                        args.romanize is not None)) if on]
        if given:
            verb = "apply" if len(given) > 1 else "applies"
            parser.error(f"{' and '.join(given)} only {verb} together with --auto")
    if args.no_browser and args.auto:
        parser.error("--no-browser and --auto exclude each other: batch mode runs without a page")
    if args.return_url and args.auto:
        parser.error("--return-url and --auto exclude each other: batch mode runs without a page")
    if args.return_name and not args.return_url:
        parser.error("--return-name only applies together with --return-url")
    if args.return_url and not local_page(args.return_url):
        parser.error("--return-url must be a page on this computer, such as http://127.0.0.1:8000/")
    if args.max_files is not None and args.max_files < 0:
        parser.error("--max-files must be 0 (unlimited) or a positive number")
    return args


def local_page(url: str) -> bool:
    """Whether *url* is a page on this computer: the only kind the Done
    button may lead to, so lyrembed cannot be used to send a tab elsewhere."""
    try:
        parts = urlsplit(url)
        return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost") and parts.port is not None
    except ValueError:
        return False


def configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)


def print_aliases() -> None:
    """Show the alias groups in effect: the built-in file plus the user's."""
    builtin = config.builtin_aliases()
    user = config.CONFIG.get("aliases") or []
    groups = config.alias_groups()
    print(f"Built-in: {len(builtin)} groups from {config.builtin_aliases_path()}")
    print(f"Yours:    {len(user)} groups from {config.CONFIG.get('_source', 'built-in defaults')}")
    print(f"In effect: {len(groups)} groups\n")
    for group in groups:
        print("  " + " = ".join(group))


# After an update, the new lyrembed takes over the old one's page: its port
# and token come in this variable ("PORT:TOKEN"), which nothing else sees
# (it is removed at once, so a cdimport started from the page does not get it).
RESTART_ENV = "LYREMBED_RESTART"


def _take_restart() -> tuple[int, str] | None:
    port, _, token = os.environ.pop(RESTART_ENV, "").partition(":")
    return (int(port), token) if port.isdigit() and token else None


def _serve_web(path: Path, cache: MetadataCache, open_browser: bool, explicit: bool = True,
               back: tuple[str, str] | None = None) -> int:
    """Open the page on *path*; a folder that cannot be used is no reason not
    to start, the page asks for another one. Started without a folder in the
    home folder - as the app menu does - it just asks, without a warning."""
    from . import web  # not needed for batch mode

    resolved = path.expanduser().resolve()
    files, problem = core.check_music_directory(resolved)
    message = problem.message if problem else ""
    if not explicit and resolved in (Path.home().resolve(), Path(resolved.anchor)):
        message = ""
    session = web.WebSession(resolved if files else None, core.sort_files(files, cache) if files else [],
                             cache, message)
    if back is not None:
        session.return_url, session.return_name = back
    restart = _take_restart()
    if restart:
        code = web.serve(session, open_browser=False, port=restart[0], token=restart[1])
    else:
        code = web.serve(session, open_browser=open_browser)
    if session.restart_to:
        _restart(*session.restart_to)
    return code


def _restart(port: int, token: str) -> None:
    """Start the lyrembed just installed in this process's place, on the
    same address: the page reloads once it answers again."""
    from . import selfupdate

    argv = selfupdate.restart_argv()
    ui.info("Starting the new lyrembed...")
    sys.stdout.flush()
    sys.stderr.flush()
    os.environ[RESTART_ENV] = f"{port}:{token}"
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:
        os.environ.pop(RESTART_ENV, None)
        ui.error(f"Could not start the new lyrembed ({exc}); start it again yourself.")


def _desktop_entry(install: bool) -> int:
    from . import desktop

    try:
        if install:
            entry, icon, command = desktop.install()
            ui.success(f"Added lyrembed to the app menu: {entry}")
            ui.info(f"It starts: {' '.join(command)}")
            ui.info("Run this again after installing lyrembed elsewhere (pipx, another checkout).")
        else:
            removed = desktop.remove()
            ui.success("Removed: " + ", ".join(str(p) for p in removed) if removed else "There was no app-menu entry.")
    except desktop.DesktopError as exc:
        ui.error(str(exc))
        return 2
    return 0


def romanize_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyrembed romanize",
        description=(
            "Romanize lyrics for display in another program, without writing "
            "anything and without network access. With --stdio it reads one JSON "
            'object per line from standard input, {"id": ..., "lyrics": "...", '
            '"han_hint": "ja" | "zh" | null}, and answers each with one line, '
            '{"id": ..., "lyrics": "..." | null, "language": "ko" | "ja" | "zh" | '
            '"mixed" | "any" | null, "reason": "..."}: the lyrics with each '
            "romanized line under its original (timestamps kept), or null and why "
            "they were left alone. It runs until standard input ends."
        ),
    )
    parser.add_argument("--stdio", action="store_true", required=True,
                        help="read requests from standard input, answer on standard output")
    return parser


def romanize_stdio(stdin, stdout) -> None:
    """The loop behind ``lyrembed romanize --stdio``: one answer per request
    line, flushed at once, so a long-running caller can wait for each.
    A line that isn't a request is answered with an error, not a crash."""
    for line in stdin:
        if not line.strip():
            continue
        answer: dict = {"id": None}
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or not isinstance(request.get("lyrics"), str):
                raise ValueError('expected {"id": ..., "lyrics": "..."}')
            answer["id"] = request.get("id")
            hint = request.get("han_hint")
            lyrics, language, reason = core.romanize_for_display(
                request["lyrics"], hint if hint in ("ja", "zh") else None)
            answer.update(lyrics=lyrics, language=language, reason=reason)
        except (ValueError, TypeError) as exc:
            answer.update(lyrics=None, language=None, error=str(exc))
        stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
        stdout.flush()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["romanize"]:
        romanize_parser().parse_args(argv[1:])
        try:
            romanize_stdio(sys.stdin, sys.stdout)
        except KeyboardInterrupt:
            return 130
        return 0
    args = parse_args(argv)
    configure_logging(args.verbose)

    try:
        loaded, warnings = config.load_config(args.config)
    except config.ConfigError as exc:
        ui.error(str(exc))
        return 2

    settings = loaded.setdefault("settings", {})
    if args.color:
        settings["color"] = args.color
    if args.sidecar:
        settings["write_sidecar_lrc"] = True
    if args.no_sidecar:
        settings["write_sidecar_lrc"] = False
    if args.max_files is not None:
        settings["max_file_count"] = args.max_files
    if args.romanize is not None:
        settings["auto_romanize"] = args.romanize

    config.set_config(loaded)
    ui.apply_color_settings()

    for message in warnings:
        ui.warn(f"Warning: {message}")
    if args.list_aliases:
        print_aliases()
        return 0
    if args.install_desktop_entry or args.remove_desktop_entry:
        return _desktop_entry(install=args.install_desktop_entry)
    unsupported = sorted(
        {str(e).lower() for e in config.get("settings", "supported_extensions") or []}
        - set(tagging.SUPPORTED_EXTENSIONS)
    )
    if unsupported:
        ui.warn(f"Warning: cannot tag {', '.join(unsupported)} - ignored in "
                f"settings.supported_extensions (supported: {', '.join(tagging.SUPPORTED_EXTENSIONS)}).")

    cache = MetadataCache()

    try:
        if not args.auto:
            return _serve_web(Path(args.path or "."), cache, open_browser=not args.no_browser,
                              explicit=args.path is not None,
                              back=(args.return_url, args.return_name) if args.return_url else None)
        resolved = batch.open_music_directory(Path(args.path or "."))
        if resolved is None:
            # Batch mode may run from cron: it never asks for another folder.
            return 2
        base_dir, files = resolved
        return batch.run_batch(core.sort_files(files, cache), base_dir, cache,
                               overwrite=args.overwrite, dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\n\nProgram aborted by user.")
        return 130
    finally:
        providers.close_session()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
