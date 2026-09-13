"""Interactive workflow: directory scanning, track navigation and menus."""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from pathlib import Path

from . import config, providers, tagging, text
from .editor import EditorError, open_editor
from .providers import Candidate, SearchReport
from .tagging import MetadataCache, TaggingError, TrackMeta
from .ui import (
    StyleUI,
    badge,
    confirm,
    print_menu,
    print_primary_action,
    disable_path_completion,
    enable_path_completion,
    error,
    exit_script,
    info,
    preview_text,
    print_banner,
    safe_input,
    success,
    transient,
    warn,
)

log = logging.getLogger(__name__)

_NATURAL_RE = re.compile(r"(\d+)")


# --- file discovery ----------------------------------------------------------
def natural_key(path: Path):
    """Sort 'Track 2' before 'Track 10' instead of lexicographically."""
    parts = _NATURAL_RE.split(path.name.lower())
    return (
        str(path.parent).lower(),
        [int(p) if p.isdigit() else p for p in parts],
    )


def tag_key(meta: TrackMeta):
    return (
        str(meta.path.parent).lower(),
        meta.disc_number or 0,
        meta.track_number or 0,
        natural_key(meta.path)[1],
    )


def find_music_files(base_dir: Path) -> tuple[list[Path] | None, str | None]:
    extensions = {
        str(e).lower() for e in (config.get("settings", "supported_extensions") or [])
    }
    max_depth = int(config.get("settings", "max_search_depth", 3))
    max_files = int(config.get("settings", "max_file_count", 250))

    found: list[Path] = []
    base = str(base_dir)

    for root, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))

        relative = os.path.relpath(root, base)
        depth = 0 if relative == "." else relative.count(os.sep) + 1
        if depth >= max_depth:
            dirs[:] = []

        root_path = Path(root)
        for name in sorted(files):
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() in extensions:
                found.append(root_path / name)
                if len(found) > max_files:
                    return None, "TOO_MANY_FILES"

    return found, None


def sort_files(files: list[Path], cache: MetadataCache) -> list[Path]:
    if config.get("settings", "sort_by_tags", False):
        return [m.path for m in sorted((cache.get(f) for f in files), key=tag_key)]
    return sorted(files, key=natural_key)


def resolve_music_directory(
    initial_path: Path | None,
    allow_cancel: bool = False,
) -> tuple[Path, list[Path]] | None:
    """Find a usable music directory, prompting until one is given.

    With *initial_path* set to None the prompt comes first, which is how the
    tree view switches to another folder. With *allow_cancel* an empty answer
    returns None instead of ending the program.
    """
    current = initial_path
    max_depth = int(config.get("settings", "max_search_depth", 3))
    max_files = int(config.get("settings", "max_file_count", 250))

    while True:
        if current is not None:
            resolved = current.expanduser().resolve()

            if resolved == Path.home() or resolved == Path(resolved.anchor):
                warn(f"\nExecution in root or home directory ('{resolved}') detected.")
                warn("To prevent unintended large-scale scans, please select a specific music folder.")
            elif resolved.is_dir():
                files, err = find_music_files(resolved)
                if err == "TOO_MANY_FILES":
                    warn(f"\n⚠️  Found more than {max_files} music files in '{resolved}'.")
                    warn("Search scope is too broad. Please specify an artist or album directory.")
                elif files:
                    return resolved, files
                else:
                    warn(
                        f"\nNo supported music files found in '{resolved}' "
                        f"(max depth: {max_depth} directory levels)."
                    )
            elif not resolved.exists():
                error(f"\nPath does not exist: {resolved}")
            else:
                error(f"\nPath is not a directory: {resolved}")

        hint = "empty to cancel" if allow_cancel else "or 'q' to quit"
        enable_path_completion()
        raw = safe_input(f"{StyleUI.BOLD}Enter music directory path ({hint}): {StyleUI.RESET}")
        disable_path_completion()

        cleaned = raw.strip().strip("'\" ")
        if not cleaned:
            if allow_cancel:
                return None
            exit_script()
        if cleaned.lower() == "q":
            if allow_cancel:
                return None
            exit_script()
        current = Path(cleaned)


