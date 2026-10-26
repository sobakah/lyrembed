"""Tag cleaning, artist matching, script detection and romanization."""

from __future__ import annotations

import re
from functools import lru_cache

from . import config

# Accepts [m:ss], [mm:ss.xx], [mmm:ss:xx] and repeated timestamps on one line.
TIMESTAMP_REGEX = re.compile(r"^(\s*(?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*)+)(.*)$")

# Metadata lines such as [ar:...] / [ti:...] / [length:...] are not timestamps.
_LRC_META_REGEX = re.compile(r"^\s*\[[a-z]{2,}:[^\]]*\]\s*$", re.IGNORECASE)

# --- optional romanization backends ------------------------------------------
try:
    import pykakasi

    HAS_KAKASI = True
except ImportError:
    HAS_KAKASI = False

try:
    from pypinyin import Style, pinyin

    HAS_PYPINYIN = True
except ImportError:
    HAS_PYPINYIN = False

try:
    from korean_romanizer.romanizer import Romanizer as KoRomanizer

    HAS_KOREAN = True
except ImportError:
    HAS_KOREAN = False

try:
    import anyascii

    HAS_ANYASCII = True
except ImportError:
    HAS_ANYASCII = False


@lru_cache(maxsize=1)
def _kakasi_instance():
    """pykakasi converters are expensive to build - create exactly one."""
    return pykakasi.kakasi()


# --- cleaning ----------------------------------------------------------------
@lru_cache(maxsize=512)
def _compiled(pattern: str, ignore_case: bool):
    return re.compile(pattern, re.IGNORECASE if ignore_case else 0)


# Innermost pair first, so nested brackets go in several passes.
_ANY_BRACKET = re.compile(r"\([^()\[\]]*\)|\[[^()\[\]]*\]")


def _strip_brackets(text: str) -> str:
    """Remove every balanced (...) and [...] group, nested ones included.

    Tags like 'Album Artist ("Member A", "Member B")' carry the individual
    performers in brackets; providers list the main artist only, so the
    bracketed part makes queries and artist matching fail. Unbalanced brackets
    are left alone rather than guessing where they end.
    """
    while True:
        stripped = _ANY_BRACKET.sub(" ", text)
        if stripped == text:
            return text
        text = stripped


def clean_tag(text: str) -> str:
    if not text:
        return ""

    cleaned = text
    for key in ("feature_regex", "trailing_feature_regex"):
        pattern = config.get("cleaning", key)
        if pattern:  # checked when the config is loaded or saved
            cleaned = _compiled(pattern, True).sub("", cleaned)

    for key in ("korean_bracket_regex", "japanese_bracket_regex", "chinese_bracket_regex"):
        pattern = config.get("cleaning", key)
        if pattern:  # checked when the config is loaded or saved
            cleaned = _compiled(pattern, False).sub("", cleaned)

    if config.get("cleaning", "strip_all_brackets", False):
        cleaned = _strip_brackets(cleaned)

    return re.sub(r"\s+", " ", cleaned).strip()


# --- artist matching ---------------------------------------------------------
@lru_cache(maxsize=2048)
def _word_pattern(needle: str) -> re.Pattern:
    """Match *needle* only at word boundaries, tolerating punctuation inside it."""
    return re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)", re.IGNORECASE)


def _contains_alias(haystack: str, needle: str) -> bool:
    if not haystack or not needle:
        return False
    return bool(_word_pattern(needle.lower()).search(haystack.lower()))


def _alias_groups():
    return config.alias_groups()


def get_artist_aliases(artist: str) -> list[str]:
    """Return alternative spellings for *artist* (word-boundary matched)."""
    if not artist:
        return []
    artist_lower = artist.lower()
    for group in _alias_groups():
        if any(_contains_alias(artist_lower, alias) for alias in group):
            return [a for a in group if a.lower() != artist_lower]
    return []


