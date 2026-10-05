"""Lyrics providers: LRCLIB, NetEase Cloud Music and the syncedlyrics engine.

All network work runs on a shared, pooled requests.Session and is fanned out
across a thread pool, so a track lookup costs roughly one round-trip time
instead of the sum of up to thirty sequential requests.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover
    from requests.packages.urllib3.util.retry import Retry  # type: ignore

from . import config
from .text import (
    clean_tag,
    get_artist_aliases,
    has_timestamps,
    is_credit_line,
    script_label,
    matches_artist,
)

log = logging.getLogger(__name__)

try:
    import syncedlyrics

    logging.getLogger("syncedlyrics").setLevel(logging.WARNING)
    HAS_SYNCEDLYRICS = True
except ImportError:
    HAS_SYNCEDLYRICS = False

NETEASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Referer": "https://music.163.com/",
}

_SESSION: requests.Session | None = None


def get_session() -> requests.Session:
    """Pooled session with retry/backoff; avoids a TLS handshake per request."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    session = requests.Session()
    session.headers.update({"User-Agent": str(config.get("api", "user_agent"))})
    retry = Retry(
        total=int(config.get("api", "retry_total", 2)),
        backoff_factor=float(config.get("api", "retry_backoff", 0.3)),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    pool = max(8, int(config.get("api", "max_workers", 8)) * 2)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=pool, pool_maxsize=pool)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    _SESSION = session
    return session


def close_session() -> None:
    global _SESSION
    if _SESSION is not None:
        _SESSION.close()
        _SESSION = None


@dataclass
class Candidate:
    provider: str
    artist: str
    title: str
    album: str
    text: str
    synced: bool = False
    latin: bool = True
    # Several Asian scripts share the lyrics (Korean with Japanese, say).
    mixed: bool = False
    duration: int = 0
    diff: int = 0
    has_duration: bool = False
    exact: bool = False
    match_artist: bool = True
    track_duration: int = 0
    # The provider returns lyrics without saying which recording they belong
    # to (syncedlyrics), so neither artist nor title could be checked.
    unverified: bool = False

    @property
    def duration_checked(self) -> bool:
        """True only when both lengths are known, so ``diff`` means something."""
        return self.has_duration and self.track_duration > 0

    @classmethod
    def build(cls, *, provider, artist, title, album, text, duration=0, track_duration=0,
              exact=False, unverified=False):
        text = text.strip()
        script = script_label(text)
        has_duration = bool(duration)
        diff = abs(duration - track_duration) if (duration and track_duration) else 0
        return cls(
            provider=provider,
            artist=artist or "",
            title=title or "",
            album=album or "N/A",
            text=text,
            synced=has_timestamps(text),
            latin=script == "latin",
            mixed=script == "mixed",
            duration=duration,
            diff=diff,
            has_duration=has_duration,
            exact=exact,
            track_duration=track_duration or 0,
            unverified=unverified,
        )


@dataclass
class SearchReport:
    candidates: list[Candidate] = field(default_factory=list)
    errors: dict[str, list[str]] = field(default_factory=dict)
    # The providers run in parallel threads and all report into one instance.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add_error(self, provider: str, message: str) -> None:
        with self._lock:
            bucket = self.errors.setdefault(provider, [])
            if message not in bucket:
                bucket.append(message)

    @property
    def has_errors(self) -> bool:
        return any(self.errors.values())


def _timeout() -> float:
    return float(config.get("api", "timeout_seconds", 6))


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout):
        return "Timeout"
    if isinstance(exc, requests.ConnectionError):
        return "Connection failed"
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        if response is not None:
            return f"HTTP {response.status_code} ({response.reason or 'error'})"
        return str(exc) or "HTTP error"
    return type(exc).__name__


# --- LRCLIB ------------------------------------------------------------------
def _lrclib_queries(title: str, artist: str, album: str, duration: int,
                    quick: bool = False) -> list[tuple[str, dict]]:
    """Build a deduplicated list of (endpoint, params) pairs.

    *quick* keeps only the canonical ``/get`` lookups. Batch mode tries those
    first: they answer most tracks with one or two requests, where the full
    fan-out costs a dozen per track against a free community service.
    """
    queries: list[tuple[str, dict]] = []
    seen: set[tuple] = set()

    def add(endpoint: str, params: dict) -> None:
        params = {k: v for k, v in params.items() if v}
        key = (endpoint, tuple(sorted(params.items())))
        if key not in seen:
            seen.add(key)
            queries.append((endpoint, params))

    if artist:
        # /get returns the canonical match and prefers synced lyrics server-side.
        if duration:
            add("get", {"track_name": title, "artist_name": artist, "album_name": album, "duration": duration})
        add("get", {"track_name": title, "artist_name": artist, "album_name": album})
        if quick:
            return queries
        add("search", {"track_name": title, "artist_name": artist, "album_name": album})
        add("search", {"track_name": title, "artist_name": artist})
        add("search", {"q": f"{artist} {title}"})
        add("search", {"q": f"{artist} {title} Romanized"})
        for alias in get_artist_aliases(artist):
            add("search", {"track_name": title, "artist_name": alias})
            add("search", {"q": f"{alias} {title}"})
    elif not quick:
        add("search", {"track_name": title})
        add("search", {"q": title})

    return queries


