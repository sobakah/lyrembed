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
import itertools
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

from . import config, core, providers, selfupdate, tagging, text, ui
from .providers import Candidate, CachedSearch, SearchReport
from .tagging import MetadataCache, TaggingError, TrackMeta

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).with_name("static")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    # lyrembed's own icon only: covers never come from here.
    ".svg": "image/svg+xml",
}
# Cookies belong to a host, not a port: two pages on 127.0.0.1 (a second
# lyrembed, lyrembed and cdport handing a tab to each other) would overwrite
# each other's token under one name. The port goes into the name.
COOKIE = "lyrembed_token"


def cookie_name(port: int) -> str:
    return f"{COOKIE}_{port}"
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


# --- importing a CD with cdport -----------------------------------------------
# cdport's lines this reads (cdport/web.py, RUNNING_LINE and FINISHED_LINE):
# its page's address, and the album folder when the run is done.
_CDPORT_PAGE = re.compile(r"cdport is running at "
                          r"(http://(?:127\.0\.0\.1|localhost):\d+/\?token=[A-Za-z0-9_-]+)")
_CDPORT_DONE = re.compile(r"^cdport finished: (.+)$")
# After cdport sends the tab back, lyrembed's page needs a moment to load:
# until then, its "closing" from leaving does not count.
IMPORT_GRACE = 30.0


def find_cdport() -> str | None:
    """The cdport command, if it is installed: on PATH, else in ~/.local/bin,
    where pipx puts it even when PATH lacks that folder."""
    found = shutil.which("cdport")
    if found is None:
        local = Path.home() / ".local" / "bin" / "cdport"
        found = str(local) if local.is_file() else None
    return found


