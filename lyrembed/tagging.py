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
# Vorbis comments have no fixed album artist field; these are the spellings
# taggers actually write (MusicBrainz Picard, foobar2000, Mp3tag).
VORBIS_ALBUM_ARTIST_KEYS = ("albumartist", "album artist", "album_artist")

# Album artist values that name no artist: searching for them finds nothing.
_COMPILATION_ARTISTS = {"various artists", "various", "va", "v.a.", "v/a", "verschiedene interpreten"}
MP4_LYRIC_KEY = "\xa9lyr"

# The containers this module can read and write. settings.supported_extensions
# may narrow the scan but cannot add formats.
SUPPORTED_EXTENSIONS = (".flac", ".mp3", ".ogg", ".opus", ".m4a")

# SYLT time stamp format 2 means milliseconds; format 1 (MPEG frames) cannot
# be converted without decoding the stream.
_SYLT_MILLISECONDS = 2


class TaggingError(Exception):
    """Raised when a file cannot be read from or written to."""


class SidecarError(TaggingError):
    """The lyrics were embedded, but the companion .lrc file could not be written.

    A subclass so that callers that only care about failure still catch it,
    while callers that report results can tell a partial success apart.
    """


@dataclass
class TrackMeta:
    path: Path
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    # MusicBrainz recording ID and lyrics language, as written by Picard;
    # language "zxx" means "no lyrics".
    recording_id: str = ""
    language: str = ""
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
    def search_artist(self) -> str:
        """The artist to search for.

        With settings.prefer_album_artist the album artist wins: per-track
        artist tags often list every member or guest ('Group (Member A,
        Member B)'), while providers file the song under the group. A
        compilation's 'Various Artists' is no artist, so the track's own
        artist is used there.
        """
        if (config.get("settings", "prefer_album_artist", False) and self.album_artist
                and self.album_artist.lower() not in _COMPILATION_ARTISTS):
            return self.album_artist
        return self.artist

    @property
    def is_taggable(self) -> bool:
        return bool(self.title and self.search_artist)


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


def _freeform(values) -> str:
    """First value of an MP4 freeform atom, which mutagen returns as bytes."""
    value = values[0] if isinstance(values, list) and values else values
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value) if value else ""


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


def _sylt_to_lrc(frame) -> str:
    """Convert an ID3 SYLT frame (synchronized lyrics) to LRC text."""
    if getattr(frame, "format", None) != _SYLT_MILLISECONDS:
        return ""
    lines = []
    for text, stamp in frame.text:
        minutes, rest = divmod(int(stamp), 60_000)
        lines.append(f"[{minutes:02d}:{rest // 1000:02d}.{rest % 1000 // 10:02d}]{text.strip()}")
    return "\n".join(lines)


