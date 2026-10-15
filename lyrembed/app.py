"""Interactive workflow: directory scanning, track navigation and menus."""

from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from enum import Enum
from pathlib import Path

import requests

from . import config, providers, tagging, text
from .editor import EditorError, EditorReturnedAtOnce, open_editor
from .providers import Candidate, SearchReport
from .tagging import MetadataCache, TaggingError, TrackMeta
from .ui import (
    StyleUI,
    badge,
    choice_key,
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


class Action(Enum):
    """What a track menu asks the driver loop to do next."""

    NEXT = "next"
    PREV = "prev"
    TREE = "tree"
    SEARCH_ONLINE = "search_online"
    APPLY = "apply"


# The navigation block shared by every per-track menu.
NAVIGATION_GROUP = ("Navigate", [
    ("p", "Previous track"),
    ("s", "Next track"),
    ("t", "Tree overview"),
    ("q", "Quit"),
])
NAVIGATION_KEYS = {"p": Action.PREV, "s": Action.NEXT, "t": Action.TREE}


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


def scan_extensions() -> set[str]:
    """Configured extensions this program can actually tag."""
    configured = {str(e).lower() for e in (config.get("settings", "supported_extensions") or [])}
    return configured & set(tagging.SUPPORTED_EXTENSIONS)


def max_file_count() -> int:
    """The scan cap; 0 or less means unlimited."""
    return int(config.get("settings", "max_file_count", 250))


def find_music_files(base_dir: Path) -> tuple[list[Path] | None, str | None]:
    extensions = scan_extensions()
    max_depth = int(config.get("settings", "max_search_depth", 3))
    max_files = max_file_count()

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
                if max_files > 0 and len(found) > max_files:
                    return None, "TOO_MANY_FILES"

    return found, None


def sort_files(files: list[Path], cache: MetadataCache) -> list[Path]:
    if config.get("settings", "sort_by_tags", False):
        return [m.path for m in sorted((cache.get(f) for f in files), key=tag_key)]
    return sorted(files, key=natural_key)


def _check_music_directory(resolved: Path) -> list[Path] | None:
    """Scan *resolved*, explaining why it cannot be used if so."""
    max_depth = int(config.get("settings", "max_search_depth", 3))

    if resolved == Path.home() or resolved == Path(resolved.anchor):
        warn(f"\nExecution in root or home directory ('{resolved}') detected.")
        warn("To prevent unintended large-scale scans, please select a specific music folder.")
    elif resolved.is_dir():
        files, err = find_music_files(resolved)
        if err == "TOO_MANY_FILES":
            warn(f"\n⚠️  Found more than {max_file_count()} music files in '{resolved}'.")
            warn("Search scope is too broad. Please specify an artist or album directory, "
                 "or raise the limit with --max-files.")
        elif files:
            return files
        else:
            warn(
                f"\nNo supported music files found in '{resolved}' "
                f"(max depth: {max_depth} directory levels)."
            )
    elif not resolved.exists():
        error(f"\nPath does not exist: {resolved}")
    else:
        error(f"\nPath is not a directory: {resolved}")
    return None


def resolve_music_directory(
    initial_path: Path | None,
    allow_cancel: bool = False,
    interactive: bool = True,
) -> tuple[Path, list[Path]] | None:
    """Find a usable music directory, prompting until one is given.

    With *initial_path* set to None the prompt comes first, which is how the
    tree view switches to another folder. With *allow_cancel* an empty answer
    returns None instead of ending the program. Without *interactive* there is
    no prompt at all: batch mode may run from cron, where a prompt would block
    or read end-of-file, so an unusable path returns None.
    """
    current = initial_path

    while True:
        if current is not None:
            resolved = current.expanduser().resolve()
            files = _check_music_directory(resolved)
            if files:
                return resolved, files
            if not interactive:
                return None

        hint = "empty to cancel" if allow_cancel else "or 'q' to quit"
        enable_path_completion()
        try:
            raw = safe_input(f"{StyleUI.BOLD}Enter music directory path ({hint}): {StyleUI.RESET}")
        finally:
            disable_path_completion()

        cleaned = raw.strip().strip("'\" ")
        if not cleaned or cleaned.lower() == "q":
            if allow_cancel:
                return None
            exit_script()
        current = Path(cleaned)


def report_editor_error(exc: EditorError) -> None:
    """Quitting a terminal editor at once is no error; it reads as one only
    for a GUI editor that did not wait, which the message explains."""
    if isinstance(exc, EditorReturnedAtOnce):
        info(str(exc))
    else:
        error(str(exc))


# --- writing -----------------------------------------------------------------
def save_lyrics(path: Path, lyrics: str, prefix: str = "") -> bool:
    """Embed *lyrics* and report the outcome; True when the tag was written.

    A failed sidecar is only a warning here: the lyrics are in the file, and
    reporting the whole write as failed made users retry a write that worked.
    """
    lead = f"{prefix}: " if prefix else ""
    try:
        tagging.embed_lyrics(path, lyrics)
    except tagging.SidecarError as exc:
        warn(f"{lead}{exc}")
    except TaggingError as exc:
        error(f"{lead}{exc}")
        return False
    stale = tagging.stale_sidecar(path)
    if stale is not None:
        warn(f"{lead}'{stale.name}' was not updated; players that prefer sidecar files "
             f"will keep showing it (--sidecar keeps it in sync).")
    return True


def backup_directory() -> Path:
    """A fresh per-run folder under $XDG_STATE_HOME/lyrembed/backups."""
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state) if state else Path.home() / ".local" / "state"
    return root / "lyrembed" / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_lyrics(backup_root: Path, base_dir: Path, path: Path, lyrics: str) -> Path:
    """Keep a copy of lyrics about to be overwritten, mirroring the music tree."""
    try:
        relative = path.relative_to(base_dir)
    except ValueError:
        relative = Path(path.name)
    target = backup_root / relative.with_name(relative.name + ".lrc")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(lyrics, encoding="utf-8")
    except OSError as exc:
        raise TaggingError(f"Could not back up the existing lyrics to '{target}': {exc}") from exc
    return target


