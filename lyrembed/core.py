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
    # Resolved too: on Fedora Atomic /home is a link to /var/home. The
    # folders above home (/home) hold every user's files, and a drive's root
    # (D:\\) all of the drive.
    home = Path.home().resolve()
    if resolved == home or resolved in home.parents or resolved == Path(resolved.anchor):
        return None, DirectoryProblem(
            f"'{resolved}' is the home folder, a folder above it or a drive's root.\n"
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


def music_folder() -> Path:
    """The desktop's music folder (XDG_MUSIC_DIR, ~/Musik on a German
    desktop), else ~/Music if it exists, else the home folder: where the
    folder picker starts when lyrembed was started without a folder."""
    home = Path.home()
    dirs = config.config_home() / "user-dirs.dirs"
    try:
        for line in dirs.read_text(encoding="utf-8").splitlines():
            match = re.match(r'\s*XDG_MUSIC_DIR="(.*)"\s*$', line)
            if match:
                value = match.group(1).replace("$HOME", str(home))
                folder = Path(value)
                if folder.is_absolute() and folder.is_dir() and folder != home:
                    return folder
    except OSError:
        pass
    fallback = home / "Music"
    return fallback if fallback.is_dir() else home


# --- writing -----------------------------------------------------------------
def write_lyrics(path: Path, lyrics: str) -> list[str]:
    """Embed *lyrics*; returns warnings, raises TaggingError when the tag
    could not be written.

    A failed sidecar is only a warning: the lyrics are in the file, and
    reporting the whole write as failed made users retry a write that worked.
    """
    warnings = []
    lyrics = text.unix_newlines(lyrics)
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
    if providers.is_musicbrainz_id(meta.recording_id) and config.get("api", "enable_musicbrainz", False):
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


def _not_installed(packages: list[str]) -> str:
    names = " and ".join(f"'{name}'" for name in packages)
    return f"{names} {'is' if len(packages) == 1 else 'are'} not installed"


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
    missing = text.missing_packages(lyrics) if key == "mixed" else [] if available() else [package]
    if missing:
        return None, _not_installed(missing)
    below = str(config.get("settings", "romanization_layout", "replace")).lower() == "below"
    result = text.romanize_lyrics(lyrics, key, keep_original=below)
    if result == lyrics:
        return None, "already romanized"
    return result, ""


def romanize_for_display(lyrics: str, han_hint: str | None = None) -> tuple[str | None, str | None, str]:
    """Romanization under each original line, for a player that shows both
    (Jewelcase, through ``lyrembed romanize --stdio``).

    Unlike :func:`auto_romanize` the layout is always "below", whatever the
    config says: the player needs the original to pair the lines, and it
    writes nothing. *han_hint* ("ja" or "zh") is what the player knows from
    elsewhere (Kana in other songs of the album, a LANGUAGE tag): it decides
    Han runs nothing else disambiguates, so Kanji-only lyrics can come out
    Japanese, but never outweighs Chinese evidence in the text. Returns
    (lyrics, language, "") or (None, language or None, reason it was left
    alone).
    """
    lyrics = text.unix_newlines(lyrics)
    if text.script_label(lyrics) == "latin":
        return None, None, "already Latin"
    key = text.detect_script(lyrics, han_hint)
    if key not in text.ROMANIZERS:
        return None, None, "no script to romanize"
    _, available, package = text.ROMANIZERS[key]
    missing = (text.missing_packages(lyrics, han_hint) if key == "mixed"
               else [] if available() else [package])
    if missing:
        return None, key, _not_installed(missing)
    han_default = han_hint if han_hint in ("ja", "zh") else "zh"
    result = text.romanize_lyrics(lyrics, key, han_default, keep_original=True)
    if result == lyrics:
        return None, key, "already romanized"
    return result, key, ""


# --- batch runs --------------------------------------------------------------
@dataclass
class Searching:
    """A search starts when the caller asks for the next step. *activity*
    says what it is ("searching", "asking MusicBrainz")."""

    prefix: str
    activity: str = "searching"


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
    # Providers still refusing after a track that another provider settled.
    limited: list[str] = []

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
        if limited:
            yield Pause(prefix, limit_pause, limited)
            limited = []
        elif searched and delay:
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
        elif candidate is not None:
            # Found elsewhere, but LRCLIB's quick lookup comes first again on
            # the next track, and each request prolongs the limit.
            limit_pause = min(max(limit_pause * 2, 10.0, *report.rate_limited.values()), 120.0)
            limited = list(report.rate_limited)

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
            except UnicodeDecodeError:
                # Its backup is kept as text, so it cannot be replaced safely.
                yield outcome("failed", f"'{sidecar.name}' is not UTF-8 text; left alone. "
                                        "Convert or remove it, then export again.")
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


# --- ISRCs ---------------------------------------------------------------------
def _release_track(tracks: list[providers.ReleaseTrack], meta: TrackMeta
                   ) -> tuple[providers.ReleaseTrack | None, str]:
    """The release's track that is this file, or why there is none.

    The recording ID settles it. Without one (or with one MusicBrainz has
    since merged away) the disc and track number decide, as in Jewelcase, but
    only when the title fits too: a wrong ISRC sends a Spotify song to the
    wrong file, a missing one only leaves it unmatched.
    """
    if meta.recording_id:
        for track in tracks:
            if track.recording_id == meta.recording_id:
                return track, ""
    discs = {track.disc for track in tracks}
    disc = meta.disc_number or (next(iter(discs)) if len(discs) == 1 else 0)
    if not disc:
        return None, "no disc number, and the album has several discs"
    if not meta.track_number:
        return None, "no track number"
    # By its position on the disc (as Picard numbers) or the number printed
    # on the release (going on across discs); the title decides between them.
    numbered = [t for t in tracks if t.disc == disc and t.numbered(meta.track_number)]
    for track in numbered:
        if text.matches_title(meta.title, track.title):
            return track, ""
    if numbered:
        return None, (f"MusicBrainz's track {disc}-{meta.track_number} is '{numbered[0].title}', "
                      "which does not fit this title")
    return None, f"MusicBrainz's album has no track {disc}-{meta.track_number}"


# A search for an album without a usable ID: how many of the best-scoring
# releases are fetched to compare with the files, and how far a track's
# length may be from the file's.
_SEARCH_LOOKUPS = 4
_LENGTH_TOLERANCE_S = 3


def _searched_track(tracks: list[providers.ReleaseTrack], meta: TrackMeta
                    ) -> tuple[providers.ReleaseTrack, bool] | None:
    """The one track of a searched release that is this file, and whether
    its title fits too. Stricter than with an album ID, since nothing but
    the search says it is the right album: the same number (and disc, where
    the release has several) and a length within the tolerance; a title that
    does not fit (translated: 'Scavengers' / 'Мусорщики') only with both
    lengths known and a track number, and the caller wants most titles to fit.
    """
    if not meta.title:
        return None
    discs = {track.disc for track in tracks}
    fitting = []
    for track in tracks:
        if meta.track_number and not track.numbered(meta.track_number):
            continue
        if meta.disc_number and len(discs) > 1 and track.disc != meta.disc_number:
            continue
        lengths_known = bool(meta.duration and track.length_ms)
        if lengths_known and abs(meta.duration - track.length_ms / 1000) > _LENGTH_TOLERANCE_S:
            continue
        if text.matches_title(meta.title, track.title):
            fitting.append((track, True))
        elif lengths_known and meta.track_number:
            fitting.append((track, False))
    by_title = [f for f in fitting if f[1]]
    if len(by_title) == 1:
        return by_title[0]
    return fitting[0] if len(fitting) == 1 else None


def _fits_release(tracks: list[providers.ReleaseTrack], group: list[TrackMeta]
                  ) -> dict[Path, providers.ReleaseTrack] | None:
    """Each file's track when every file of the album fits the release and
    more than half of them by title too (a single needs its title)."""
    matched = {meta.path: _searched_track(tracks, meta) for meta in group}
    if not all(matched.values()):
        return None
    if sum(1 for _, by_title in matched.values() if not by_title) * 2 >= len(group):
        return None
    return {path: track for path, (track, _) in matched.items()}


@dataclass
class _Search:
    """What searching for one album found: the release that fits all its
    files and each file's track, or why there is none."""

    release: providers.FoundRelease | None = None
    tracks: dict[Path, providers.ReleaseTrack] = field(default_factory=dict)
    reason: str = ""
    failed: bool = False


def _album_artist(meta: TrackMeta) -> str:
    return meta.album_artist or meta.artist


def isrcs(files: list[Path], cache: MetadataCache, dry_run: bool) -> Iterator[Step]:
    """Add the ISRCs MusicBrainz knows to tracks that have none.

    A track is found by the release ID Picard writes (MUSICBRAINZ_ALBUMID):
    one request per album, whatever the number of its tracks, paced by
    providers to one a second. An album without a usable ID (none, or one
    MusicBrainz has no release for, such as a release group's) is searched
    for by its title and album artist, and a release is only taken when
    every track of the album in the folder fits it. An ISRC the file already
    has is never replaced, so nothing needs a backup.
    """
    # Per run, so a second run sees what was added on MusicBrainz meanwhile:
    # the release's tracks, None for an unknown release, or the error.
    releases: dict[str, list[providers.ReleaseTrack] | None | str] = {}
    searches: dict[tuple, _Search] = {}
    # The tracks a search must fit: those of the run still without an ISRC,
    # by folder, album and album artist.
    albums: dict[tuple, list[TrackMeta]] = {}
    metas = {path: cache.get(path) for path in files}
    for meta in metas.values():
        if not meta.error and not meta.isrc:
            albums.setdefault(_album_key(meta), []).append(meta)

    def release_tracks(release_id: str) -> list[providers.ReleaseTrack] | None | str:
        if release_id not in releases:
            try:
                releases[release_id] = providers.musicbrainz_release(release_id)
            except (requests.RequestException, ValueError) as exc:
                releases[release_id] = f"MusicBrainz lookup failed: {exc}"
        return releases[release_id]

    def search(prefix: str, meta: TrackMeta, release_group: str) -> Iterator[Step]:
        """Fills searches[key] for meta's album, as steps (each request
        runs when the step after its Searching is asked for)."""
        key = (_album_key(meta), release_group)
        if key in searches:
            return
        group = albums.get(_album_key(meta), [meta])
        result = searches[key] = _Search()
        if not release_group and not (meta.album and _album_artist(meta)):
            result.reason = "no album and album artist to search MusicBrainz for"
            return
        yield Searching(prefix, "searching MusicBrainz")
        try:
            found = providers.musicbrainz_search_releases(meta.album, _album_artist(meta), release_group)
        except (requests.RequestException, ValueError) as exc:
            result.reason, result.failed = f"MusicBrainz search failed: {exc}", True
            return
        candidates = [r for r in found if r.track_count >= len(group)][:_SEARCH_LOOKUPS]
        for candidate in candidates:
            if candidate.id not in releases:
                yield Searching(prefix, "asking MusicBrainz")
            tracks = release_tracks(candidate.id)
            if isinstance(tracks, str):
                result.reason, result.failed = tracks, True
                return
            matched = _fits_release(tracks or [], group)
            if matched:
                result.release, result.tracks = candidate, matched
                yield Notice(prefix, f"{len(group)} track{'' if len(group) == 1 else 's'} of "
                                     f"'{meta.album}' found on MusicBrainz as {candidate.label}: {candidate.url}")
                return
        what = "the release group's releases" if release_group else \
            f"'{meta.album}' by {_album_artist(meta)}"
        result.reason = (f"no MusicBrainz release of {what} fits all {len(group)} of its tracks here"
                         if candidates else f"MusicBrainz found no release of {what}")

    for index, path in enumerate(files, 1):
        meta = metas[path]
        prefix = f"[{index}/{len(files)}] {meta.label}"

        def outcome(result: str, message: str, **kwargs) -> Outcome:
            return Outcome(prefix, path, result, message, **kwargs)

        if meta.error:
            yield outcome("failed", f"unreadable ({meta.error})")
            continue
        if meta.isrc:
            yield outcome("skipped", f"has the ISRC {meta.isrc}, skipped.")
            continue

        album = meta.album_id.lower()
        why_search = ""
        release_group = ""
        if not album:
            why_search = "no MusicBrainz album ID"
        elif not providers.is_musicbrainz_id(album):
            why_search = f"the MusicBrainz album ID '{meta.album_id}' is no valid ID"
        else:
            if album not in releases:
                yield Searching(prefix, "asking MusicBrainz")
            release = release_tracks(album)
            if isinstance(release, str):
                yield outcome("failed", release)
                continue
            if release is None:
                # Often a release group's ID: its releases are the candidates.
                why_search = f"MusicBrainz has no release {album}"
                release_group = album
            else:
                track, reason = _release_track(release, meta)
                if track is None:
                    yield outcome("skipped", f"{reason}, skipped.", notable=True)
                    continue

        found_by = ""
        if why_search:
            yield from search(prefix, meta, release_group)
            result = searches[(_album_key(meta), release_group)]
            track = result.tracks.get(path)
            if track is None:
                yield outcome("failed" if result.failed else "skipped",
                              f"{why_search}, and {result.reason or 'it did not fit the release found'}"
                              f"{'' if result.failed else ', skipped'}.", notable=not result.failed)
                continue
            found_by = f" (album found by searching: {result.release.label})"

        if not track.isrcs:
            yield outcome("skipped", f"MusicBrainz has no ISRC for this recording{found_by}, skipped.")
            continue
        if dry_run:
            yield outcome("planned", f"would add {_isrc_list(track.isrcs)}{found_by}.")
            continue
        try:
            written = tagging.write_isrcs(path, track.isrcs)
        except TaggingError as exc:
            yield outcome("failed", str(exc))
            continue
        finally:
            cache.invalidate(path)
        yield outcome("done", f"added {_isrc_list(written)}{found_by}.")


def _album_key(meta: TrackMeta) -> tuple:
    return (meta.path.parent, meta.album.casefold(), _album_artist(meta).casefold())


def _isrc_list(codes: list[str]) -> str:
    return f"the ISRC{'' if len(codes) == 1 else 's'} {', '.join(codes)}"
