"""Lyrics providers: LRCLIB, NetEase Cloud Music and the syncedlyrics engine.

All network work runs on a shared, pooled requests.Session and is fanned out
across a thread pool, so a track lookup costs roughly one round-trip time
instead of the sum of up to thirty sequential requests.
"""

from __future__ import annotations

import logging
import threading
import time
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
    instrumental_marker,
    is_credit_line,
    script_label,
    matches_artist,
    matches_title,
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
    match_title: bool = True
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
    # Results whose artist or title does not fit the track. Kept apart instead
    # of dropped: a translated title can still be the right song.
    unrelated: list[Candidate] = field(default_factory=list)
    errors: dict[str, list[str]] = field(default_factory=dict)
    # Providers that mark the searched track as instrumental, with details.
    instrumental: dict[str, str] = field(default_factory=dict)
    # Whether the artist's aliases were searched too, and whether that
    # happened on its own because the first round found too little.
    aliases_searched: bool = False
    alias_fallback: bool = False
    # Whether the syncedlyrics scrapers ran because the first round fell short.
    scraper_fallback: bool = False
    # The providers run in parallel threads and all report into one instance.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add_error(self, provider: str, message: str) -> None:
        with self._lock:
            bucket = self.errors.setdefault(provider, [])
            if message not in bucket:
                bucket.append(message)

    def mark_instrumental(self, provider: str, detail: str) -> None:
        with self._lock:
            self.instrumental.setdefault(provider, detail)

    @property
    def has_errors(self) -> bool:
        return any(self.errors.values())

    def instrumental_summary(self) -> str:
        return ", ".join(f"{p} ({d})" if d else p for p, d in self.instrumental.items())


def _timeout() -> float:
    return float(config.get("api", "timeout_seconds", 6))


def _tolerance() -> int:
    return int(config.get("api", "duration_tolerance_seconds", 2))


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
def _artist_names(artist: str, aliases: str) -> list[str]:
    """The artist names to search with: the tag's ("none"), only its aliases
    ("only", the second round) or both ("all")."""
    if not artist:
        return []
    own = [] if aliases == "only" else [artist]
    return own + (get_artist_aliases(artist) if aliases in ("only", "all") else [])


def _lrclib_queries(title: str, artist: str, album: str, duration: int,
                    quick: bool = False, aliases: str = "none") -> list[tuple[str, dict]]:
    """Build a deduplicated list of (endpoint, params) pairs.

    *quick* keeps only the canonical ``/get`` lookups. Batch mode tries those
    first: they answer most tracks with one or two requests, where the full
    fan-out costs a dozen per track against a free community service.
    *aliases* is "none", "only" or "all"; see ``_artist_names``.
    """
    queries: list[tuple[str, dict]] = []
    seen: set[tuple] = set()

    def add(endpoint: str, params: dict) -> None:
        params = {k: v for k, v in params.items() if v}
        key = (endpoint, tuple(sorted(params.items())))
        if key not in seen:
            seen.add(key)
            queries.append((endpoint, params))

    if artist and aliases != "only":
        # /get returns the canonical match and prefers synced lyrics server-side.
        # With a duration it only matches within 2 s; query_lrclib asks again
        # without one only when that finds nothing, rather than every time.
        add("get", {"track_name": title, "artist_name": artist, "album_name": album, "duration": duration})
        if quick:
            return queries
        # Each search finds uploads the others miss (measured on eight tracks:
        # with album 39 new lyrics, without 8, free text 21). A "<title>
        # Romanized" query found none and was dropped.
        add("search", {"track_name": title, "artist_name": artist, "album_name": album})
        add("search", {"track_name": title, "artist_name": artist})
        add("search", {"q": f"{artist} {title}"})
    elif not artist and not quick and aliases != "only":
        add("search", {"track_name": title})
        add("search", {"q": title})
    if artist and not quick and aliases in ("only", "all"):
        for alias in get_artist_aliases(artist):
            add("search", {"track_name": title, "artist_name": alias})
            add("search", {"q": f"{alias} {title}"})

    return queries


def _cleaned(raw: str) -> str:
    """clean_tag, but never empty: a title written entirely in a bracketed
    localized form ('[自由]') would otherwise turn into an empty query."""
    return clean_tag(raw) or (raw or "").strip()