# --- rendering helpers -------------------------------------------------------
_SCRIPT_BADGES = {
    "latin": ("LATIN", "CYAN"),
    "original": ("ORIG", "MAGENTA"),
    "mixed": ("MIXED", "YELLOW"),
}


def script_badge(label: str, align: bool = False) -> str:
    """LATIN, ORIG or MIXED; MIXED warns that the lyrics need the Mixed
    scripts romanization, because one backend would get some lines wrong.
    *align* pads to a common width for the candidate list."""
    name, color = _SCRIPT_BADGES[label]
    return badge(name.ljust(5) if align else name, getattr(StyleUI, color))


def status_badges(lyrics: str) -> str:
    mode = badge("SYNC", StyleUI.GREEN) if text.has_timestamps(lyrics) else badge("PLAIN", StyleUI.YELLOW)
    return f"{mode} {script_badge(text.script_label(lyrics))}"


def print_provider_errors(report: SearchReport) -> None:
    if not report.has_errors:
        return
    print(f"{StyleUI.RED}{StyleUI.BOLD}⚠️  Provider notices:{StyleUI.RESET}")
    for provider, messages in report.errors.items():
        for message in messages:
            print(f"  {StyleUI.RED}• {provider}: {message}{StyleUI.RESET}")


def _format_length(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def duration_label(cand: Candidate) -> str:
    """The duration column. "Duration OK" requires both lengths to be known;
    with the track's own length missing there is nothing to compare."""
    if cand.duration_checked:
        return f"±{cand.diff}s" if cand.diff else "Duration OK"
    if cand.has_duration:
        return _format_length(cand.duration)
    return "N/A"


def print_candidates(candidates: list[Candidate], highlight: int | None = None) -> None:
    print(f"{StyleUI.BOLD}Found lyrics ({len(candidates)}):{StyleUI.RESET}")
    colors = {"LRCLIB": StyleUI.BLUE, "NETEASE": StyleUI.MAGENTA, "QQMUSIC": StyleUI.GREEN,
              "MEGALOBIZ": StyleUI.YELLOW, "SYNCEDLYRICS": StyleUI.CYAN}
    for index, cand in enumerate(candidates, 1):
        sync = badge("SYNC", StyleUI.GREEN) if cand.synced else badge("PLAIN", StyleUI.YELLOW)
        script = script_badge("latin" if cand.latin else "mixed" if cand.mixed else "original", align=True)
        source = badge(cand.provider, colors.get(cand.provider, StyleUI.WHITE))

        duration = duration_label(cand)
        exact = f" {StyleUI.GREEN}✓exact{StyleUI.RESET}" if cand.exact else ""
        unverified = f" {StyleUI.YELLOW}?unverified{StyleUI.RESET}" if cand.unverified else ""
        mismatch = [label for label, ok in (("artist", cand.match_artist), ("title", cand.match_title)) if not ok]
        if mismatch:
            unverified += f" {StyleUI.RED}≠{'/'.join(mismatch)}{StyleUI.RESET}"

        selected = index == highlight
        marker = f"{StyleUI.GREEN}{StyleUI.BOLD}▶{StyleUI.RESET}" if selected else " "
        number = choice_key(str(index), StyleUI.GREEN, selected)
        artist = f"{cand.artist} - " if cand.artist else ""
        title = f"{StyleUI.BOLD}{artist}{cand.title}{StyleUI.RESET}" if selected else f"{artist}{cand.title}"

        print(
            f"{marker} {number} {source} {sync} {script} "
            f"{StyleUI.GRAY}{duration:<12}{StyleUI.RESET} {title} "
            f"{StyleUI.GRAY}({cand.album}){StyleUI.RESET}{exact}{unverified}"
        )


def describe_candidate(index: int, cand: Candidate) -> str:
    artist = f"{cand.artist} - " if cand.artist else ""
    tags = ["synced" if cand.synced else "plain"]
    if cand.exact:
        tags.append("exact")
    if cand.unverified:
        tags.append("unverified")
    if cand.duration_checked:
        tags.append("duration OK" if not cand.diff else f"±{cand.diff}s")
    return f"#{index} {artist}{cand.title} [{cand.provider}, {', '.join(tags)}]"


# --- romanization prompt -----------------------------------------------------
def prompt_han_default() -> str | None:
    """Ask how Han runs should be read when nothing else disambiguates them."""
    print(f"\n{StyleUI.BOLD}Chinese and Japanese share Han characters.{StyleUI.RESET}")
    print(f"{StyleUI.GRAY}Runs next to Kana, or containing characters unique to one system,{StyleUI.RESET}")
    print(f"{StyleUI.GRAY}are detected automatically. Choose how to read the rest:{StyleUI.RESET}")
    print(f"  {choice_key('1', StyleUI.GREEN, True)} Chinese → Pīnyīn   "
          f"{choice_key('2', StyleUI.GREEN)} Japanese → Rōmaji   "
          f"{choice_key('c', StyleUI.YELLOW)} Cancel")

    answer = safe_input(f"{StyleUI.BOLD}Choice (Enter = 1): {StyleUI.RESET}").strip().lower()
    if answer in ("", "1"):
        return "zh"
    if answer == "2":
        return "ja"
    return None


def prompt_layout() -> bool | None:
    """Ask whether romanized lines replace the original or follow it.

    settings.romanization_layout decides what Enter picks, so a user who always
    keeps the original does not have to type it for every song.

    Returns True to keep the original, False to replace it, None to cancel.
    """
    below_by_default = str(config.get("settings", "romanization_layout", "replace")).lower() == "below"
    print(f"\n{StyleUI.BOLD}Layout:{StyleUI.RESET}")
    print(f"  {choice_key('1', StyleUI.GREEN, not below_by_default)} Replace the original   "
          f"{choice_key('2', StyleUI.GREEN, below_by_default)} Keep the original, romanized line below   "
          f"{choice_key('c', StyleUI.YELLOW)} Cancel")
    answer = safe_input(
        f"{StyleUI.BOLD}Choice (Enter = {2 if below_by_default else 1}): {StyleUI.RESET}"
    ).strip().lower()
    if answer == "":
        return below_by_default
    if answer == "1":
        return False
    if answer == "2":
        return True
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
        print(f"  {choice_key(str(index), StyleUI.GREEN, key == suggestion)} {label} [{state}]{hint}")
    print(f"  {choice_key('c', StyleUI.YELLOW)} Cancel")

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

    keep_original = prompt_layout()
    if keep_original is None:
        return lyrics

    info("Converting lyric lines...")
    return text.romanize_lyrics(lyrics, key, han_default=han_default, keep_original=keep_original)


# --- menus -------------------------------------------------------------------
def inspect_and_confirm(lyrics: str, source_label: str = "Selection", source_url: str = "") -> str | None:
    """Return the confirmed text, or None when the user goes back.

    *source_url* is the lyrics' page at their source; Megalobiz's terms ask
    to link to it wherever its lyrics are shown."""
    current = lyrics
    show_preview = True

    while True:
        print(f"\n{StyleUI.BOLD}Status [{source_label}]:{StyleUI.RESET} {status_badges(current)}")
        if source_url:
            print(f"{StyleUI.GRAY}Source: {source_url}{StyleUI.RESET}")
        if show_preview:
            preview_text(current)
        show_preview = True

        print_primary_action("Enter", "Apply these lyrics")
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
                edited = open_editor(current)
            except EditorError as exc:
                report_editor_error(exc)
                continue
            if edited == current:
                info("No changes.")
            else:
                current = edited
                success("Edits taken over - press Enter to apply them to the file.")
        elif action == "r":
            current = prompt_romanization(current)
        elif action in ("", "y"):  # 'y' still works for those used to it
            if not current.strip():
                warn("Lyrics are empty - nothing to apply.")
                continue
            return current
        elif action == "b":
            return None
        else:
            warn("Unknown option.")


def _delete_lyrics(meta: TrackMeta, cache: MetadataCache) -> bool:
    """Remove the embedded lyrics and, where wanted, the sidecar."""
    try:
        tagging.delete_lyrics(meta.path)
        if config.get("settings", "write_sidecar_lrc", False):
            tagging.delete_sidecar(meta.path)
        else:
            stale = tagging.stale_sidecar(meta.path)
            # The sidecar would keep showing the deleted lyrics in most players.
            if stale is not None and confirm(f"Also delete '{stale.name}'?"):
                tagging.delete_sidecar(meta.path)
    except TaggingError as exc:
        error(str(exc))
        return False
    cache.invalidate(meta.path)
    meta.lyrics = ""
    return True


def manage_existing_lyrics(meta: TrackMeta, cache: MetadataCache) -> Action:
    """Menu for a track that already carries lyrics. Returns a navigation action."""
    saved = meta.lyrics
    current = saved
    show_preview = True

    def persist(new_text: str) -> bool:
        if not save_lyrics(meta.path, new_text):
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
            NAVIGATION_GROUP,
        ])

        action = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()

        if action == "q":
            exit_script()

        elif action == "v":
            preview_text(current, max_lines=max(1, len(current.splitlines())))
            show_preview = False

        elif action == "e":
            try:
                edited = open_editor(current)
            except EditorError as exc:
                report_editor_error(exc)
                continue
            # Compared with what went into the editor, not with the file: after
            # a romanization that was not saved, closing the editor untouched
            # asked "Save changes?" again.
            if edited == current:
                info("No changes.")
                continue
            current = edited
            if current == saved:
                info("Back to the lyrics in the file.")
            elif confirm("Save changes?", default=True) and persist(current):
                saved = current

        elif action == "r":
            current = prompt_romanization(current)
            if current != saved and confirm("Save romanized lyrics?", default=True) and persist(current):
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
            if not _delete_lyrics(meta, cache):
                continue
            warn("Lyrics removed.")
            if confirm("Search online for new lyrics now?"):
                return Action.SEARCH_ONLINE
            return Action.NEXT

        elif action == "o":
            if dirty and not confirm("Discard unsaved changes and search online?"):
                continue
            return Action.SEARCH_ONLINE

        elif action in NAVIGATION_KEYS:
            if dirty and not confirm("Discard unsaved changes?"):
                continue
            return NAVIGATION_KEYS[action]

        else:
            warn("Unknown option.")