def _cleaned(raw: str) -> str:
    """clean_tag, but never empty: a title written entirely in a bracketed
    localized form ('[自由]') would otherwise turn into an empty query."""
    return clean_tag(raw) or (raw or "").strip()


def query_lrclib(title: str, artist: str, album: str, duration: int, report: SearchReport,
                 quick: bool = False) -> list[Candidate]:
    if not config.get("api", "enable_lrclib", True):
        return []

    clean_t, clean_a, clean_alb = _cleaned(title), _cleaned(artist), clean_tag(album)
    if not clean_t:
        return []
    base_url = str(config.get("api", "lrclib_url")).rstrip("/")
    session = get_session()
    queries = _lrclib_queries(clean_t, clean_a, clean_alb, duration, quick=quick)
    workers = min(len(queries), int(config.get("api", "max_workers", 8)))
    if workers <= 0:
        return []

    def fetch(job):
        endpoint, params = job
        response = session.get(f"{base_url}/{endpoint}", params=params, timeout=_timeout())
        if response.status_code == 404:
            return endpoint, []
        if response.status_code != 200:
            reason = response.reason or "Unexpected status"
            raise requests.HTTPError(f"HTTP {response.status_code} ({reason})")
        payload = response.json()
        if isinstance(payload, dict):
            return endpoint, [payload]
        return endpoint, payload if isinstance(payload, list) else []

    items: list[tuple[str, dict]] = []
    seen_ids: dict = {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, job): job for job in queries}
        for future in as_completed(futures):
            try:
                endpoint, payload = future.result()
            except (requests.RequestException, ValueError) as exc:
                report.add_error("LRCLIB", _describe_error(exc) if isinstance(exc, requests.RequestException) else str(exc))
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if item_id is None:
                    items.append((endpoint, item))
                    continue
                if item_id in seen_ids:
                    # The same record often comes back from /get and /search at
                    # once. Since the queries run concurrently, keeping the
                    # first endpoint to arrive would flag a record as exact or
                    # not at random, so exactness is merged across responses.
                    if endpoint == "get":
                        position = seen_ids[item_id]
                        items[position] = ("get", items[position][1])
                    continue
                seen_ids[item_id] = len(items)
                items.append((endpoint, item))

    candidates = []
    for endpoint, item in items:
        text = item.get("syncedLyrics") or item.get("plainLyrics") or ""
        if not text.strip():
            continue
        raw_duration = item.get("duration")
        candidates.append(
            Candidate.build(
                provider="LRCLIB",
                artist=item.get("artistName") or "",
                title=item.get("trackName") or clean_t,
                album=item.get("albumName") or "N/A",
                text=text,
                duration=int(raw_duration) if isinstance(raw_duration, (int, float)) else 0,
                track_duration=duration,
                exact=(endpoint == "get"),
            )
        )
    return candidates