class Import:
    """cdport running for lyrembed's page: started with this page as its
    way back, and read to its end for the album it imported."""

    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.url = ""
        self.album: Path | None = None
        self.ended_at: float | None = None
        self.lines: list[str] = []
        self._found = threading.Event()
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, errors="replace")
        threading.Thread(target=self._read, name="lyrembed-import", daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            line = line.rstrip("\n")
            self.lines = (self.lines + [line])[-20:]
            if not self.url and (page := _CDPORT_PAGE.search(line)):
                self.url = page.group(1)
                self._found.set()
            elif done := _CDPORT_DONE.match(line):
                self.album = Path(done.group(1))
        self.process.wait()
        self.ended_at = time.monotonic()
        self._found.set()

    def wait_for_page(self, timeout: float) -> str:
        self._found.wait(timeout)
        return self.url

    @property
    def running(self) -> bool:
        # Not the process: it can be gone while its last line, the album,
        # still waits to be read, and the page stops asking once this is False.
        return self.ended_at is None


# --- bulk runs -----------------------------------------------------------------
JOB_LABELS = {
    "fetch": "Fetch lyrics automatically",
    "romanize": "Romanize embedded lyrics",
    "sidecars": "Extract embedded lyrics to .lrc files",
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
    ("Updates", [("settings", "check_for_updates")]),
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
        self._converting: dict[tuple[Path, float], threading.Lock] = {}
        self._copy_numbers = itertools.count()
        self._temp: Path | None = None
        self.job: Job | None = None
        # Set when another program's page opened lyrembed (--return-url): its
        # Done button ends lyrembed and sends the tab back there.
        self.return_url = ""
        self.return_name = ""
        # Importing a CD: this page's own address (cdport leads back to it),
        # the cdport found (checked once), the run, the album it brought.
        self.page_url = ""
        self._cdport: tuple[bool, str | None] = (False, None)
        self.importing: Import | None = None
        self.imported = ""
        # Embedded cover per (path, mtime) and cover file per (path, mtime_ns):
        # its id (a digest, so equal covers are recognised) and type; None
        # when there is none. Apart, as a cover file changes without its tracks.
        self._covers: dict[tuple[Path, float], dict | None] = {}
        self._file_covers: dict[tuple[Path, int], dict | None] = {}
        # A newer lyrembed; once it is installed, the page may ask for a
        # restart, and serve() then says where the new process should listen.
        self.updates = Updates()
        self.restart_requested = False
        self.restart_to: tuple[int, str] | None = None

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

    @staticmethod
    def _embedded_cover(path: Path) -> bytes | None:
        try:
            data = tagging.embedded_cover(path)
        except TaggingError:
            return None
        return data if data and tagging.image_type(data) else None

    @staticmethod
    def _file_cover(found: Path) -> bytes | None:
        try:
            if found.stat().st_size <= MAX_COVER_FILE:
                data = found.read_bytes()
                if tagging.image_type(data):
                    return data
        except OSError:
            pass
        return None

    def _cover_data(self, path: Path) -> bytes | None:
        """The cover's bytes: embedded in the file, else an image file next to it."""
        data = self._embedded_cover(path)
        if data is None and (found := tagging.cover_file(path.parent)) is not None:
            data = self._file_cover(found)
        return data

    @staticmethod
    def _cover_entry(data: bytes | None) -> dict | None:
        if data is None:
            return None
        return {"id": hashlib.sha1(data).hexdigest()[:16], "type": tagging.image_type(data)}

    def _cover(self, path: Path) -> dict | None:
        try:
            key = (path, path.stat().st_mtime)
        except OSError:
            return None
        if key not in self._covers:
            self._covers[key] = self._cover_entry(self._embedded_cover(path))
        if self._covers[key] is not None:
            return self._covers[key]
        found = tagging.cover_file(path.parent)
        if found is None:
            return None
        try:
            file_key = (found, found.stat().st_mtime_ns)
        except OSError:
            return None
        if file_key not in self._file_covers:
            self._file_covers[file_key] = self._cover_entry(self._file_cover(found))
        return self._file_covers[file_key]

    def cover(self, index: int) -> tuple[bytes, str]:
        with self.lock:
            path = self._meta(index).path
        data = self._cover_data(path)
        if data is None:
            raise ApiError(404, "This track has no cover.")
        return data, tagging.image_type(data)

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
            # First, so the answer already shows an album cdport just brought.
            imported = self._take_imported()
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
                # The quick romanization's starting point: off unless the
                # setting batch mode follows is on.
                "cdport": bool(self.cdport()) and not self.return_url,
                "importing": bool(self.importing and self.importing.running),
                "imported": imported,
                "returnUrl": self.return_url,
                "returnName": self.return_name or "the previous page",
                "autoRomanize": bool(config.get("settings", "auto_romanize", False)),
                "romanizeBelow": str(config.get("settings", "romanization_layout", "replace")).lower() == "below",
            }

    def open_folder(self, raw: str) -> dict:
        self._enter_folder(raw)
        return self.library()

    def _enter_folder(self, raw: str) -> None:
        path = Path(raw.strip().strip("'\"")).expanduser()
        if not raw.strip():
            raise ApiError(400, "Enter a folder.")
        resolved = path.resolve()
        files, problem = core.check_music_directory(resolved)
        if problem is not None:
            raise ApiError(400, problem.message)
        with self.lock:
            # The run's rows point at tracks by their number in this folder.
            if self.job is not None and self.job.running:
                raise ApiError(409, "A bulk run is still going; stop it or wait for it to finish "
                                    "before opening another folder.")
            self.base_dir, self.files, self.problem = resolved, core.sort_files(files, self.cache), ""

    def folders(self, raw: str) -> dict:
        """Subfolders of *raw* for the folder picker: the browser can't hand out
        paths, so the server lists them."""
        if raw:
            path = Path(raw).expanduser()
        elif self.base_dir is not None:
            path = self.base_dir.parent
        else:
            path = core.music_folder()
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
        if not path.is_file():
            raise ApiError(404, f"'{path.name}' is no longer there; it was moved or deleted.")
        if not _is_alac(path):
            return path, _AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise ApiError(415, self._playback(path)["note"])
        key = (path, path.stat().st_mtime)
        with self.lock:
            copy = self._converted.get(key)
            if copy is not None and copy.is_file():
                return copy, "audio/flac"
            converting = self._converting.setdefault(key, threading.Lock())
        # Outside self.lock, or the whole page waits for ffmpeg; a second
        # request for this track waits here and takes the copy.
        with converting:
            with self.lock:
                copy = self._converted.get(key)
                if copy is not None and copy.is_file():
                    return copy, "audio/flac"
                if self._temp is None:
                    self._temp = Path(tempfile.mkdtemp(prefix="lyrembed-listen-"))
                copy = self._temp / f"{next(self._copy_numbers)}.flac"
            try:
                subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(path),
                                "-map", "0:a:0", "-c:a", "flac", str(copy)],
                               check=True, capture_output=True, timeout=300)
            except (OSError, subprocess.SubprocessError) as exc:
                detail = getattr(exc, "stderr", b"") or b""
                raise ApiError(500, f"Could not convert '{path.name}' for listening: "
                                    f"{detail.decode(errors='replace').strip() or exc}") from exc
            with self.lock:
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
    def auto_romanize(lyrics: str) -> dict:
        """The quick romanization when saving a search result: what batch mode
        does with settings.auto_romanize (detected script, configured layout).
        *blocked* says why it cannot be done; lyrics with nothing to romanize
        come back unchanged, without lyrics."""
        result, reason = core.auto_romanize(lyrics)
        key = text.detect_script(lyrics) if lyrics.strip() else ""
        label = text.ROMANIZERS[key][0] if key in text.ROMANIZERS else ""
        return {
            "lyrics": result,
            "reason": reason,
            "blocked": reason if reason.endswith("not installed") else "",
            "language": label,
            "below": str(config.get("settings", "romanization_layout", "replace")).lower() == "below",
        }

    @staticmethod
    def romanize(lyrics: str, key: str, han_default: str, keep_original: bool) -> dict:
        if key not in text.ROMANIZERS:
            raise ApiError(400, f"Unknown romanization: {key}")
        _, available, package = text.ROMANIZERS[key]
        missing = text.missing_packages(lyrics) if key == "mixed" else [] if available() else [package]
        if missing:
            raise ApiError(400, f"Romanizing these lyrics needs {', '.join(missing)}, which "
                                f"{'is' if len(missing) == 1 else 'are'} not installed.")
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

    def extract(self, index: int) -> dict:
        """Write a track's embedded lyrics to its .lrc file. This is the bulk
        action for one track, so a differing file is backed up the same way."""
        with self.lock:
            meta = self._meta(index)
            if not meta.lyrics:
                raise ApiError(400, "This track has no embedded lyrics to extract.")
            steps = core.bulk("sidecars", [meta.path], self.base_dir, self.cache, core.backup_directory())
            step = next(s for s in steps if isinstance(s, core.Outcome))
            if step.result == "failed":
                raise ApiError(500, step.message)
        name = tagging.sidecar_path(meta.path).name
        if step.result == "skipped":
            message = f"{name} is already up to date."
        elif step.backed_up:
            message = f"Wrote {name}; the previous one was backed up."
        else:
            message = f"Wrote {name}."
        return {"message": message, "track": self.track(index)}

    # importing a CD
    def cdport(self) -> str | None:
        checked, found = self._cdport
        if not checked:
            found = find_cdport()
            self._cdport = (True, found)
        return found

    def start_import(self) -> dict:
        """Start cdport on its own page, which leads back here; returns its
        address. A run still going is joined instead of starting another."""
        with self.lock:
            if self.importing is not None and self.importing.running and self.importing.url:
                return {"url": self.importing.url}
            found = self.cdport()
            if found is None:
                raise ApiError(404, "cdport is not installed.")
            if not self.page_url:
                raise ApiError(409, "This page's address is not known.")
            run = Import([found, "--web", "--no-browser", "--return-url", self.page_url, "--return-name", "lyrembed"])
            self.importing = run
        url = run.wait_for_page(20.0)
        if not url:
            last = "; ".join(run.lines[-3:]) or "no output"
            raise ApiError(500, f"cdport did not open its page: {last}")
        return {"url": url}

    def import_away(self) -> bool:
        """The tab is on cdport's page, or only just came back."""
        run = self.importing
        if run is None:
            return False
        return run.running or (run.ended_at is not None and time.monotonic() - run.ended_at < IMPORT_GRACE)

    def _take_imported(self) -> str:
        """Once cdport is done: open the album it brought, and say so once."""
        run = self.importing
        busy = self.job is not None and self.job.running
        if run is not None and not run.running and run.album is not None and not busy:
            album, run.album = run.album, None
            if album.is_dir():
                try:
                    self._enter_folder(str(album))
                    self.imported = album.name
                except ApiError as exc:
                    log.warning("cannot open the imported album: %s", exc.message)
        imported, self.imported = self.imported, ""
        return imported

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

    # a newer lyrembed
    def request_restart(self) -> dict:
        """Start the newly installed lyrembed in this one's place. Not while a
        bulk run goes on: closing would stop it after its current track."""
        if self.updates.view()["state"] != "installed":
            raise ApiError(409, "No new lyrembed is installed yet.")
        if self.job is not None and self.job.running:
            raise ApiError(409, "A bulk run is still going; restart lyrembed when it is done.")
        self.restart_requested = True
        return {"ok": True}

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

    def save_settings(self, changes: list) -> dict:
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
        self._settings_changed(rebuild_session="api" in grouped)
        return {"path": str(path), **WebSession.settings()}

    def _settings_changed(self, rebuild_session: bool = False) -> None:
        """Cached searches were ranked and matched with the old settings and
        aliases; the HTTP session keeps the retries it was built with."""
        with self.lock:
            self.searches.clear()
        if rebuild_session:
            providers.close_session()

    @staticmethod
    def aliases() -> dict:
        user = config.CONFIG.get("aliases") or []
        return {
            "builtin": [list(group) for group in config.builtin_aliases()],
            "user": [list(group) for group in user if isinstance(group, list)],
            "path": str(config.user_config_path()),
        }

    def save_aliases(self, groups: list) -> dict:
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
        self._settings_changed()
        return WebSession.aliases()

    @staticmethod
    def inspect(lyrics: str) -> dict:
        """What the editor shows while typing: synced or plain, and the script."""
        return {"synced": text.has_timestamps(lyrics), "script": text.script_label(lyrics) if lyrics.strip() else ""}