def default_highlight(report: SearchReport, duration: int) -> int | None:
    """The entry Enter saves after a search: the one batch mode would apply
    (synced, matching, right length), else an exact match on top."""
    confident = providers.pick_automatic(report, duration)
    if confident is not None:
        return report.candidates.index(confident) + 1
    if report.candidates and report.candidates[0].exact:
        return 1
    return None


def select_lyrics(meta: TrackMeta, cache: providers.SearchCache) -> tuple[Action, str | None]:
    """Search menu. Returns (action, lyrics)."""
    current_title, current_artist, current_album = meta.title, meta.search_artist, meta.album
    last_query = f"{current_artist} - {meta.title}" if current_artist else meta.title

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
            need_search = False
            force_refresh = False

            if entry is None:
                try:
                    with transient("Searching lyrics (LRCLIB + NetEase + QQ Music)... Ctrl+C cancels"):
                        report = providers.collect_candidates(
                            current_title, current_artist, current_album, meta.duration
                        )
                except KeyboardInterrupt:
                    # Only the search is abandoned, not the session; nothing
                    # is cached, so 'R' starts it over.
                    print()
                    warn("Search cancelled - press R to search again.")
                else:
                    highlight = default_highlight(report, meta.duration)
                    entry = cache.set(key, providers.CachedSearch(report, highlight))
            else:
                info("Using cached search results.")

            if entry is not None:
                print_provider_errors(entry.report)
                fallback = []
                if entry.report.alias_fallback:
                    fallback.append("as " + ", ".join(text.get_artist_aliases(text.clean_tag(current_artist) or current_artist)))
                if entry.report.megalobiz_fallback:
                    fallback.append("on Megalobiz")
                if entry.report.scraper_fallback:
                    fallback.append("via syncedlyrics (" + ", ".join(config.get("api", "syncedlyrics_providers") or ["all sources"]) + ")")
                if fallback:
                    info(f"No synced lyrics from LRCLIB, NetEase or QQ Music - also searched {' and '.join(fallback)}.")

        report = entry.report if entry else None
        hidden = report.unrelated if report else []
        unsearched_aliases = (
            text.get_artist_aliases(text.clean_tag(current_artist) or current_artist)
            if report is not None and not report.aliases_searched else []
        )
        candidates = (report.candidates + (hidden if entry.show_unrelated else [])) if report else []
        if report is not None and report.instrumental:
            warn(f"♪ Marked as instrumental by {report.instrumental_summary()} - "
                 f"this track most likely has no lyrics.")
        if not candidates:
            if entry is not None:
                warn("No results match this track's title and artist.")
                entry.highlight = None
        else:
            print_candidates(candidates, highlight=entry.highlight if entry else None)
        if hidden and not entry.show_unrelated:
            info(f"{len(hidden)} result(s) for other titles or artists hidden - 'u' shows them.")

        # Enter confirms the highlighted entry: the exact match a search found,
        # or the one inspected last before returning to this list.
        highlight = entry.highlight if entry else None
        confirmable = candidates[highlight - 1] if highlight and highlight <= len(candidates) else None

        upper = len(candidates) if candidates else 1
        preview_on_enter = bool(config.get("settings", "enter_opens_preview", False))
        if confirmable is not None:
            verb = "Inspect" if preview_on_enter else "Save"
            print_primary_action("Enter", f"{verb} {describe_candidate(highlight, confirmable)}")
        print_menu([
            ("Lyrics", ([(f"1-{upper}", "Inspect and select")] if candidates else []) + [
                ("m", "Adjust search query"),
                ("n", "Enter manually (editor)"),
                ("R", "Repeat search"),
            ] + ([("u", ("Hide" if entry.show_unrelated else "Show") + f" {len(hidden)} unrelated")] if hidden else [])
              + ([("a", "Search with aliases too")] if unsearched_aliases else [])),
            NAVIGATION_GROUP,
        ])

        choice = safe_input(f"{StyleUI.BOLD}Selection: {StyleUI.RESET}").strip()
        lowered = choice.lower()

        if choice == "" and confirmable is not None and preview_on_enter:
            # Same as typing the entry's number: preview and options first.
            choice = lowered = str(highlight)

        if choice == "" and confirmable is not None:
            success(f"Selected {describe_candidate(highlight, confirmable)}")
            return Action.APPLY, confirmable.text
        elif choice == "":
            warn("No entry highlighted - pick a number first.")
        elif lowered == "q":
            exit_script()
        elif lowered in NAVIGATION_KEYS:
            return NAVIGATION_KEYS[lowered], None
        elif lowered == "r":
            need_search = True
            force_refresh = True
        elif lowered == "a" and unsearched_aliases:
            info(f"Also searching as {', '.join(unsearched_aliases)}.")
            try:
                with transient("Searching lyrics with aliases... Ctrl+C cancels"):
                    report = providers.collect_candidates(
                        current_title, current_artist, current_album, meta.duration, aliases=True
                    )
            except KeyboardInterrupt:
                print()
                warn("Search cancelled.")
                continue
            highlight = default_highlight(report, meta.duration)
            entry = cache.set(key, providers.CachedSearch(report, highlight))
            print_provider_errors(report)
        elif lowered == "u" and hidden:
            entry.show_unrelated = not entry.show_unrelated
            if entry.highlight and entry.highlight > len(report.candidates):
                entry.highlight = None
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
                report_editor_error(exc)
                continue
            if not manual.strip():
                warn("No text entered.")
                continue
            confirmed = inspect_and_confirm(manual, source_label="Manual")
            if confirmed:
                return Action.APPLY, confirmed
        elif choice.isdigit() and 1 <= int(choice) <= len(candidates):
            index = int(choice)
            selected = candidates[index - 1]
            confirmed = inspect_and_confirm(
                selected.text, source_label=describe_candidate(index, selected), source_url=selected.url
            )
            if confirmed:
                return Action.APPLY, confirmed
            # Keep the inspected entry highlighted so it is obvious where the
            # user came back from, and let Enter confirm it. Stored on the
            # cache entry so it survives leaving and returning to the track.
            if entry:
                entry.highlight = index
        else:
            warn("Unknown option.")