def matches_artist(target_artist: str, candidate_artist: str) -> bool:
    if not target_artist or not candidate_artist:
        return True

    target = target_artist.lower()
    candidate = candidate_artist.lower()

    if _contains_alias(target, candidate) or _contains_alias(candidate, target):
        return True

    for group in _alias_groups():
        in_target = any(_contains_alias(target, alias) for alias in group)
        in_candidate = any(_contains_alias(candidate, alias) for alias in group)
        if in_target and in_candidate:
            return True

    ignore_words = {w.lower() for w in (config.get("cleaning", "ignore_words_artist_match") or [])}
    target_words = {w for w in re.findall(r"\w+", target) if w not in ignore_words and len(w) > 1}
    candidate_words = {w for w in re.findall(r"\w+", candidate) if w not in ignore_words and len(w) > 1}
    if not target_words or not candidate_words:
        return False

    overlap = target_words & candidate_words
    if not overlap:
        return False
    # Require a clear majority of the shorter name, not a single stray token:
    # "Red Velvet" must not match "The Velvet Underground".
    return len(overlap) / min(len(target_words), len(candidate_words)) > 0.5


# --- title matching ----------------------------------------------------------
# Version notes that mean "no vocals". MR ("music recorded") is the Korean
# term; it must not be followed by a dot, or "(Mr. Big Cover)" would count.
_INSTRUMENTAL_WORDS = re.compile(
    r"(?<!\w)(?:inst\.?|instrumental|off[\s-]?vocal|karaoke|backing\s+track|"
    r"minus\s+one|mr(?![\w.]))(?!\w)",
    re.IGNORECASE,
)
# Japanese and Chinese markers, recognised anywhere in the title.
_INSTRUMENTAL_CJK = re.compile(r"オフボーカル|インスト|カラオケ|伴奏|纯音乐|純音樂")
_TITLE_NOTES = re.compile(r"[\(\[（【]([^\)\]）】]*)[\)\]）】]|\s[-–]\s(.+)$")


def instrumental_marker(title: str) -> str | None:
    """The version note that marks *title* as instrumental, if any.

    Latin words only count inside brackets or after ' - ', where version notes
    live: a song may well be called 'Instrumental Love' or 'Karaoke Night'.
    """
    if not title:
        return None
    cjk = _INSTRUMENTAL_CJK.search(title)
    if cjk:
        return cjk.group(0)
    for match in _TITLE_NOTES.finditer(title):
        note = match.group(1) or match.group(2) or ""
        word = _INSTRUMENTAL_WORDS.search(note)
        if word:
            return word.group(0)
    return None


def _title_words(title: str) -> list[str]:
    """Lower-case words of a title without features, bracketed notes and
    ' - Remastered'-style suffixes, which providers write inconsistently."""
    core = re.sub(r"\s[-–]\s.*$", "", _strip_brackets(clean_tag(title)))
    words = re.findall(r"\w+", core.casefold())
    return words or re.findall(r"\w+", (title or "").casefold())


def matches_title(target_title: str, candidate_title: str) -> bool:
    """Whether two titles name the same song, apart from version notes.

    Unknown titles count as matching. Translated titles ('사랑을 했다' vs
    'LOVE SCENARIO') do not match; callers accept those on a matching
    duration instead.
    """
    target, candidate = _title_words(target_title), _title_words(candidate_title)
    if not target or not candidate:
        return True
    joined_t, joined_c = " ".join(target), " ".join(candidate)
    if joined_t == joined_c or _contains_alias(joined_t, joined_c) or _contains_alias(joined_c, joined_t):
        return True
    overlap = set(target) & set(candidate)
    return len(overlap) / min(len(set(target)), len(set(candidate))) > 0.5


# --- script detection --------------------------------------------------------
_LATIN_RANGES = (
    (0x0041, 0x005A),  # Basic Latin uppercase
    (0x0061, 0x007A),  # Basic Latin lowercase
    (0x00C0, 0x024F),  # Latin-1 Supplement / Extended-A / Extended-B
    (0x1E00, 0x1EFF),  # Latin Extended Additional
    (0x2C60, 0x2C7F),  # Latin Extended-C
    (0xA720, 0xA7FF),  # Latin Extended-D
    (0xAB30, 0xAB6F),  # Latin Extended-E
)