# --- rendering helpers -------------------------------------------------------
def status_badges(lyrics: str) -> str:
    mode = badge("SYNC", StyleUI.GREEN) if text.has_timestamps(lyrics) else badge("PLAIN", StyleUI.YELLOW)
    script = badge("ORIGINAL", StyleUI.MAGENTA) if text.is_non_latin(lyrics) else badge("LATIN", StyleUI.CYAN)
    return f"{mode} {script}"


def print_provider_errors(report: SearchReport) -> None:
    if not report.has_errors:
        return
    print(f"{StyleUI.RED}{StyleUI.BOLD}⚠️  Provider notices:{StyleUI.RESET}")
    for provider, messages in report.errors.items():
        for message in messages:
            print(f"  {StyleUI.RED}• {provider}: {message}{StyleUI.RESET}")


def print_candidates(candidates: list[Candidate], highlight: int | None = None) -> None:
    print(f"{StyleUI.BOLD}Found lyrics ({len(candidates)}):{StyleUI.RESET}")
    colors = {"LRCLIB": StyleUI.BLUE, "NetEase": StyleUI.MAGENTA, "MULTI": StyleUI.CYAN}
    for index, cand in enumerate(candidates, 1):
        sync = badge("SYNC", StyleUI.GREEN) if cand.synced else badge("PLAIN", StyleUI.YELLOW)
        script = badge("LATIN", StyleUI.CYAN) if cand.latin else badge("ORIG ", StyleUI.MAGENTA)
        source = badge(cand.provider, colors.get(cand.provider, StyleUI.WHITE))

        if cand.has_duration:
            duration = f"±{cand.diff}s" if cand.diff else "Duration OK"
        else:
            duration = "N/A"
        exact = f" {StyleUI.GREEN}✓exact{StyleUI.RESET}" if cand.exact else ""

        selected = index == highlight
        marker = f"{StyleUI.GREEN}{StyleUI.BOLD}▶{StyleUI.RESET}" if selected else " "
        number = (
            f"{StyleUI.GREEN}{StyleUI.BOLD}{index}{StyleUI.RESET}"
            if selected else f"{StyleUI.GREEN}{index}{StyleUI.RESET}"
        )
        artist = f"{cand.artist} - " if cand.artist else ""
        title = f"{StyleUI.BOLD}{artist}{cand.title}{StyleUI.RESET}" if selected else f"{artist}{cand.title}"

        print(
            f"{marker} [{number}] {source} {sync} {script} "
            f"{StyleUI.GRAY}{duration:<12}{StyleUI.RESET} {title} "
            f"{StyleUI.GRAY}({cand.album}){StyleUI.RESET}{exact}"
        )


def describe_candidate(index: int, cand: Candidate) -> str:
    artist = f"{cand.artist} - " if cand.artist else ""
    tags = ["synced" if cand.synced else "plain"]
    if cand.exact:
        tags.append("exact")
    if cand.has_duration:
        tags.append("duration OK" if not cand.diff else f"±{cand.diff}s")
    return f"#{index} {artist}{cand.title} [{cand.provider}, {', '.join(tags)}]"


# --- romanization prompt -----------------------------------------------------
def prompt_han_default() -> str | None:
    """Ask how Han runs should be read when nothing else disambiguates them."""
    print(f"\n{StyleUI.BOLD}Chinese and Japanese share Han characters.{StyleUI.RESET}")
    print(f"{StyleUI.GRAY}Runs next to Kana, or containing characters unique to one system,{StyleUI.RESET}")
    print(f"{StyleUI.GRAY}are detected automatically. Choose how to read the rest:{StyleUI.RESET}")
    print(f"  [{StyleUI.GREEN}1{StyleUI.RESET}] Chinese → Pīnyīn   [{StyleUI.GREEN}2{StyleUI.RESET}] Japanese → Rōmaji   [{StyleUI.YELLOW}c{StyleUI.RESET}] Cancel")

    answer = safe_input(f"{StyleUI.BOLD}Choice (Enter = 1): {StyleUI.RESET}").strip().lower()
    if answer in ("", "1"):
        return "zh"
    if answer == "2":
        return "ja"
    return None


