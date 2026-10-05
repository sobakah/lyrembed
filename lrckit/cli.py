"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import app, config, providers, tagging, ui
from .config import __version__
from .tagging import MetadataCache


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lrckit",
        description="Search, inspect, romanize and embed synchronized and plain lyrics "
                    "in FLAC, MP3, OGG Vorbis, Opus and M4A files.",
    )
    parser.add_argument("path", nargs="?", default=".", help="music directory (default: current directory)")
    parser.add_argument("--version", action="version", version=f"lrckit {__version__}")
    parser.add_argument("-c", "--config", type=Path, metavar="FILE", help="use this config file instead of the search paths")
    parser.add_argument("--max-files", type=int, metavar="N",
                        help="scan at most N files (0 = unlimited; default: settings.max_file_count)")

    batch = parser.add_argument_group("batch mode")
    batch.add_argument("--auto", action="store_true", help="tag every track with a confident match, without prompting")
    batch.add_argument("--overwrite", action="store_true", help="in --auto mode, also replace existing lyrics (backed up first)")
    batch.add_argument("--dry-run", action="store_true", help="in --auto mode, report what would change and write nothing")

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
        given = [flag for flag, on in (("--overwrite", args.overwrite), ("--dry-run", args.dry_run)) if on]
        if given:
            verb = "apply" if len(given) > 1 else "applies"
            parser.error(f"{' and '.join(given)} only {verb} together with --auto")
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

    config.set_config(loaded)
    ui.apply_color_settings()

    for message in warnings:
        ui.warn(f"Warning: {message}")
    unsupported = sorted(
        {str(e).lower() for e in config.get("settings", "supported_extensions") or []}
        - set(tagging.SUPPORTED_EXTENSIONS)
    )
    if unsupported:
        ui.warn(f"Warning: cannot tag {', '.join(unsupported)} - ignored in "
                f"settings.supported_extensions (supported: {', '.join(tagging.SUPPORTED_EXTENSIONS)}).")
    if not ui.HAS_READLINE and not args.auto:
        ui.warn("readline is unavailable - history, Tab completion and prefilled prompts are disabled. "
                "On Windows: pip install pyreadline3")

    cache = MetadataCache()

    try:
        resolved = app.resolve_music_directory(Path(args.path), interactive=not args.auto)
        if resolved is None:
            # Only reachable without prompts: batch mode never asks for a path.
            return 2
        base_dir, files = resolved
        files = app.sort_files(files, cache)

        if args.auto:
            return app.run_batch(files, base_dir, cache, overwrite=args.overwrite, dry_run=args.dry_run)
        return app.run_interactive(files, base_dir, cache)

    except ui.QuitRequested as quit_request:
        return quit_request.code
    except KeyboardInterrupt:
        print("\n\nProgram aborted by user.")
        return 130
    finally:
        providers.close_session()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