# --- tree view ---------------------------------------------------------------
def display_tree(files: list[Path], base_dir: Path, cache: MetadataCache,
                 selected: int | None = 1) -> None:
    """*selected* is the 1-based track Enter starts with."""
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
            print(f"  {choice_key(f'{index:3d}', StyleUI.GREEN, index == selected)} {marker} {meta.label}")


# --- instrumental tracks -----------------------------------------------------
def instrumental_hint(meta: TrackMeta) -> str | None:
    """Why the track is probably instrumental, judged before any lyrics search:
    a version note in the title, the 'no lyrics' language tag, or - when
    enabled - MusicBrainz. A failed MusicBrainz lookup is reported, not fatal."""
    marker = text.instrumental_marker(meta.title)
    if marker:
        return f"the title says '{marker}'"
    if meta.language.lower() == "zxx":
        return "the language tag is 'zxx' (no lyrics)"
    if meta.recording_id and config.get("api", "enable_musicbrainz", False):
        try:
            verdict = providers.musicbrainz_instrumental(meta.recording_id)
        except (requests.RequestException, ValueError) as exc:
            warn(f"MusicBrainz lookup failed: {exc}")
            return None
        if verdict:
            return f"MusicBrainz: {verdict}"
    return None


# --- batch mode --------------------------------------------------------------
def find_automatic(meta: TrackMeta) -> tuple[SearchReport, Candidate | None]:
    """Search for an unattended match, as cheaply as possible.

    LRCLIB's canonical /get lookup settles most tracks with one or two
    requests; the full fan-out across all providers only runs when it does
    not produce a confident match.
    """
    report = providers.collect_candidates(meta.title, meta.search_artist, meta.album, meta.duration, quick=True)
    candidate = providers.pick_automatic(report, meta.duration)
    # An exact LRCLIB record marked instrumental settles it as well.
    if candidate is not None or report.instrumental:
        return report, candidate
    # The full search takes over what the quick one found instead of sending
    # the same /get lookup again.
    report = providers.collect_candidates(meta.title, meta.search_artist, meta.album, meta.duration,
                                          after_quick=report)
    return report, providers.pick_automatic(report, meta.duration)