def prompt_romanization(lyrics: str) -> str:
    suggestion = text.detect_script(lyrics)
    keys = list(text.ROMANIZERS)

    print(f"\n{StyleUI.BOLD}Select romanization:{StyleUI.RESET}")
    for index, key in enumerate(keys, 1):
        label, available, package = text.ROMANIZERS[key]
        if key == "mixed":
            state = "Uses the backends below"
        else:
            state = "Available" if available() else f"Missing: pip install {package}"
        hint = f" {StyleUI.GREEN}(detected){StyleUI.RESET}" if key == suggestion else ""
        print(f"  [{StyleUI.GREEN}{index}{StyleUI.RESET}] {label} [{state}]{hint}")
    print(f"  [{StyleUI.YELLOW}c{StyleUI.RESET}] Cancel")

    default_index = keys.index(suggestion) + 1 if suggestion in keys else None
    prompt = f"{StyleUI.BOLD}Choose [1-{len(keys)}/c]"
    prompt += f" (Enter = {default_index}): " if default_index else ": "

    choice = safe_input(prompt).strip().lower()
    if not choice and default_index:
        choice = str(default_index)
    if not choice.isdigit() or not 1 <= int(choice) <= len(keys):
        return lyrics

    key = keys[int(choice) - 1]
    label, available, package = text.ROMANIZERS[key]

    han_default = "zh"
    if key == "mixed":
        missing = [
            pkg for k, (_, avail, pkg) in text.ROMANIZERS.items()
            if k != "mixed" and not avail()
        ]
        if missing:
            warn(f"Not installed, those scripts fall back to anyascii or stay unchanged: {', '.join(missing)}")
        chosen = prompt_han_default()
        if chosen is None:
            return lyrics
        han_default = chosen
    elif not available():
        error(f"Package '{package}' is not installed.")
        return lyrics

    info("Converting lyric lines...")
    return text.romanize_lyrics(lyrics, key, han_default=han_default)


# --- menus -------------------------------------------------------------------
def inspect_and_confirm(lyrics: str, source_label: str = "Selection") -> str | None:
    """Return the confirmed text, or None when the user goes back."""
    current = lyrics
    show_preview = True

    while True:
        print(f"\n{StyleUI.BOLD}Status [{source_label}]:{StyleUI.RESET} {status_badges(current)}")
        if show_preview:
            preview_text(current)
        show_preview = True

        print_primary_action("y", "Apply these lyrics")
        print_menu([
            ("Edit", [
                ("e", "Open in editor"),
                ("r", "Romanize"),
                ("v", "Full preview"),
            ]),
            ("Navigate", [
                ("b", "Back to candidates"),
                ("q", "Quit"),
            ]),
        ])

        action = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()

        if action == "q":
            exit_script()
        elif action == "v":
            preview_text(current, max_lines=max(1, len(current.splitlines())))
            show_preview = False
        elif action == "e":
            try:
                current = open_editor(current)
                success("Changes applied.")
            except EditorError as exc:
                error(str(exc))
        elif action == "r":
            current = prompt_romanization(current)
        elif action == "y":
            if not current.strip():
                warn("Lyrics are empty - nothing to apply.")
                continue
            return current
        elif action == "b":
            return None
        else:
            warn("Unknown option.")


