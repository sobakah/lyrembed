"""Reading and writing lyrics tags across FLAC, MP3, OGG, Opus and M4A."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from mutagen import MutagenError
from mutagen.flac import FLAC
from mutagen.id3 import ID3, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from . import config

log = logging.getLogger(__name__)

VORBIS_LYRIC_KEYS = ("LYRICS", "UNSYNCEDLYRICS", "SYNCEDLYRICS")
MP4_LYRIC_KEY = "\xa9lyr"


class TaggingError(Exception):
    """Raised when a file cannot be read from or written to."""


@dataclass
class TrackMeta:
    path: Path
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: int = 0
    lyrics: str = ""
    track_number: int = 0
    disc_number: int = 0
    error: str = ""

    @property
    def label(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        return self.title or self.path.name

    @property
    def is_taggable(self) -> bool:
        return bool(self.title and self.artist)


# --- helpers -----------------------------------------------------------------
def _open_audio(path: Path):
    ext = path.suffix.lower()
    if ext == ".flac":
        return FLAC(path)
    if ext == ".ogg":
        return OggVorbis(path)
    if ext == ".opus":
        return OggOpus(path)
    if ext == ".mp3":
        return MP3(path, ID3=ID3)
    if ext == ".m4a":
        return MP4(path)
    raise TaggingError(f"Unsupported file type: {ext}")


def _first(values) -> str:
    if isinstance(values, (list, tuple)):
        return str(values[0]) if values else ""
    return str(values) if values is not None else ""


def _id3_text(tags, key: str) -> str:
    """Read an ID3 text frame without the NUL-joined ``str(frame)`` artefact."""
    if not tags:
        return ""
    frame = tags.get(key)
    if frame is None:
        return ""
    texts = getattr(frame, "text", None)
    if texts:
        return str(texts[0])
    return ""


def _parse_number(raw) -> int:
    """Parse '5', '5/12' or (5, 12) into an int."""
    if isinstance(raw, (list, tuple)) and raw:
        raw = raw[0]
    if isinstance(raw, (list, tuple)) and raw:
        raw = raw[0]
    if isinstance(raw, int):
        return raw
    text = str(raw or "").strip()
    if not text:
        return 0
    head = text.split("/")[0].strip()
    return int(head) if head.isdigit() else 0


# --- reading -----------------------------------------------------------------
def read_metadata(path: Path) -> TrackMeta:
    meta = TrackMeta(path=path)
    ext = path.suffix.lower()
    try:
        audio = _open_audio(path)

        if ext in (".flac", ".ogg", ".opus"):
            meta.title = _first(audio.get("title"))
            meta.artist = _first(audio.get("artist"))
            meta.album = _first(audio.get("album"))
            meta.track_number = _parse_number(audio.get("tracknumber"))
            meta.disc_number = _parse_number(audio.get("discnumber"))
            for key in VORBIS_LYRIC_KEYS:
                value = _first(audio.get(key))
                if value.strip():
                    meta.lyrics = value
                    break

        elif ext == ".mp3":
            tags = audio.tags
            meta.title = _id3_text(tags, "TIT2")
            meta.artist = _id3_text(tags, "TPE1")
            meta.album = _id3_text(tags, "TALB")
            meta.track_number = _parse_number(_id3_text(tags, "TRCK"))
            meta.disc_number = _parse_number(_id3_text(tags, "TPOS"))
            if tags:
                for frame in tags.getall("USLT"):
                    if str(frame.text).strip():
                        meta.lyrics = str(frame.text)
                        break

        elif ext == ".m4a":
            meta.title = _first(audio.get("\xa9nam"))
            meta.artist = _first(audio.get("\xa9ART"))
            meta.album = _first(audio.get("\xa9alb"))
            meta.track_number = _parse_number(audio.get("trkn"))
            meta.disc_number = _parse_number(audio.get("disk"))
            meta.lyrics = _first(audio.get(MP4_LYRIC_KEY))

        info = getattr(audio, "info", None)
        meta.duration = int(info.length) if info and getattr(info, "length", None) else 0

    except (MutagenError, OSError, ValueError) as exc:
        meta.error = f"{type(exc).__name__}: {exc}"
        log.warning("Could not read metadata from %s: %s", path, exc)

    meta.title = meta.title.strip()
    meta.artist = meta.artist.strip()
    meta.album = meta.album.strip()
    meta.lyrics = meta.lyrics.strip()
    return meta


# --- metadata cache ----------------------------------------------------------
@dataclass
class MetadataCache:
    """Caches parsed tags keyed by (path, mtime, size).

    The tree view previously re-parsed every file on each redraw; for a
    250-track directory that is 250 mutagen parses per keystroke.
    """

    _store: dict = field(default_factory=dict)

    def _stamp(self, path: Path):
        try:
            stat = path.stat()
            return (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return None

    def get(self, path: Path) -> TrackMeta:
        stamp = self._stamp(path)
        cached = self._store.get(path)
        if cached and stamp is not None and cached[0] == stamp:
            return cached[1]
        meta = read_metadata(path)
        self._store[path] = (stamp, meta)
        return meta

    def invalidate(self, path: Path) -> None:
        self._store.pop(path, None)

    def clear(self) -> None:
        self._store.clear()


# --- writing -----------------------------------------------------------------
def embed_lyrics(path: Path, lyrics: str) -> None:
    """Write *lyrics* into the file's native lyrics container.

    Raises TaggingError instead of letting mutagen/OS errors escape, so the
    interactive session survives a read-only file or a stale network mount.
    """
    ext = path.suffix.lower()
    try:
        audio = _open_audio(path)

        if ext in (".flac", ".ogg", ".opus"):
            # Drop competing fields so players do not pick up stale lyrics.
            for key in VORBIS_LYRIC_KEYS:
                if key in audio:
                    del audio[key]
            audio["LYRICS"] = lyrics
            audio.save()

        elif ext == ".mp3":
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("USLT")
            language = str(config.get("settings", "uslt_language", "eng"))[:3] or "eng"
            audio.tags.add(USLT(encoding=3, lang=language, desc="", text=lyrics))
            version = int(config.get("settings", "id3_v2_version", 4))
            audio.save(v2_version=3 if version == 3 else 4)

        elif ext == ".m4a":
            audio[MP4_LYRIC_KEY] = [lyrics]
            audio.save()

        else:
            raise TaggingError(f"Unsupported file type: {ext}")

    except TaggingError:
        raise
    except (MutagenError, OSError, ValueError) as exc:
        raise TaggingError(f"Could not write lyrics to '{path.name}': {exc}") from exc

    if config.get("settings", "write_sidecar_lrc", False):
        write_sidecar(path, lyrics)


def delete_lyrics(path: Path) -> None:
    ext = path.suffix.lower()
    try:
        audio = _open_audio(path)
        changed = False

        if ext in (".flac", ".ogg", ".opus"):
            for key in VORBIS_LYRIC_KEYS:
                if key in audio:
                    del audio[key]
                    changed = True
            if changed:
                audio.save()

        elif ext == ".mp3":
            if audio.tags:
                audio.tags.delall("USLT")
                version = int(config.get("settings", "id3_v2_version", 4))
                audio.save(v2_version=3 if version == 3 else 4)

        elif ext == ".m4a":
            if MP4_LYRIC_KEY in audio:
                del audio[MP4_LYRIC_KEY]
                audio.save()

        else:
            raise TaggingError(f"Unsupported file type: {ext}")

    except TaggingError:
        raise
    except (MutagenError, OSError, ValueError) as exc:
        raise TaggingError(f"Could not remove lyrics from '{path.name}': {exc}") from exc


def write_sidecar(path: Path, lyrics: str) -> Path:
    """Write a companion .lrc file next to the track (Navidrome/Jellyfin read these)."""
    sidecar = path.with_suffix(".lrc")
    try:
        sidecar.write_text(lyrics, encoding="utf-8")
    except OSError as exc:
        raise TaggingError(f"Could not write sidecar '{sidecar.name}': {exc}") from exc
    return sidecar


def delete_sidecar(path: Path) -> bool:
    sidecar = path.with_suffix(".lrc")
    try:
        if sidecar.is_file():
            sidecar.unlink()
            return True
    except OSError as exc:
        raise TaggingError(f"Could not remove sidecar '{sidecar.name}': {exc}") from exc
    return False