def run_batch(files: list[Path], base_dir: Path, cache: MetadataCache,
              overwrite: bool, dry_run: bool) -> int:
    romanize = bool(config.get("settings", "auto_romanize", False))
    print_banner(f"Batch mode ({len(files)} files){', romanizing' if romanize else ''}"
                 f"{' - dry run' if dry_run else ''}")
    applied = skipped = failed = 0
    delay = max(0.0, float(config.get("api", "batch_delay_seconds", 1.0)))
    backup_root = backup_directory() if overwrite and not dry_run else None
    backed_up = 0
    searched = False
    limit_pause = 0.0

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
        hint = instrumental_hint(meta)
        if hint:
            print(f"{StyleUI.GRAY}{prefix}: probably instrumental ({hint}), skipped.{StyleUI.RESET}")
            skipped += 1
            continue

        # LRCLIB is a free community service: pace the requests rather than
        # sending a burst for every track of a large library.
        if searched and delay:
            time.sleep(delay)
        searched = True

        with transient(f"{prefix}: searching..."):
            report, candidate = find_automatic(meta)
        if candidate is None and report.rate_limited:
            # Skipping track after track while LRCLIB refuses would waste the
            # run: wait, longer each time the limit persists, and try again once.
            limit_pause = min(max(limit_pause * 2, 10.0, *report.rate_limited.values()), 120.0)
            print(f"{StyleUI.GRAY}{prefix}: {', '.join(report.rate_limited)} is limiting requests; "
                  f"waiting {limit_pause:g}s, then trying again.{StyleUI.RESET}")
            time.sleep(limit_pause)
            with transient(f"{prefix}: searching..."):
                report, candidate = find_automatic(meta)
        if not report.rate_limited:
            limit_pause = 0.0

        if candidate is None and report.instrumental:
            print(f"{StyleUI.GRAY}{prefix}: instrumental according to "
                  f"{report.instrumental_summary()}, skipped.{StyleUI.RESET}")
            skipped += 1
            continue
        if candidate is None:
            reason = "no confident match"
            if report.has_errors:
                notes = "; ".join(f"{p}: {', '.join(m)}" for p, m in report.errors.items() if m)
                reason = f"{reason} ({notes})"
            warn(f"{prefix}: {reason}.")
            skipped += 1
            continue

        position = report.candidates.index(candidate) + 1
        lyrics, note = candidate.text, ""
        if romanize:
            # Same conversion as the bulk action; lyrics that need none (Latin,
            # romanized before) are embedded as they are without comment.
            romanized, reason = auto_romanize(lyrics)
            if romanized is not None:
                lyrics, note = romanized, ", romanized"
            elif reason not in ("already Latin", "already romanized"):
                note = f" (not romanized: {reason})"
        if dry_run:
            print(f"{StyleUI.CYAN}{prefix}: would apply "
                  f"{describe_candidate(position, candidate)}{note}.{StyleUI.RESET}")
            applied += 1
            continue

        if meta.lyrics and backup_root is not None:
            try:
                backup_lyrics(backup_root, base_dir, path, meta.lyrics)
            except TaggingError as exc:
                # Never overwrite lyrics that could not be saved elsewhere.
                error(f"{prefix}: {exc} - not overwritten.")
                failed += 1
                continue
            backed_up += 1

        if not save_lyrics(path, lyrics, prefix=prefix):
            failed += 1
            continue

        cache.invalidate(path)
        success(f"{prefix}: tagged with {describe_candidate(position, candidate)}{note}.")
        applied += 1

    print_banner("Batch summary")
    print(f"  {StyleUI.GREEN}Applied: {applied}{StyleUI.RESET}   "
          f"{StyleUI.YELLOW}Skipped: {skipped}{StyleUI.RESET}   "
          f"{StyleUI.RED}Failed: {failed}{StyleUI.RESET}")
    if backed_up and backup_root is not None:
        info(f"  Replaced lyrics were backed up to {backup_root}")
    return 1 if failed else 0