def _same_version(searched: str, record_title: str) -> bool:
    """Whether a provider record's instrumental flag can speak for the searched
    track. matches_title ignores version notes, so '1&Only (Inst.)' would match
    '1&Only' and mark the sung track instrumental when an album has both."""
    if (instrumental_marker(searched) is None) != (instrumental_marker(record_title) is None):
        return False
    return matches_title(searched, record_title)


def query_lrclib(title: str, artist: str, album: str, duration: int, report: SearchReport,
                 quick: bool = False, aliases: str = "none") -> list[Candidate]:
    if not config.get("api", "enable_lrclib", True):
        return []

    clean_t, clean_a, clean_alb = _cleaned(title), _cleaned(artist), clean_tag(album)
    if not clean_t:
        return []
    base_url = str(config.get("api", "lrclib_url")).rstrip("/")
    session = get_session()
    queries = _lrclib_queries(clean_t, clean_a, clean_alb, duration, quick=quick, aliases=aliases)
    workers = min(len(queries), int(config.get("api", "max_workers", 8)))
    if workers <= 0:
        return []

    def fetch(job):
        endpoint, params = job
        response = session.get(f"{base_url}/{endpoint}", params=params, timeout=_timeout())
        if response.status_code == 404 and endpoint == "get" and "duration" in params:
            params = {k: v for k, v in params.items() if k != "duration"}
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
        if item.get("instrumental") is True:
            # Only a record for this very track says anything about it; a
            # fuzzy /search hit may be another song's instrumental.
            if endpoint == "get":
                report.mark_instrumental("LRCLIB", "exact match")
            elif (_same_version(clean_t, item.get("trackName") or "")
                  and matches_artist(clean_a, item.get("artistName") or "")):
                report.mark_instrumental("LRCLIB", item.get("trackName") or "")
            continue
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
# NetEase throttles quickly: after a burst it answers every search with popular
# songs. Lyrics are fetched for at most this many of the best-fitting songs.
_NETEASE_LYRIC_FETCHES = 3