def _id3_save_version(tags) -> int:
    """Keep the ID3v2 version a file already has.

    Converting a v2.3 tag to v2.4 just to add lyrics changes how every other
    frame is stored, and some players only read v2.3. The configured version
    applies to files that had no ID3v2 tag.
    """
    existing = getattr(tags, "version", None) or ()
    if len(existing) > 1 and existing[1] in (3, 4):
        return existing[1]
    return 3 if int(config.get("settings", "id3_v2_version", 4)) == 3 else 4


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
# --- cover art -----------------------------------------------------------------
# Front cover in the picture types of FLAC, ID3 and Vorbis pictures.
_FRONT_COVER = 3
# Names a cover file next to the tracks usually has, without the extension.
COVER_FILE_STEMS = ("cover", "folder", "front", "albumart", "album")
_COVER_FILE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def image_type(data: bytes) -> str | None:
    """The MIME type of *data* judged by its first bytes, for the formats a
    page may show; anything else (SVG can carry scripts) is None. A tag's own
    MIME field is often wrong or empty."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _pick_picture(pictures) -> bytes | None:
    """The front cover if there is one, else the first picture."""
    pictures = [p for p in pictures if getattr(p, "data", None)]
    if not pictures:
        return None
    front = [p for p in pictures if getattr(p, "type", None) == _FRONT_COVER]
    return (front or pictures)[0].data


def embedded_cover(path: Path) -> bytes | None:
    """The cover picture stored in the file, or None. Raises TaggingError
    for a file that cannot be read."""
    ext = path.suffix.lower()
    try:
        audio = _open_audio(path)
        if ext == ".flac":
            return _pick_picture(audio.pictures)
        if ext in (".ogg", ".opus"):
            from base64 import b64decode
            from mutagen.flac import Picture

            pictures = []
            for raw in audio.get("metadata_block_picture") or []:
                try:
                    pictures.append(Picture(b64decode(raw)))
                except (ValueError, MutagenError):
                    continue
            return _pick_picture(pictures)
        if ext == ".mp3":
            return _pick_picture(audio.tags.getall("APIC")) if audio.tags else None
        if ext == ".m4a":
            covers = (audio.tags or {}).get("covr") or []
            return bytes(covers[0]) if covers else None
    except (MutagenError, OSError) as exc:
        raise TaggingError(f"Could not read the cover of '{path.name}': {exc}") from exc
    return None


def cover_file(folder: Path) -> Path | None:
    """A cover image next to the tracks (cover.jpg, folder.png, ...)."""
    try:
        candidates = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        return None
    for stem in COVER_FILE_STEMS:
        for extension in _COVER_FILE_EXTENSIONS:
            found = candidates.get(stem + extension)
            if found is not None:
                return found
    return None


def read_metadata(path: Path) -> TrackMeta:
    meta = TrackMeta(path=path)
    ext = path.suffix.lower()
    try:
        audio = _open_audio(path)

        if ext in (".flac", ".ogg", ".opus"):
            meta.title = _first(audio.get("title"))
            meta.artist = _first(audio.get("artist"))
            meta.album = _first(audio.get("album"))
            for key in VORBIS_ALBUM_ARTIST_KEYS:
                value = _first(audio.get(key))
                if value.strip():
                    meta.album_artist = value
                    break
            meta.recording_id = _first(audio.get("musicbrainz_trackid"))
            meta.language = _first(audio.get("language"))
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
            meta.album_artist = _id3_text(tags, "TPE2")
            meta.language = _id3_text(tags, "TLAN")
            ufid = tags.get("UFID:http://musicbrainz.org") if tags else None
            if ufid is not None:
                meta.recording_id = bytes(ufid.data).decode("ascii", "replace")
            meta.track_number = _parse_number(_id3_text(tags, "TRCK"))
            meta.disc_number = _parse_number(_id3_text(tags, "TPOS"))
            if tags:
                for frame in tags.getall("USLT"):
                    if str(frame.text).strip():
                        meta.lyrics = str(frame.text)
                        break
                else:
                    for frame in tags.getall("SYLT"):
                        converted = _sylt_to_lrc(frame)
                        if converted.strip():
                            meta.lyrics = converted
                            break

        elif ext == ".m4a":
            meta.title = _first(audio.get("\xa9nam"))
            meta.artist = _first(audio.get("\xa9ART"))
            meta.album = _first(audio.get("\xa9alb"))
            meta.album_artist = _first(audio.get("aART"))
            meta.recording_id = _freeform(audio.get("----:com.apple.iTunes:MusicBrainz Track Id"))
            meta.language = _freeform(audio.get("----:com.apple.iTunes:LANGUAGE"))
            meta.track_number = _parse_number(audio.get("trkn"))
            meta.disc_number = _parse_number(audio.get("disk"))
            meta.lyrics = _first(audio.get(MP4_LYRIC_KEY))

        info = getattr(audio, "info", None)
        meta.duration = int(info.length) if info and getattr(info, "length", None) else 0

    except TaggingError as exc:
        meta.error = str(exc)
        log.warning("Could not read metadata from %s: %s", path, exc)
    except (MutagenError, OSError, ValueError) as exc:
        meta.error = f"{type(exc).__name__}: {exc}"
        log.warning("Could not read metadata from %s: %s", path, exc)

    meta.title = meta.title.strip()
    meta.artist = meta.artist.strip()
    meta.album = meta.album.strip()
    meta.album_artist = meta.album_artist.strip()
    meta.recording_id = meta.recording_id.strip()
    meta.language = meta.language.strip()
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
            had_tags = audio.tags is not None
            if not had_tags:
                audio.add_tags()
            version = _id3_save_version(audio.tags if had_tags else None)
            # SYLT is replaced too: players prefer it over USLT, so a stale
            # synchronized frame would hide the new lyrics.
            audio.tags.delall("USLT")
            audio.tags.delall("SYLT")
            language = str(config.get("settings", "uslt_language", "eng"))[:3] or "eng"
            audio.tags.add(USLT(encoding=3, lang=language, desc="", text=lyrics))
            audio.save(v2_version=version)

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
            # Only rewrite the file when there is something to remove.
            if audio.tags and (audio.tags.getall("USLT") or audio.tags.getall("SYLT")):
                version = _id3_save_version(audio.tags)
                audio.tags.delall("USLT")
                audio.tags.delall("SYLT")
                audio.save(v2_version=version)

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


def sidecar_path(path: Path) -> Path:
    return path.with_suffix(".lrc")


def stale_sidecar(path: Path) -> Path | None:
    """An existing .lrc next to *path* that this run will not keep up to date.

    Navidrome and Jellyfin often prefer the sidecar over the embedded tag, so
    such a file hides whatever is written into the track.
    """
    if config.get("settings", "write_sidecar_lrc", False):
        return None
    sidecar = sidecar_path(path)
    return sidecar if sidecar.is_file() else None


def write_sidecar(path: Path, lyrics: str) -> Path:
    """Write a companion .lrc file next to the track (Navidrome/Jellyfin read these)."""
    sidecar = sidecar_path(path)
    try:
        sidecar.write_text(lyrics, encoding="utf-8")
    except OSError as exc:
        raise SidecarError(
            f"Lyrics embedded, but the sidecar '{sidecar.name}' could not be written: {exc}"
        ) from exc
    return sidecar


def delete_sidecar(path: Path) -> bool:
    sidecar = sidecar_path(path)
    try:
        if sidecar.is_file():
            sidecar.unlink()
            return True
    except OSError as exc:
        raise TaggingError(f"Could not remove sidecar '{sidecar.name}': {exc}") from exc
    return False
