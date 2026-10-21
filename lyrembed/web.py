"""The browser front end: a small HTTP server on 127.0.0.1 and its JSON API.

The page (static/) is plain HTML and JavaScript with Preact and htm copied
into the package, so there is no build step and nothing is loaded from the
internet. Only this machine can connect, and only with the token from the
address lyrembed opens, which the first visit turns into a cookie. Every
change to a file goes through core, like the terminal's.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import config, core, providers, tagging, text, ui
from .providers import Candidate, CachedSearch, SearchReport
from .tagging import MetadataCache, TaggingError, TrackMeta

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}
COOKIE = "lyrembed_token"
# A page pings every 15 s; Chrome runs a hidden tab's timers only once a
# minute, so silence shorter than that does not mean the page is gone.
HEARTBEAT_TIMEOUT = 90.0
# Reloading sends "closing" just like closing the tab; the reloaded page pings
# again within this time.
CLOSE_GRACE = 10.0
# Everything the page needs comes from lyrembed itself; lyrics are text and
# never become markup, but a policy keeps it that way should a bug try.
_CSP = ("default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'")
MAX_BODY = 4 * 1024 * 1024
_AUDIO_TYPES = {".flac": "audio/flac", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
                ".opus": "audio/ogg", ".m4a": "audio/mp4"}
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
# A cover file larger than this is no cover.
MAX_COVER_FILE = 20 * 1024 * 1024
_CHUNK = 256 * 1024
MAX_FOLDER_ENTRIES = 500


class ApiError(Exception):
    """A request the page should show as a message, with its HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# --- what the page gets ------------------------------------------------------
_PROVIDER_NAMES = {"LRCLIB": "LRCLIB", "NETEASE": "NetEase", "QQMUSIC": "QQ Music",
                   "MEGALOBIZ": "Megalobiz", "SYNCEDLYRICS": "syncedlyrics"}


def _status(meta: TrackMeta) -> str:
    if meta.error:
        return "unreadable"
    if meta.lyrics:
        return "sync" if text.has_timestamps(meta.lyrics) else "plain"
    if not meta.is_taggable:
        return "incomplete"
    return "none"


def candidate_json(cand: Candidate, number: int) -> dict:
    return {
        "number": number,
        "provider": cand.provider,
        "providerName": _PROVIDER_NAMES.get(cand.provider, cand.provider),
        "artist": cand.artist,
        "title": cand.title,
        "album": cand.album,
        "text": cand.text,
        "synced": cand.synced,
        "script": "latin" if cand.latin else "mixed" if cand.mixed else "original",
        "duration": cand.duration if cand.has_duration else None,
        # The difference only means something with both lengths known.
        "diff": cand.diff if cand.duration_checked else None,
        "exact": cand.exact,
        "unverified": cand.unverified,
        "mismatch": [label for label, ok in (("artist", cand.match_artist), ("title", cand.match_title)) if not ok],
        "url": cand.url,
        "description": core.describe_candidate(number, cand),
    }


def report_json(entry: CachedSearch, artist: str, cached: bool) -> dict:
    report: SearchReport = entry.report
    aliases = text.get_artist_aliases(text.clean_tag(artist) or artist) if not report.aliases_searched else []
    count = len(report.candidates)
    return {
        "cached": cached,
        "candidates": [candidate_json(c, i) for i, c in enumerate(report.candidates, 1)],
        "unrelated": [candidate_json(c, i) for i, c in enumerate(report.unrelated, count + 1)],
        "highlight": entry.highlight,
        "errors": report.errors,
        "instrumental": report.instrumental_summary() if report.instrumental else "",
        "fallback": core.fallback_sources(report, artist),
        "rateLimited": report.rate_limited,
        "unsearchedAliases": aliases,
    }


def _is_alac(path: Path) -> bool:
    """Apple Lossless in an M4A file: Chrome and Firefox refuse to play it
    (tested 2026-10-21), AAC in the same container plays."""
    if path.suffix.lower() != ".m4a":
        return False
    try:
        from mutagen.mp4 import MP4

        return MP4(path).info.codec == "alac"
    except Exception:  # unreadable: let the browser try and report it
        return False


# --- bulk runs -----------------------------------------------------------------
JOB_LABELS = {
    "fetch": "Fetch lyrics automatically",
    "romanize": "Romanize embedded lyrics",
    "sidecars": "Write .lrc files from embedded lyrics",
    "delete": "Delete embedded lyrics",
}


