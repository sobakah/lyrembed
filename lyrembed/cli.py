"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import batch, config, core, providers, tagging, ui
from .config import __version__
from .tagging import MetadataCache


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyrembed",
        description="Search, check, romanize and embed synchronized and plain lyrics "
                    "in FLAC, MP3, OGG Vorbis, Opus and M4A files. Opens a page in your browser "
                    "(only this computer can reach it); --auto works without one.",
    )
    parser.add_argument("path", nargs="?", default=".", help="music directory (default: current directory)")
    parser.add_argument("--version", action="version", version=f"lyrembed {__version__}")
    parser.add_argument("-c", "--config", type=Path, metavar="FILE", help="use this config file instead of the search paths")
    parser.add_argument("--list-aliases", action="store_true",
                        help="print the artist alias groups in effect and where they come from, then exit")
    parser.add_argument("--max-files", type=int, metavar="N",
                        help="scan at most N files (0 = unlimited; default: settings.max_file_count)")

    browser = parser.add_argument_group("browser")
    browser.add_argument("--no-browser", action="store_true",
                         help="print the page's address instead of opening it")

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
    if args.max_files is not None and args.max_files < 0:
        parser.error("--max-files must be 0 (unlimited) or a positive number")
    return args


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


def _serve_web(path: Path, cache: MetadataCache, open_browser: bool) -> int:
    """Open the page on *path*; a folder that cannot be used is no reason not
    to start, the page asks for another one."""
    from . import web  # not needed for batch mode

    resolved = path.expanduser().resolve()
    files, problem = core.check_music_directory(resolved)
    session = web.WebSession(resolved if files else None, core.sort_files(files, cache) if files else [],
                             cache, problem.message if problem else "")
    return web.serve(session, open_browser=open_browser)


def main(argv: list[str] | None = None) -> int:
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
            return _serve_web(Path(args.path), cache, open_browser=not args.no_browser)
        resolved = batch.open_music_directory(Path(args.path))
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