def query_netease(title: str, artist: str, duration: int, report: SearchReport,
                  aliases: str = "none") -> list[Candidate]:
    if not config.get("api", "enable_netease", True):
        return []

    clean_t, clean_a = _cleaned(title), _cleaned(artist)
    search_url = str(config.get("api", "netease_search_url"))
    lyric_url = str(config.get("api", "netease_lyric_url"))
    limit = int(config.get("api", "netease_search_limit", 6))
    session = get_session()

    if clean_a:
        terms = [f"{name} {clean_t}".strip() for name in _artist_names(clean_a, aliases)]
    else:
        terms = [] if aliases == "only" else [clean_t]
    terms = [t for t in dict.fromkeys(terms) if t]
    if not terms:
        return []

    def search(term):
        params = {"s": term, "type": 1, "offset": 0, "limit": limit}
        response = session.get(search_url, params=params, headers=NETEASE_HEADERS, timeout=_timeout())
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Unexpected response format")
        if payload.get("code") not in (None, 200):
            # -462: NetEase wants a verified (phone-bound) account - its way
            # of turning away a client that sent too many requests.
            raise ValueError(f"refused the search (code {payload.get('code')}) - rate-limited? "
                             f"Press R to try again later")
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
    # One search after another: a parallel burst is what triggers throttling.
    for term in terms:
        try:
            found = search(term)
        except (requests.RequestException, ValueError) as exc:
            report.add_error("NetEase", _describe_error(exc) if isinstance(exc, requests.RequestException) else str(exc))
            continue
        for song in found:
            song_id = song.get("id")
            if song_id and song_id not in seen_ids:
                seen_ids.add(song_id)
                songs.append(song)

    def artist_names_of(song) -> str:
        artists = song.get("artists")
        if not isinstance(artists, list):
            return ""
        return ", ".join(a.get("name", "") for a in artists if isinstance(a, dict))

    def seconds_of(song) -> int:
        raw = song.get("duration", 0)
        return int(raw / 1000) if isinstance(raw, (int, float)) else 0

    def fit(song) -> int:
        # NetEase pads a search without good hits with popular songs. Their
        # lyrics are not worth a request each: only songs whose title, artist
        # or length fits are fetched, the best-fitting first.
        close = bool(duration and seconds_of(song) and abs(seconds_of(song) - duration) <= _tolerance())
        return (2 * matches_title(clean_t, song.get("name") or "")
                + 2 * bool(clean_a and matches_artist(clean_a, artist_names_of(song))) + close)

    plausible = sorted((song for song in songs if fit(song)), key=fit, reverse=True)
    if songs and not plausible and clean_a:
        report.add_error("NetEase", "returned only unrelated songs - it does so when rate-limiting, "
                                    "or when it does not know the song. Press R to try again later")
    songs = plausible[:_NETEASE_LYRIC_FETCHES]
    if not songs:
        return []

    def fetch_lyric(song):
        # rv=-1 also asks for "romalrc", the romanization NetEase users submit.
        params = {"id": song["id"], "lv": -1, "kv": -1, "tv": -1, "rv": -1}
        response = session.get(lyric_url, params=params, headers=NETEASE_HEADERS, timeout=_timeout())
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return song, "", "", False

        def lyric_of(key: str) -> str:
            block = payload.get(key)
            text = block.get("lyric") if isinstance(block, dict) else ""
            return text if isinstance(text, str) else ""

        # pureMusic: NetEase's instrumental flag. Its "lyrics" are then only
        # the notice 纯音乐，请欣赏 ("pure music, please enjoy").
        pure = payload.get("pureMusic") is True
        return song, strip_credit_lines(lyric_of("lrc")), lyric_of("romalrc"), pure

    candidates = []
    workers = max(1, min(len(songs), int(config.get("api", "max_workers", 8))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed({pool.submit(fetch_lyric, song) for song in songs}):
            try:
                song, lyric, romanized, pure = future.result()
            except (requests.RequestException, ValueError) as exc:
                report.add_error("NetEase", _describe_error(exc) if isinstance(exc, requests.RequestException) else "Invalid response")
                continue

            artist_names = artist_names_of(song)
            song_duration = seconds_of(song)
            if pure:
                # NetEase returns loosely related songs, so the flag only
                # counts for a song with this title by this artist - or, as
                # artist names are often in another script, of this length.
                close = bool(duration and song_duration and abs(song_duration - duration) <= _tolerance())
                if _same_version(clean_t, song.get("name") or "") and (matches_artist(clean_a, artist_names) or close):
                    report.mark_instrumental("NetEase", song.get("name") or "")
                continue
            if not lyric.strip():
                continue

            album_obj = song.get("album")
            album_name = album_obj.get("name", "NetEase") if isinstance(album_obj, dict) else "NetEase"
            common = dict(
                provider="NETEASE",
                artist=artist_names,
                title=song.get("name") or clean_t,
                duration=song_duration,
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
def query_syncedlyrics(title: str, artist: str, report: SearchReport,
                       aliases: str = "none") -> list[Candidate]:
    if not (HAS_SYNCEDLYRICS and config.get("api", "enable_syncedlyrics", True)):
        return []

    clean_t, clean_a = _cleaned(title), _cleaned(artist)
    if clean_a:
        queries = [f"{name} - {clean_t}" for name in _artist_names(clean_a, aliases)]
    else:
        queries = [] if aliases == "only" else [clean_t]
    queries = [q for q in dict.fromkeys(queries) if q.strip()]
    if not queries:
        return []

    # LRCLIB and NetEase are queried directly; letting syncedlyrics ask them
    # again only adds requests, and NetEase throttles. An empty list lets it
    # use all of its sources.
    sources = [str(name) for name in (config.get("api", "syncedlyrics_providers") or [])]

    def search(query: str) -> str:
        if sources:
            try:
                return syncedlyrics.search(query, providers=sources) or ""
            except TypeError:
                pass  # releases before 0.7 cannot choose their sources
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
            provider="SYNCEDLYRICS",
            artist="",
            title=clean_t,
            album="source unknown",
            text=text,
            unverified=True,
        )
        for text in dict.fromkeys(texts)
    ]


# --- aggregation -------------------------------------------------------------
def rank_key(candidate: Candidate):
    """Sort order: synced, exact match, script preference, duration confidence.

    Synced comes first (settings.prefer_synced): LRCLIB's exact match is often
    an upload with plain lyrics only, and ranked above everything it hid the
    synced versions of the same song further down. With the setting off, the
    exact match leads as before.
    """
    # Off by default: the script then plays no part, so the original lyrics
    # are not pushed below a romanization someone uploaded.
    prefer_latin = bool(config.get("settings", "prefer_latin", False))
    script_rank = (0 if candidate.latin else 1) if prefer_latin else 0
    # Candidates without a known duration must not outrank duration-verified ones.
    duration_rank = 0 if candidate.has_duration else 1
    exact_rank = 0 if candidate.exact else 1
    synced_rank = 0 if candidate.synced else 1
    first, second = (
        (synced_rank, exact_rank) if config.get("settings", "prefer_synced", True) else (exact_rank, synced_rank)
    )
    return (
        first,
        second,
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
    show_unrelated: bool = False


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


def _gather(title: str, artist: str, album: str, duration: int, report: SearchReport,
            quick: bool, aliases: str | None, scrapers: str | None = None) -> list[Candidate]:
    """Run providers concurrently for one round of queries.

    *aliases* is the round for LRCLIB and NetEase ("none", "only", "all"), or
    None to skip them; *scrapers* the same for syncedlyrics. Ctrl+C returns at
    once instead of waiting for the requests in flight; they finish in the
    background and are discarded.
    """
    jobs = {}
    if aliases is not None:
        jobs["LRCLIB"] = lambda: query_lrclib(title, artist, album, duration, report, quick=quick, aliases=aliases)
        if not quick:
            jobs["NetEase"] = lambda: query_netease(title, artist, duration, report, aliases=aliases)
    if scrapers is not None and not quick:
        jobs["syncedlyrics"] = lambda: query_syncedlyrics(title, artist, report, aliases=scrapers)
    if not jobs:
        return []

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
    return collected


def _classify(collected: list[Candidate], title: str, artist: str) -> tuple[list[Candidate], list[Candidate]]:
    """Deduplicate and split into results that fit the track and the rest."""
    clean_a = clean_tag(artist)
    searched_instrumental = instrumental_marker(title) is not None
    seen_texts: set[str] = set()
    related: list[Candidate] = []
    unrelated: list[Candidate] = []
    for candidate in collected:
        fingerprint = " ".join(candidate.text.split())
        if fingerprint in seen_texts:
            continue
        seen_texts.add(fingerprint)
        candidate.match_artist = matches_artist(clean_a, candidate.artist)
        candidate.match_title = _title_fits(title, candidate)
        (related if is_related(candidate, searched_instrumental) else unrelated).append(candidate)
    return related, unrelated


def _enough(collected: list[Candidate], title: str, artist: str) -> bool:
    """Whether a round found synced lyrics that fit and say where they are from."""
    related, _ = _classify(collected, title, artist)
    return any(c.synced and not c.unverified for c in related)


def collect_candidates(title: str, artist: str, album: str, duration: int,
                       quick: bool = False, aliases: bool | None = None) -> SearchReport:
    """Query the providers and return ranked candidates.

    *quick* asks LRCLIB's canonical ``/get`` endpoint only; see
    ``_lrclib_queries``. Searching goes in rounds, the next one only when the
    previous found no synced lyrics that fit - every extra query costs time,
    and NetEase throttles after a burst:

    1. LRCLIB and NetEase with the tag's artist - with its aliases too when
       *aliases* is true (default: settings.search_all_aliases).
    2. LRCLIB and NetEase with the aliases, if not searched yet, and the
       syncedlyrics scrapers (Musixmatch and others). Those run in round 1
       already unless api.syncedlyrics_as_fallback is set.
    """
    if aliases is None:
        aliases = bool(config.get("settings", "search_all_aliases", False))
    first = "all" if aliases else "none"
    scrapers_later = bool(config.get("api", "syncedlyrics_as_fallback", True))
    report = SearchReport(aliases_searched=aliases)
    collected = _gather(title, artist, album, duration, report, quick, first,
                        None if scrapers_later else first)

    # An instrumental track gets no second round: whatever it finds is the
    # sung version.
    searched_instrumental = instrumental_marker(title) is not None
    if not (quick or report.instrumental or searched_instrumental) and not _enough(collected, title, artist):
        alias_round = "only" if not aliases and get_artist_aliases(_cleaned(artist)) else None
        scraper_round = first if scrapers_later else None
        if alias_round or scraper_round:
            collected += _gather(title, artist, album, duration, report, quick, alias_round, scraper_round)
            report.alias_fallback = alias_round is not None
            report.aliases_searched = aliases or report.alias_fallback
            report.scraper_fallback = scraper_round is not None and HAS_SYNCEDLYRICS and bool(
                config.get("api", "enable_syncedlyrics", True))

    # No fallback to the unrelated results: when nothing fits, NetEase's
    # popular-song filler used to fill the list.
    related, unrelated = _classify(collected, title, artist)
    report.candidates = sorted(related, key=rank_key)
    report.unrelated = sorted(unrelated, key=rank_key)
    return report


def _title_fits(title: str, candidate: Candidate) -> bool:
    """Title check including the version: lyrics of the sung version are no
    match for an '(Inst.)' track, and an instrumental's are none for a song.
    A result that cannot say what it is (syncedlyrics) fits no instrumental."""
    searched_instrumental = instrumental_marker(title) is not None
    if candidate.unverified:
        return not searched_instrumental
    if searched_instrumental != (instrumental_marker(candidate.title) is not None):
        return False
    return matches_title(title, candidate.title)


def is_related(candidate: Candidate, searched_instrumental: bool = False) -> bool:
    """Exact matches always; otherwise the artist must fit, and the title -
    or, for titles in another language, the duration. Neither exactness nor
    length vouches for an instrumental track: its sung version has the same."""
    if searched_instrumental and not candidate.match_title:
        # LRCLIB's exact lookup ignores "(Inst.)" and returns the sung version.
        return False
    if candidate.exact:
        return True
    if not candidate.match_artist:
        return False
    if candidate.match_title:
        return True
    if searched_instrumental or candidate.unverified:
        return False
    return candidate.duration_checked and candidate.diff <= _tolerance()


def pick_automatic(report: SearchReport, duration: int) -> Candidate | None:
    """Return a candidate confident enough for unattended batch tagging.

    Nothing when a provider marks the track as instrumental: any lyrics found
    then most likely belong to the sung version.
    """
    if report.instrumental:
        return None
    tolerance = _tolerance()
    for candidate in report.candidates:
        if not candidate.synced or not candidate.match_artist or candidate.unverified:
            continue
        if not (candidate.match_title or candidate.exact):
            continue
        if duration and candidate.has_duration and candidate.diff > tolerance:
            continue
        if duration and not candidate.has_duration and not candidate.exact:
            continue
        return candidate
    return None


# --- MusicBrainz -------------------------------------------------------------
_MB_LOCK = threading.Lock()
_MB_LAST_CALL = 0.0
_MB_CACHE: dict[str, str | None] = {}
# MusicBrainz allows one request per second per client; more gets a 503.
_MB_INTERVAL = 1.0


def musicbrainz_instrumental(recording_id: str) -> str | None:
    """Ask MusicBrainz whether a recording has no vocals.

    Two kinds of data say so: a performance relationship with the attribute
    "instrumental" (an instrumental version of a song), or works whose lyrics
    language is "zxx" ("no linguistic content"). Returns the reason, or None
    when MusicBrainz knows of none. Raises requests/ValueError errors for the
    caller to report. Answers are cached for the session.
    """
    global _MB_LAST_CALL
    if recording_id in _MB_CACHE:
        return _MB_CACHE[recording_id]
    base = str(config.get("api", "musicbrainz_url", "https://musicbrainz.org/ws/2")).rstrip("/")
    with _MB_LOCK:
        wait = _MB_LAST_CALL + _MB_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            response = get_session().get(
                f"{base}/recording/{recording_id}",
                params={"inc": "work-rels", "fmt": "json"},
                timeout=_timeout(),
            )
        finally:
            _MB_LAST_CALL = time.monotonic()
    if response.status_code == 404:
        _MB_CACHE[recording_id] = None
        return None
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected MusicBrainz response")

    verdict = None
    works = []
    for relation in payload.get("relations") or []:
        if not isinstance(relation, dict) or relation.get("type") != "performance":
            continue
        if "instrumental" in (relation.get("attributes") or []):
            verdict = "instrumental recording"
            break
        work = relation.get("work")
        if isinstance(work, dict):
            works.append(work)
    if verdict is None and works:
        languages = [set(w.get("languages") or ([w["language"]] if w.get("language") else [])) for w in works]
        if all(langs == {"zxx"} for langs in languages):
            verdict = "work without lyrics"
    _MB_CACHE[recording_id] = verdict
    return verdict
