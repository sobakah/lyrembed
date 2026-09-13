"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import app, config, providers, ui
from .config import __version__
from .tagging import MetadataCache


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch-lyrics",
        description="Search, inspect, romanize and embed synchronized and plain lyrics "
                    "in FLAC, MP3, OGG Vorbis, Opus and M4A files.",
    )
    parser.add_argument("path", nargs="?", default=".", help="music directory (default: current directory)")
    parser.add_argument("--version", action="version", version=f"fetch-lyrics {__version__}")
    parser.add_argument("-c", "--config", type=Path, metavar="FILE", help="use this config file instead of the search paths")

    batch = parser.add_argument_group("batch mode")
    batch.add_argument("--auto", action="store_true", help="tag every track with a confident match, without prompting")
    batch.add_argument("--overwrite", action="store_true", help="in --auto mode, also replace existing lyrics")
    batch.add_argument("--dry-run", action="store_true", help="in --auto mode, report what would change and write nothing")

    output = parser.add_argument_group("output")
    output.add_argument("--color", choices=("auto", "always", "never"), help="override colour handling")
    output.add_argument("-v", "--verbose", action="count", default=0, help="increase log verbosity (repeatable)")
    output.add_argument("--sidecar", action="store_true", help="also write a companion .lrc file next to each track")
    output.add_argument("--no-sidecar", action="store_true", help="never write companion .lrc files")

    return parser


def configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)

    loaded, warnings = config.load_config(args.config)
    if args.color:
        loaded.setdefault("settings", {})["color"] = args.color
    if args.sidecar:
        loaded.setdefault("settings", {})["write_sidecar_lrc"] = True
    if args.no_sidecar:
        loaded.setdefault("settings", {})["write_sidecar_lrc"] = False

    config.set_config(loaded)
    ui.apply_color_settings()

    for message in warnings:
        ui.warn(f"Warning: {message}")
    if args.config and not args.config.is_file():
        ui.error(f"Config file not found: {args.config}")
        return 2
    if not ui.HAS_READLINE:
        ui.warn("readline is unavailable - history, Tab completion and prefilled prompts are disabled. "
                "On Windows: pip install pyreadline3")

    cache = MetadataCache()

    try:
        base_dir, files = app.resolve_music_directory(Path(args.path))
        files = app.sort_files(files, cache)

        if args.auto:
            return app.run_batch(files, cache, overwrite=args.overwrite, dry_run=args.dry_run)
        return app.run_interactive(files, base_dir, cache)

    except KeyboardInterrupt:
        print("\n\nProgram aborted by user.")
        return 130
    finally:
        providers.close_session()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