class Job:
    """A batch or bulk run in a thread of its own; the page reads its rows as
    they come. Stopping takes effect between tracks: a search that has started
    finishes, a rate-limit pause ends at once."""

    def __init__(self, action: str, total: int, indices: dict[Path, int], backup_root: Path | None,
                 dry_run: bool) -> None:
        self.action = action
        self.label = JOB_LABELS[action]
        self.total = total
        self.dry_run = dry_run
        self.backup_root = backup_root
        self.rows: list[dict] = []
        self.current = ""
        self.running = True
        self.error = ""
        self.tally = core.Tally()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self._indices = indices
        self._lock = threading.Lock()

    def _add(self, row: dict) -> None:
        with self._lock:
            self.rows.append(row)

    def run(self, steps) -> None:
        try:
            for step in steps:
                # Every step that arrives is recorded, then the run may stop:
                # an outcome arrives after its file was written.
                if isinstance(step, core.Searching):
                    self.current = f"{step.prefix}: searching…"
                elif isinstance(step, core.Pause):
                    if step.limited:
                        self.current = f"{step.prefix}: {', '.join(step.limited)} is limiting requests; waiting {step.seconds:g} s"
                        self._add({"kind": "pause", "prefix": step.prefix, "message": self.current})
                    if self.stop.wait(step.seconds):
                        break
                elif isinstance(step, core.Notice):
                    self._add({"kind": "notice", "prefix": step.prefix, "message": step.message})
                elif isinstance(step, core.Outcome):
                    self.tally.add(step)
                    self._add({"kind": "outcome", "prefix": step.prefix, "result": step.result,
                               "message": step.message, "notable": step.notable, "warnings": step.warnings,
                               "index": self._indices.get(step.path)})
                    self.current = ""
                if self.stop.is_set():
                    break
        except Exception as exc:  # reported on the page, not lost in a thread
            log.exception("bulk run failed")
            self.error = f"The run stopped on an error: {exc}"
        finally:
            steps.close()
            self.current = ""
            self.running = False

    def view(self, since: int = 0) -> dict:
        with self._lock:
            rows = self.rows[since:]
            count = len(self.rows)
        tally = self.tally
        return {
            "action": self.action, "label": self.label, "dryRun": self.dry_run,
            "running": self.running, "stopping": self.stop.is_set() and self.running,
            "stopped": self.stop.is_set() and not self.running, "error": self.error,
            "current": self.current, "total": self.total, "rows": rows, "count": count,
            "tally": {"done": tally.done, "skipped": tally.skipped, "failed": tally.failed,
                      "backedUp": tally.backed_up},
            "processed": tally.done + tally.skipped + tally.failed,
            "backupRoot": str(self.backup_root) if self.backup_root and tally.backed_up else "",
        }


# The settings page's groups, in order; keys not listed land in "More".
SETTING_GROUPS = [
    ("Lyrics sources", [
        ("api", "enable_lrclib"), ("api", "enable_netease"), ("api", "enable_qqmusic"),
        ("api", "enable_megalobiz"), ("api", "megalobiz_api_key"), ("api", "enable_syncedlyrics"),
        ("api", "syncedlyrics_as_fallback"), ("api", "syncedlyrics_providers"), ("api", "enable_musicbrainz"),
    ]),
    ("Lyrics and files", [
        ("settings", "prefer_synced"), ("settings", "prefer_latin"), ("settings", "write_sidecar_lrc"),
        ("settings", "auto_romanize"), ("settings", "romanization_layout"), ("settings", "prefer_album_artist"),
        ("settings", "search_all_aliases"), ("settings", "sort_by_tags"), ("settings", "supported_extensions"),
        ("settings", "max_file_count"), ("settings", "max_search_depth"), ("settings", "id3_v2_version"),
        ("settings", "uslt_language"),
    ]),
    ("Searching", [
        ("api", "timeout_seconds"), ("api", "duration_tolerance_seconds"), ("api", "batch_delay_seconds"),
        ("api", "netease_search_limit"), ("api", "max_workers"), ("api", "retry_total"), ("api", "retry_backoff"),
        ("api", "syncedlyrics_timeout_seconds"), ("settings", "search_cache_entries"),
        ("settings", "non_latin_ratio_threshold"),
    ]),
    ("Cleaning titles and artists", [("cleaning", key) for key in config.DEFAULT_CONFIG["cleaning"]]),
    ("Terminal output", [("settings", "color")]),
]
# Derived from the version; a saved copy would freeze it.
_HIDDEN_SETTINGS = {("api", "user_agent")}