# --- bulk actions ------------------------------------------------------------
def parse_selection(raw: str, count: int) -> list[int] | None:
    """Turn '1-5, 8 10' into sorted 0-based indices; empty means all tracks.

    Returns None for anything malformed or out of range rather than guessing,
    since the result decides which files get rewritten.
    """
    raw = raw.strip()
    if not raw or raw.lower() == "all":
        return list(range(count))
    chosen: set[int] = set()
    for part in re.split(r"[,\s]+", raw):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            return None
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start > end:
            start, end = end, start
        if start < 1 or end > count:
            return None
        chosen.update(range(start - 1, end))
    return sorted(chosen)


def auto_romanize(lyrics: str) -> tuple[str | None, str]:
    """Romanize without asking: detected script, configured layout.

    Returns (new lyrics, "") or (None, reason it was left alone). Han runs that
    nothing else disambiguates are read as Chinese, the interactive default.
    Running it twice changes nothing: Latin lyrics are skipped, and with the
    "below" layout an existing romanization is recognised.
    """
    if text.script_label(lyrics) == "latin":
        return None, "already Latin"
    key = text.detect_script(lyrics)
    if key not in text.ROMANIZERS:
        return None, "no script to romanize"
    _, available, package = text.ROMANIZERS[key]
    if not available():
        return None, f"'{package}' is not installed"
    below = str(config.get("settings", "romanization_layout", "replace")).lower() == "below"
    result = text.romanize_lyrics(lyrics, key, keep_original=below)
    if result == lyrics:
        return None, "already romanized"
    return result, ""