def _is_latin_char(char: str) -> bool:
    code = ord(char)
    for low, high in _LATIN_RANGES:
        if low <= code <= high:
            return True
        if code < low:
            return False
    return False


def unix_newlines(text: str) -> str:
    """Lyrics with Windows (CRLF) or old Mac (CR) line ends as plain LF.

    The page's editor splits on LF, and a leftover CR kept every timestamp
    line from matching, so synced lyrics showed as untimed.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_timestamps(text: str) -> str:
    lines = []
    for line in text.splitlines():
        match = TIMESTAMP_REGEX.match(line)
        lines.append(match.group(2) if match else line)
    return "\n".join(lines)


def _lyric_lines(text: str) -> list[str]:
    """The sung lines: without timestamps, LRC headers, credits and blank lines.

    Headers such as [ar:...] and [al:...] are usually long and in English, and
    NetEase credits ('作词 : Name') carry Latin names; both are metadata and
    must not decide which script the lyrics are in.
    """
    lines = []
    for line in text.splitlines():
        if _LRC_META_REGEX.match(line) or is_credit_line(line):
            continue
        body = strip_timestamps(line).strip()
        if body:
            lines.append(body)
    return lines


def _has_non_latin_letter(line: str) -> bool:
    return any(c.isalpha() and not _is_latin_char(c) for c in line)


@lru_cache(maxsize=256)
def _non_latin_line_share(text: str) -> float:
    """Share of lyric lines containing any non-Latin letter.

    Counted per line, not per letter: one Hangul or Han character holds a
    whole syllable where English needs several letters, so a letter count let
    a single English chorus outweigh the Korean verses. Romanized lyrics have
    essentially no non-Latin letters at all, so even a small share of lines
    means the original script is still there.
    """
    lines = _lyric_lines(text)
    if not lines:
        return 0.0
    return sum(1 for line in lines if _has_non_latin_letter(line)) / len(lines)


def is_non_latin(text: str) -> bool:
    if not text:
        return False
    threshold = float(config.get("settings", "non_latin_ratio_threshold", 0.1))
    share = _non_latin_line_share(text)
    return share > 0 and share >= threshold


def script_label(text: str) -> str:
    """'latin', 'original', or 'mixed' when several Asian scripts need
    romanizing - the case where only the Mixed scripts mode gets every line
    right, which the user should know before picking a romanization."""
    if not is_non_latin(text):
        return "latin"
    return "mixed" if detect_script(text) == "mixed" else "original"


@lru_cache(maxsize=256)
def has_timestamps(text: str) -> bool:
    for line in text.splitlines():
        if not line.strip() or _LRC_META_REGEX.match(line):
            continue
        if TIMESTAMP_REGEX.match(line):
            return True
    return False


# --- script segmentation -----------------------------------------------------
# Han characters are shared between Japanese Kanji and Chinese Hanzi, so a whole
# line can never be handed to a single romanizer: pykakasi reads Hanzi as Kanji,
# and pypinyin leaves Kana untouched. Worse, running one pass after another
# destroys the previous result, because tone marks are not ASCII and pykakasi
# splits them into separate tokens. Every romanization therefore works on script
# runs and never touches text that is already Latin.

_HANGUL_RANGES = ((0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xAC00, 0xD7A3))
_KANA_RANGES = ((0x3040, 0x309F), (0x30A0, 0x30FF), (0x31F0, 0x31FF), (0xFF66, 0xFF9D))
_HAN_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))
_MIDDLE_DOTS = "・･"
_PROLONGED_MARKS = "ーｰ"

KEEP = "keep"
HANGUL = "hangul"
KANA = "kana"
HAN = "han"
OTHER = "other"


def _in_ranges(code: int, ranges) -> bool:
    return any(low <= code <= high for low, high in ranges)


def _char_class(char: str) -> str:
    code = ord(char)
    if _in_ranges(code, _HANGUL_RANGES):
        return HANGUL
    # The katakana middle dot separates names and titles in Chinese and
    # Korean lyrics as well (约翰・史密斯, 사랑・이별); as Kana it made them
    # look Japanese, and Han next to it was read with Japanese readings.
    if char in _MIDDLE_DOTS:
        return KEEP
    if _in_ranges(code, _KANA_RANGES) or char in "ーゝゞヽヾ":
        return KANA
    if _in_ranges(code, _HAN_RANGES) or char == "々":
        return HAN
    # Anything that is not a letter is punctuation, a digit or whitespace and
    # needs no transliteration. Testing isalpha() rather than isascii() keeps
    # typographic characters such as … — ' ' out of the script statistics,
    # where they used to be counted as a foreign script of their own.
    if not char.isalpha():
        return KEEP
    if _is_latin_char(char):
        return KEEP
    return OTHER


def _char_classes(text: str) -> list[str]:
    """_char_class for every character, with the prolonged sound mark ー
    judged by its neighbours: it is Kana only after or before Kana. Chinese
    text sometimes uses it as a dash, which made the whole song Japanese."""
    kinds = [_char_class(char) for char in text]
    marks = [i for i, char in enumerate(text) if char in _PROLONGED_MARKS]
    for i in marks:
        kinds[i] = KEEP
    for i in marks:
        before = next((kinds[j] for j in range(i - 1, -1, -1) if text[j] not in _PROLONGED_MARKS), None)
        after = next((kinds[j] for j in range(i + 1, len(text)) if text[j] not in _PROLONGED_MARKS), None)
        if KANA in (before, after):
            kinds[i] = KANA
    return kinds


def segment_line(line: str) -> list[tuple[str, str]]:
    """Split *line* into maximal runs of one script class."""
    runs: list[tuple[str, str]] = []
    for char, kind in zip(line, _char_classes(line)):
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + char)
        else:
            runs.append((kind, char))
    return runs


# Which writing systems use a character is read from the national character
# set standards shipped with Python's codecs rather than from a hand-kept list:
# such lists end up containing characters that Traditional Chinese (問 間 師)
# or Simplified Chinese (区 医 欠) use as well. GB2312 is the simplified Chinese
# standard, CP950 (Microsoft's Big5, the de facto Taiwanese encoding) the
# traditional one, EUC-JP covers JIS X 0208 and 0212. CP950 rather than plain
# Big5, which lacks variants like 裏; not Big5-HKSCS, which adds Japanese
# characters such as 駅 and 円.
_CHARSET_CODECS = ("gb2312", "cp950", "euc_jp")


def _encodable(char: str, codec: str) -> bool:
    try:
        char.encode(codec)
    except UnicodeEncodeError:
        return False
    return True


@lru_cache(maxsize=8192)
def _charset_evidence(char: str) -> str | None:
    """'ja' when only Japanese standards contain *char* (shinjitai, kokuji),
    'zh' when no Japanese standard does, None when the systems share it."""
    gb, big5, jis = (_encodable(char, codec) for codec in _CHARSET_CODECS)
    if jis and not (gb or big5):
        return "ja"
    if (gb or big5) and not jis:
        return "zh"
    return None


@lru_cache(maxsize=8192)
def _kakasi_reads(char: str) -> bool:
    """Whether pykakasi has a reading for the single character *char*.

    Probed per character because pykakasi does not merely return an empty
    reading for a character it does not know: it may also swallow the one after
    it ('时候' comes back as a single token '时'), so a whole-run conversion
    cannot tell which characters were lost.
    """
    try:
        items = _kakasi_instance().convert(char)
    except Exception:
        return False
    return any(item.get("hepburn", "").strip() for item in items)


def _kakasi_han_coverage(fragment: str) -> float:
    """Share of Han characters in *fragment* that pykakasi has a reading for.

    pykakasi's dictionary contains no simplified Chinese forms, so a coverage
    below 1.0 means the run cannot be Japanese text.
    """
    if not HAS_KAKASI:
        return 1.0
    han = [char for char in fragment if _char_class(char) == HAN]
    if not han:
        return 1.0
    return sum(1 for char in han if _kakasi_reads(char)) / len(han)


def _han_evidence(fragment: str, trust_ja: bool = False) -> str | None:
    """Look for proof that a Han run belongs to one writing system.

    Chinese evidence from the character sets wins over Japanese. A character
    pykakasi cannot read counts as Chinese only when the text has no Kana
    (*trust_ja* False): pykakasi lacks 17 of the JIS X 0208 kanji (唸, 萍,
    錺, ...), and one of them made a Japanese song "mixed", so its other Han
    runs were read as Pinyin. _romanize_japanese handles such a character.
    """
    votes = {_charset_evidence(char) for char in fragment if _char_class(char) == HAN}
    if "zh" in votes:
        return "zh"
    if "ja" in votes:
        return "ja"
    if not trust_ja and _kakasi_han_coverage(fragment) < 1.0:
        return "zh"
    return None


def _neighbour_kind(runs: list[tuple[str, str]], index: int, step: int, skip_keep: bool) -> str | None:
    """Kind of the neighbouring run, optionally looking past spaces and
    punctuation ('「夢」、そして')."""
    position = index + step
    while 0 <= position < len(runs):
        kind, fragment = runs[position]
        if skip_keep and kind == KEEP:
            position += step
            continue
        return kind
    return None


def _line_looks_japanese(runs: list[tuple[str, str]], trust_ja: bool = True) -> bool:
    """True when the line contains Kana and nothing pointing elsewhere."""
    if not any(kind == KANA for kind, _ in runs):
        return False
    if any(kind == HANGUL for kind, _ in runs):
        return False
    return not any(
        kind == HAN and _han_evidence(fragment, trust_ja) == "zh" for kind, fragment in runs
    )


def contains_kana(text: str) -> bool:
    return KANA in _char_classes(text)


def _resolve_han(runs: list[tuple[str, str]], index: int, han_default: str,
                 trust_ja: bool = True) -> str:
    """Decide whether an ambiguous Han run is Japanese or Chinese.

    Character-level proof comes first, because a simplified form cannot occur in
    Japanese text no matter what surrounds it. Only then does context count:
    Kana touching the run, or - if nothing on the line points to another
    language - Kana separated from it by spaces or punctuation. Runs written
    purely in shared characters stay ambiguous and follow the caller's choice.

    Without *trust_ja* a character only Japanese standards contain is not
    proof: the whole text has no Kana, which Japanese lyrics practically always
    have, so such a character is more likely a rare Chinese variant missing
    from the character sets (Hong Kong forms such as 峯 or 綫).
    """
    fragment = runs[index][1]

    evidence = _han_evidence(fragment, trust_ja)
    if evidence == "zh" or (evidence == "ja" and trust_ja):
        return evidence

    if KANA in (
        _neighbour_kind(runs, index, -1, skip_keep=False),
        _neighbour_kind(runs, index, +1, skip_keep=False),
    ):
        return "ja"

    if _line_looks_japanese(runs, trust_ja) and KANA in (
        _neighbour_kind(runs, index, -1, skip_keep=True),
        _neighbour_kind(runs, index, +1, skip_keep=True),
    ):
        return "ja"

    return han_default


def _convert_run(fragment: str, lang: str) -> str:
    """Romanize one run, falling back to anyascii when a backend is missing."""
    if lang == "ko" and HAS_KOREAN:
        return romanize_segment(fragment, "ko")
    if lang == "ja" and HAS_KAKASI:
        return romanize_segment(fragment, "ja")
    if lang == "zh" and HAS_PYPINYIN:
        return romanize_segment(fragment, "zh")
    if HAS_ANYASCII:
        return romanize_segment(fragment, "any")
    return fragment


def _join_pieces(pieces: list[tuple[str, bool]]) -> str:
    """Concatenate converted and kept fragments, inserting a space where two
    word-like fragments would otherwise collide ("nǐ hǎo" + "world").

    A separator is only needed at a boundary where at least one side was
    romanized; untouched text keeps its original spacing.
    """
    out = ""
    previous_converted = False
    for fragment, converted in pieces:
        if out and fragment:
            boundary_converted = converted or previous_converted
            if boundary_converted and out[-1].isalnum() and fragment[0].isalnum():
                out += " "
        out += fragment
        if fragment:
            previous_converted = converted
    return re.sub(r"[ \t]{2,}", " ", out).rstrip()


def _plan_runs(segment: str, mode: str, han_default: str,
               trust_ja: bool = True) -> list[tuple[str | None, str]]:
    """Map each script run to the backend that should convert it.

    A target of None means the fragment is left untouched. Adjacent runs with
    the same target are merged afterwards, which matters a great deal: kakasi
    reads a lone 見 as "ken" but 見つけた as "mitsuketa", so Kanji must reach it
    together with the Kana that follows.
    """
    runs = segment_line(segment)
    planned: list[tuple[str | None, str]] = []

    single = {
        "ko": {HANGUL: "ko"},
        "ja": {KANA: "ja", HAN: "ja"},
        "zh": {HAN: "zh"},
        "any": {HANGUL: "any", KANA: "any", HAN: "any", OTHER: "any"},
    }

    for index, (kind, fragment) in enumerate(runs):
        if mode == "mixed":
            if kind == HANGUL:
                target = "ko"
            elif kind == KANA:
                target = "ja"
            elif kind == HAN:
                target = _resolve_han(runs, index, han_default, trust_ja)
            elif kind == OTHER:
                target = "any"
            else:
                target = None
        else:
            target = single.get(mode, {}).get(kind)
        planned.append((target, fragment))

    merged: list[tuple[str | None, str]] = []
    for target, fragment in planned:
        if merged and merged[-1][0] == target:
            merged[-1] = (target, merged[-1][1] + fragment)
        else:
            merged.append((target, fragment))
    return merged


def _run_plan(segment: str, mode: str, han_default: str, trust_ja: bool = True) -> str:
    if not segment.strip():
        return segment
    pieces = [
        (_convert_run(fragment, target) if target else fragment, target is not None)
        for target, fragment in _plan_runs(segment, mode, han_default, trust_ja)
    ]
    return _join_pieces(pieces)


def romanize_mixed_segment(segment: str, han_default: str = "zh", trust_ja: bool = True) -> str:
    """Romanize a segment that may contain several scripts at once.

    *trust_ja* is False when the surrounding text has no Kana; see
    ``_resolve_han``.
    """
    return _run_plan(segment, "mixed", han_default, trust_ja)


def romanize_single_segment(segment: str, lang: str) -> str:
    """Romanize only the runs belonging to *lang*, leaving everything else alone."""
    return _run_plan(segment, lang, "zh")


# --- romanization ------------------------------------------------------------
ROMANIZERS = {
    "mixed": ("Mixed scripts (detect per segment)", lambda: HAS_KOREAN or HAS_KAKASI or HAS_PYPINYIN, "—"),
    "ko": ("Korean (Hangul → Romaja)", lambda: HAS_KOREAN, "korean-romanizer"),
    "ja": ("Japanese (Kanji/Kana → Rōmaji)", lambda: HAS_KAKASI, "pykakasi"),
    "zh": ("Mandarin (Hanzi → Pīnyīn)", lambda: HAS_PYPINYIN, "pypinyin"),
    "any": ("Universal (anyascii)", lambda: HAS_ANYASCII, "anyascii"),
}


def _join_kakasi(items) -> str:
    """Join pykakasi tokens without splitting words apart.

    kakasi cuts 見つけた into 見つ + けたよ, so joining every token with a space
    produces "mitsu ketayo". A boundary is okurigana - the Kana tail of a verb
    or adjective - when the previous token contains a Kanji and ends in Kana
    while the next one starts with Kana; those are glued back together. Real
    word boundaries, where a Kanji starts or ends a token, keep their space.
    """
    out = ""
    previous = None
    for item in items:
        hepburn = item.get("hepburn", "")
        original = item.get("orig", "")
        if previous is not None and out and hepburn:
            prev_orig = previous.get("orig", "")
            okurigana = (
                any(_char_class(c) == HAN for c in prev_orig)
                and prev_orig
                and _char_class(prev_orig[-1]) == KANA
                and original
                and _char_class(original[0]) == KANA
            )
            if not okurigana and not out[-1].isspace() and not hepburn[0].isspace():
                out += " "
        out += hepburn
        previous = item
    return out


def _romanize_unreadable_han(fragment: str) -> str:
    """Fallback for Han characters pykakasi has no reading for."""
    if HAS_PYPINYIN:
        return romanize_segment(fragment, "zh")
    if HAS_ANYASCII:
        return romanize_segment(fragment, "any")
    return fragment


def _kakasi_lossless(chunk: str) -> str | None:
    """Convert *chunk* with pykakasi, or None if any character went missing."""
    items = _kakasi_instance().convert(chunk)
    if "".join(item.get("orig", "") for item in items) != chunk:
        return None
    if any(item.get("orig", "").strip() and not item.get("hepburn", "").strip() for item in items):
        return None
    return _join_kakasi(items)


def _romanize_japanese(segment: str) -> str:
    """pykakasi conversion that never loses characters.

    Characters pykakasi cannot read are cut out before conversion, because it
    would otherwise drop them together with their neighbour, and are converted
    by the Chinese fallback instead. Readable stretches stay whole so Kanji
    keep their Kana context. If a stretch still comes back incomplete, it is
    passed through unchanged rather than saved with holes in it.
    """
    pieces: list[tuple[str, bool]] = []
    chunk = ""
    unreadable = ""

    def flush() -> None:
        nonlocal chunk, unreadable
        if chunk:
            converted = _kakasi_lossless(chunk)
            pieces.append((converted if converted is not None else chunk, converted is not None))
            chunk = ""
        if unreadable:
            # Kept together so pypinyin can use phrase context for polyphones.
            pieces.append((_romanize_unreadable_han(unreadable), True))
            unreadable = ""

    for char in segment:
        if _char_class(char) == HAN and not _kakasi_reads(char):
            if chunk:
                flush()
            unreadable += char
        else:
            if unreadable:
                flush()
            chunk += char
    flush()
    return _join_pieces(pieces)


def romanize_segment(segment: str, lang: str) -> str:
    if not segment.strip():
        return segment
    try:
        if lang == "ko" and HAS_KOREAN:
            result = KoRomanizer(segment).romanize()
        elif lang == "ja" and HAS_KAKASI:
            result = _romanize_japanese(segment)
        elif lang == "zh" and HAS_PYPINYIN:
            result = " ".join(token[0] for token in pinyin(segment, style=Style.TONE))
        elif lang == "any" and HAS_ANYASCII:
            # anyascii maps characters it does not know to "", per character.
            result = "".join(anyascii.anyascii(char) or char for char in segment)
        else:
            return segment
    except Exception:
        return segment

    # pykakasi silently deletes characters it has no reading for, so an empty
    # result means the text would be lost rather than transliterated.
    if not result.strip():
        return segment
    return result


def romanize_lyrics(lyrics: str, lang: str, han_default: str = "zh",
                    keep_original: bool = False) -> str:
    """Romanize every line, preserving LRC timestamps.

    With *keep_original* each changed line is kept and followed by its
    romanization under the same timestamp, which players supporting
    multi-line LRC show as a pair. Unchanged lines are not duplicated.
    """
    trust_ja = contains_kana(lyrics)

    def convert(segment: str) -> str:
        if lang == "mixed":
            return romanize_mixed_segment(segment, han_default, trust_ja)
        return romanize_single_segment(segment, lang)

    lines = lyrics.splitlines()
    converted = []
    for index, line in enumerate(lines):
        if keep_original and _LRC_META_REGEX.match(line):
            # A second [ti:] or [al:] header is no translation pair.
            converted.append(line)
            continue
        match = TIMESTAMP_REGEX.match(line)
        if match:
            result = f"{match.group(1)}{convert(match.group(2))}"
        else:
            result = convert(line)
        if keep_original and result.split() == line.split():
            # Nothing romanized, only spacing tidied: an English line with
            # a trailing space must not show up twice.
            result = line
        if keep_original and result != line:
            converted.append(line)
            # Already followed by its romanization (romanized before with this
            # layout): adding it again would double every line.
            if index + 1 < len(lines) and lines[index + 1] == result:
                continue
        converted.append(result)
    return "\n".join(converted)


# NetEase prepends credit headers to its LRC files: "作词 : ...", "作曲 : ...",
# "编曲 : ...". Those are metadata in Chinese, not lyrics, and they made every
# Korean or Japanese song from that provider look like mixed-script text.
_CREDIT_LABELS = {
    "作词", "作詞", "作曲", "编曲", "編曲", "制作", "製作", "制作人", "出品人",
    "录音", "錄音", "混音", "母带", "母帶", "监制", "監製", "和声", "和聲",
    "吉他", "贝斯", "貝斯", "键盘", "鍵盤", "统筹", "統籌", "发行", "發行",
    "演唱", "词", "詞", "曲", "编", "編", "歌手", "原唱", "翻唱",
}
_CREDIT_LINE_REGEX = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*(?P<label>[\u3400-\u9fff]{1,4}|OP|SP)\s*[:：]",
    re.IGNORECASE,
)


def is_header_line(line: str) -> bool:
    """True for LRC header tags such as '[ti:...]', '[ar:...]' or '[offset:0]'."""
    return bool(_LRC_META_REGEX.match(line))


def is_credit_line(line: str) -> bool:
    """True for provider credit headers such as '作词 : ...'."""
    match = _CREDIT_LINE_REGEX.match(line)
    if not match:
        return False
    label = match.group("label")
    return label.upper() in ("OP", "SP") or label in _CREDIT_LABELS


def detect_script(text: str) -> str | None:
    """Best guess at the dominant script, for romanization defaults.

    Returns "mixed" only when two different Asian scripts share the text, since
    that is the case a single backend cannot handle. One Asian script next to
    Latin needs no mixed mode - the Latin part is left alone anyway. Han runs
    are classified with the same evidence the converter uses, so Chinese next
    to Japanese is recognised instead of being absorbed into it. Provider
    credit headers and LRC tags ([ti:...]) are ignored, because they are
    metadata rather than lyrics.
    """
    asian, has_other = _asian_languages(text)
    if len(asian) > 1:
        return "mixed"
    if asian:
        return asian.pop()
    return "any" if has_other else None


def _asian_languages(text: str) -> tuple[set[str], bool]:
    """The Asian languages in the lyrics ("ko", "ja", "zh"), and whether
    other non-Latin letters occur."""
    asian: set[str] = set()
    has_other = False
    trust_ja = contains_kana(text)

    for line in text.splitlines():
        if is_credit_line(line) or _LRC_META_REGEX.match(line):
            continue
        runs = segment_line(strip_timestamps(line))
        line_has_kana = any(kind == KANA for kind, _ in runs)
        for kind, fragment in runs:
            if kind == HANGUL:
                asian.add("ko")
            elif kind == KANA:
                asian.add("ja")
            elif kind == HAN:
                evidence = _han_evidence(fragment, trust_ja)
                if evidence == "ja" and not trust_ja:
                    evidence = None
                asian.add(evidence or ("ja" if line_has_kana else "zh"))
            elif kind == OTHER:
                has_other = True

    return asian, has_other


def missing_packages(text: str) -> list[str]:
    """The romanization packages the lyrics' languages need but are not
    installed. Mixed mode reported "already romanized" when they were all
    missing, and half-converted lyrics when some were."""
    asian, _ = _asian_languages(text)
    return [ROMANIZERS[lang][2] for lang in ("ko", "ja", "zh")
            if lang in asian and not ROMANIZERS[lang][1]()]