def _setting_json(section: str, key: str) -> dict:
    default = config.DEFAULT_CONFIG[section][key]
    kind = ("bool" if isinstance(default, bool) else "int" if isinstance(default, int)
            else "float" if isinstance(default, float) else "list" if isinstance(default, list) else "str")
    choices = config._CHOICES.get((section, key))
    if (section, key) == ("settings", "color"):
        choices = ("auto", "always", "never")
    help_text = config.SETTING_HELP.get(section, {}).get(key, "")
    return {"section": section, "key": key, "kind": kind, "value": config.get(section, key),
            "default": default, "choices": list(choices) if choices else None,
            "minimum": config._MINIMUMS.get((section, key)), "help": help_text}


# --- the state a page works on -----------------------------------------------
class WebSession:
    """One folder, its tracks and the caches, shared by the page's requests.

    Requests arrive on several threads. The lock covers the caches and the
    writes; provider searches run outside it, so a slow search does not hold
    up the rest of the page.
    """

    def __init__(self, base_dir: Path | None, files: list[Path], cache: MetadataCache,
                 problem: str = "") -> None:
        self.lock = threading.RLock()
        self.cache = cache
        self.searches = providers.SearchCache(int(config.get("settings", "search_cache_entries", 64)))
        self.base_dir = base_dir
        self.files = files
        # Why the folder lyrembed was started with could not be opened; the
        # page shows it above the folder picker.
        self.problem = problem
        # FLAC copies of ALAC tracks, made for listening only: (path, mtime) -> copy.
        self._converted: dict[tuple[Path, float], Path] = {}
        self._temp: Path | None = None
        self.job: Job | None = None
        # Cover per (path, mtime): its id (a digest, so equal covers are
        # recognised) and where it is; None when the track has none.
        self._covers: dict[tuple[Path, float], dict | None] = {}

    def close(self) -> None:
        """Let a bulk run finish its track, then remove the listening copies."""
        job = self.job
        if job is not None and job.running:
            job.stop.set()
            if job.thread is not None:
                job.thread.join(timeout=120)
        if self._temp is not None:
            shutil.rmtree(self._temp, ignore_errors=True)
            self._temp = None

    # tracks
    def _meta(self, index: int) -> TrackMeta:
        if not 0 <= index < len(self.files):
            raise ApiError(404, "There is no such track in this folder (anymore).")
        return self.cache.get(self.files[index])

    def _cover_data(self, path: Path) -> tuple[bytes, Path | None] | None:
        """The cover's bytes: embedded in the file, else an image file next to it."""
        try:
            data = tagging.embedded_cover(path)
        except TaggingError:
            data = None
        if data and tagging.image_type(data):
            return data, None
        found = tagging.cover_file(path.parent)
        try:
            if found is not None and found.stat().st_size <= MAX_COVER_FILE:
                data = found.read_bytes()
                if tagging.image_type(data):
                    return data, found
        except OSError:
            pass
        return None

    def _cover(self, path: Path) -> dict | None:
        try:
            key = (path, path.stat().st_mtime)
        except OSError:
            return None
        if key not in self._covers:
            found = self._cover_data(path)
            self._covers[key] = None if found is None else {
                "id": hashlib.sha1(found[0]).hexdigest()[:16],
                "type": tagging.image_type(found[0]),
            }
        return self._covers[key]

    def cover(self, index: int) -> tuple[bytes, str]:
        with self.lock:
            path = self._meta(index).path
        found = self._cover_data(path)
        if found is None:
            raise ApiError(404, "This track has no cover.")
        return found[0], tagging.image_type(found[0])

    def _summary(self, index: int, meta: TrackMeta) -> dict:
        path = meta.path
        group = ""
        if self.base_dir is not None:
            try:
                relative = path.parent.relative_to(self.base_dir)
                group = "" if str(relative) == "." else relative.as_posix()
            except ValueError:
                group = path.parent.name
        status = _status(meta)
        return {
            "index": index,
            "file": path.name,
            "group": group,
            "title": meta.title,
            "artist": meta.artist,
            "album": meta.album,
            "label": meta.label,
            "duration": meta.duration,
            "format": path.suffix.lstrip(".").upper(),
            "status": status,
            "script": text.script_label(meta.lyrics) if meta.lyrics else "",
            # Only what costs nothing to find out; the track view adds MusicBrainz.
            "instrumental": bool(text.instrumental_marker(meta.title) or meta.language.lower() == "zxx"),
            "error": meta.error,
            "hasSidecar": tagging.stale_sidecar(path) is not None,
            "cover": (self._cover(path) or {}).get("id", ""),
        }

    def library(self) -> dict:
        with self.lock:
            tracks = [self._summary(i, self.cache.get(p)) for i, p in enumerate(self.files)]
            covers = {t["cover"] for t in tracks}
            # One cover for every track is the album's: shown with the folder.
            shared = covers.pop() if len(covers) == 1 else ""
            return {
                "folder": str(self.base_dir) if self.base_dir else "",
                "name": self.base_dir.name if self.base_dir else "",
                "problem": self.problem,
                "tracks": tracks,
                "cover": shared,
            }

    def open_folder(self, raw: str) -> dict:
        path = Path(raw.strip().strip("'\"")).expanduser()
        if not raw.strip():
            raise ApiError(400, "Enter a folder.")
        resolved = path.resolve()
        files, problem = core.check_music_directory(resolved)
        if problem is not None:
            raise ApiError(400, problem.message)
        with self.lock:
            self.base_dir, self.files, self.problem = resolved, core.sort_files(files, self.cache), ""
        return self.library()

    def folders(self, raw: str) -> dict:
        """Subfolders of *raw* for the folder picker: the browser can't hand out
        paths, so the server lists them."""
        if raw:
            path = Path(raw).expanduser()
        elif self.base_dir is not None:
            path = self.base_dir.parent
        else:
            path = Path.home()
        try:
            path = path.resolve()
            entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            raise ApiError(400, f"Cannot open '{path}': {exc.strerror or exc}") from exc
        extensions = core.scan_extensions()
        dirs, music = [], 0
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    # Full paths, so the page never joins them (Windows uses "\").
                    dirs.append({"name": entry.name, "path": str(entry)})
                elif entry.suffix.lower() in extensions:
                    music += 1
            except OSError:
                continue
        return {
            "path": str(path),
            "parent": str(path.parent) if path.parent != path else "",
            "dirs": dirs[:MAX_FOLDER_ENTRIES],
            "truncated": len(dirs) > MAX_FOLDER_ENTRIES,
            "musicFiles": music,
            "home": str(Path.home()),
        }

    def track(self, index: int) -> dict:
        with self.lock:
            meta = self._meta(index)
            data = self._summary(index, meta)
        hint, problem = core.instrumental_hint(meta)
        stale = tagging.stale_sidecar(meta.path)
        data.update({
            "lyrics": meta.lyrics,
            "albumArtist": meta.album_artist,
            "searchArtist": meta.search_artist,
            "instrumentalHint": hint or "",
            "notice": problem or "",
            "staleSidecar": stale.name if stale is not None else "",
            "writesSidecar": bool(config.get("settings", "write_sidecar_lrc", False)),
            "playback": self._playback(meta.path) if not meta.error else {"mode": "none", "note": ""},
        })
        return data

    # listening
    @staticmethod
    def _playback(path: Path) -> dict:
        if not _is_alac(path):
            return {"mode": "direct", "note": ""}
        if shutil.which("ffmpeg") is None:
            return {"mode": "none", "note": "Apple Lossless (ALAC) doesn't play in browsers, and ffmpeg, "
                                            "which lyrembed needs to make a copy for listening, isn't installed."}
        return {"mode": "converted", "note": "Apple Lossless (ALAC) doesn't play in browsers: you hear a "
                                             "temporary FLAC copy. The file itself isn't changed."}

    def audio(self, index: int) -> tuple[Path, str]:
        """The file to play for track *index* and its type: the track itself,
        or for ALAC a FLAC copy, made once per version of the file."""
        with self.lock:
            path = self._meta(index).path
        if not _is_alac(path):
            return path, _AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise ApiError(415, self._playback(path)["note"])
        key = (path, path.stat().st_mtime)
        with self.lock:
            copy = self._converted.get(key)
            if copy is None or not copy.is_file():
                if self._temp is None:
                    self._temp = Path(tempfile.mkdtemp(prefix="lyrembed-listen-"))
                copy = self._temp / f"{len(self._converted)}.flac"
                try:
                    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(path),
                                    "-map", "0:a:0", "-c:a", "flac", str(copy)],
                                   check=True, capture_output=True, timeout=300)
                except (OSError, subprocess.SubprocessError) as exc:
                    detail = getattr(exc, "stderr", b"") or b""
                    raise ApiError(500, f"Could not convert '{path.name}' for listening: "
                                        f"{detail.decode(errors='replace').strip() or exc}") from exc
                self._converted[key] = copy
        return copy, "audio/flac"

    @staticmethod
    def romanizers(lyrics: str) -> dict:
        """The choices of the terminal's romanization prompt, for the editor."""
        options = []
        for key, (label, available, package) in text.ROMANIZERS.items():
            options.append({"key": key, "label": label, "available": bool(available()),
                            "package": "" if key == "mixed" else package})
        return {
            "detected": text.detect_script(lyrics) if lyrics.strip() else "",
            "options": options,
            "below": str(config.get("settings", "romanization_layout", "replace")).lower() == "below",
        }

    @staticmethod
    def romanize(lyrics: str, key: str, han_default: str, keep_original: bool) -> dict:
        if key not in text.ROMANIZERS:
            raise ApiError(400, f"Unknown romanization: {key}")
        _, available, package = text.ROMANIZERS[key]
        if not available():
            raise ApiError(400, f"The Python package '{package}' is not installed.")
        if han_default not in ("zh", "ja"):
            han_default = "zh"
        return {"lyrics": text.romanize_lyrics(lyrics, key, han_default=han_default, keep_original=keep_original)}

    # searching
    def search(self, index: int, title: str | None = None, artist: str | None = None,
               refresh: bool = False, aliases: bool = False) -> dict:
        """Search like the terminal's track menu: cached per query, *refresh*
        asks the providers again, *aliases* also searches the artist's other
        names. A query typed by the user drops the album, as 'm' does."""
        with self.lock:
            meta = self._meta(index)
        if not meta.is_taggable and not (title or "").strip():
            raise ApiError(400, "This track has no title or artist to search for; enter them above.")
        custom = title is not None or artist is not None
        query_title = (title if title is not None else meta.title).strip()
        query_artist = (artist if artist is not None else meta.search_artist).strip()
        album = "" if custom else meta.album
        if not query_title:
            raise ApiError(400, "Enter a title to search for.")
        key = providers.SearchCache.key(query_title, query_artist, album, meta.duration)
        with self.lock:
            if refresh:
                self.searches.discard(key)
            entry = self.searches.get(key)
        cached = entry is not None and not (aliases and not entry.report.aliases_searched)
        if not cached:
            report = providers.collect_candidates(query_title, query_artist, album, meta.duration, aliases=aliases)
            entry = CachedSearch(report, core.default_highlight(report, meta.duration))
            with self.lock:
                self.searches.set(key, entry)
        result = report_json(entry, query_artist, cached)
        result["query"] = {"title": query_title, "artist": query_artist, "album": album}
        return result

    # writing
    def save(self, index: int, lyrics: str) -> dict:
        if not lyrics.strip():
            raise ApiError(400, "The lyrics are empty - nothing to save.")
        with self.lock:
            meta = self._meta(index)
            try:
                warnings = core.write_lyrics(meta.path, lyrics)
            except TaggingError as exc:
                raise ApiError(500, str(exc)) from exc
            self.cache.invalidate(meta.path)
        return {"warnings": warnings, "track": self.track(index)}

    def delete(self, index: int, sidecar: bool) -> dict:
        """Delete the embedded lyrics; *sidecar* also deletes a stale .lrc file
        (one kept in sync by settings.write_sidecar_lrc always goes)."""
        with self.lock:
            meta = self._meta(index)
            delete_sidecar = bool(config.get("settings", "write_sidecar_lrc", False)) or (
                sidecar and tagging.stale_sidecar(meta.path) is not None)
            try:
                core.delete_lyrics(meta, self.cache, delete_sidecar)
            except TaggingError as exc:
                raise ApiError(500, str(exc)) from exc
        return {"track": self.track(index)}

    # bulk runs
    def start_job(self, action: str, indices: list, overwrite: bool = False, dry_run: bool = False,
                  romanize: bool | None = None, delete_sidecars: bool = False) -> dict:
        if action not in JOB_LABELS:
            raise ApiError(400, f"Unknown bulk action: {action}")
        with self.lock:
            if self.job is not None and self.job.running:
                raise ApiError(409, "A bulk run is still going; stop it or wait for it to finish.")
            try:
                chosen = sorted({int(i) for i in indices})
            except (TypeError, ValueError) as exc:
                raise ApiError(400, "Tracks must be given by number.") from exc
            if not chosen:
                raise ApiError(400, "Choose at least one track.")
            for i in chosen:
                self._meta(i)
            files = [self.files[i] for i in chosen]
            positions = {self.files[i]: i for i in chosen}
            if action == "fetch":
                backup_root = core.backup_directory() if overwrite and not dry_run else None
                steps = core.batch(files, self.base_dir, self.cache, overwrite, dry_run, backup_root, romanize)
            else:
                backup_root = core.backup_directory()
                sidecars = bool(config.get("settings", "write_sidecar_lrc", False)) or delete_sidecars
                steps = core.bulk(action, files, self.base_dir, self.cache, backup_root, delete_sidecars=sidecars)
            job = Job(action, len(files), positions, backup_root, dry_run and action == "fetch")
            job.thread = threading.Thread(target=job.run, args=(steps,), name="lyrembed-bulk", daemon=True)
            self.job = job
            job.thread.start()
            return job.view()

    def job_view(self, since: int) -> dict | None:
        return self.job.view(since) if self.job is not None else None

    def stop_job(self) -> dict | None:
        if self.job is not None and self.job.running:
            self.job.stop.set()
        return self.job_view(0)

    # settings
    @staticmethod
    def settings() -> dict:
        listed = {item for _, items in SETTING_GROUPS for item in items}
        groups = [{"name": name, "settings": [_setting_json(s, k) for s, k in items]} for name, items in SETTING_GROUPS]
        more = [(s, k) for s in ("settings", "api", "cleaning") for k in config.DEFAULT_CONFIG[s]
                if (s, k) not in listed and (s, k) not in _HIDDEN_SETTINGS]
        if more:
            groups.append({"name": "More", "settings": [_setting_json(s, k) for s, k in more]})
        return {"path": str(config.user_config_path()), "groups": groups}

    @staticmethod
    def save_settings(changes: list) -> dict:
        grouped: dict[str, dict] = {}
        for change in changes:
            if not isinstance(change, dict):
                raise ApiError(400, "Each change needs a section, a key and a value.")
            section, key = str(change.get("section", "")), str(change.get("key", ""))
            if (section, key) in _HIDDEN_SETTINGS:
                raise ApiError(400, f"'{section}.{key}' is not changed here.")
            grouped.setdefault(section, {})[key] = change.get("value")
        try:
            path = config.save_settings(grouped)
        except config.ConfigError as exc:
            raise ApiError(400, str(exc)) from exc
        return {"path": str(path), **WebSession.settings()}

    @staticmethod
    def aliases() -> dict:
        user = config.CONFIG.get("aliases") or []
        return {
            "builtin": [list(group) for group in config.builtin_aliases()],
            "user": [list(group) for group in user if isinstance(group, list)],
            "path": str(config.user_config_path()),
        }

    @staticmethod
    def save_aliases(groups: list) -> dict:
        cleaned = []
        for group in groups if isinstance(groups, list) else []:
            names = [str(n).strip() for n in group if str(n).strip()] if isinstance(group, list) else []
            if len(names) == 1:
                raise ApiError(400, f"'{names[0]}' needs at least one other name to be an alias group.")
            if names:
                cleaned.append(names)
        try:
            config.save_settings({}, aliases=cleaned)
        except config.ConfigError as exc:
            raise ApiError(400, str(exc)) from exc
        return WebSession.aliases()

    @staticmethod
    def inspect(lyrics: str) -> dict:
        """What the editor shows while typing: synced or plain, and the script."""
        return {"synced": text.has_timestamps(lyrics), "script": text.script_label(lyrics) if lyrics.strip() else ""}