BULK_ACTIONS = {
    "1": "Fetch lyrics for tracks without lyrics",
    "2": "Romanize embedded lyrics",
    "3": "Write .lrc sidecars from embedded lyrics",
    "4": "Delete embedded lyrics",
}


def _bulk_summary(done: int, skipped: int, failed: int, backup_root: Path | None, backed_up: int) -> None:
    print_banner("Bulk summary")
    print(f"  {StyleUI.GREEN}Done: {done}{StyleUI.RESET}   "
          f"{StyleUI.YELLOW}Skipped: {skipped}{StyleUI.RESET}   "
          f"{StyleUI.RED}Failed: {failed}{StyleUI.RESET}")
    if backed_up and backup_root is not None:
        info(f"  The previous lyrics were backed up to {backup_root}")


def run_bulk_action(action: str, files: list[Path], base_dir: Path, cache: MetadataCache) -> None:
    """Apply one action that needs no per-track decision to every file.

    Romanizing and deleting rewrite lyrics the user may have edited by hand, so
    the previous text is backed up first, as batch mode does for --overwrite;
    a track whose backup fails is left untouched.
    """
    if action == "1":
        try:
            run_batch(files, base_dir, cache, overwrite=False, dry_run=False)
        except KeyboardInterrupt:
            warn("\nStopped.")
        return

    delete_sidecars = False
    if action == "4":
        if not confirm(f"{StyleUI.RED}Delete the lyrics of {len(files)} track(s)?{StyleUI.RESET}"):
            return
        delete_sidecars = bool(config.get("settings", "write_sidecar_lrc", False)) or (
            any(tagging.stale_sidecar(f) for f in files)
            and confirm("Also delete their .lrc sidecar files?")
        )

    print_banner(f"{BULK_ACTIONS[action]} ({len(files)} files)")
    backup_root = backup_directory() if action in ("2", "4") else None
    done = skipped = failed = backed_up = 0

    try:
        for index, path in enumerate(files, 1):
            meta = cache.get(path)
            prefix = f"[{index}/{len(files)}] {meta.label}"
            if meta.error:
                error(f"{prefix}: unreadable ({meta.error})")
                failed += 1
                continue
            if not meta.lyrics:
                print(f"{StyleUI.GRAY}{prefix}: no lyrics, skipped.{StyleUI.RESET}")
                skipped += 1
                continue

            new_lyrics = None
            if action == "2":
                new_lyrics, reason = auto_romanize(meta.lyrics)
                if new_lyrics is None:
                    print(f"{StyleUI.GRAY}{prefix}: {reason}, skipped.{StyleUI.RESET}")
                    skipped += 1
                    continue

            if action == "3":
                sidecar = tagging.sidecar_path(path)
                try:
                    existing = sidecar.read_text(encoding="utf-8") if sidecar.is_file() else None
                except OSError as exc:
                    error(f"{prefix}: could not read '{sidecar.name}': {exc}")
                    failed += 1
                    continue
                if existing == meta.lyrics:
                    print(f"{StyleUI.GRAY}{prefix}: {sidecar.name} is up to date, skipped.{StyleUI.RESET}")
                    skipped += 1
                    continue
                try:
                    if existing is not None:
                        if backup_root is None:
                            backup_root = backup_directory()
                        backup_lyrics(backup_root, base_dir, path, existing)
                        backed_up += 1
                    tagging.write_sidecar(path, meta.lyrics)
                except TaggingError as exc:
                    error(f"{prefix}: {exc}")
                    failed += 1
                    continue
                success(f"{prefix}: wrote {tagging.sidecar_path(path).name}.")
                done += 1
                continue

            try:
                backup_lyrics(backup_root, base_dir, path, meta.lyrics)
            except TaggingError as exc:
                error(f"{prefix}: {exc} - left unchanged.")
                failed += 1
                continue
            backed_up += 1

            if action == "2":
                if not save_lyrics(path, new_lyrics, prefix=prefix):
                    failed += 1
                    continue
                success(f"{prefix}: romanized.")
            else:
                try:
                    tagging.delete_lyrics(path)
                    if delete_sidecars:
                        tagging.delete_sidecar(path)
                except TaggingError as exc:
                    error(f"{prefix}: {exc}")
                    failed += 1
                    continue
                success(f"{prefix}: lyrics deleted.")
            cache.invalidate(path)
            done += 1
    except KeyboardInterrupt:
        warn("\nStopped; the remaining tracks were not touched.")

    _bulk_summary(done, skipped, failed, backup_root, backed_up)