# --- a newer lyrembed --------------------------------------------------------
def wants_update_check(session) -> bool:
    """Whether the page looks for a newer lyrembed: at every start (in a thread,
    so the start doesn't wait), unless switched off or opened by cdport."""
    return bool(config.get("settings", "check_for_updates", True)) and not session.return_url


class Updates:
    """The check for a newer lyrembed and the update, for the page (as picktag
    does it). git and pipx take seconds to minutes, so both run in a thread
    and the page asks for the state (/api/update) meanwhile.

    States: idle, checking, current, failed (the check), available,
    installing, installed, install-failed. quiet marks the check at the
    start: the page then mentions only a newer version.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._view: dict = {"state": "idle", "current": config.__version__}
        self._how: selfupdate.Install | None = None

    def view(self) -> dict:
        with self.lock:
            return dict(self._view)

    def _set(self, **view) -> None:
        with self.lock:
            self._view = {"current": config.__version__, **view}

    def check(self, quiet: bool = False) -> dict:
        with self.lock:
            if self._view["state"] in ("checking", "installing", "installed"):
                return dict(self._view)
            self._view = {"state": "checking", "current": config.__version__, "quiet": quiet}
        threading.Thread(target=self._check, args=(quiet,), name="lyrembed-update-check", daemon=True).start()
        return self.view()

    def _check(self, quiet: bool) -> None:
        try:
            found = selfupdate.latest()
        except selfupdate.UpdateError as exc:
            self._set(state="failed", quiet=quiet, message=str(exc))
            return
        if not selfupdate.newer(found):
            self._set(state="current", quiet=quiet)
            return
        how = selfupdate.how_installed(found)
        self._how = how
        self._set(state="available", quiet=quiet, version=found, command=how.command,
                  canInstall=how.argv is not None, notes=f"{selfupdate.RELEASES}/tag/{found}")

    def install(self) -> dict:
        with self.lock:
            view, how = self._view, self._how
            if view["state"] not in ("available", "install-failed") or how is None or not how.argv:
                raise ApiError(409, "There is no update lyrembed can install.")
            # A second try forgets the first one's failure.
            view = {k: v for k, v in view.items() if k not in ("message", "code", "log")}
            self._view = {**view, "state": "installing"}
        log_file = selfupdate.state_dir() / "update.log"

        def run() -> None:
            code, tail = selfupdate.install(how.argv, log_file)
            if code == 0:
                self._set(**{**view, "state": "installed"})
            else:
                self._set(**{**view, "state": "install-failed", "message": tail or f"pipx ended with code {code}",
                             "code": code, "log": str(log_file)})
        threading.Thread(target=run, name="lyrembed-update", daemon=True).start()
        return self.view()


# --- HTTP --------------------------------------------------------------------
_TRACK_ROUTE = re.compile(r"^/api/tracks/(\d+)(?:/(search|save|delete|extract))?$")
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
            if name == cookie_name(self.server.port):
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
            elif url.path == "/api/quit" or (url.path == "/api/update/restart"
                                              and self.server.session.restart_requested):
                self.wfile.flush()
                self.server.stop.set()
        except (BrokenPipeError, ConnectionResetError):
            return  # the page went away mid-answer (a closed tab, a reload)
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
                "Set-Cookie": f"{cookie_name(self.server.port)}={self.server.token}; HttpOnly; SameSite=Strict; Path=/",
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
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ApiError(404, f"'{path.name}' cannot be read: {exc.strerror}") from exc
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
        if method == "POST" and path == "/api/auto-romanize":
            return session.auto_romanize(str(body.get("lyrics", "")))
        if method == "POST" and path == "/api/romanizers":
            return session.romanizers(str(body.get("lyrics", "")))
        if method == "POST" and path == "/api/import":
            return session.start_import()
        if method == "POST" and path == "/api/romanize":
            return session.romanize(str(body.get("lyrics", "")), str(body.get("key", "")),
                                    str(body.get("hanDefault", "zh")), bool(body.get("keepOriginal")))
        if method == "GET" and path == "/api/update":
            return session.updates.view()
        if method == "POST" and path == "/api/update/check":
            return session.updates.check()
        if method == "POST" and path == "/api/update/install":
            return session.updates.install()
        if method == "POST" and path == "/api/update/restart":
            return session.request_restart()
        if method == "POST" and path == "/api/ping":
            # The page learns about a newer lyrembed found meanwhile.
            return {"ok": True, "update": session.updates.view()}
        if method == "POST" and path in ("/api/closing", "/api/quit"):
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
            if method == "POST" and action == "extract":
                return session.extract(index)
        raise ApiError(404, "Not found.")


# The page's address, the first line serve prints: read by cdport and by
# dbus_service, keep the wording.
RUNNING_LINE = "lyrembed is running at {url}"


def serve(session: WebSession, open_browser: bool = True, port: int = 0, token: str = "",
          unvisited: float = 0) -> int:
    """Run the page until it is quit or closed; returns the exit code.

    port and token come from the lyrembed this one replaced after an update
    (cli.RESTART_ENV): the same address and cookie, so its open tab only
    needs to reload. When the port is taken meanwhile, a new one is printed.
    unvisited: stop when nobody opened the page for that many seconds (0:
    wait for ever). Pages started over D-Bus use it: a caller that never
    opened the address would otherwise keep lyrembed, and the service that
    started it, running until logout.
    """
    restarted = bool(token)
    began = time.monotonic()
    token = token or secrets.token_urlsafe(24)
    try:
        server = _Server(session, token, port)
    except OSError as exc:
        if not restarted:
            raise
        ui.warn(f"The page's port {port} is no longer free ({exc.strerror or exc}); it has a new address.")
        token = secrets.token_urlsafe(24)
        server = _Server(session, token, 0)
        open_browser = True
    url = f"http://127.0.0.1:{server.port}/?token={token}"
    # Where cdport's page sends the tab back; the cookie is already set.
    session.page_url = f"http://127.0.0.1:{server.port}/"
    thread = threading.Thread(target=server.serve_forever, name="lyrembed-web", daemon=True)
    thread.start()
    try:
        # The first line's wording is read by cdport (web._HANDOFF there) and
        # by dbus_service (_PAGE).
        ui.info(RUNNING_LINE.format(url=url))
        opened = open_browser and webbrowser.open(url)
        if session.return_url:
            ui.info(f"Its Done button ends lyrembed and goes back to {session.return_name or 'the previous page'}.")
        else:
            if restarted and not open_browser:
                ui.info("The page that was open reloads by itself.")
            elif not opened:
                ui.info("Open that address in your browser.")
            ui.info("Quit on the page, or press Ctrl+C here.")
        # Whoever started lyrembed may be reading the address from a pipe.
        sys.stdout.flush()
        if wants_update_check(session):
            session.updates.check(quiet=True)
        while not server.stop.wait(1.0):
            if server.page_gone() and not session.import_away():
                ui.info("The page was closed - stopping.")
                break
            if unvisited and server.last_contact is None and time.monotonic() - began > unvisited:
                ui.info(f"Nobody opened the page in {unvisited:.0f} s - stopping.")
                break
        if session.restart_requested:
            session.restart_to = (server.port, token)
        if session.job is not None and session.job.running:
            ui.info("Finishing the current track of the bulk run...")
    finally:
        server.shutdown()
        server.server_close()
        session.close()
    return 0