# --- NetEase -----------------------------------------------------------------
def query_netease(title: str, artist: str, duration: int, report: SearchReport) -> list[Candidate]:
    if not config.get("api", "enable_netease", True):
        return []

    clean_t, clean_a = _cleaned(title), _cleaned(artist)
    search_url = str(config.get("api", "netease_search_url"))
    lyric_url = str(config.get("api", "netease_lyric_url"))
    limit = int(config.get("api", "netease_search_limit", 6))
    session = get_session()

    terms = []
    if clean_a:
        terms.append(f"{clean_a} {clean_t}".strip())
        terms += [f"{alias} {clean_t}".strip() for alias in get_artist_aliases(clean_a)]
    else:
        terms.append(clean_t)
    terms = [t for t in dict.fromkeys(terms) if t]

    def search(term):
        params = {"s": term, "type": 1, "offset": 0, "limit": limit}
        response = session.get(search_url, params=params, headers=NETEASE_HEADERS, timeout=_timeout())
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Unexpected response format")
        result = payload.get("result")
        if result is None:
            return []
        if not isinstance(result, dict):
            # The /search/get/web endpoint started returning an encrypted
            # string; staying silent made the provider look merely empty.
            raise ValueError("Unexpected response format (endpoint changed?)")
        songs = result.get("songs")
        return [s for s in songs if isinstance(s, dict)] if isinstance(songs, list) else []

    songs: list[dict] = []
    seen_ids: set = set()
    workers = max(1, min(len(terms), int(config.get("api", "max_workers", 8))))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed({pool.submit(search, term) for term in terms}):
            try:
                found = future.result()
            except (requests.RequestException, ValueError) as exc:
                report.add_error("NetEase", _describe_error(exc) if isinstance(exc, requests.RequestException) else str(exc))
                continue
            for song in found:
                song_id = song.get("id")
                if song_id and song_id not in seen_ids:
                    seen_ids.add(song_id)
                    songs.append(song)

    if not songs:
        return []

    def fetch_lyric(song):
        # rv=-1 also asks for "romalrc", the romanization NetEase users submit.
        params = {"id": song["id"], "lv": -1, "kv": -1, "tv": -1, "rv": -1}
        response = session.get(lyric_url, params=params, headers=NETEASE_HEADERS, timeout=_timeout())
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return song, "", ""

        def lyric_of(key: str) -> str:
            block = payload.get(key)
            text = block.get("lyric") if isinstance(block, dict) else ""
            return text if isinstance(text, str) else ""

        return song, strip_credit_lines(lyric_of("lrc")), lyric_of("romalrc")

    candidates = []
    workers = max(1, min(len(songs), int(config.get("api", "max_workers", 8))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed({pool.submit(fetch_lyric, song) for song in songs}):
            try:
                song, lyric, romanized = future.result()
            except (requests.RequestException, ValueError) as exc:
                report.add_error("NetEase", _describe_error(exc) if isinstance(exc, requests.RequestException) else "Invalid response")
                continue
            if not lyric.strip():
                continue

            artists = song.get("artists")
            artist_names = (
                ", ".join(a.get("name", "") for a in artists if isinstance(a, dict))
                if isinstance(artists, list)
                else ""
            )
            album_obj = song.get("album")
            album_name = album_obj.get("name", "NetEase") if isinstance(album_obj, dict) else "NetEase"
            raw_duration = song.get("duration", 0)
            common = dict(
                provider="NetEase",
                artist=artist_names,
                title=song.get("name") or clean_t,
                duration=int(raw_duration / 1000) if isinstance(raw_duration, (int, float)) else 0,
                track_duration=duration,
            )
            candidates.append(Candidate.build(album=album_name, text=lyric, **common))
            if has_timestamps(romanized):
                candidates.append(
                    Candidate.build(album=f"{album_name} · user romanization", text=romanized, **common)
                )
    return candidates


def strip_credit_lines(lyrics: str) -> str:
    """Drop provider credit headers ('作词 : …') - metadata, not lyrics."""
    return "\n".join(line for line in lyrics.splitlines() if not is_credit_line(line))


# --- syncedlyrics ------------------------------------------------------------
def query_syncedlyrics(title: str, artist: str, report: SearchReport) -> list[Candidate]:
    if not (HAS_SYNCEDLYRICS and config.get("api", "enable_syncedlyrics", True)):
        return []

    clean_t, clean_a = _cleaned(title), _cleaned(artist)
    queries = [f"{clean_a} - {clean_t}"] if clean_a else [clean_t]
    queries += [f"{alias} - {clean_t}" for alias in get_artist_aliases(clean_a)]
    queries = [q for q in dict.fromkeys(queries) if q.strip()]

    def search(query: str) -> str:
        try:
            return syncedlyrics.search(query, allow_plain_format=True) or ""
        except TypeError:
            # Parameter was renamed across syncedlyrics releases.
            try:
                return syncedlyrics.search(query, plain_only=False) or ""
            except TypeError:
                return syncedlyrics.search(query) or ""

    texts: list[str] = []
    workers = max(1, min(len(queries), int(config.get("api", "max_workers", 8))))
    # syncedlyrics has no timeout of its own and walks its scrapers one after
    # another, so the wait is capped here. The worker threads cannot be killed;
    # they finish in the background and their results are dropped.
    limit = float(config.get("api", "syncedlyrics_timeout_seconds", 15))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        for future in as_completed({pool.submit(search, q) for q in queries}, timeout=limit):
            try:
                text = future.result()
            except Exception as exc:  # third-party scrapers raise freely
                report.add_error("syncedlyrics", type(exc).__name__)
                continue
            if text and text.strip():
                texts.append(text.strip())
    except FutureTimeout:
        report.add_error("syncedlyrics", f"Timeout after {limit:g}s")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # The engine returns bare text: which recording it belongs to is unknown,
    # so the result must not borrow the searched artist and pass as a match.
    return [
        Candidate.build(
            provider="MULTI",
            artist="",
            title=clean_t,
            album="Web/Multi",
            text=text,
            unverified=True,
        )
        for text in dict.fromkeys(texts)
    ]


# --- aggregation -------------------------------------------------------------
def rank_key(candidate: Candidate):
    """Sort order: exact match, synced, script preference, duration confidence."""
    prefer_latin = bool(config.get("settings", "prefer_latin", True))
    if prefer_latin:
        script_rank = 0 if candidate.latin else 1
    else:
        script_rank = 0 if not candidate.latin else 1
    # Candidates without a known duration must not outrank duration-verified ones.
    duration_rank = 0 if candidate.has_duration else 1
    return (
        0 if candidate.exact else 1,
        0 if candidate.synced else 1,
        1 if candidate.unverified else 0,
        script_rank,
        duration_rank,
        candidate.diff,
    )


@dataclass
class CachedSearch:
    """A finished search plus the entry that was highlighted when leaving it."""

    report: SearchReport
    highlight: int | None = None


class SearchCache:
    """Remembers searches for the duration of the session.

    Walking back to a track that was already looked at should not cost another
    round of provider queries. Entries are keyed by the query actually sent, so
    an adjusted query gets its own slot, and evicted least-recently-used once
    the cap is reached.
    """

    def __init__(self, max_entries: int = 64) -> None:
        self.max_entries = max(1, int(max_entries))
        self._store: OrderedDict[tuple, CachedSearch] = OrderedDict()

    @staticmethod
    def key(title: str, artist: str, album: str, duration: int) -> tuple:
        return (
            clean_tag(title).lower(),
            clean_tag(artist).lower(),
            clean_tag(album).lower(),
            int(duration or 0),
        )

    def get(self, key: tuple) -> CachedSearch | None:
        entry = self._store.get(key)
        if entry is not None:
            self._store.move_to_end(key)
        return entry

    def set(self, key: tuple, entry: CachedSearch) -> CachedSearch:
        self._store[key] = entry
        self._store.move_to_end(key)
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)
        return entry

    def discard(self, key: tuple) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


def collect_candidates(title: str, artist: str, album: str, duration: int,
                       quick: bool = False) -> SearchReport:
    """Query every enabled provider concurrently and return ranked candidates.

    *quick* asks LRCLIB's canonical ``/get`` endpoint only; see
    ``_lrclib_queries``. Ctrl+C returns at once instead of waiting for the
    requests in flight; they finish in the background and are discarded.
    """
    report = SearchReport()
    jobs = {"LRCLIB": lambda: query_lrclib(title, artist, album, duration, report, quick=quick)}
    if not quick:
        jobs["NetEase"] = lambda: query_netease(title, artist, duration, report)
        jobs["syncedlyrics"] = lambda: query_syncedlyrics(title, artist, report)

    collected: list[Candidate] = []
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    try:
        futures = {pool.submit(job): name for name, job in jobs.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                collected.extend(future.result())
            except Exception as exc:  # a broken provider must not kill the run
                log.exception("Provider %s failed", name)
                report.add_error(name, type(exc).__name__)
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)

    clean_a = clean_tag(artist)
    seen_texts: set[str] = set()
    unique: list[Candidate] = []
    for candidate in collected:
        fingerprint = " ".join(candidate.text.split())
        if fingerprint in seen_texts:
            continue
        seen_texts.add(fingerprint)
        candidate.match_artist = matches_artist(clean_a, candidate.artist)
        unique.append(candidate)

    matching = [c for c in unique if c.match_artist]
    report.candidates = sorted(matching or unique, key=rank_key)
    return report


def pick_automatic(report: SearchReport, duration: int) -> Candidate | None:
    """Return a candidate confident enough for unattended batch tagging."""
    tolerance = int(config.get("api", "duration_tolerance_seconds", 2))
    for candidate in report.candidates:
        if not candidate.synced or not candidate.match_artist or candidate.unverified:
            continue
        if duration and candidate.has_duration and candidate.diff > tolerance:
            continue
        if duration and not candidate.has_duration and not candidate.exact:
            continue
        return candidate
    return None