def manage_existing_lyrics(meta: TrackMeta, cache: MetadataCache) -> str:
    """Menu for a track that already carries lyrics. Returns a navigation action."""
    saved = meta.lyrics
    current = saved
    show_preview = True

    def persist(new_text: str) -> bool:
        try:
            tagging.embed_lyrics(meta.path, new_text)
        except TaggingError as exc:
            error(str(exc))
            return False
        cache.invalidate(meta.path)
        meta.lyrics = new_text
        success("File updated.")
        return True

    while True:
        dirty = current != saved
        marker = f" {StyleUI.YELLOW}(modified, unsaved){StyleUI.RESET}" if dirty else ""
        print(f"\n{StyleUI.BOLD}Embedded lyrics found:{StyleUI.RESET} {status_badges(current)}{marker}")
        if show_preview:
            preview_text(current)
        show_preview = True

        print_menu([
            ("Edit", [
                ("e", "Open in editor"),
                ("r", "Romanize"),
                ("v", "Full preview"),
            ]),
            ("File", [
                ("w", "Save changes"),
                ("z", "Revert changes"),
                ("d", "Delete lyrics tag"),
                ("o", "Search online instead"),
            ]),
            ("Navigate", [
                ("p", "Previous track"),
                ("s", "Next track"),
                ("t", "Tree overview"),
                ("q", "Quit"),
            ]),
        ])

        action = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()

        if action == "q":
            exit_script()

        elif action == "v":
            preview_text(current, max_lines=max(1, len(current.splitlines())))
            show_preview = False

        elif action == "e":
            try:
                current = open_editor(current)
            except EditorError as exc:
                error(str(exc))
                continue
            if confirm("Save changes?") and persist(current):
                saved = current

        elif action == "r":
            current = prompt_romanization(current)
            if current != saved and confirm("Save romanized lyrics?") and persist(current):
                saved = current

        elif action == "w":
            if not dirty:
                info("Nothing to save.")
            elif persist(current):
                saved = current

        elif action == "z":
            if dirty:
                current = saved
                info("Reverted to the embedded version.")
            else:
                info("Nothing to revert.")

        elif action == "d":
            if not confirm(f"{StyleUI.RED}Delete lyrics from file?{StyleUI.RESET}"):
                continue
            try:
                tagging.delete_lyrics(meta.path)
                if config.get("settings", "write_sidecar_lrc", False):
                    tagging.delete_sidecar(meta.path)
            except TaggingError as exc:
                error(str(exc))
                continue
            cache.invalidate(meta.path)
            meta.lyrics = ""
            warn("Lyrics removed.")
            if confirm("Search online for new lyrics now?"):
                return "search_online"
            return "next"

        elif action == "o":
            if dirty and not confirm("Discard unsaved changes and search online?"):
                continue
            return "search_online"

        elif action in ("s", "p", "t"):
            if dirty and not confirm("Discard unsaved changes?"):
                continue
            return {"s": "next", "p": "prev", "t": "tree"}[action]

        else:
            warn("Unknown option.")


def select_lyrics(meta: TrackMeta, cache: providers.SearchCache) -> tuple[str, str | None]:
    """Search menu. Returns (action, lyrics)."""
    current_title, current_artist, current_album = meta.title, meta.artist, meta.album
    last_query = f"{meta.artist} - {meta.title}" if meta.artist else meta.title

    entry: providers.CachedSearch | None = None
    need_search = True
    force_refresh = False

    while True:
        if need_search:
            key = providers.SearchCache.key(
                current_title, current_artist, current_album, meta.duration
            )
            if force_refresh:
                cache.discard(key)
            entry = None if force_refresh else cache.get(key)

            if entry is None:
                with transient("Searching lyrics (LRCLIB + NetEase + syncedlyrics)..."):
                    report = providers.collect_candidates(
                        current_title, current_artist, current_album, meta.duration
                    )
                highlight = 1 if (report.candidates and report.candidates[0].exact) else None
                entry = cache.set(key, providers.CachedSearch(report, highlight))
            else:
                info("Using cached search results.")

            need_search = False
            force_refresh = False
            print_provider_errors(entry.report)

        report = entry.report if entry else None
        candidates = report.candidates if report else []
        if not candidates:
            warn("No matching results found.")
            if entry:
                entry.highlight = None
        else:
            print_candidates(candidates, highlight=entry.highlight if entry else None)

        # Enter confirms the highlighted entry: the exact match a search found,
        # or the one inspected last before returning to this list.
        highlight = entry.highlight if entry else None
        confirmable = candidates[highlight - 1] if highlight and highlight <= len(candidates) else None

        upper = len(candidates) if candidates else 1
        if confirmable is not None:
            print_primary_action("Enter", f"Save {describe_candidate(highlight, confirmable)}")
        print_menu([
            ("Lyrics", [
                (f"1-{upper}", "Inspect and select"),
                ("m", "Adjust search query"),
                ("n", "Enter manually (editor)"),
                ("R", "Repeat search"),
            ]),
            ("Navigate", [
                ("p", "Previous track"),
                ("s", "Next track"),
                ("t", "Tree overview"),
                ("q", "Quit"),
            ]),
        ])

        choice = safe_input(f"{StyleUI.BOLD}Selection: {StyleUI.RESET}").strip()
        lowered = choice.lower()

        if choice == "" and confirmable is not None:
            success(f"Selected {describe_candidate(highlight, confirmable)}")
            return "apply", confirmable.text
        elif choice == "":
            warn("No entry highlighted - pick a number first.")
        elif lowered == "q":
            exit_script()
        elif lowered == "s":
            return "next", None
        elif lowered == "p":
            return "prev", None
        elif lowered == "t":
            return "tree", None
        elif lowered == "r":
            need_search = True
            force_refresh = True
        elif lowered == "m":
            query = safe_input("New search query (title or 'Artist - Title'): ", default_text=last_query).strip()
            if query:
                last_query = query
                if " - " in query:
                    current_artist, current_title = (p.strip() for p in query.split(" - ", 1))
                else:
                    current_title, current_artist = query, ""
                current_album = ""
                need_search = True
        elif lowered == "n":
            info("Opening editor for manual input...")
            try:
                manual = open_editor("")
            except EditorError as exc:
                error(str(exc))
                continue
            if not manual.strip():
                warn("No text entered.")
                continue
            confirmed = inspect_and_confirm(manual, source_label="Manual")
            if confirmed:
                return "apply", confirmed
        elif choice.isdigit() and 1 <= int(choice) <= len(candidates):
            index = int(choice)
            selected = candidates[index - 1]
            confirmed = inspect_and_confirm(
                selected.text, source_label=describe_candidate(index, selected)
            )
            if confirmed:
                return "apply", confirmed
            # Keep the inspected entry highlighted so it is obvious where the
            # user came back from, and let Enter confirm it. Stored on the
            # cache entry so it survives leaving and returning to the track.
            if entry:
                entry.highlight = index
        else:
            warn("Unknown option.")