# --- HTTP --------------------------------------------------------------------
_TRACK_ROUTE = re.compile(r"^/api/tracks/(\d+)(?:/(search|save|delete))?$")
_AUDIO_ROUTE = re.compile(r"^/api/tracks/(\d+)/audio$")
_COVER_ROUTE = re.compile(r"^/api/tracks/(\d+)/cover$")


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, session: WebSession, token: str, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.session = session
        self.token = token
        self.stop = threading.Event()
        self.last_contact: float | None = None
        self.closing_at: float | None = None

    @property
    def port(self) -> int:
        return self.server_address[1]

    def contact(self) -> None:
        self.last_contact = time.monotonic()
        self.closing_at = None

    def page_gone(self) -> bool:
        """True once the page was closed: it said so and did not come back, or
        it stopped pinging. Before the first visit nothing counts as gone; the
        browser may not have opened, and the address is printed to open it."""
        now = time.monotonic()
        if self.closing_at is not None and now - self.closing_at > CLOSE_GRACE:
            return True
        return self.last_contact is not None and now - self.last_contact > HEARTBEAT_TIMEOUT


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "lyrembed"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - the base class's name
        log.debug("web: " + format, *args)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    # checks
    def _host_ok(self) -> bool:
        # Another site's page could reach 127.0.0.1 through a name it controls
        # (DNS rebinding); the Host header then carries that name.
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}")

    def _cookie_token(self) -> str:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return ""

    def _authorized(self) -> bool:
        return hmac.compare_digest(self._cookie_token().encode(), self.server.token.encode())

    def _check_origin(self) -> None:
        if self.headers.get("Origin", "") != f"http://{self.headers.get('Host', '')}":
            raise ApiError(403, "Requests must come from lyrembed's own page.")

    def _body(self, json_required: bool) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "Request too large.")
        raw = self.rfile.read(length) if length else b""
        if not json_required:
            return {}
        # A form on another site cannot send this type without asking first.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ApiError(415, "Expected JSON.")
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
            raise ApiError(400, f"Invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "Expected a JSON object.")
        return data

    # responses
    def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None,
              cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send_text(self, status: int, message: str) -> None:
        self._send(status, message.encode("utf-8"), "text/plain; charset=utf-8")

    # routing
    def _dispatch(self, method: str) -> None:
        try:
            if not self._host_ok():
                raise ApiError(400, "Unexpected host.")
            url = urlsplit(self.path)
            if method == "GET" and url.path == "/":
                self._index(url.query)
                return
            if not self._authorized():
                raise ApiError(403, "Open lyrembed through the address it printed in the terminal.")
            if method == "GET" and url.path.startswith("/static/"):
                self._static(url.path[len("/static/"):])
                return
            if not url.path.startswith("/api/"):
                raise ApiError(404, "Not found.")
            audio = _AUDIO_ROUTE.match(url.path) if method == "GET" else None
            if audio:
                self.server.contact()
                self._audio(int(audio.group(1)))
                return
            cover = _COVER_ROUTE.match(url.path) if method == "GET" else None
            if cover:
                data, content_type = self.server.session.cover(int(cover.group(1)))
                # The page asks with ?v=<cover id>, so a changed cover is a new address.
                self._send(200, data, content_type, cache="private, max-age=86400")
                return
            if method == "POST":
                self._check_origin()
            body = self._body(json_required=url.path != "/api/closing") if method == "POST" else {}
            self.server.contact()
            self._send_json(200, self._api(method, url.path, parse_qs(url.query), body))
            # Only once the answer is out: stopping first could end the
            # program while the answer is still being written.
            if url.path == "/api/closing":
                self.server.closing_at = time.monotonic()
            elif url.path == "/api/quit":
                self.wfile.flush()
                self.server.stop.set()
        except ApiError as exc:
            if self.path.startswith("/api/"):
                self._send_json(exc.status, {"error": exc.message})
            else:
                self._send_text(exc.status, exc.message)
        except Exception as exc:  # a bug must not kill the server thread silently
            log.exception("web request failed: %s %s", method, self.path)
            self._send_json(500, {"error": f"Internal error: {exc}"})

    def _index(self, query: str) -> None:
        given = (parse_qs(query).get("token") or [""])[0]
        if given and hmac.compare_digest(given.encode(), self.server.token.encode()):
            # The token leaves the address bar (and the history) at once.
            self._send(303, b"", "text/plain", {
                "Location": "/",
                "Set-Cookie": f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/",
            })
            return
        if not self._authorized():
            raise ApiError(403, "Open lyrembed through the address it printed in the terminal.")
        self._static("index.html")

    def _static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            raise ApiError(404, "Not found.")
        content_type = _CONTENT_TYPES.get(target.suffix)
        if content_type is None:
            raise ApiError(404, "Not found.")
        headers = {"Content-Security-Policy": _CSP} if target.suffix == ".html" else None
        self._send(200, target.read_bytes(), content_type, headers)

    def _audio(self, index: int) -> None:
        """Serve a track with byte ranges: without them a browser cannot seek."""
        path, content_type = self.server.session.audio(index)
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        wanted = self.headers.get("Range")
        if wanted:
            match = _RANGE.fullmatch(wanted.strip())
            if not match or not (match.group(1) or match.group(2)):
                raise ApiError(416, "Unsupported range.")
            if match.group(1):
                start = int(match.group(1))
                end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
            else:
                start = max(0, size - int(match.group(2)))
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        remaining = end - start + 1
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                while remaining > 0:
                    chunk = handle.read(min(_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser dropped the request, as it does on every seek

    def _api(self, method: str, path: str, query: dict, body: dict):
        session = self.server.session
        if method == "GET" and path == "/api/library":
            return session.library()
        if method == "GET" and path == "/api/folders":
            return session.folders((query.get("path") or [""])[0])
        if method == "POST" and path == "/api/folder":
            return session.open_folder(str(body.get("path", "")))
        if method == "POST" and path == "/api/inspect":
            return session.inspect(str(body.get("lyrics", "")))
        if method == "GET" and path == "/api/jobs/current":
            since = (query.get("since") or ["0"])[0]
            return {"job": session.job_view(int(since) if since.isdigit() else 0)}
        if method == "POST" and path == "/api/jobs":
            romanize = body.get("romanize")
            return {"job": session.start_job(
                str(body.get("action", "")), body.get("tracks") or [], overwrite=bool(body.get("overwrite")),
                dry_run=bool(body.get("dryRun")), romanize=None if romanize is None else bool(romanize),
                delete_sidecars=bool(body.get("deleteSidecars")))}
        if method == "POST" and path == "/api/jobs/current/stop":
            return {"job": session.stop_job()}
        if method == "GET" and path == "/api/settings":
            return session.settings()
        if method == "POST" and path == "/api/settings":
            return session.save_settings(body.get("changes") or [])
        if method == "GET" and path == "/api/aliases":
            return session.aliases()
        if method == "POST" and path == "/api/aliases":
            return session.save_aliases(body.get("groups"))
        if method == "POST" and path == "/api/romanizers":
            return session.romanizers(str(body.get("lyrics", "")))
        if method == "POST" and path == "/api/romanize":
            return session.romanize(str(body.get("lyrics", "")), str(body.get("key", "")),
                                    str(body.get("hanDefault", "zh")), bool(body.get("keepOriginal")))
        if method == "POST" and path in ("/api/ping", "/api/closing", "/api/quit"):
            return {"ok": True}
        match = _TRACK_ROUTE.match(path)
        if match:
            index, action = int(match.group(1)), match.group(2)
            if method == "GET" and action is None:
                return session.track(index)
            if method == "POST" and action == "search":
                title, artist = body.get("title"), body.get("artist")
                return session.search(index, title=None if title is None else str(title),
                                      artist=None if artist is None else str(artist),
                                      refresh=bool(body.get("refresh")), aliases=bool(body.get("aliases")))
            if method == "POST" and action == "save":
                return session.save(index, str(body.get("lyrics", "")))
            if method == "POST" and action == "delete":
                return session.delete(index, bool(body.get("sidecar")))
        raise ApiError(404, "Not found.")


def serve(session: WebSession, open_browser: bool = True, port: int = 0) -> int:
    """Run the page until it is quit or closed; returns the exit code."""
    token = secrets.token_urlsafe(24)
    server = _Server(session, token, port)
    url = f"http://127.0.0.1:{server.port}/?token={token}"
    thread = threading.Thread(target=server.serve_forever, name="lyrembed-web", daemon=True)
    thread.start()
    try:
        ui.info(f"lyrembed is running at {url}")
        if not (open_browser and webbrowser.open(url)):
            ui.info("Open that address in your browser.")
        ui.info("Quit on the page, or press Ctrl+C here.")
        # Whoever started lyrembed may be reading the address from a pipe.
        sys.stdout.flush()
        while not server.stop.wait(1.0):
            if server.page_gone():
                ui.info("The page was closed - stopping.")
                break
        if session.job is not None and session.job.running:
            ui.info("Finishing the current track of the bulk run...")
    finally:
        server.shutdown()
        server.server_close()
        session.close()
    return 0