def bulk_menu(files: list[Path], base_dir: Path, cache: MetadataCache) -> None:
    """Pick an action, then the tracks it applies to."""
    print(f"\n{StyleUI.BOLD}Bulk actions{StyleUI.RESET} {StyleUI.GRAY}(run without further questions){StyleUI.RESET}")
    for key, label in BULK_ACTIONS.items():
        print(f"  {choice_key(key, StyleUI.GREEN)} {label}")
    print(f"  {choice_key('b', StyleUI.YELLOW)} Back")
    action = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()
    if action not in BULK_ACTIONS:
        return
    if action == "2":
        layout = str(config.get("settings", "romanization_layout", "replace")).lower()
        info("Uses the detected script per track; Han characters default to Chinese. "
             f"Layout: {'original kept, romanized line below' if layout == 'below' else 'replace'} "
             "(settings.romanization_layout).")

    while True:
        raw = safe_input(f"{StyleUI.BOLD}Tracks (Enter = all {len(files)}, e.g. 1-5,8; b = back): {StyleUI.RESET}")
        if raw.strip().lower() == "b":
            return
        selection = parse_selection(raw, len(files))
        if selection is not None:
            break
        warn(f"Use numbers from 1 to {len(files)}, ranges like 3-7, separated by commas.")

    run_bulk_action(action, [files[i] for i in selection], base_dir, cache)


# --- interactive driver ------------------------------------------------------
def run_interactive(files: list[Path], base_dir: Path, cache: MetadataCache) -> int:
    search_cache = providers.SearchCache(
        int(config.get("settings", "search_cache_entries", 64))
    )
    current_index = 0
    # Direction of travel, so an unreadable track is skipped the way the user
    # was going - stepping back must not bounce forward again.
    step = 1
    show_tree = True

    def move(action: Action) -> None:
        nonlocal current_index, step, show_tree
        if action is Action.TREE:
            show_tree = True
            return
        step = -1 if action is Action.PREV else 1
        current_index += step

    while True:
        if show_tree:
            # Back from a track with 't', Enter continues where the user was
            # instead of starting the folder over.
            resume = current_index if 0 <= current_index < len(files) else 0
            display_tree(files, base_dir, cache, selected=resume + 1)
            print(f"\n{StyleUI.BOLD}Current folder:{StyleUI.RESET} {StyleUI.GRAY}{base_dir}{StyleUI.RESET}")
            print_primary_action("Enter", f"Continue with track {resume + 1}" if resume else "Start with the first track")
            print_menu([
                ("Navigate", [
                    (f"1-{len(files)}", "Jump to track"),
                    ("c", "Change directory"),
                    ("q", "Quit"),
                ]),
                ("Tracks", [
                    ("a", "Bulk actions"),
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
            elif choice == "a":
                bulk_menu(files, base_dir, cache)
                continue
            elif choice == "":
                current_index = resume
                show_tree = False
            elif choice.isdigit() and 1 <= int(choice) <= len(files):
                current_index = int(choice) - 1
                show_tree = False
            else:
                warn("Unknown option.")
                continue
            step = 1

        if current_index < 0:
            warn("\nAlready at the first song.")
            current_index = 0
            step = 1
        elif current_index >= len(files):
            success("\nReached end of track list.")
            show_tree = True
            continue

        path = files[current_index]
        meta = cache.get(path)
        print_banner(f"[{current_index + 1}/{len(files)}] {path.name}")

        if meta.error:
            error(f"File could not be read: {meta.error}")
            current_index += step
            continue

        if not meta.is_taggable:
            warn("Incomplete tags: title or artist missing.")
            print_menu([NAVIGATION_GROUP])
            action = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()
            if action == "q":
                exit_script()
            move(NAVIGATION_KEYS.get(action, Action.NEXT))
            continue

        length = f"{_format_length(meta.duration)} min" if meta.duration else "N/A"
        print(f"{StyleUI.BOLD}Track:{StyleUI.RESET}  {meta.artist} - {meta.title}")
        if meta.search_artist != meta.artist:
            print(f"{StyleUI.BOLD}Search:{StyleUI.RESET} {StyleUI.GRAY}as album artist{StyleUI.RESET} {meta.search_artist}")
        print(f"{StyleUI.BOLD}Album:{StyleUI.RESET}  {meta.album or 'N/A'} | {length}")
        hint = instrumental_hint(meta)
        if hint:
            warn(f"♪ Probably instrumental: {hint}.")

        if meta.lyrics:
            navigation = manage_existing_lyrics(meta, cache)
            if navigation is not Action.SEARCH_ONLINE:
                move(navigation)
                continue

        action, payload = select_lyrics(meta, search_cache)

        if action is Action.APPLY and payload:
            if not save_lyrics(path, payload):
                continue
            # The search stays cached: choosing "search online" on this track
            # later shows the same results without asking the providers again.
            cache.invalidate(path)
            success("✓ Lyrics successfully embedded into file.")
            move(Action.NEXT)
        elif action is Action.APPLY:
            warn("Skipped.")
            move(Action.NEXT)
        else:
            move(action)