# --- tree view ---------------------------------------------------------------
def display_tree(files: list[Path], base_dir: Path, cache: MetadataCache) -> None:
    print_banner(f"Tracks Overview ({len(files)} files)")

    grouped: dict[str, list[tuple[int, TrackMeta]]] = defaultdict(list)
    with transient("Reading tags..."):
        for index, path in enumerate(files, 1):
            meta = cache.get(path)
            try:
                relative = path.parent.relative_to(base_dir)
            except ValueError:
                relative = Path(path.parent.name)
            key = str(relative) if str(relative) != "." else base_dir.name
            grouped[key].append((index, meta))

    for folder, items in grouped.items():
        print(f"\n{StyleUI.CYAN}{StyleUI.BOLD}📁 {folder}/{StyleUI.RESET}")
        for index, meta in items:
            if meta.error:
                marker = f"{StyleUI.RED}[UNREADABLE]{StyleUI.RESET}"
            elif meta.lyrics:
                marker = badge("SYNC", StyleUI.GREEN) if text.has_timestamps(meta.lyrics) else badge("PLAIN", StyleUI.YELLOW)
            else:
                marker = f"{StyleUI.GRAY}[NO LYRICS]{StyleUI.RESET}"
            print(f"  [{StyleUI.GREEN}{index:3d}{StyleUI.RESET}] {marker} {meta.label}")


# --- batch mode --------------------------------------------------------------
def run_batch(files: list[Path], cache: MetadataCache, overwrite: bool, dry_run: bool) -> int:
    print_banner(f"Batch mode ({len(files)} files){' - dry run' if dry_run else ''}")
    applied = skipped = failed = 0

    for index, path in enumerate(files, 1):
        meta = cache.get(path)
        prefix = f"[{index}/{len(files)}] {meta.label}"

        if meta.error:
            error(f"{prefix}: unreadable ({meta.error})")
            failed += 1
            continue
        if not meta.is_taggable:
            warn(f"{prefix}: incomplete tags, skipped.")
            skipped += 1
            continue
        if meta.lyrics and not overwrite:
            print(f"{StyleUI.GRAY}{prefix}: already tagged, skipped.{StyleUI.RESET}")
            skipped += 1
            continue

        with transient(f"{prefix}: searching..."):
            report = providers.collect_candidates(meta.title, meta.artist, meta.album, meta.duration)
        candidate = providers.pick_automatic(report, meta.duration)

        if candidate is None:
            reason = "no confident match"
            if report.has_errors:
                notes = "; ".join(f"{p}: {', '.join(m)}" for p, m in report.errors.items() if m)
                reason = f"{reason} ({notes})"
            warn(f"{prefix}: {reason}.")
            skipped += 1
            continue
        if dry_run:
            position = report.candidates.index(candidate) + 1
            print(f"{StyleUI.CYAN}{prefix}: would apply "
                  f"{describe_candidate(position, candidate)}.{StyleUI.RESET}")
            applied += 1
            continue

        try:
            tagging.embed_lyrics(path, candidate.text)
        except TaggingError as exc:
            error(f"{prefix}: {exc}")
            failed += 1
            continue

        cache.invalidate(path)
        position = report.candidates.index(candidate) + 1
        success(f"{prefix}: tagged with {describe_candidate(position, candidate)}.")
        applied += 1

    print_banner("Batch summary")
    print(f"  {StyleUI.GREEN}Applied: {applied}{StyleUI.RESET}   "
          f"{StyleUI.YELLOW}Skipped: {skipped}{StyleUI.RESET}   "
          f"{StyleUI.RED}Failed: {failed}{StyleUI.RESET}")
    return 1 if failed else 0


