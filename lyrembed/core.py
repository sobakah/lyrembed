"""What every front end shares: scanning, searching, saving, batch runs.

Nothing here prints or prompts. Functions return results or raise, and the
long runs (batch mode, bulk actions) are generators of steps: the terminal
prints each step as it comes, and a web page can stream the same steps. They
never sleep themselves but yield a `Pause`, so the caller decides how to wait
and whether it can be interrupted.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

from . import config, providers, tagging, text
from .providers import Candidate, SearchReport
from .tagging import MetadataCache, TaggingError, TrackMeta

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


def scan_extensions() -> set[str]:
    """Configured extensions this program can actually tag."""
    configured = {str(e).lower() for e in (config.get("settings", "supported_extensions") or [])}
    return configured & set(tagging.SUPPORTED_EXTENSIONS)


def max_file_count() -> int:
    """The scan cap; 0 or less means unlimited."""
    return int(config.get("settings", "max_file_count", 250))


def max_search_depth() -> int:
    return int(config.get("settings", "max_search_depth", 3))


def find_music_files(base_dir: Path) -> tuple[list[Path] | None, str | None]:
    extensions = scan_extensions()
    max_depth = max_search_depth()
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


@dataclass
class DirectoryProblem:
    """Why a folder cannot be used. *severe* marks a path that is wrong
    (missing, not a folder) rather than one that holds nothing usable."""

    message: str
    severe: bool = False


def check_music_directory(resolved: Path) -> tuple[list[Path] | None, DirectoryProblem | None]:
    """Scan *resolved*: its music files, or why it cannot be used."""
    if resolved == Path.home() or resolved == Path(resolved.anchor):
        return None, DirectoryProblem(
            f"Execution in root or home directory ('{resolved}') detected.\n"
            "To prevent unintended large-scale scans, please select a specific music folder."
        )
    if resolved.is_dir():
        files, err = find_music_files(resolved)
        if err == "TOO_MANY_FILES":
            return None, DirectoryProblem(
                f"⚠️  Found more than {max_file_count()} music files in '{resolved}'.\n"
                "Search scope is too broad. Please specify an artist or album directory, "
                "or raise the limit with --max-files."
            )
        if files:
            return files, None
        return None, DirectoryProblem(
            f"No supported music files found in '{resolved}' "
            f"(max depth: {max_search_depth()} directory levels)."
        )
    if not resolved.exists():
        return None, DirectoryProblem(f"Path does not exist: {resolved}", severe=True)
    return None, DirectoryProblem(f"Path is not a directory: {resolved}", severe=True)


# --- writing -----------------------------------------------------------------
def write_lyrics(path: Path, lyrics: str) -> list[str]:
    """Embed *lyrics*; returns warnings, raises TaggingError when the tag
    could not be written.

    A failed sidecar is only a warning: the lyrics are in the file, and
    reporting the whole write as failed made users retry a write that worked.
    """
    warnings = []
    try:
        tagging.embed_lyrics(path, lyrics)
    except tagging.SidecarError as exc:
        warnings.append(str(exc))
    stale = tagging.stale_sidecar(path)
    if stale is not None:
        warnings.append(f"'{stale.name}' was not updated; players that prefer sidecar files "
                        f"will keep showing it (--sidecar keeps it in sync).")
    return warnings


def delete_lyrics(meta: TrackMeta, cache: MetadataCache, delete_sidecar: bool) -> None:
    """Remove the embedded lyrics and, with *delete_sidecar*, the .lrc file.
    Raises TaggingError."""
    tagging.delete_lyrics(meta.path)
    if delete_sidecar:
        tagging.delete_sidecar(meta.path)
    cache.invalidate(meta.path)
    meta.lyrics = ""


def backup_directory() -> Path:
    """A fresh per-run folder under $XDG_STATE_HOME/lyrembed/backups. Only
    computed here; it is created by the first backup written into it."""
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


# --- searching ---------------------------------------------------------------
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


def default_highlight(report: SearchReport, duration: int) -> int | None:
    """The 1-based entry to offer first after a search: the one batch mode
    would apply (synced, matching, right length), else an exact match on top."""
    confident = providers.pick_automatic(report, duration)
    if confident is not None:
        return report.candidates.index(confident) + 1
    if report.candidates and report.candidates[0].exact:
        return 1
    return None


def fallback_sources(report: SearchReport, artist: str) -> list[str]:
    """Where a search looked beyond LRCLIB, NetEase and QQ Music because they
    found no synced lyrics, phrased to follow "also searched"."""
    sources = []
    if report.alias_fallback:
        sources.append("as " + ", ".join(text.get_artist_aliases(text.clean_tag(artist) or artist)))
    if report.megalobiz_fallback:
        sources.append("on Megalobiz")
    if report.scraper_fallback:
        sources.append("via syncedlyrics ("
                       + ", ".join(config.get("api", "syncedlyrics_providers") or ["all sources"]) + ")")
    return sources


def instrumental_hint(meta: TrackMeta) -> tuple[str | None, str | None]:
    """Why the track is probably instrumental, judged before any lyrics search:
    a version note in the title, the 'no lyrics' language tag, or - when
    enabled - MusicBrainz. Returns (hint, problem); a failed MusicBrainz
    lookup is the problem, not an error, since the search can go ahead."""
    marker = text.instrumental_marker(meta.title)
    if marker:
        return f"the title says '{marker}'", None
    if meta.language.lower() == "zxx":
        return "the language tag is 'zxx' (no lyrics)", None
    if meta.recording_id and config.get("api", "enable_musicbrainz", False):
        try:
            verdict = providers.musicbrainz_instrumental(meta.recording_id)
        except (requests.RequestException, ValueError) as exc:
            return None, f"MusicBrainz lookup failed: {exc}"
        if verdict:
            return f"MusicBrainz: {verdict}", None
    return None, None


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


# --- batch runs --------------------------------------------------------------
@dataclass
class Searching:
    """A search starts when the caller asks for the next step."""

    prefix: str


@dataclass
class Pause:
    """Wait *seconds* before asking for the next step: pacing between tracks,
    or, with *limited* naming the providers, a rate limit."""

    prefix: str
    seconds: float
    limited: list[str] = field(default_factory=list)


@dataclass
class Notice:
    """Something worth saying that does not decide the track."""

    prefix: str
    message: str


@dataclass
class Outcome:
    """What happened to one track. *result* is "done", "planned" (a dry run's
    would-be change), "skipped" or "failed"; a *notable* skip is one the user
    may want to fix (incomplete tags, nothing found), not one that was meant
    to happen (already tagged, instrumental)."""

    prefix: str
    path: Path
    result: str
    message: str
    notable: bool = False
    warnings: list[str] = field(default_factory=list)
    backed_up: bool = False


Step = Searching | Pause | Notice | Outcome


@dataclass
class Tally:
    """Counts a run's outcomes. Kept by the caller, so a run stopped halfway
    still has its numbers."""

    done: int = 0
    skipped: int = 0
    failed: int = 0
    backed_up: int = 0

    def add(self, outcome: Outcome) -> None:
        if outcome.result in ("done", "planned"):
            self.done += 1
        elif outcome.result == "skipped":
            self.skipped += 1
        else:
            self.failed += 1
        if outcome.backed_up:
            self.backed_up += 1


def batch(files: list[Path], base_dir: Path, cache: MetadataCache, overwrite: bool, dry_run: bool,
          backup_root: Path | None, romanize: bool | None = None) -> Iterator[Step]:
    """Embed the confident match for every track, without asking anything.

    *backup_root* receives lyrics about to be overwritten; without one,
    existing lyrics are never replaced. *romanize* None follows
    settings.auto_romanize.
    """
    if romanize is None:
        romanize = bool(config.get("settings", "auto_romanize", False))
    delay = max(0.0, float(config.get("api", "batch_delay_seconds", 1.0)))
    searched = False
    limit_pause = 0.0

    for index, path in enumerate(files, 1):
        meta = cache.get(path)
        prefix = f"[{index}/{len(files)}] {meta.label}"

        def outcome(result: str, message: str, **kwargs) -> Outcome:
            return Outcome(prefix, path, result, message, **kwargs)

        if meta.error:
            yield outcome("failed", f"unreadable ({meta.error})")
            continue
        if not meta.is_taggable:
            yield outcome("skipped", "incomplete tags, skipped.", notable=True)
            continue
        if meta.lyrics and not overwrite:
            yield outcome("skipped", "already tagged, skipped.")
            continue
        hint, problem = instrumental_hint(meta)
        if problem:
            yield Notice(prefix, problem)
        if hint:
            yield outcome("skipped", f"probably instrumental ({hint}), skipped.")
            continue

        # LRCLIB is a free community service: pace the requests rather than
        # sending a burst for every track of a large library.
        if searched and delay:
            yield Pause(prefix, delay)
        searched = True

        yield Searching(prefix)
        report, candidate = find_automatic(meta)
        if candidate is None and report.rate_limited:
            # Skipping track after track while LRCLIB refuses would waste the
            # run: wait, longer each time the limit persists, and try again once.
            limit_pause = min(max(limit_pause * 2, 10.0, *report.rate_limited.values()), 120.0)
            yield Pause(prefix, limit_pause, list(report.rate_limited))
            yield Searching(prefix)
            report, candidate = find_automatic(meta)
        if not report.rate_limited:
            limit_pause = 0.0

        if candidate is None and report.instrumental:
            yield outcome("skipped", f"instrumental according to {report.instrumental_summary()}, skipped.")
            continue
        if candidate is None:
            reason = "no confident match"
            if report.has_errors:
                notes = "; ".join(f"{p}: {', '.join(m)}" for p, m in report.errors.items() if m)
                reason = f"{reason} ({notes})"
            yield outcome("skipped", f"{reason}.", notable=True)
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
            yield outcome("planned", f"would apply {describe_candidate(position, candidate)}{note}.")
            continue

        backed_up = False
        if meta.lyrics:
            if backup_root is None:
                yield outcome("failed", "has lyrics and no backup folder was given - not overwritten.")
                continue
            try:
                backup_lyrics(backup_root, base_dir, path, meta.lyrics)
            except TaggingError as exc:
                # Never overwrite lyrics that could not be saved elsewhere.
                yield outcome("failed", f"{exc} - not overwritten.")
                continue
            backed_up = True

        try:
            warnings = write_lyrics(path, lyrics)
        except TaggingError as exc:
            yield outcome("failed", str(exc), backed_up=backed_up)
            continue
        cache.invalidate(path)
        yield outcome("done", f"tagged with {describe_candidate(position, candidate)}{note}.",
                      warnings=warnings, backed_up=backed_up)


BULK_ACTIONS = ("romanize", "sidecars", "delete")


def bulk(action: str, files: list[Path], base_dir: Path, cache: MetadataCache, backup_root: Path,
         delete_sidecars: bool = False) -> Iterator[Step]:
    """Apply one of BULK_ACTIONS, none of which needs a per-track decision.

    Romanizing and deleting rewrite lyrics the user may have edited by hand,
    and exporting may replace a differing sidecar, so the previous text goes
    to *backup_root* first, as batch mode does for --overwrite; a track whose
    backup fails is left untouched.
    """
    if action not in BULK_ACTIONS:
        raise ValueError(f"unknown bulk action: {action!r}")

    for index, path in enumerate(files, 1):
        meta = cache.get(path)
        prefix = f"[{index}/{len(files)}] {meta.label}"

        def outcome(result: str, message: str, **kwargs) -> Outcome:
            return Outcome(prefix, path, result, message, **kwargs)

        if meta.error:
            yield outcome("failed", f"unreadable ({meta.error})")
            continue
        if not meta.lyrics:
            yield outcome("skipped", "no lyrics, skipped.")
            continue

        if action == "sidecars":
            sidecar = tagging.sidecar_path(path)
            try:
                existing = sidecar.read_text(encoding="utf-8") if sidecar.is_file() else None
            except OSError as exc:
                yield outcome("failed", f"could not read '{sidecar.name}': {exc}")
                continue
            if existing == meta.lyrics:
                yield outcome("skipped", f"{sidecar.name} is up to date, skipped.")
                continue
            backed_up = False
            try:
                if existing is not None:
                    backup_lyrics(backup_root, base_dir, path, existing)
                    backed_up = True
                tagging.write_sidecar(path, meta.lyrics)
            except TaggingError as exc:
                yield outcome("failed", str(exc), backed_up=backed_up)
                continue
            yield outcome("done", f"wrote {sidecar.name}.", backed_up=backed_up)
            continue

        new_lyrics = None
        if action == "romanize":
            new_lyrics, reason = auto_romanize(meta.lyrics)
            if new_lyrics is None:
                yield outcome("skipped", f"{reason}, skipped.")
                continue

        try:
            backup_lyrics(backup_root, base_dir, path, meta.lyrics)
        except TaggingError as exc:
            yield outcome("failed", f"{exc} - left unchanged.")
            continue

        warnings: list[str] = []
        try:
            if action == "romanize":
                warnings = write_lyrics(path, new_lyrics)
            else:
                delete_lyrics(meta, cache, delete_sidecars)
        except TaggingError as exc:
            yield outcome("failed", str(exc), backed_up=True)
            continue
        cache.invalidate(path)
        yield outcome("done", "romanized." if action == "romanize" else "lyrics deleted.",
                      warnings=warnings, backed_up=True)
