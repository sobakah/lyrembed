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


def clean_tag(text: str) -> str:
    if not text:
        return ""

    cleaned = text
    for key in ("feature_regex", "trailing_feature_regex"):
        pattern = config.get("cleaning", key)
        if pattern:
            try:
                cleaned = _compiled(pattern, True).sub("", cleaned)
            except re.error:
                pass

    for key in ("korean_bracket_regex", "japanese_bracket_regex", "chinese_bracket_regex"):
        pattern = config.get("cleaning", key)
        if pattern:
            try:
                cleaned = _compiled(pattern, False).sub("", cleaned)
            except re.error:
                pass

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


def _alias_groups() -> list[list[str]]:
    groups = config.CONFIG.get("aliases", [])
    return [g for g in groups if isinstance(g, list)] if isinstance(groups, list) else []


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


def strip_timestamps(text: str) -> str:
    lines = []
    for line in text.splitlines():
        match = TIMESTAMP_REGEX.match(line)
        lines.append(match.group(2) if match else line)
    return "\n".join(lines)


@lru_cache(maxsize=256)
def _non_latin_ratio(text: str) -> float:
    letters = [c for c in strip_timestamps(text) if c.isalpha()]
    if not letters:
        return 0.0
    non_latin = sum(1 for c in letters if not _is_latin_char(c))
    return non_latin / len(letters)


def is_non_latin(text: str) -> bool:
    if not text:
        return False
    threshold = float(config.get("settings", "non_latin_ratio_threshold", 0.25))
    return _non_latin_ratio(text) > threshold


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


def segment_line(line: str) -> list[tuple[str, str]]:
    """Split *line* into maximal runs of one script class."""
    runs: list[tuple[str, str]] = []
    for char in line:
        kind = _char_class(char)
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + char)
        else:
            runs.append((kind, char))
    return runs


# Characters that exist in only one writing system. The Japanese list is the
# primary signal for shinjitai; the Chinese one is only a fallback for when
# pykakasi is not installed, because the coverage probe below is far more
# complete than any hand-maintained list.
_ZH_ONLY = set("爱们这个来时说请边过还让给龙东车书长门为习乐买卖见觉学关变风飞马鸟鱼"
               "热对难发图园员优众亲儿开问间师产权级传伤价")
_JP_ONLY = set("円気駅働峠込辻畑様発桜実検読売価恵戦沢栄済県帰圧囲医応帯芸欠区験"
               "関問間師産権級伝傷価")


@lru_cache(maxsize=2048)
def _kakasi_han_coverage(fragment: str) -> float:
    """Share of Han characters in *fragment* that pykakasi has a reading for.

    pykakasi silently drops characters it does not know, and its dictionary
    contains no simplified Chinese forms. A coverage below 1.0 therefore means
    the run cannot be Japanese text. This probes the whole dictionary rather
    than a curated character list.
    """
    if not HAS_KAKASI:
        return 1.0
    total = dropped = 0
    try:
        items = _kakasi_instance().convert(fragment)
    except Exception:
        return 1.0
    for item in items:
        han = sum(1 for char in item.get("orig", "") if _char_class(char) == HAN)
        if not han:
            continue
        total += han
        if not item.get("hepburn", "").strip():
            dropped += han
    if not total:
        return 1.0
    return (total - dropped) / total


def _han_evidence(fragment: str) -> str | None:
    """Look for proof that a Han run belongs to one writing system."""
    if any(char in _JP_ONLY for char in fragment):
        return "ja"
    if HAS_KAKASI:
        if _kakasi_han_coverage(fragment) < 1.0:
            return "zh"
    elif any(char in _ZH_ONLY for char in fragment):
        return "zh"
    return None


def _neighbour_kind(runs: list[tuple[str, str]], index: int, step: int, skip_space: bool) -> str | None:
    """Kind of the neighbouring run, optionally looking past separators."""
    position = index + step
    while 0 <= position < len(runs):
        kind, fragment = runs[position]
        if skip_space and kind == KEEP and not fragment.strip():
            position += step
            continue
        return kind
    return None