# --- interactive driver ------------------------------------------------------
def run_interactive(files: list[Path], base_dir: Path, cache: MetadataCache) -> int:
    search_cache = providers.SearchCache(
        int(config.get("settings", "search_cache_entries", 64))
    )
    current_index = 0
    show_tree = True

    while True:
        if show_tree:
            display_tree(files, base_dir, cache)
            print(f"\n{StyleUI.BOLD}Current folder:{StyleUI.RESET} {StyleUI.GRAY}{base_dir}{StyleUI.RESET}")
            print_primary_action("Enter", "Start with the first track")
            print_menu([
                ("Navigate", [
                    (f"1-{len(files)}", "Jump to track"),
                    ("c", "Change directory"),
                    ("q", "Quit"),
                ]),
            ])

            choice = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()
            if choice == "q":
                exit_script()
            elif choice == "c":
                changed = resolve_music_directory(None, allow_cancel=True)
                if changed is None:
                    info("Keeping the current folder.")
                    continue
                base_dir, discovered = changed
                files = sort_files(discovered, cache)
                current_index = 0
                success(f"Switched to {base_dir} ({len(files)} files).")
                continue
            elif choice == "":
                current_index = 0
                show_tree = False
            elif choice.isdigit() and 1 <= int(choice) <= len(files):
                current_index = int(choice) - 1
                show_tree = False
            else:
                warn("Unknown option.")
                continue

        if current_index < 0:
            warn("\nAlready at the first song.")
            current_index = 0
        elif current_index >= len(files):
            success("\nReached end of track list.")
            show_tree = True
            continue

        path = files[current_index]
        meta = cache.get(path)
        print_banner(f"[{current_index + 1}/{len(files)}] {path.name}")

        if meta.error:
            error(f"File could not be read: {meta.error}")
            current_index += 1
            continue

        if not meta.is_taggable:
            warn("Incomplete tags: title or artist missing.")
            print_menu([
                ("Navigate", [
                    ("p", "Previous track"),
                    ("s", "Next track"),
                    ("t", "Tree overview"),
                    ("q", "Quit"),
                ]),
            ])
            action = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()
            if action == "q":
                exit_script()
            elif action == "p":
                current_index -= 1
            elif action == "t":
                show_tree = True
            else:
                current_index += 1
            continue

        duration_label = f"{meta.duration // 60}:{meta.duration % 60:02d} min" if meta.duration else "N/A"
        print(f"{StyleUI.BOLD}Track:{StyleUI.RESET}  {meta.artist} - {meta.title}")
        print(f"{StyleUI.BOLD}Album:{StyleUI.RESET}  {meta.album or 'N/A'} | {duration_label}")

        if meta.lyrics:
            navigation = manage_existing_lyrics(meta, cache)
            if navigation == "tree":
                show_tree = True
                continue
            if navigation == "prev":
                current_index -= 1
                continue
            if navigation == "next":
                current_index += 1
                continue

        action, payload = select_lyrics(meta, search_cache)

        if action == "tree":
            show_tree = True
        elif action == "prev":
            current_index -= 1
        elif action == "next":
            current_index += 1
        elif action == "apply" and payload:
            try:
                tagging.embed_lyrics(path, payload)
            except TaggingError as exc:
                error(str(exc))
                continue
            cache.invalidate(path)
            search_cache.discard(providers.SearchCache.key(
                meta.title, meta.artist, meta.album, meta.duration
            ))
            success("✓ Lyrics successfully embedded into file.")
            current_index += 1
        else:
            warn("Skipped.")
            current_index += 1

    return 0