def _line_looks_japanese(runs: list[tuple[str, str]]) -> bool:
    """True when the line contains Kana and nothing pointing elsewhere."""
    if not any(kind == KANA for kind, _ in runs):
        return False
    if any(kind == HANGUL for kind, _ in runs):
        return False
    return not any(
        kind == HAN and _han_evidence(fragment) == "zh" for kind, fragment in runs
    )


def _resolve_han(runs: list[tuple[str, str]], index: int, han_default: str) -> str:
    """Decide whether an ambiguous Han run is Japanese or Chinese.

    Character-level proof comes first, because a simplified form cannot occur in
    Japanese text no matter what surrounds it. Only then does context count:
    Kana touching the run, or - if nothing on the line points to another
    language - Kana separated from it by spaces. Runs written purely in shared
    characters stay ambiguous and follow the caller's choice.
    """
    fragment = runs[index][1]

    evidence = _han_evidence(fragment)
    if evidence:
        return evidence

    if KANA in (
        _neighbour_kind(runs, index, -1, skip_space=False),
        _neighbour_kind(runs, index, +1, skip_space=False),
    ):
        return "ja"

    if _line_looks_japanese(runs) and KANA in (
        _neighbour_kind(runs, index, -1, skip_space=True),
        _neighbour_kind(runs, index, +1, skip_space=True),
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


def _plan_runs(segment: str, mode: str, han_default: str) -> list[tuple[str | None, str]]:
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
                target = _resolve_han(runs, index, han_default)
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


def _run_plan(segment: str, mode: str, han_default: str) -> str:
    if not segment.strip():
        return segment
    pieces = [
        (_convert_run(fragment, target) if target else fragment, target is not None)
        for target, fragment in _plan_runs(segment, mode, han_default)
    ]
    return _join_pieces(pieces)


def romanize_mixed_segment(segment: str, han_default: str = "zh") -> str:
    """Romanize a segment that may contain several scripts at once."""
    return _run_plan(segment, "mixed", han_default)


def romanize_single_segment(segment: str, lang: str) -> str:
    """Romanize only the runs belonging to *lang*, leaving everything else alone."""
    return _run_plan(segment, lang, "zh")


# --- romanization ------------------------------------------------------------
ROMANIZERS = {
    "mixed": ("Mixed scripts (detect per segment)", lambda: True, "—"),
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


def romanize_segment(segment: str, lang: str) -> str:
    if not segment.strip():
        return segment
    try:
        if lang == "ko" and HAS_KOREAN:
            result = KoRomanizer(segment).romanize()
        elif lang == "ja" and HAS_KAKASI:
            result = _join_kakasi(_kakasi_instance().convert(segment))
        elif lang == "zh" and HAS_PYPINYIN:
            result = " ".join(token[0] for token in pinyin(segment, style=Style.TONE))
        elif lang == "any" and HAS_ANYASCII:
            result = anyascii.anyascii(segment)
        else:
            return segment
    except Exception:
        return segment

    # pykakasi silently deletes characters it has no reading for, so an empty
    # result means the text would be lost rather than transliterated.
    if not result.strip():
        return segment
    return result


def romanize_lyrics(lyrics: str, lang: str, han_default: str = "zh") -> str:
    def convert(segment: str) -> str:
        if lang == "mixed":
            return romanize_mixed_segment(segment, han_default)
        return romanize_single_segment(segment, lang)

    converted = []
    for line in lyrics.splitlines():
        match = TIMESTAMP_REGEX.match(line)
        if match:
            converted.append(f"{match.group(1)}{convert(match.group(2))}")
        else:
            converted.append(convert(line))
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
    credit headers are ignored, because they are metadata rather than lyrics.
    """
    asian: set[str] = set()
    has_other = False

    for line in text.splitlines():
        if is_credit_line(line):
            continue
        runs = segment_line(strip_timestamps(line))
        line_has_kana = any(kind == KANA for kind, _ in runs)
        for kind, fragment in runs:
            if kind == HANGUL:
                asian.add("ko")
            elif kind == KANA:
                asian.add("ja")
            elif kind == HAN:
                asian.add(_han_evidence(fragment) or ("ja" if line_has_kana else "zh"))
            elif kind == OTHER:
                has_other = True

    if len(asian) > 1:
        return "mixed"
    if asian:
        return asian.pop()
    return "any" if has_other else None
