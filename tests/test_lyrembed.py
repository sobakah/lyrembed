"""Regression tests for lyrembed."""

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lyrembed import config, providers, tagging, text  # noqa: E402
from lyrembed import batch, core, ui  # noqa: E402
from lyrembed.core import natural_key  # noqa: E402
from lyrembed import selfupdate  # noqa: E402

REAL_LATEST = selfupdate.latest  # before no_update_check replaces it


@pytest.fixture(autouse=True)
def no_update_check(monkeypatch, tmp_path):
    """The check for a newer lyrembed asks GitHub and keeps a state file:
    neither for real in tests, unless a test fakes the answer itself."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    def offline(timeout=30):
        raise selfupdate.UpdateError("no network in tests")
    monkeypatch.setattr(selfupdate, "latest", offline)


# --- config merging ----------------------------------------------------------
def test_partial_user_config_keeps_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"settings": {"search_cache_entries": 40}}', encoding="utf-8")
    merged, warnings = config.load_config(path)
    assert warnings == []
    assert merged["settings"]["search_cache_entries"] == 40
    # Sections omitted by the user must survive the merge.
    assert "non_latin_ratio_threshold" in merged["settings"]
    assert "ignore_words_artist_match" in merged["cleaning"]


def test_invalid_config_on_search_path_falls_back_with_warning(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(config, "config_search_paths", lambda: [path])
    merged, warnings = config.load_config()
    assert warnings
    assert merged["api"]["lrclib_url"].startswith("https://")


def test_invalid_explicit_config_is_fatal(tmp_path):
    # -c names a file on purpose; silently running with defaults hides that.
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config(path)
    with pytest.raises(config.ConfigError):
        config.load_config(tmp_path / "missing.json")


def test_defaults_are_not_mutated_by_merge(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"aliases": [["foo"]]}', encoding="utf-8")
    config.load_config(path)
    assert config.DEFAULT_CONFIG["aliases"] == []


# --- artist matching ---------------------------------------------------------
def test_alias_lookup_respects_word_boundaries():
    # "Liu Yifei" used to match the IU alias group via a naive substring test.
    assert text.get_artist_aliases("Liu Yifei") == []
    assert "이지은" in text.get_artist_aliases("IU")
    assert "tomorrow x together" in [a.lower() for a in text.get_artist_aliases("TXT")]


def test_matches_artist_rejects_substring_false_positives():
    assert not text.matches_artist("IU", "Liu Yifei")
    assert text.matches_artist("IU", "아이유 (IU)")
    assert text.matches_artist("Tomorrow X Together", "TXT")
    assert not text.matches_artist("Red Velvet", "The Velvet Underground")


# --- timestamps and script detection -----------------------------------------
@pytest.mark.parametrize("line", ["[0:12.34]hello", "[00:12.34]hello", "[00:12]hello", "[000:12:34]hello"])
def test_single_digit_and_variant_timestamps(line):
    assert text.has_timestamps(line)


def test_lrc_metadata_is_not_a_timestamp():
    assert not text.has_timestamps("[ar:Some Artist]\n[ti:Song]\nplain line")


def test_non_latin_detection():
    assert text.is_non_latin("사랑해 사랑해 사랑해")
    assert not text.is_non_latin("[00:10.00]I love you so")
    assert not text.is_non_latin("Café naïve résumé")


def test_korean_lyrics_with_an_english_chorus_are_original(cfg):
    # Counted per letter, one English line outweighed several Korean ones
    # (13% non-Latin letters) and the lyrics were labelled LATIN.
    lyrics = (
        "[00:01.00]I got that boom boom boom, baby let me show you\n"
        "[00:05.00]너를 보면 내 맘이 떨려\n"
        "[00:09.00]Oh yeah, I can't stop loving you tonight\n"
        "[00:13.00]사랑해 영원히\n"
        "[00:17.00]I got that boom boom boom, baby let me show you"
    )
    assert text.is_non_latin(lyrics)
    assert text.script_label(lyrics) == "original"


def test_lrc_tags_and_credits_do_not_count_as_lyrics(cfg):
    korean = ("[ar:Some Artist Name]\n[ti:Some Long English Title Here]\n"
              "[al:The Album Name Deluxe Edition]\n[by:lyricsuploader]\n"
              "[00:01.00]너를 보면\n[00:05.00]사랑해")
    assert text.is_non_latin(korean)
    romanized = "[00:00.00]作词 : 김이나\n[00:01.00]neoreul bomyeon\n[00:05.00]saranghae"
    assert text.script_label(romanized) == "latin"
    # A Chinese album tag must not turn a Korean song into a mixed one.
    assert text.script_label("[al:我的歌]\n[00:01.00]사랑해") == "original"


def test_mixed_label_needs_two_asian_scripts(cfg):
    assert text.script_label("[00:01.00]사랑해\n[00:02.00]ありがとう") == "mixed"
    assert text.script_label("[00:01.00]사랑해\n[00:02.00]I love you") == "original"
    candidate = providers.Candidate.build(provider="LRCLIB", artist="A", title="T", album="",
                                          text="[00:01.00]사랑해\n[00:02.00]ありがとう")
    assert candidate.mixed and not candidate.latin


def test_detect_script():
    assert text.detect_script("사랑해") == "ko"
    assert text.detect_script("ありがとう 世界") == "ja"
    assert text.detect_script("我爱你") == "zh"
    assert text.detect_script("Just English") is None


def test_romanization_preserves_timestamps():
    lyrics = "[00:10.00]사랑해\n[00:12.50]hello"
    out = text.romanize_lyrics(lyrics, "ko")
    assert out.splitlines()[0].startswith("[00:10.00]")
    assert out.splitlines()[1] == "[00:12.50]hello"


# --- mixed-script romanization -----------------------------------------------
def test_segmentation_splits_by_script():
    runs = text.segment_line("사랑 我爱 ありがとう ok")
    kinds = [kind for kind, _ in runs]
    assert text.HANGUL in kinds and text.HAN in kinds and text.KANA in kinds
    assert "".join(frag for _, frag in runs) == "사랑 我爱 ありがとう ok"


def test_latin_text_is_never_touched():
    # The core bug: a second pass used to shred the first pass's output.
    assert text.romanize_lyrics("wǒ ài nǐ", "ja") == "wǒ ài nǐ"
    assert text.romanize_lyrics("wǒ ài nǐ", "ko") == "wǒ ài nǐ"
    assert text.romanize_lyrics("hello world", "mixed") == "hello world"


@pytest.mark.skipif(not (text.HAS_KAKASI and text.HAS_PYPINYIN and text.HAS_KOREAN),
                    reason="all three romanization backends required")
def test_three_scripts_in_one_line():
    out = text.romanize_lyrics("[00:12.30]사랑해 我爱你 ありがとう", "mixed")
    assert out.startswith("[00:12.30]")
    assert "saranghae" in out          # Hangul via korean-romanizer
    assert "wǒ" in out                 # Hanzi via pypinyin, tone marks intact
    assert "arigatou" in out           # Kana via pykakasi
    assert "ware" not in out           # was misread as Kanji before


@pytest.mark.skipif(not (text.HAS_KAKASI and text.HAS_PYPINYIN),
                    reason="pykakasi and pypinyin required")
def test_sequential_passes_are_lossless():
    after_zh = text.romanize_lyrics("我爱你 ありがとう", "zh")
    assert "wǒ ài nǐ" in after_zh
    after_ja = text.romanize_lyrics(after_zh, "ja")
    assert "wǒ ài nǐ" in after_ja      # pinyin survived the Japanese pass
    assert "arigatou" in after_ja


@pytest.mark.skipif(not text.HAS_KAKASI, reason="pykakasi required")
def test_kana_adjacency_marks_han_as_japanese():
    assert text.romanize_lyrics("東京の夜", "mixed") == "toukyou no yoru"


@pytest.mark.skipif(not text.HAS_KAKASI, reason="pykakasi required")
def test_kanji_keeps_its_kana_context():
    # A lone 見 is read "ken"; together with its Kana tail it is "mitsu".
    # Splitting script runs apart used to destroy that context.
    out = text.romanize_lyrics("見つけたよ ココロが安らぐ", "mixed")
    assert "ken" not in out
    assert "mitsuketayo" in out
    assert "yasuragu" in out
    assert "kokoro ga" in out


@pytest.mark.skipif(not text.HAS_KAKASI, reason="pykakasi required")
def test_okurigana_is_not_split_by_spaces():
    assert text.romanize_lyrics("愛してる", "ja") == "itoshiteru"
    assert text.romanize_lyrics("心が震える", "ja") == "kokoro ga furueru"
    assert text.romanize_lyrics("空を見上げて", "ja") == "sora wo miagete"
    # Genuine word boundaries keep their space.
    assert text.romanize_lyrics("東京の夜", "ja") == "toukyou no yoru"


@pytest.mark.skipif(not (text.HAS_KAKASI and text.HAS_PYPINYIN and text.HAS_KOREAN),
                    reason="all three romanization backends required")
def test_adjacent_runs_of_different_languages_stay_separate():
    out = text.romanize_lyrics("사랑해 我爱你 ありがとう", "mixed", han_default="zh")
    assert out == "saranghae wǒ ài nǐ arigatou"


def test_han_evidence_from_unique_characters():
    assert text._han_evidence("気持") == "ja"      # shinjitai-only
    assert text._han_evidence("山川") is None      # shared, stays ambiguous


def test_traditional_and_simplified_chinese_are_not_taken_for_japanese():
    # A hand-kept "Japanese only" list contained these; Traditional Chinese
    # uses 問 間 師 級 傷, Simplified Chinese uses 区 医 欠 芸.
    for run in ("時間", "老師", "問題", "等級", "傷心", "医生", "欠", "芸"):
        assert text._han_evidence(run) != "ja", run
    assert text._han_evidence("区别") == "zh"
    assert text._han_evidence("駅前") == "ja"


@pytest.mark.skipif(not text.HAS_KAKASI, reason="pykakasi required")
def test_kakasi_coverage_detects_simplified_chinese():
    # pykakasi's dictionary holds no simplified forms, so incomplete coverage
    # is proof the run is not Japanese.
    assert text._kakasi_han_coverage("东车书长门") < 1.0
    assert text._kakasi_han_coverage("東京物語") == 1.0
    assert text._han_evidence("我爱你") == "zh"
    assert text._han_evidence("这个时候") == "zh"


@pytest.mark.skipif(not text.HAS_KAKASI, reason="pykakasi required")
def test_romanizer_never_deletes_text():
    # pykakasi drops characters it cannot read; the result must not vanish.
    out = text.romanize_lyrics("东车书长门", "ja")
    assert out.strip()
    if text.HAS_PYPINYIN:
        assert out == "dōng chē shū cháng mén"


@pytest.mark.skipif(not (text.HAS_KAKASI and text.HAS_PYPINYIN),
                    reason="pykakasi and pypinyin required")
def test_romanizer_never_deletes_part_of_the_text():
    # The old guard only caught a completely empty result: 区别 became 'ku',
    # 我们区别 became 'ware'.
    assert text.romanize_lyrics("我们区别", "mixed") == "wǒ men qū bié"
    assert text.romanize_lyrics("区别", "mixed") == "qū bié"
    # Forced Japanese: unreadable characters are cut out before pykakasi sees
    # them, because it also swallows the following character ('时候' -> '时').
    forced = text.romanize_lyrics("这个时候", "ja")
    assert len(forced.split()) == 4, forced
    assert text.romanize_lyrics("区别", "ja").endswith("bié")


@pytest.mark.skipif(not (text.HAS_KAKASI and text.HAS_PYPINYIN),
                    reason="pykakasi and pypinyin required")
def test_traditional_chinese_follows_the_han_default():
    assert text.romanize_lyrics("時間", "mixed", han_default="zh") == "shí jiān"
    assert text.romanize_lyrics("老師", "mixed", han_default="zh") == "lǎo shī"
    # Japanese still works where the evidence or context says so.
    assert text.romanize_lyrics("時間です", "mixed") == "jikan desu"
    assert text.romanize_lyrics("気持ち", "mixed") == "kimochi"


@pytest.mark.skipif(not text.HAS_KAKASI, reason="pykakasi required")
def test_spaced_kana_still_marks_han_as_japanese():
    runs = text.segment_line("東京 の 夜")
    kind = next(text._resolve_han(runs, i, "zh") for i, (k, _) in enumerate(runs) if k == text.HAN)
    assert kind == "ja"


@pytest.mark.skipif(not text.HAS_KAKASI, reason="pykakasi required")
def test_spaced_kana_does_not_capture_chinese_on_a_mixed_line():
    # The user-facing case: Korean, Chinese and Japanese separated by spaces.
    runs = text.segment_line("사랑해 我爱你 ありがとう")
    kind = next(text._resolve_han(runs, i, "ja") for i, (k, _) in enumerate(runs) if k == text.HAN)
    assert kind == "zh"


@pytest.mark.skipif(not (text.HAS_KAKASI and text.HAS_PYPINYIN),
                    reason="pykakasi and pypinyin required")
def test_han_default_decides_ambiguous_runs():
    ja = text.romanize_lyrics("東京物語", "mixed", han_default="ja")
    zh = text.romanize_lyrics("東京物語", "mixed", han_default="zh")
    assert ja != zh
    assert "toukyou" in ja


def test_detect_script_reports_mixed():
    assert text.detect_script("사랑해 我爱你 ありがとう") == "mixed"
    assert text.detect_script("사랑해 ありがとう") == "mixed"
    assert text.detect_script("我的世界") == "zh"
    assert text.detect_script("ありがとう") == "ja"
    assert text.detect_script("사랑해") == "ko"
    assert text.detect_script("plain english") is None
    assert text.detect_script("[00:10.00]") is None


def test_netease_credit_headers_are_not_lyrics():
    # NetEase prepends "作词/作曲/编曲" credits; they are Chinese metadata and
    # used to make every Korean song from that provider look mixed.
    assert text.is_credit_line("[00:00.000] 作词 : 김동현, MARK")
    assert text.is_credit_line("作曲：Timothy Bullock")
    assert text.is_credit_line("OP : Some Publisher")
    # A lyric line that merely contains a colon must not be swallowed.
    assert not text.is_credit_line("[01:00.00]我说:别走")
    assert not text.is_credit_line("[01:00.00]차가운 세상")


def test_credit_headers_do_not_trigger_mixed_mode():
    lrc = (
        "[00:00.000] 作词 : 김동현\n"
        "[00:01.000] 作曲 : Timothy Bullock\n"
        "[00:02.000] 编曲 : Bos Billions\n"
        "[00:17.810]차가운 세상 눈을 감고\n"
        "[00:26.880]We'll take it slow"
    )
    assert text.detect_script(lrc) == "ko"


def test_typographic_punctuation_is_not_a_script():
    # …, em dashes and curly quotes are not ASCII but need no transliteration.
    for mark in ("…", "—", "\u2018", "\u2019", "\u201c", "\u00bb"):
        assert text._char_class(mark) == text.KEEP


def test_one_asian_script_beside_latin_is_not_mixed():
    # The mixed option must not be preselected when a single backend suffices.
    assert text.detect_script("사랑해… 언제나") == "ko"
    assert text.detect_script("너의 이름 — forever") == "ko"
    assert text.detect_script("\u2018사랑\u2019 이라는 말") == "ko"
    assert text.detect_script("이 밤 hello world") == "ko"
    assert text.detect_script("ありがとう…ずっと") == "ja"


@pytest.mark.skipif(not text.HAS_KAKASI, reason="pykakasi required")
def test_chinese_beside_japanese_is_still_mixed():
    # Kana on the line must not absorb a proven Chinese run.
    assert text.detect_script("我爱你 ありがとう") == "mixed"


def test_latin_only_text_needs_no_romanization():
    assert text.detect_script("Caf\u00e9 na\u00efve r\u00e9sum\u00e9") is None


def test_no_collision_between_converted_and_kept_text():
    out = text.romanize_lyrics("사랑world", "mixed")
    assert " " in out and "world" in out


# --- editor ------------------------------------------------------------------


# --- ranking -----------------------------------------------------------------
def _cand(**kwargs):
    base = dict(provider="LRCLIB", artist="A", title="T", album="X", text="[00:01.00]la")
    base.update(kwargs)
    return providers.Candidate(**base)


def test_unknown_duration_does_not_outrank_verified_match():
    verified = _cand(synced=True, latin=True, has_duration=True, diff=2)
    unknown = _cand(synced=True, latin=True, has_duration=False, diff=0)
    ranked = sorted([unknown, verified], key=providers.rank_key)
    assert ranked[0] is verified


def test_synced_lyrics_rank_above_an_exact_plain_match(cfg):
    # LRCLIB's exact match is often a plain-only upload; ranked first, it
    # pushed the synced versions down and was what Enter saved.
    exact = _cand(synced=False, exact=True, has_duration=True)
    fuzzy = _cand(synced=True, exact=False, has_duration=True)
    exact_synced = _cand(synced=True, exact=True, has_duration=True)
    assert sorted([exact, fuzzy], key=providers.rank_key)[0] is fuzzy
    assert sorted([exact, fuzzy, exact_synced], key=providers.rank_key)[0] is exact_synced
    cfg["settings"]["prefer_synced"] = False
    assert sorted([fuzzy, exact], key=providers.rank_key)[0] is exact


def test_enter_saves_the_synced_match_not_the_exact_plain_one(cfg):
    plain = _built(provider="LRCLIB", artist="A", title="Song", duration=200, exact=True, text="la la")
    synced = _built(provider="NETEASE", artist="A", title="Song", duration=201)
    report = providers.SearchReport(candidates=sorted([plain, synced], key=providers.rank_key))
    assert report.candidates[core.default_highlight(report, 200) - 1] is synced
    only_plain = providers.SearchReport(candidates=[plain])
    assert core.default_highlight(only_plain, 200) == 1


def test_prefer_latin_is_off_by_default_and_then_neutral(cfg):
    original = _cand(synced=True, latin=False, has_duration=True, diff=0)
    romanized = _cand(synced=True, latin=True, has_duration=True, diff=3)
    # Off: the script plays no part, so the closer duration wins.
    assert sorted([romanized, original], key=providers.rank_key)[0] is original
    assert sorted([original, romanized], key=providers.rank_key)[0] is original

    cfg["settings"]["prefer_latin"] = True
    assert sorted([original, romanized], key=providers.rank_key)[0] is romanized


def test_exact_flag_does_not_depend_on_response_order(monkeypatch):
    # /get and /search return the same record; the concurrent responses used to
    # decide the exact flag by whichever arrived first.
    record = {"id": 42, "trackName": "Song", "artistName": "A",
              "syncedLyrics": "[00:01.00]x", "duration": 200}

    def make_session(order):
        responses = iter(order)

        class FakeResponse:
            status_code = 200
            reason = "OK"

            def __init__(self, endpoint):
                self.endpoint = endpoint

            def json(self):
                return record if self.endpoint == "get" else [record]

        class FakeSession:
            def get(self, url, params=None, timeout=None, headers=None):
                return FakeResponse(next(responses))

        return FakeSession()

    for order in (["get", "search"], ["search", "get"]):
        monkeypatch.setattr(providers, "get_session", lambda o=order: make_session(o))
        monkeypatch.setattr(providers, "_lrclib_queries",
                            lambda *a, **k: [("get", {"q": "x"}), ("search", {"q": "x"})])
        report = providers.SearchReport()
        found = providers.query_lrclib("Song", "A", "", 200, report)
        assert len(found) == 1, order
        assert found[0].exact is True, order


def test_prefer_album_artist_searches_with_the_album_artist(cfg, tmp_path, monkeypatch):
    from mutagen.flac import FLAC

    path = _make_flac(tmp_path / "song.flac")
    audio = FLAC(path)
    audio["title"], audio["artist"], audio["albumartist"] = "Song", "Member A, Member B", "Group"
    audio.save()
    meta = tagging.read_metadata(path)
    assert meta.album_artist == "Group"

    searched = []
    monkeypatch.setattr(providers, "collect_candidates",
                        lambda title, artist, *a, **k: searched.append(artist) or providers.SearchReport())
    core.find_automatic(meta)
    assert set(searched) == {"Member A, Member B"}          # off by default

    searched.clear()
    cfg["settings"]["prefer_album_artist"] = True
    core.find_automatic(meta)
    assert set(searched) == {"Group"}


def test_prefer_album_artist_skips_compilations(cfg):
    cfg["settings"]["prefer_album_artist"] = True
    meta = tagging.TrackMeta(path=Path("x.flac"), title="Song", artist="Artist")
    assert meta.search_artist == "Artist"                  # no album artist
    meta.album_artist = "Various Artists"
    assert meta.search_artist == "Artist"
    meta.album_artist, meta.artist = "Group", ""
    assert meta.search_artist == "Group" and meta.is_taggable


def test_strip_all_brackets_removes_member_lists_from_queries(cfg, monkeypatch):
    # Tags such as 'Group ("Member A", "Member B")' sent the member list to
    # LRCLIB, which lists only the group, so nothing was found.
    sent = []

    class FakeResponse:
        status_code = 404
        reason = "Not Found"

        def json(self):
            return {}

    class FakeSession:
        def get(self, url, params=None, timeout=None, headers=None):
            sent.append(params or {})
            return FakeResponse()

    monkeypatch.setattr(providers, "get_session", lambda: FakeSession())
    artist = 'Group ("Member A", "Member B")'

    providers.query_lrclib("Song [Live]", artist, "", 0, providers.SearchReport())
    assert any(p.get("artist_name") == artist for p in sent)        # off by default

    sent.clear()
    cfg["cleaning"]["strip_all_brackets"] = True
    providers.query_lrclib("Song [Live]", artist, "", 0, providers.SearchReport())
    assert sent and all("(" not in str(p) and "[" not in str(p) for p in sent)
    assert {"track_name": "Song", "artist_name": "Group"} in sent


def test_strip_all_brackets_handles_nesting_and_keeps_bracket_only_tags(cfg):
    cfg["cleaning"]["strip_all_brackets"] = True
    assert text.clean_tag("Song (Version (Live) [2020]) Edit") == "Song Edit"
    assert text.clean_tag("Song (unclosed") == "Song (unclosed"
    assert providers._cleaned("[Intro]") == "[Intro]"   # never an empty query


def test_lrclib_queries_are_deduplicated():
    queries = providers._lrclib_queries("Song", "IU", "Album", 200)
    assert len(queries) == len(set((e, tuple(sorted(p.items()))) for e, p in queries))
    assert any(endpoint == "get" for endpoint, _ in queries)


# --- search cache ------------------------------------------------------------
def test_search_cache_normalises_the_key():
    a = providers.SearchCache.key("Song (feat. X)", "IU", "Album", 200)
    b = providers.SearchCache.key("  song  ", "iu", "ALBUM", 200)
    assert a == b


def test_search_cache_keeps_a_different_query_apart():
    a = providers.SearchCache.key("Song", "IU", "", 200)
    b = providers.SearchCache.key("Other Song", "IU", "", 200)
    assert a != b


def test_search_cache_returns_the_stored_report():
    cache = providers.SearchCache()
    key = providers.SearchCache.key("Song", "IU", "", 200)
    report = providers.SearchReport(candidates=[_cand()])
    cache.set(key, providers.CachedSearch(report, highlight=2))

    entry = cache.get(key)
    assert entry is not None
    assert entry.report is report
    assert entry.highlight == 2


def test_search_cache_evicts_least_recently_used():
    cache = providers.SearchCache(max_entries=2)
    keys = [providers.SearchCache.key(name, "A", "", 0) for name in ("one", "two", "three")]
    for key in keys[:2]:
        cache.set(key, providers.CachedSearch(providers.SearchReport()))
    cache.get(keys[0])                # touch the first so the second is oldest
    cache.set(keys[2], providers.CachedSearch(providers.SearchReport()))

    assert len(cache) == 2
    assert cache.get(keys[0]) is not None
    assert cache.get(keys[1]) is None


def test_search_cache_discard_forces_a_new_search():
    cache = providers.SearchCache()
    key = providers.SearchCache.key("Song", "IU", "", 200)
    cache.set(key, providers.CachedSearch(providers.SearchReport()))
    cache.discard(key)
    assert cache.get(key) is None


# --- menu rendering ----------------------------------------------------------
def _menu_groups():
    return [
        ("Edit", [("e", "Open in editor"), ("r", "Romanize"), ("v", "Full preview")]),
        ("Navigate", [("p", "Previous track"), ("s", "Next track"), ("q", "Quit")]),
    ]


# --- candidate selection UI --------------------------------------------------
def test_describe_candidate_names_the_entry():
    cand = _cand(artist="IU", title="Love Wins All", synced=True, exact=True,
                 has_duration=True, diff=0, duration=200, track_duration=200)
    line = core.describe_candidate(1, cand)
    assert line.startswith("#1 IU - Love Wins All")
    assert "exact" in line and "synced" in line
    assert "duration OK" in line and "±0s" not in line


def test_describe_candidate_without_duration():
    line = core.describe_candidate(3, _cand(artist="", title="Song", synced=False))
    assert line.startswith("#3 Song")
    assert "plain" in line and "duration" not in line


def test_exact_match_is_preselected_only_when_first_result_is_exact():
    # Enter confirms the top entry only when the search produced an exact hit.
    exact_first = [_cand(exact=True), _cand()]
    fuzzy_first = [_cand(), _cand(exact=True)]
    assert (1 if exact_first[0].exact else None) == 1
    assert (1 if fuzzy_first[0].exact else None) is None


# --- sorting -----------------------------------------------------------------
def test_natural_sort_orders_numbers_numerically():
    names = ["Track 10.flac", "Track 2.flac", "Track 1.flac"]
    paths = [Path("/music") / n for n in names]
    ordered = [p.name for p in sorted(paths, key=natural_key)]
    assert ordered == ["Track 1.flac", "Track 2.flac", "Track 10.flac"]


# --- tagging -----------------------------------------------------------------
def test_id3_text_avoids_nul_join():
    from mutagen.id3 import ID3, TPE1

    tags = ID3()
    tags.add(TPE1(encoding=3, text=["Artist A", "Artist B"]))
    assert tagging._id3_text(tags, "TPE1") == "Artist A"
    assert "\x00" not in tagging._id3_text(tags, "TPE1")


def test_parse_number_handles_slash_form():
    assert tagging._parse_number("5/12") == 5
    assert tagging._parse_number([(3, 10)]) == 3
    assert tagging._parse_number("") == 0


def test_embed_and_delete_roundtrip_flac(tmp_path):
    from mutagen.flac import FLAC

    src = _make_flac(tmp_path / "song.flac")
    audio = FLAC(src)
    audio["UNSYNCEDLYRICS"] = "stale text"
    audio.save()

    tagging.embed_lyrics(src, "[00:01.00]fresh")
    audio = FLAC(src)
    assert audio["LYRICS"][0] == "[00:01.00]fresh"
    # The competing field must be gone, not merely shadowed.
    assert "unsyncedlyrics" not in {k.lower() for k in audio.keys()}

    tagging.delete_lyrics(src)
    assert "lyrics" not in {k.lower() for k in FLAC(src).keys()}


def test_embed_raises_tagging_error_on_readonly(tmp_path):
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("permission semantics differ for root/Windows")
    src = _make_flac(tmp_path / "ro.flac")
    src.chmod(0o444)
    with pytest.raises(tagging.TaggingError):
        tagging.embed_lyrics(src, "text")


def test_metadata_cache_reuses_until_mtime_changes(tmp_path, monkeypatch):
    src = _make_flac(tmp_path / "cached.flac")
    cache = tagging.MetadataCache()

    calls = {"n": 0}
    original = tagging.read_metadata

    def counting(path):
        calls["n"] += 1
        return original(path)

    monkeypatch.setattr(tagging, "read_metadata", counting)
    cache.get(src)
    cache.get(src)
    assert calls["n"] == 1

    tagging.embed_lyrics(src, "new")
    cache.get(src)
    assert calls["n"] == 2


def test_unreadable_file_is_reported_not_swallowed(tmp_path):
    broken = tmp_path / "broken.flac"
    broken.write_bytes(b"not a flac file at all")
    meta = tagging.read_metadata(broken)
    assert meta.error
    assert not meta.is_taggable


# A minimal but valid FLAC stream: "fLaC" plus a single STREAMINFO block
# (44100 Hz, stereo, 16 bit, 10 s). Enough for mutagen to read and tag.
MINIMAL_FLAC_B64 = "ZkxhQ4AAACIQABAAAAAAAAAACsRC8AAGuqgAAAAAAAAAAAAAAAAAAAAA"


def _make_flac(path: Path) -> Path:
    import base64

    path.write_bytes(base64.b64decode(MINIMAL_FLAC_B64))
    return path


# --- review fixes ------------------------------------------------------------
import copy  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

from lyrembed import cli  # noqa: E402


@pytest.fixture
def cfg(monkeypatch):
    """A private copy of the defaults as the active config, restored afterwards."""
    fresh = copy.deepcopy(config.DEFAULT_CONFIG)
    monkeypatch.setattr(config, "CONFIG", fresh)
    return fresh


# launcher
def test_launcher_explains_a_missing_package(tmp_path):
    # Named lyrembed.py, the launcher imported itself when lyrembed/ was missing
    # and the explanation below was unreachable.
    launcher = tmp_path / "run.py"
    shutil.copy(Path(__file__).resolve().parent.parent / "run.py", launcher)
    (tmp_path / "cli.py").write_text("", encoding="utf-8")
    # -S -I: an installed lyrembed (pip install -e, PYTHONPATH) must not be found
    # instead of the missing sibling package.
    result = subprocess.run([sys.executable, "-S", "-I", str(launcher)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "package was not found" in result.stderr
    assert "mv cli.py" in result.stderr


def test_project_url_is_not_a_placeholder():
    assert "<" not in config.PROJECT_URL
    assert "<" not in config.DEFAULT_CONFIG["api"]["user_agent"]


# config
def test_user_config_comes_before_a_checkout_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_search_paths()[0] == tmp_path / "lyrembed" / "config.json"


def test_wrong_types_fall_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        '{"settings": {"supported_extensions": ".flac", "search_cache_entries": "many",'
        ' "prefer_latin": 1, "colour": "never"}, "api": {"batch_delay_seconds": 2}}',
        encoding="utf-8",
    )
    merged, warnings = config.load_config(path)
    assert merged["settings"]["supported_extensions"] == config.DEFAULT_CONFIG["settings"]["supported_extensions"]
    assert merged["settings"]["search_cache_entries"] == 64
    assert merged["settings"]["prefer_latin"] is False
    assert merged["api"]["batch_delay_seconds"] == 2      # an int is a valid float
    joined = " ".join(warnings)
    assert "supported_extensions" in joined and "search_cache_entries" in joined
    assert "settings.colour" in joined                    # typo reported, not ignored


def test_example_config_matches_the_defaults():
    import json

    path = Path(__file__).resolve().parent.parent / "config.example.json"
    assert json.loads(path.read_text(encoding="utf-8")) == config.example_config()


def test_example_config_explains_every_key_and_loads_as_the_defaults():
    # A copied example must work: its '_' comments are ignored, not reported.
    path = Path(__file__).resolve().parent.parent / "config.example.json"
    merged, warnings = config.load_config(path)
    assert warnings == []
    merged.pop("_source")
    expected = copy.deepcopy(config.DEFAULT_CONFIG)
    assert merged == expected
    example = config.example_config()
    for section, default in config.DEFAULT_CONFIG.items():
        if isinstance(default, dict):
            for key in default:
                if key != "user_agent":
                    assert example[section][f"_{key}"], f"{section}.{key} has no explanation"


def test_builtin_aliases_come_from_the_data_file(cfg):
    assert any("BTS" in g for g in config.builtin_aliases())
    assert "투모로우바이투게더" in text.get_artist_aliases("TXT")
    assert "周杰倫" in text.get_artist_aliases("Jay Chou")
    assert text.matches_artist("Eason Chan", "陳奕迅")


def test_builtin_aliases_are_unambiguous():
    # A name in two groups, or matching inside another group's name, would
    # join two artists into one.
    groups = config.builtin_aliases()
    names = [name.lower() for group in groups for name in group]
    assert len(names) == len(set(names))
    for i, group in enumerate(groups):
        for name in group:
            for j, other in enumerate(groups):
                if i != j:
                    assert not any(text._contains_alias(o, name) for o in other), (name, other)


def test_user_aliases_extend_and_merge_with_the_builtin_ones(cfg):
    cfg["aliases"] = [["TXT", "투바투"], ["Some Band", "어떤 밴드"]]
    assert {"투모로우바이투게더", "투바투"} <= set(text.get_artist_aliases("TXT"))
    assert text.get_artist_aliases("Some Band") == ["어떤 밴드"]
    assert "아이유" in text.get_artist_aliases("IU")                # built-ins still there


def test_list_aliases_prints_the_groups_in_effect(cfg, capsys, monkeypatch, tmp_path):
    from lyrembed import cli

    user = tmp_path / "config.json"
    user.write_text('{"aliases": [["Some Band", "어떤 밴드"]]}', encoding="utf-8")
    assert cli.main(["--list-aliases", "-c", str(user), "--color", "never"]) == 0
    out = capsys.readouterr().out
    assert "Some Band = 어떤 밴드" in out and "TOMORROW X TOGETHER = TXT" in out


# command line
def test_batch_only_flags_require_auto(capsys):
    for argv in (["--dry-run"], ["--overwrite"], ["--sidecar", "--no-sidecar"], ["--max-files", "-1"],
                 ["--romanize"], ["--no-romanize"], ["--auto", "--romanize", "--no-romanize"]):
        with pytest.raises(SystemExit) as raised:
            cli.parse_args(argv)
        assert raised.value.code == 2, argv


def test_batch_mode_never_prompts_for_a_path(tmp_path, monkeypatch, cfg):
    # Run from cron, a prompt either blocks or reads EOF and exited with 0.
    monkeypatch.setattr(config, "load_config", lambda path=None: (cfg, []))
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("batch mode prompted")))
    assert cli.main(["--auto", str(tmp_path / "missing")]) == 2
    assert cli.main(["--auto", str(tmp_path)]) == 2       # exists, but empty


# scanning and tagging
def test_unsupported_configured_extension_is_skipped_not_fatal(tmp_path, cfg):
    cfg["settings"]["supported_extensions"] = [".flac", ".wav"]
    (tmp_path / "song.wav").write_bytes(b"RIFF")
    _make_flac(tmp_path / "song.flac")
    files, err = core.find_music_files(tmp_path)
    assert err is None
    assert [f.name for f in files] == ["song.flac"]
    # Reading a file of that type directly is an error on the track, not a crash.
    assert "Unsupported" in tagging.read_metadata(tmp_path / "song.wav").error


def test_max_file_count_zero_means_unlimited(tmp_path, cfg):
    for n in range(3):
        _make_flac(tmp_path / f"{n}.flac")
    cfg["settings"]["max_file_count"] = 2
    assert core.find_music_files(tmp_path) == (None, "TOO_MANY_FILES")
    cfg["settings"]["max_file_count"] = 0
    files, err = core.find_music_files(tmp_path)
    assert err is None and len(files) == 3


def test_id3_version_of_the_file_is_kept(tmp_path, cfg):
    from mutagen.id3 import ID3, TIT2

    path = tmp_path / "tags.id3"
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TIT2(encoding=3, text="x"))
    tags.save(path, v2_version=3)
    assert tagging._id3_save_version(ID3(path)) == 3
    assert tagging._id3_save_version(None) == 4
    cfg["settings"]["id3_v2_version"] = 3
    assert tagging._id3_save_version(None) == 3


def test_sylt_frames_are_read_as_lrc():
    from mutagen.id3 import SYLT

    frame = SYLT(encoding=3, lang="eng", format=2, type=1, desc="",
                 text=[("hello", 1500), ("world", 62_340)])
    assert tagging._sylt_to_lrc(frame) == "[00:01.50]hello\n[01:02.34]world"
    frames = SYLT(encoding=3, lang="eng", format=1, type=1, desc="", text=[("x", 10)])
    assert tagging._sylt_to_lrc(frames) == ""      # MPEG-frame stamps: not convertible


def test_overwritten_lyrics_are_backed_up(tmp_path):
    base = tmp_path / "music"
    track = base / "Album" / "01.flac"
    target = core.backup_lyrics(tmp_path / "backup", base, track, "[00:01.00]old")
    assert target == tmp_path / "backup" / "Album" / "01.flac.lrc"
    assert target.read_text(encoding="utf-8") == "[00:01.00]old"


# navigation


# providers


def test_syncedlyrics_results_are_unverified(monkeypatch, cfg):
    class FakeEngine:
        @staticmethod
        def search(query, **kwargs):
            return "[00:01.00]some lyrics"

    monkeypatch.setattr(providers, "HAS_SYNCEDLYRICS", True)
    monkeypatch.setattr(providers, "syncedlyrics", FakeEngine, raising=False)
    found = providers.query_syncedlyrics("Song", "Artist", providers.SearchReport())
    assert found and all(c.unverified and c.artist == "" for c in found)
    report = providers.SearchReport(candidates=found)
    assert providers.pick_automatic(report, 0) is None


def test_syncedlyrics_wait_is_capped(monkeypatch, cfg):
    class SlowEngine:
        @staticmethod
        def search(query, **kwargs):
            time.sleep(2)
            return "late"

    cfg["api"]["syncedlyrics_timeout_seconds"] = 0.2
    monkeypatch.setattr(providers, "HAS_SYNCEDLYRICS", True)
    monkeypatch.setattr(providers, "syncedlyrics", SlowEngine, raising=False)
    report = providers.SearchReport()
    started = time.monotonic()
    assert providers.query_syncedlyrics("Song", "", report) == []
    assert time.monotonic() - started < 1.5
    assert "Timeout" in report.errors["syncedlyrics"][0]


def _netease_session(lyric_payload):
    class Response:
        status_code = 200
        reason = "OK"

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            if "lyric" in url:
                return Response(lyric_payload)
            return Response({"result": {"songs": [{
                "id": 1, "name": "Song", "artists": [{"name": "Artist"}],
                "album": {"name": "Album"}, "duration": 200_000}]}})

    return Session()


def test_netease_credit_lines_are_not_embedded(monkeypatch, cfg):
    payload = {"lrc": {"lyric": "[00:00.00] 作词 : Someone\n[00:00.50] 作曲 : Other\n"
                                "[00:01.00]차가운 세상\n[00:02.00]我说:别走"}}
    monkeypatch.setattr(providers, "get_session", lambda: _netease_session(payload))
    found = providers.query_netease("Song", "Artist", 200, providers.SearchReport())
    assert found[0].text == "[00:01.00]차가운 세상\n[00:02.00]我说:别走"


def test_netease_user_romanization_is_offered(monkeypatch, cfg):
    payload = {"lrc": {"lyric": "[00:01.00]夜に駆ける"},
               "romalrc": {"lyric": "[by:someone]\n[00:01.00]yo ru ni ka ke ru"}}
    monkeypatch.setattr(providers, "get_session", lambda: _netease_session(payload))
    found = providers.query_netease("Song", "Artist", 200, providers.SearchReport())
    assert len(found) == 2
    romanized = next(c for c in found if c.latin)
    assert "user romanization" in romanized.album and romanized.synced


def test_netease_format_change_is_reported(monkeypatch, cfg):
    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            class Response:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"result": "35b1748964af8a7c1d88"}   # encrypted payload

            return Response()

    monkeypatch.setattr(providers, "get_session", lambda: Session())
    report = providers.SearchReport()
    assert providers.query_netease("Song", "Artist", 200, report) == []
    assert report.errors["NetEase"]


def test_quick_lrclib_queries_use_only_get():
    queries = providers._lrclib_queries("Song", "IU", "Album", 200, quick=True)
    assert queries and {endpoint for endpoint, _ in queries} == {"get"}


def test_batch_skips_the_full_search_when_get_is_confident(monkeypatch, cfg):
    calls = []
    exact = _cand(synced=True, exact=True, has_duration=True, duration=200, track_duration=200)

    def fake_collect(title, artist, album, duration, quick=False):
        calls.append(quick)
        return providers.SearchReport(candidates=[exact] if quick else [])

    monkeypatch.setattr(providers, "collect_candidates", fake_collect)
    meta = tagging.TrackMeta(path=Path("/m/a.flac"), title="T", artist="A", duration=200)
    assert core.find_automatic(meta)[1] is exact
    assert calls == [True]


# editor


# romanization layout and terminal width
@pytest.mark.skipif(not (text.HAS_KOREAN or text.HAS_ANYASCII),
                    reason="korean-romanizer or anyascii required")
def test_romanization_can_keep_the_original_line():
    out = text.romanize_lyrics("[00:01.00]사랑해\n[00:02.00]hello", "mixed", keep_original=True)
    lines = out.splitlines()
    assert lines[0] == "[00:01.00]사랑해"
    assert lines[1].startswith("[00:01.00]") and lines[1] != lines[0]
    assert lines[2:] == ["[00:02.00]hello"]           # unchanged lines are not doubled


@pytest.fixture
def colors(cfg):
    cfg["settings"]["color"] = "always"
    ui.apply_color_settings()
    yield ui.StyleUI
    cfg["settings"]["color"] = "never"
    ui.apply_color_settings()


def _yellow_brackets(out: str) -> list[str]:
    """The keys shown with yellow brackets, i.e. the selected choices."""
    yellow = re.escape(ui.StyleUI.YELLOW + ui.StyleUI.BOLD + "[" + ui.StyleUI.RESET)
    return [m.strip() for m in re.findall(yellow + r"(?:\x1b\[[0-9;]*m)*([^\x1b\]]+)", out)]


def test_truncation_counts_terminal_columns():
    line = "사랑해" * 20                                 # 60 characters, 120 columns
    cut = ui._truncate(line, 40)
    assert ui.display_width(cut) <= 40
    assert cut.endswith("…")
    assert ui._truncate("short", 40) == "short"


def test_rare_chinese_variants_are_not_taken_for_japanese():
    # 裏 is a Traditional Chinese variant missing from plain Big5 (CP950 has
    # it); Hong Kong forms such as 峯 are in no Chinese standard at all.
    assert text._charset_evidence("裏") is None
    assert text.detect_script("[00:01.00]七裏香\n[00:02.00]我爱你") == "zh"
    assert text.detect_script("[00:01.00]峯\n[00:02.00]我爱你") == "zh"


@pytest.mark.skipif(not (text.HAS_KAKASI and text.HAS_PYPINYIN),
                    reason="pykakasi and pypinyin required")
def test_japanese_only_characters_need_kana_somewhere_in_the_text():
    # Without any Kana, a "Japanese only" character is more likely a variant
    # the character sets lack; the run follows the Han default instead.
    assert text.romanize_lyrics("[al:峯]\n[00:01.00]我爱你", "mixed") == "[al:fēng]\n[00:01.00]wǒ ài nǐ"
    # With Kana in the song, shinjitai still mark a Kanji-only line as Japanese.
    assert text.romanize_lyrics("[00:01.00]駅前\n[00:02.00]ありがとう", "mixed") == \
        "[00:01.00]ekimae\n[00:02.00]arigatou"


# bulk actions
def _flac_with_lyrics(path: Path, lyrics: str) -> Path:
    from mutagen.flac import FLAC

    _make_flac(path)
    audio = FLAC(path)
    audio["title"], audio["artist"] = path.stem, "Artist"
    audio["LYRICS"] = lyrics
    audio.save()
    return path


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


@pytest.mark.skipif(not (text.HAS_KOREAN or text.HAS_ANYASCII),
                    reason="korean-romanizer or anyascii required")
def test_keeping_the_original_twice_does_not_double_lines():
    once = text.romanize_lyrics("[00:01.00]사랑해\n[00:02.00]hello", "ko", keep_original=True)
    assert text.romanize_lyrics(once, "ko", keep_original=True) == once


# instrumental tracks and unrelated results
def test_instrumental_markers_in_titles():
    for title in ("Blueming (Inst.)", "Song - Instrumental", "夜に駆ける (オフボーカル)",
                  "Song (MR)", "Song [Off Vocal Ver.]", "Song (Karaoke Version)", "平凡之路 (伴奏)"):
        assert text.instrumental_marker(title), title
    for title in ("Mr. Brightside", "Song (Mr. Big Cover)", "Instrumental Love", "Blueming"):
        assert text.instrumental_marker(title) is None, title


def test_title_matching():
    assert text.matches_title("Love Scenario", "LOVE SCENARIO")
    assert text.matches_title("Hey Jude - Remastered 2015", "Hey Jude")
    assert text.matches_title("Merry Christmas Mr. Lawrence", "Merry Christmas, Mr. Lawrence")
    assert not text.matches_title("Opening", "平凡之路")
    assert not text.matches_title("Opening", "New Boy")


def _fake_providers(monkeypatch, found, instrumental=None):
    def lrclib(title, artist, album, duration, report, quick=False, aliases="none", with_get=True):
        for provider, detail in (instrumental or {}).items():
            report.mark_instrumental(provider, detail)
        return list(found)
    monkeypatch.setattr(providers, "query_lrclib", lrclib)
    monkeypatch.setattr(providers, "query_netease", lambda *a, **k: [])
    monkeypatch.setattr(providers, "query_qqmusic", lambda *a, **k: [])
    monkeypatch.setattr(providers, "query_megalobiz", lambda *a, **k: [])
    monkeypatch.setattr(providers, "query_syncedlyrics", lambda *a, **k: [])


def _built(**kwargs):
    base = dict(provider="NetEase", album="X", text="[00:01.00]la la", track_duration=200)
    base.update(kwargs)
    return providers.Candidate.build(**base)


def test_unrelated_results_are_kept_apart_not_shown(cfg, monkeypatch):
    # NetEase pads searches without good hits with popular songs; when no
    # artist matched, all of them used to be listed.
    filler = _built(artist="朴树", title="平凡之路", duration=300)
    _fake_providers(monkeypatch, [filler])
    report = providers.collect_candidates("Opening", "Studio Ghibli Orchestra", "", 200)
    assert report.candidates == [] and report.unrelated == [filler]


def test_translated_title_counts_on_matching_duration(cfg, monkeypatch):
    right = _built(artist="iKON", title="LOVE SCENARIO", duration=201)
    other = _built(artist="iKON", title="KILLING ME", duration=180, text="[00:01.00]other")
    _fake_providers(monkeypatch, [right, other])
    report = providers.collect_candidates("사랑을 했다", "iKON", "", 200)
    assert report.candidates == [right] and report.unrelated == [other]


def test_vocal_version_is_no_match_for_an_instrumental(cfg, monkeypatch):
    # Same artist, same length - but the sung version's lyrics.
    vocal = _built(artist="IU", title="Blueming", duration=200)
    exact = _built(provider="LRCLIB", artist="IU", title="Blueming (Love poem)", duration=200,
                   exact=True, text="[00:01.00]exact")
    _fake_providers(monkeypatch, [vocal, exact])
    report = providers.collect_candidates("Blueming (Inst.)", "IU", "", 200)
    assert report.candidates == [] and len(report.unrelated) == 2
    assert providers.pick_automatic(report, 200) is None


def test_lrclib_instrumental_flag_is_reported(cfg, monkeypatch):
    record = {"id": 1, "trackName": "Merry Christmas Mr. Lawrence", "artistName": "Ryuichi Sakamoto",
              "duration": 347, "instrumental": True, "plainLyrics": None, "syncedLyrics": None}

    class FakeResponse:
        status_code = 200
        reason = "OK"

        def __init__(self, url):
            self.url = url

        def json(self):
            return record if self.url.endswith("/get") else []

    class FakeSession:
        def get(self, url, params=None, timeout=None, headers=None):
            return FakeResponse(url)

    monkeypatch.setattr(providers, "get_session", lambda: FakeSession())
    report = providers.SearchReport()
    assert providers.query_lrclib("Merry Christmas Mr. Lawrence", "Ryuichi Sakamoto", "", 347, report) == []
    assert report.instrumental == {"LRCLIB": "exact match"}


def test_instrumental_version_does_not_flag_the_sung_track(cfg, monkeypatch):
    # An album with both '1&Only' and '1&Only (Inst.)': the instrumental's
    # record matched the sung title once version notes were ignored.
    records = [
        {"id": 1, "trackName": "1&Only (Inst.)", "artistName": "XLOV", "duration": 200,
         "instrumental": True, "plainLyrics": None, "syncedLyrics": None},
        {"id": 2, "trackName": "1&Only", "artistName": "XLOV", "duration": 200,
         "instrumental": False, "plainLyrics": "la", "syncedLyrics": "[00:01.00]la"},
    ]

    class FakeResponse:
        status_code = 200
        reason = "OK"

        def __init__(self, url):
            self.url = url

        def json(self):
            return records[1] if self.url.endswith("/get") else records

    class FakeSession:
        def get(self, url, params=None, timeout=None, headers=None):
            return FakeResponse(url)

    monkeypatch.setattr(providers, "get_session", lambda: FakeSession())
    report = providers.SearchReport()
    found = providers.query_lrclib("1&Only", "XLOV", "", 200, report)
    assert report.instrumental == {}
    assert [c.title for c in found] == ["1&Only"]

    report = providers.SearchReport()
    providers.query_lrclib("1&Only (instrumental)", "XLOV", "", 200, report)
    assert report.instrumental == {"LRCLIB": "1&Only (Inst.)"}


def test_netease_pure_music_of_another_version_is_ignored(cfg, monkeypatch):
    song = {"id": 7, "name": "1&Only (Inst.)", "artists": [{"name": "XLOV"}],
            "album": {"name": "I ONE"}, "duration": 200000}

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeSession:
        def get(self, url, params=None, timeout=None, headers=None):
            if "lyric" in url:
                return FakeResponse({"pureMusic": True, "lrc": {"lyric": "[00:05.00]纯音乐，请欣赏"}})
            return FakeResponse({"result": {"songs": [song]}})

    monkeypatch.setattr(providers, "get_session", lambda: FakeSession())
    report = providers.SearchReport()
    providers.query_netease("1&Only", "XLOV", 200, report)
    assert report.instrumental == {}


def test_netease_pure_music_is_flagged_not_listed(cfg, monkeypatch):
    song = {"id": 7, "name": "Merry Christmas Mr. Lawrence", "artists": [{"name": "坂本龍一"}],
            "album": {"name": "A"}, "duration": 347000}

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeSession:
        def get(self, url, params=None, timeout=None, headers=None):
            if "lyric" in url:
                return FakeResponse({"pureMusic": True, "lrc": {"lyric": "[00:05.00]纯音乐，请欣赏"}})
            return FakeResponse({"result": {"songs": [song]}})

    monkeypatch.setattr(providers, "get_session", lambda: FakeSession())
    report = providers.SearchReport()
    assert providers.query_netease("Merry Christmas Mr. Lawrence", "Ryuichi Sakamoto", 347, report) == []
    assert "NetEase" in report.instrumental


def test_batch_skips_instrumental_tracks_without_searching(cfg, tmp_path, monkeypatch, capsys):
    from mutagen.flac import FLAC

    inst = _make_flac(tmp_path / "inst.flac")
    audio = FLAC(inst)
    audio["title"], audio["artist"] = "Blueming (Inst.)", "IU"
    audio.save()
    zxx = _make_flac(tmp_path / "zxx.flac")
    audio = FLAC(zxx)
    audio["title"], audio["artist"], audio["language"] = "Opening", "Orchestra", "zxx"
    audio.save()
    monkeypatch.setattr(providers, "collect_candidates",
                        lambda *a, **k: pytest.fail("instrumental tracks must not be searched"))
    assert batch.run_batch([inst, zxx], tmp_path, tagging.MetadataCache(), False, False) == 0
    out = capsys.readouterr().out
    assert "title says 'Inst.'" in out and "'zxx'" in out


def test_musicbrainz_verdicts(cfg, monkeypatch, tmp_path):
    from mutagen.flac import FLAC

    answers = {
        "inst": {"relations": [{"type": "performance", "attributes": ["instrumental"],
                                "work": {"languages": ["kor"]}}]},
        "zxx": {"relations": [{"type": "performance", "attributes": [], "work": {"languages": ["zxx"]}}]},
        "song": {"relations": [{"type": "performance", "attributes": [], "work": {"languages": ["kor"]}}]},
    }

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeSession:
        def get(self, url, params=None, timeout=None, headers=None):
            return FakeResponse(answers[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(providers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(providers, "_MB_INTERVAL", 0.0)
    monkeypatch.setattr(providers, "_MB_CACHE", {})
    assert providers.musicbrainz_instrumental("inst") == "instrumental recording"
    assert providers.musicbrainz_instrumental("zxx") == "work without lyrics"
    assert providers.musicbrainz_instrumental("song") is None

    path = _make_flac(tmp_path / "a.flac")
    audio = FLAC(path)
    audio["title"], audio["artist"], audio["musicbrainz_trackid"] = "Song", "A", "zxx"
    audio.save()
    meta = tagging.read_metadata(path)
    assert meta.recording_id == "zxx"
    assert core.instrumental_hint(meta) == (None, None)     # off by default
    cfg["api"]["enable_musicbrainz"] = True
    assert core.instrumental_hint(meta) == ("MusicBrainz: work without lyrics", None)


def test_netease_results_are_labelled_in_upper_case(cfg, monkeypatch):
    song = {"id": 7, "name": "Song", "artists": [{"name": "Artist"}], "album": {"name": "A"}, "duration": 200000}

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeSession:
        def get(self, url, params=None, timeout=None, headers=None):
            if "lyric" in url:
                return FakeResponse({"lrc": {"lyric": "[00:01.00]la la"}})
            return FakeResponse({"result": {"songs": [song]}})

    monkeypatch.setattr(providers, "get_session", lambda: FakeSession())
    found = providers.query_netease("Song", "Artist", 200, providers.SearchReport())
    assert [c.provider for c in found] == ["NETEASE"]


# alias rounds and NetEase load
def test_lrclib_alias_queries_depend_on_the_round():
    def artists(aliases):
        return {p.get("artist_name") or p.get("q", "").rsplit(" ", 1)[0]
                for _, p in providers._lrclib_queries("Song", "IU", "", 200, aliases=aliases)}
    assert "아이유" not in artists("none") and "IU" in artists("none")
    assert "아이유" in artists("only") and "IU" not in artists("only")
    assert {"IU", "아이유"} <= artists("all")


def _recording_providers(monkeypatch, first_round):
    rounds = []

    def lrclib(title, artist, album, duration, report, quick=False, aliases="none", with_get=True):
        rounds.append(aliases)
        return list(first_round) if aliases != "only" else []
    monkeypatch.setattr(providers, "query_lrclib", lrclib)
    monkeypatch.setattr(providers, "query_netease", lambda *a, **k: [])
    monkeypatch.setattr(providers, "query_qqmusic", lambda *a, **k: [])
    monkeypatch.setattr(providers, "query_megalobiz", lambda *a, **k: [])
    monkeypatch.setattr(providers, "query_syncedlyrics", lambda *a, **k: [])
    return rounds


def test_aliases_are_searched_only_when_the_artist_finds_too_little(cfg, monkeypatch):
    synced = _built(provider="LRCLIB", artist="IU", title="Blueming", duration=200)
    rounds = _recording_providers(monkeypatch, [synced])
    report = providers.collect_candidates("Blueming", "IU", "", 200)
    assert rounds == ["none"] and not report.aliases_searched

    rounds = _recording_providers(monkeypatch, [])
    report = providers.collect_candidates("Blueming", "IU", "", 200)
    assert rounds == ["none", "only"] and report.alias_fallback

    rounds = _recording_providers(monkeypatch, [])
    providers.collect_candidates("Song", "Unknown Band", "", 200)
    assert rounds == ["none"]                                  # no aliases to try

    cfg["settings"]["search_all_aliases"] = True
    rounds = _recording_providers(monkeypatch, [])
    providers.collect_candidates("Blueming", "IU", "", 200)
    assert rounds == ["all"]


class _NetEaseSession:
    """Answers NetEase searches with *songs* and counts lyric requests."""

    def __init__(self, songs, search_payload=None):
        self.songs, self.search_payload, self.lyric_requests = songs, search_payload, 0

    def get(self, url, params=None, timeout=None, headers=None):
        session = self

        class Response:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                if "lyric" in url:
                    session.lyric_requests += 1
                    return {"lrc": {"lyric": f"[00:01.00]la {session.lyric_requests}"}}
                return session.search_payload or {"result": {"songs": session.songs}}
        return Response()


def test_netease_fetches_lyrics_for_the_best_songs_only(cfg, monkeypatch):
    songs = [{"id": i, "name": "Song", "artists": [{"name": "Artist"}], "album": {"name": "A"},
              "duration": 200000} for i in range(1, 7)]
    session = _NetEaseSession(songs)
    monkeypatch.setattr(providers, "get_session", lambda: session)
    providers.query_netease("Song", "Artist", 200, providers.SearchReport())
    assert session.lyric_requests == 3


def test_netease_filler_and_refusals_are_reported(cfg, monkeypatch):
    filler = [{"id": 1, "name": "平凡之路", "artists": [{"name": "朴树"}], "duration": 300000}]
    session = _NetEaseSession(filler)
    monkeypatch.setattr(providers, "get_session", lambda: session)
    report = providers.SearchReport()
    assert providers.query_netease("Flashy", "P1Harmony", 189, report) == []
    assert session.lyric_requests == 0
    assert "only unrelated songs" in report.errors["NetEase"][0]

    session = _NetEaseSession([], search_payload={"code": -462, "message": "请绑定手机后再试哦~"})
    monkeypatch.setattr(providers, "get_session", lambda: session)
    report = providers.SearchReport()
    providers.query_netease("Flashy", "P1Harmony", 189, report)
    assert "code -462" in report.errors["NetEase"][0]


# request rounds
class _LrclibSession:
    """Records LRCLIB requests; /get answers 404 when *get_found* is false."""

    def __init__(self, get_found):
        self.get_found, self.requests = get_found, []

    def get(self, url, params=None, timeout=None, headers=None):
        self.requests.append((url.rsplit("/", 1)[1], dict(params or {})))
        found = self.get_found

        class Response:
            status_code = 200 if (found or not url.endswith("/get")) else 404
            reason = "OK"

            def json(self):
                return [] if url.endswith("/search") else {"id": 1, "trackName": "Song", "artistName": "A",
                                                           "syncedLyrics": "[00:01.00]la", "duration": 200}
        return Response()


def test_lrclib_asks_without_duration_only_when_needed(cfg, monkeypatch):
    for found, gets in ((True, 1), (False, 2)):
        session = _LrclibSession(found)
        monkeypatch.setattr(providers, "get_session", lambda: session)
        providers.query_lrclib("Song", "A", "Album", 200, providers.SearchReport())
        get_params = [p for endpoint, p in session.requests if endpoint == "get"]
        assert len(get_params) == gets
        assert "duration" in get_params[0] and all("duration" not in p for p in get_params[1:])
        assert not any("Romanized" in str(p) for _, p in session.requests)
        assert len(session.requests) == gets + 3


def _round_recorder(monkeypatch, first_round):
    rounds = []

    def lrclib(title, artist, album, duration, report, quick=False, aliases="none", with_get=True):
        rounds.append(("lrclib", aliases))
        return list(first_round) if aliases != "only" else []

    def scrapers(title, artist, report, aliases="none"):
        rounds.append(("syncedlyrics", aliases))
        return []
    monkeypatch.setattr(providers, "query_lrclib", lrclib)
    monkeypatch.setattr(providers, "query_netease", lambda *a, **k: [])
    monkeypatch.setattr(providers, "query_qqmusic", lambda *a, **k: [])
    monkeypatch.setattr(providers, "query_megalobiz", lambda *a, **k: [])
    monkeypatch.setattr(providers, "query_syncedlyrics", scrapers)
    monkeypatch.setattr(providers, "HAS_SYNCEDLYRICS", True)
    return rounds


def test_syncedlyrics_runs_only_when_the_first_round_falls_short(cfg, monkeypatch):
    synced = _built(provider="LRCLIB", artist="Band", title="Song", duration=200)
    rounds = _round_recorder(monkeypatch, [synced])
    providers.collect_candidates("Song", "Band", "", 200)
    assert rounds == [("lrclib", "none")]

    rounds = _round_recorder(monkeypatch, [])
    report = providers.collect_candidates("Song", "Band", "", 200)
    assert rounds == [("lrclib", "none"), ("syncedlyrics", "none")] and report.scraper_fallback

    rounds = _round_recorder(monkeypatch, [])
    providers.collect_candidates("Song (Inst.)", "Band", "", 200)
    assert rounds == [("lrclib", "none")]                      # nothing to gain

    cfg["api"]["syncedlyrics_as_fallback"] = False
    rounds = _round_recorder(monkeypatch, [synced])
    providers.collect_candidates("Song", "Band", "", 200)
    assert sorted(rounds) == [("lrclib", "none"), ("syncedlyrics", "none")]


def test_syncedlyrics_skips_the_sources_lyrembed_asks_itself(cfg, monkeypatch):
    seen = []

    class FakeEngine:
        @staticmethod
        def search(query, providers=None):
            seen.append(providers)
            return ""
    monkeypatch.setattr(providers, "HAS_SYNCEDLYRICS", True)
    monkeypatch.setattr(providers, "syncedlyrics", FakeEngine, raising=False)
    providers.query_syncedlyrics("Song", "Band", providers.SearchReport())
    assert seen == [["Musixmatch", "Genius"]]


def test_config_from_before_the_rename_is_still_read(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = tmp_path / "lrckit" / "config.json"
    legacy.parent.mkdir()
    legacy.write_text('{"settings": {"search_cache_entries": 5}}', encoding="utf-8")
    merged, warnings = config.load_config(None)
    assert merged["settings"]["search_cache_entries"] == 5
    assert any("move it to" in w and "lyrembed" in w for w in warnings)

    current = tmp_path / "lyrembed" / "config.json"
    current.parent.mkdir()
    current.write_text('{"settings": {"search_cache_entries": 7}}', encoding="utf-8")
    merged, warnings = config.load_config(None)
    assert merged["settings"]["search_cache_entries"] == 7 and warnings == []


# batch romanization
def _batch_track(tmp_path: Path, monkeypatch, text_found: str) -> Path:
    """A taggable FLAC whose automatic search finds text_found."""
    from mutagen.flac import FLAC

    path = _make_flac(tmp_path / "track.flac")
    audio = FLAC(path)
    audio["title"], audio["artist"] = "Love wins all", "IU"
    audio.save()
    found = _cand(synced=True, exact=True, text=text_found)
    monkeypatch.setattr(core, "find_automatic",
                        lambda meta: (providers.SearchReport(candidates=[found]), found))
    return path


@pytest.mark.skipif(not text.HAS_KOREAN, reason="korean-romanizer not installed")
def test_batch_romanizes_only_when_asked(cfg, tmp_path, monkeypatch, capsys):
    from mutagen.flac import FLAC

    path = _batch_track(tmp_path, monkeypatch, "[00:01.00]사랑해")
    assert batch.run_batch([path], tmp_path, tagging.MetadataCache(), False, False) == 0
    assert FLAC(path)["LYRICS"] == ["[00:01.00]사랑해"]  # off by default

    path.unlink()
    path = _batch_track(tmp_path, monkeypatch, "[00:01.00]사랑해")
    cfg["settings"]["auto_romanize"] = True
    cfg["settings"]["romanization_layout"] = "below"
    assert batch.run_batch([path], tmp_path, tagging.MetadataCache(), False, False) == 0
    saved = FLAC(path)["LYRICS"][0]
    assert saved.startswith("[00:01.00]사랑해") and "saranghae" in saved  # original kept, line below
    assert ", romanized" in capsys.readouterr().out


def test_batch_romanization_reports_a_missing_backend(cfg, tmp_path, monkeypatch, capsys):
    from mutagen.flac import FLAC

    path = _batch_track(tmp_path, monkeypatch, "[00:01.00]사랑해")
    cfg["settings"]["auto_romanize"] = True
    monkeypatch.setattr(core, "auto_romanize", lambda lyrics: (None, "'korean-romanizer' is not installed"))
    assert batch.run_batch([path], tmp_path, tagging.MetadataCache(), False, False) == 0
    assert FLAC(path)["LYRICS"] == ["[00:01.00]사랑해"]  # still tagged, as found
    assert "not romanized: 'korean-romanizer' is not installed" in capsys.readouterr().out


def test_romanize_flags_override_the_setting(cfg, tmp_path, monkeypatch):
    user = tmp_path / "config.json"
    user.write_text('{"settings": {"auto_romanize": true}}', encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    cli.main([str(empty), "--auto", "--no-romanize", "-c", str(user), "--color", "never"])
    assert config.get("settings", "auto_romanize") is False
    cli.main([str(empty), "--auto", "-c", str(user), "--color", "never"])
    assert config.get("settings", "auto_romanize") is True


# --- fixes from a third-party review -----------------------------------------
def test_duplicate_text_keeps_the_fitting_upload_not_the_first(cfg, monkeypatch):
    # Results arrive in whatever order the requests finish. A cover with the
    # same text used to hide the original, a fuzzy hit the exact match.
    cover = _built(provider="LRCLIB", artist="Cover Band", title="Song", duration=200)
    fuzzy = _built(provider="LRCLIB", artist="A", title="Song", duration=200)
    exact = _built(provider="LRCLIB", artist="A", title="Song", duration=200, exact=True)

    _fake_providers(monkeypatch, [cover, exact])
    report = providers.collect_candidates("Song", "A", "", 200)
    assert [c.artist for c in report.candidates] == ["A"]

    _fake_providers(monkeypatch, [fuzzy, exact])
    report = providers.collect_candidates("Song", "A", "", 200)
    assert len(report.candidates) == 1 and report.candidates[0].exact


def test_middle_dot_and_dash_do_not_make_lyrics_japanese():
    assert text.detect_script("我爱你・你爱我\n今天天气很好") == "zh"
    assert text.detect_script("사랑・이별\n사랑해요") == "ko"
    assert text.detect_script("你好ー世界\n我们走吧") == "zh"     # ー used as a dash
    assert not text.contains_kana("约翰・史密斯")
    assert text.detect_script("コーヒー・ブレイク\n君が好き") == "ja"
    assert text.contains_kana("すごーい")


@pytest.mark.skipif(not (text.HAS_PYPINYIN and text.HAS_KAKASI), reason="needs pypinyin and pykakasi")
def test_han_next_to_a_middle_dot_is_not_read_as_japanese():
    out = text.romanize_lyrics("你・我是朋友", "mixed")
    assert out.startswith("nǐ・wǒ"), out


@pytest.mark.skipif(not text.HAS_KOREAN, reason="needs the Korean romanizer")
def test_layout_below_adds_no_copies_of_unchanged_lines():
    lyrics = "[ti:사랑]\n[00:01.00]Hello \n[00:02.00]Hello  world\n[00:03.00]사랑해"
    out = text.romanize_lyrics(lyrics, "ko", keep_original=True).splitlines()
    assert out[:3] == ["[ti:사랑]", "[00:01.00]Hello ", "[00:02.00]Hello  world"]
    assert out[3] == "[00:03.00]사랑해" and out[4].startswith("[00:03.00]") and len(out) == 5


def test_bracketed_instrumental_note_counts_for_the_flag(cfg, monkeypatch):
    # Cleaning drops '(インスト)' with the other bracketed Japanese text.
    record = {"id": 1, "trackName": "Song (Inst.)", "artistName": "A", "duration": 200,
              "instrumental": True, "plainLyrics": None, "syncedLyrics": None}

    class FakeResponse:
        status_code = 200
        reason = "OK"

        def json(self):
            return [record]

    class FakeSession:
        def get(self, url, params=None, timeout=None, headers=None):
            return FakeResponse()

    monkeypatch.setattr(providers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(providers, "_lrclib_queries", lambda *a, **k: [("search", {"q": "x"})])
    report = providers.SearchReport()
    providers.query_lrclib("Song (インスト)", "A", "", 200, report)
    assert report.instrumental == {"LRCLIB": "Song (Inst.)"}


def test_bracketed_titles_get_separate_cache_slots():
    key = providers.SearchCache.key
    assert key("(사랑)", "A", "X", 0) != key("(이별)", "A", "X", 0)


def test_unusable_config_values_fall_back_with_a_warning(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        '{"aliases": ["BTS", "Bangtan Boys"],'
        ' "api": {"max_workers": 0},'
        ' "settings": {"search_cache_entries": -5, "color": "invalid", "romanization_layout": "Below"}}',
        encoding="utf-8",
    )
    merged, warnings = config.load_config(path)
    joined = " ".join(warnings)
    assert "'aliases' must be a list of groups" in joined
    assert merged["api"]["max_workers"] == 8 and "api.max_workers" in joined
    assert merged["settings"]["search_cache_entries"] == 64 and "settings.search_cache_entries" in joined
    assert merged["settings"]["color"] == "auto" and "settings.color" in joined
    assert merged["settings"]["romanization_layout"] == "Below"   # case does not matter
    assert "romanization_layout" not in joined


# --- LRCLIB rate limit -------------------------------------------------------
class _CountingSession:
    """Answers LRCLIB with *status* (404 for /get, [] for /search when 200)
    and NetEase with no songs; counts LRCLIB requests and their overlap."""

    def __init__(self, status=200, retry_after="7"):
        import threading

        self.status, self.retry_after = status, retry_after
        self.lrclib = []
        self.active = self.peak = 0
        self._lock = threading.Lock()

    def get(self, url, params=None, timeout=None, headers=None):
        class Response:
            reason = "x"

            def __init__(self, status, payload, headers):
                self.status_code, self._payload, self.headers = status, payload, headers

            def json(self):
                return self._payload

            def raise_for_status(self):
                pass

        if "lrclib" not in url:
            return Response(200, {"result": {"songs": []}}, {})
        with self._lock:
            self.lrclib.append(url.rsplit("/", 1)[1])
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.01)
        with self._lock:
            self.active -= 1
        if self.status == 429:
            return Response(429, {}, {"Retry-After": self.retry_after})
        return Response(404, None, {}) if url.endswith("/get") else Response(200, [], {})


def test_rate_limit_is_reported_not_retried(cfg, monkeypatch):
    session = _CountingSession(status=429)
    monkeypatch.setattr(providers, "get_session", lambda: session)
    cfg["api"]["enable_syncedlyrics"] = False
    report = providers.collect_candidates("Song", "IU", "Album", 200)
    assert report.rate_limited == {"LRCLIB": 7.0}
    assert any("Rate limited" in m for m in report.errors["LRCLIB"])
    # The first refusal stops the queued queries, and the alias round does
    # not ask LRCLIB again: at most the requests already in flight.
    assert len(session.lrclib) <= providers._LRCLIB_PARALLEL


def test_session_does_not_retry_429(cfg, monkeypatch):
    monkeypatch.setattr(providers, "_SESSION", None)
    retry = providers.get_session().get_adapter("https://lrclib.net").max_retries
    providers.close_session()
    assert 429 not in retry.status_forcelist and 503 in retry.status_forcelist


def test_lrclib_requests_are_not_sent_all_at_once(cfg, monkeypatch):
    session = _CountingSession()
    monkeypatch.setattr(providers, "get_session", lambda: session)
    providers.query_lrclib("Song", "IU", "Album", 200, providers.SearchReport(), aliases="all")
    assert len(session.lrclib) > 4
    assert session.peak <= 2


def test_batch_search_asks_get_only_once(cfg, monkeypatch):
    session = _CountingSession()
    monkeypatch.setattr(providers, "get_session", lambda: session)
    cfg["api"]["enable_syncedlyrics"] = False
    meta = tagging.TrackMeta(path=Path("x.flac"), title="Song", artist="Band", album="Album", duration=200)
    core.find_automatic(meta)
    # /get with duration, then without; the full search does not repeat them.
    assert session.lrclib.count("get") == 2
    assert session.lrclib.count("search") == 3


def test_batch_waits_and_retries_a_rate_limited_track(cfg, tmp_path, monkeypatch, capsys):
    from mutagen.flac import FLAC

    track = _make_flac(tmp_path / "a.flac")
    audio = FLAC(track)
    audio["title"], audio["artist"] = "Song", "A"
    audio.save()
    found = _built(provider="LRCLIB", artist="A", title="Song", duration=10, track_duration=10)
    limited = providers.SearchReport(rate_limited={"LRCLIB": 3.0})
    answers = iter([(limited, None), (providers.SearchReport(candidates=[found]), found)])
    monkeypatch.setattr(core, "find_automatic", lambda meta: next(answers))
    waits = []
    monkeypatch.setattr(batch.time, "sleep", waits.append)
    failures = batch.run_batch([track], tmp_path, tagging.MetadataCache(), overwrite=False, dry_run=True)
    out = capsys.readouterr().out
    assert waits == [10.0]
    assert "limiting requests; waiting 10s" in out and "would apply" in out
    assert failures == 0


# --- QQ Music ----------------------------------------------------------------
def _qq_session(songs, lyrics):
    """Fake session: LRCLIB and NetEase find nothing, QQ Music answers a
    search with *songs* and lyrics from *lyrics* by songmid."""
    fetched = []

    class Response:
        status_code = 200
        reason = "OK"
        headers: dict = {}

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, params=None, timeout=None, headers=None):
            if "client_search_cp" in url:
                return Response({"code": 0, "data": {"song": {"list": songs}}})
            if "fcg_query_lyric" in url:
                fetched.append(params["songmid"])
                return Response({"retcode": 0, "code": 0, "lyric": lyrics.get(params["songmid"], "")})
            if "lrclib" in url:
                response = Response([])
                if url.endswith("/get"):
                    response.status_code = 404
                return response
            return Response({"result": {"songs": []}})

    return Session(), fetched


def test_qqmusic_results_are_listed_without_its_header_lines(cfg, monkeypatch):
    songs = [
        {"songmid": "m1", "songname": "BIZNESS", "singer": [{"name": "XLOV"}], "albumname": "I ONE", "interval": 156},
        {"songmid": "m2", "songname": "半句再见", "singer": [{"name": "孙燕姿"}], "albumname": "X", "interval": 242},
    ]
    lyrics = {"m1": "[ti:BIZNESS]\n[ar:XLOV]\n[al:I ONE]\n[by:]\n[offset:0]\n[kana:1よね1づ]\n"
                    "[00:00.00]BIZNESS - XLOV\n[00:00.31]词：우무티\n[00:00.63]曲：QSTNMRKS\n[00:12.00]la la"}
    session, fetched = _qq_session(songs, lyrics)
    monkeypatch.setattr(providers, "get_session", lambda: session)
    cfg["api"]["enable_syncedlyrics"] = False
    report = providers.collect_candidates("BIZNESS", "XLOV", "I ONE", 156)
    assert fetched == ["m1"]                            # the filler's lyrics are not requested
    assert [c.provider for c in report.candidates] == ["QQMUSIC"]
    assert report.candidates[0].text == "[00:12.00]la la"
    assert report.candidates[0].album == "I ONE"


def test_qqmusic_pure_music_notice_marks_the_track_instrumental(cfg, monkeypatch):
    songs = [{"songmid": "m1", "songname": "Merry Christmas Mr. Lawrence", "singer": [{"name": "坂本龙一"}],
              "albumname": "A", "interval": 331}]
    lyrics = {"m1": "[00:00:00]此歌曲为没有填词的纯音乐，请您欣赏"}
    session, _ = _qq_session(songs, lyrics)
    monkeypatch.setattr(providers, "get_session", lambda: session)
    report = providers.SearchReport()
    # Artist names differ in script (龍/龙); the length vouches instead.
    assert providers.query_qqmusic("Merry Christmas Mr. Lawrence", "坂本龍一", 331, report) == []
    assert report.instrumental == {"QQ Music": "Merry Christmas Mr. Lawrence"}


def test_instrumental_version_does_not_fit_by_length(cfg, monkeypatch):
    sung = _built(provider="QQMUSIC", artist="XLOV", title="BIZNESS", duration=156, track_duration=156,
                  text="[00:01.00]sung")
    inst = _built(provider="QQMUSIC", artist="XLOV", title="BIZNESS (纯音乐)", duration=156, track_duration=156,
                  text="[00:01.00]other")
    _fake_providers(monkeypatch, [sung, inst])
    report = providers.collect_candidates("BIZNESS", "XLOV", "", 156)
    assert [c.title for c in report.candidates] == ["BIZNESS"]
    assert [c.title for c in report.unrelated] == ["BIZNESS (纯音乐)"]


# --- Megalobiz ---------------------------------------------------------------
def test_megalobiz_is_asked_only_when_the_first_round_falls_short(cfg, monkeypatch):
    calls = []
    cfg["api"]["enable_syncedlyrics"] = False

    found = _built(provider="LRCLIB", artist="A", title="Song", duration=200)
    _fake_providers(monkeypatch, [found])
    monkeypatch.setattr(providers, "query_megalobiz", lambda *a, **k: calls.append(k) or [])
    report = providers.collect_candidates("Song", "A", "", 200)
    assert calls == [] and not report.megalobiz_fallback

    _fake_providers(monkeypatch, [])
    monkeypatch.setattr(providers, "query_megalobiz", lambda *a, **k: calls.append(k) or [])
    report = providers.collect_candidates("Song", "A", "", 200)
    assert calls == [{"aliases": "none"}] and report.megalobiz_fallback


def _megalobiz_session(results, sheets, status=200):
    requests_seen = []

    class Response:
        reason = "x"

        def __init__(self, code, payload, headers=None):
            self.status_code, self._payload, self.headers = code, payload, headers or {}

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, params=None, timeout=None, headers=None):
            requests_seen.append((url.rsplit("/v1/", 1)[1], dict(headers or {})))
            if status == 429:
                return Response(429, {}, {"Retry-After": "3600"})
            if url.endswith("/search"):
                return Response(200, {"results": results})
            return Response(200, sheets[int(url.rsplit("/", 1)[1])])

    return Session(), requests_seen


def test_megalobiz_fetches_only_fitting_sheets_and_keeps_their_link(cfg, monkeypatch):
    monkeypatch.setattr(providers, "_megalobiz_blocked_until", 0.0)
    results = [
        {"id": 1, "title": "BIZNESS", "artist": "XLOV", "duration": 156.2, "url": "https://megalobiz.com/lrc/1/b"},
        {"id": 2, "title": "1&Only", "artist": "XLOV", "duration": 175.0, "url": "https://megalobiz.com/lrc/2/o"},
    ]
    sheets = {1: {"id": 1, "title": "BIZNESS", "artist": "XLOV", "duration": 156.2, "synced_by": "fan",
                  "url": "https://megalobiz.com/lrc/1/b",
                  "lrc": "[ar:XLOV]\n[ti:BIZNESS]\n[by:fan]\n[length:02:36.20]\n[re:Megalobiz]\n[00:01.00]la"}}
    session, seen = _megalobiz_session(results, sheets)
    monkeypatch.setattr(providers, "get_session", lambda: session)
    cfg["api"]["megalobiz_api_key"] = "mbz_test"
    found = providers.query_megalobiz("BIZNESS", "XLOV", 156, providers.SearchReport())
    assert [path for path, _ in seen] == ["search", "lrc/1"]        # '1&Only' is not fetched
    assert all(h.get("X-API-Key") == "mbz_test" for _, h in seen)
    assert len(found) == 1
    cand = found[0]
    assert (cand.provider, cand.album, cand.url, cand.diff) == ("MEGALOBIZ", "synced by fan", "https://megalobiz.com/lrc/1/b", 0)
    assert cand.text == "[00:01.00]la"                # header tags removed


def test_megalobiz_rate_limit_skips_it_without_stalling_a_batch(cfg, monkeypatch):
    monkeypatch.setattr(providers, "_megalobiz_blocked_until", 0.0)
    session, seen = _megalobiz_session([], {}, status=429)
    monkeypatch.setattr(providers, "get_session", lambda: session)
    report = providers.SearchReport()
    assert providers.query_megalobiz("Song", "A", 200, report) == []
    assert "rate limit reached, skipped for 1h 00min" in report.errors["Megalobiz"][0]
    assert report.rate_limited == {}                # a batch run does not wait for it
    later = providers.SearchReport()
    assert providers.query_megalobiz("Other", "A", 200, later) == []
    assert len(seen) == 1                           # not asked again while blocked
    assert "skipped: rate limit reached, free again in" in later.errors["Megalobiz"][0]


def test_qqmusic_title_line_is_found_despite_version_notes():
    lyrics = "[ti:Lemon]\n[offset:-300]\n[00:00.00]Lemon - 米津玄師 (よねづ けんし)\n[00:00.81]夢ならば"
    # The song is 'Lemon (Live)', the line says 'Lemon'; the offset shifts
    # the timing and stays.
    assert providers._strip_qqmusic_extras(lyrics, "Lemon (Live)") == "[offset:-300]\n[00:00.81]夢ならば"
    # A first lyric line that merely contains ' - ' is kept.
    assert providers._strip_qqmusic_extras("[00:01.00]Run - run away", "Lemon") == "[00:01.00]Run - run away"


# --- core: no output, steps any front end can show ----------------------------
def test_batch_steps_neither_print_nor_sleep(cfg, tmp_path, monkeypatch, capsys):
    from mutagen.flac import FLAC

    track = _make_flac(tmp_path / "a.flac")
    audio = FLAC(track)
    audio["title"], audio["artist"] = "Song", "A"
    audio.save()
    found = _built(provider="LRCLIB", artist="A", title="Song", duration=10, track_duration=10)
    limited = providers.SearchReport(rate_limited={"LRCLIB": 3.0})
    answers = iter([(limited, None), (providers.SearchReport(candidates=[found]), found)])
    monkeypatch.setattr(core, "find_automatic", lambda meta: next(answers))

    def no_sleep(seconds):
        raise AssertionError("the core must leave waiting to its caller")

    monkeypatch.setattr(batch.time, "sleep", no_sleep)
    steps = list(core.batch([track], tmp_path, tagging.MetadataCache(), overwrite=False, dry_run=True,
                            backup_root=None))
    assert capsys.readouterr().out == ""
    assert [type(s).__name__ for s in steps] == ["Searching", "Pause", "Searching", "Outcome"]
    assert steps[1].seconds == 10.0 and steps[1].limited == ["LRCLIB"]
    assert steps[3].result == "planned" and "would apply" in steps[3].message


def test_a_stopped_bulk_run_keeps_its_count_and_the_rest_untouched(cfg, tmp_path, state_dir):
    music = tmp_path / "music"
    music.mkdir()
    files = [_flac_with_lyrics(music / f"{n}.flac", f"[00:01.00]{n}") for n in ("a", "b")]
    steps = core.bulk("delete", files, music, tagging.MetadataCache(), core.backup_directory())
    tally = core.Tally()
    tally.add(next(steps))
    steps.close()                            # a web page's "Stop", or Ctrl+C
    assert (tally.done, tally.backed_up) == (1, 1)
    assert [tagging.read_metadata(f).lyrics for f in files] == ["", "[00:01.00]b"]


def test_folder_problems_are_returned_not_printed(tmp_path, cfg, capsys):
    files, problem = core.check_music_directory(tmp_path / "missing")
    assert files is None and problem.severe and "does not exist" in problem.message
    files, problem = core.check_music_directory(tmp_path)
    assert files is None and not problem.severe and "No supported music files" in problem.message
    assert capsys.readouterr().out == ""


def test_write_lyrics_returns_the_stale_sidecar_warning(tmp_path, cfg, capsys):
    src = _make_flac(tmp_path / "song.flac")
    (tmp_path / "song.lrc").write_text("old", encoding="utf-8")
    warnings = core.write_lyrics(src, "[00:01.00]new")
    assert len(warnings) == 1 and "'song.lrc' was not updated" in warnings[0]
    assert capsys.readouterr().out == ""


# --- the browser front end ------------------------------------------------------
import http.client  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402

from lyrembed import web  # noqa: E402

TOKEN = "test-token"


@pytest.fixture
def web_server(cfg, tmp_path):
    """A running server on a free port; no browser, no providers."""
    session = web.WebSession(tmp_path, [], tagging.MetadataCache())
    server = web._Server(session, TOKEN)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _http(server, method, path, body=None, headers=None, cookie=True):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    sent = {"Host": f"127.0.0.1:{server.port}"}
    if cookie:
        sent["Cookie"] = f"{web.cookie_name(server.port)}={TOKEN}"
    if body is not None:
        body = json.dumps(body).encode()
        sent.update({"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.port}"})
    sent.update(headers or {})
    conn.request(method, path, body=body, headers=sent)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


def test_web_page_opens_only_with_the_token(web_server):
    response, _ = _http(web_server, "GET", "/", cookie=False)
    assert response.status == 403
    response, _ = _http(web_server, "GET", "/?token=wrong", cookie=False)
    assert response.status == 403
    response, _ = _http(web_server, "GET", f"/?token={TOKEN}", cookie=False)
    # The token becomes a cookie and leaves the address bar at once.
    assert response.status == 303 and response.getheader("Location") == "/"
    cookie = response.getheader("Set-Cookie")
    assert f"{web.cookie_name(web_server.port)}={TOKEN}" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    response, page = _http(web_server, "GET", "/")
    assert response.status == 200 and b'src="/static/app.js"' in page
    assert "default-src 'self'" in response.getheader("Content-Security-Policy")
    response, _ = _http(web_server, "GET", "/api/library", cookie=False)
    assert response.status == 403


def test_web_refuses_other_hosts_origins_and_forms(web_server):
    # Another site reaching 127.0.0.1 through its own name (DNS rebinding).
    response, _ = _http(web_server, "GET", "/api/library", headers={"Host": f"evil.example:{web_server.port}"})
    assert response.status == 400
    response, _ = _http(web_server, "POST", "/api/quit", {}, headers={"Origin": "http://evil.example"})
    assert response.status == 403 and not web_server.stop.is_set()
    # A plain HTML form can only send form types, never JSON.
    response, _ = _http(web_server, "POST", "/api/quit", {}, headers={"Content-Type": "text/plain"})
    assert response.status == 415 and not web_server.stop.is_set()
    response, _ = _http(web_server, "POST", "/api/quit", {})
    assert response.status == 200 and web_server.stop.wait(2)


def test_web_static_files_stay_in_their_folder(web_server):
    response, _ = _http(web_server, "GET", "/static/vendor/LICENSES.txt")
    assert response.status == 200
    for path in ("/static/../web.py", "/static/%2e%2e/web.py", "/static/../../pyproject.toml"):
        response, _ = _http(web_server, "GET", path)
        assert response.status == 404, path


def test_web_session_lists_searches_saves_and_deletes(cfg, tmp_path, monkeypatch):
    from mutagen.flac import FLAC

    music = tmp_path / "music"
    (music / "CD1").mkdir(parents=True)
    plain = _flac_with_lyrics(music / "CD1" / "a.flac", "words only")
    empty, inst = _make_flac(music / "CD1" / "b.flac"), _make_flac(music / "c.flac")
    for path, title in ((empty, "Song"), (inst, "Song (Inst.)")):
        audio = FLAC(path)
        audio["title"], audio["artist"] = title, "Band"
        audio.save()
    session = web.WebSession(music, [plain, empty, inst], tagging.MetadataCache())

    tracks = session.library()["tracks"]
    assert [(t["status"], t["group"], t["instrumental"]) for t in tracks] == [
        ("plain", "CD1", False), ("none", "CD1", False), ("none", "", True)]

    found = _built(provider="LRCLIB", artist="Band", title="Song", duration=10, track_duration=10,
                   text="[00:01.00]la")
    _fake_providers(monkeypatch, [found])
    result = session.search(1)
    assert result["cached"] is False and result["highlight"] == 1
    assert [c["providerName"] for c in result["candidates"]] == ["LRCLIB"]
    assert session.search(1)["cached"] is True

    saved = session.save(1, "[00:01.00]la")
    assert saved["track"]["status"] == "sync" and FLAC(empty)["LYRICS"] == ["[00:01.00]la"]
    with pytest.raises(web.ApiError):
        session.save(1, "   ")
    assert session.delete(1, sidecar=False)["track"]["status"] == "none"
    with pytest.raises(web.ApiError) as missing:
        session.track(5)
    assert missing.value.status == 404
    assert session.inspect("[00:01.00]사랑해") == {"synced": True, "script": "original"}


def test_web_folder_picker_and_unusable_folders(cfg, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    _make_flac(music / "a.flac")
    (tmp_path / ".hidden").mkdir()
    session = web.WebSession(None, [], tagging.MetadataCache(), problem="Path does not exist: /x")
    assert session.library()["problem"] == "Path does not exist: /x"

    listing = session.folders(str(tmp_path))
    assert [d["name"] for d in listing["dirs"]] == ["music"]
    assert listing["dirs"][0]["path"] == str(music)
    assert session.folders(str(music))["musicFiles"] == 1

    with pytest.raises(web.ApiError) as refused:
        session.open_folder(str(tmp_path / "missing"))
    assert "does not exist" in refused.value.message
    opened = session.open_folder(str(music))
    assert opened["folder"] == str(music) and len(opened["tracks"]) == 1 and opened["problem"] == ""


def test_web_stops_once_the_page_is_gone(web_server, monkeypatch):
    assert not web_server.page_gone()            # nobody visited yet: keep waiting
    web_server.contact()
    assert not web_server.page_gone()
    web_server.closing_at = time.monotonic() - web.CLOSE_GRACE - 1
    assert web_server.page_gone()                # closed and did not come back
    web_server.contact()                         # a reload pings again
    assert not web_server.page_gone()
    web_server.last_contact = time.monotonic() - web.HEARTBEAT_TIMEOUT - 1
    assert web_server.page_gone()


def test_no_browser_is_for_the_page_only(capsys):
    with pytest.raises(SystemExit):
        cli.parse_args(["--auto", "--no-browser"])
    assert cli.parse_args(["--no-browser"]).no_browser


def test_web_audio_serves_byte_ranges(cfg, tmp_path):
    track = _make_flac(tmp_path / "a.flac")
    size = track.stat().st_size
    session = web.WebSession(tmp_path, [track], tagging.MetadataCache())
    server = web._Server(session, TOKEN)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        response, data = _http(server, "GET", "/api/tracks/0/audio")
        assert response.status == 200 and data == track.read_bytes()
        assert response.getheader("Content-Type") == "audio/flac" and response.getheader("Accept-Ranges") == "bytes"
        # Seeking: the browser asks for the part it needs.
        response, data = _http(server, "GET", "/api/tracks/0/audio", headers={"Range": "bytes=10-19"})
        assert response.status == 206 and data == track.read_bytes()[10:20]
        assert response.getheader("Content-Range") == f"bytes 10-19/{size}"
        response, data = _http(server, "GET", "/api/tracks/0/audio", headers={"Range": "bytes=-5"})
        assert response.status == 206 and data == track.read_bytes()[-5:]
        response, _ = _http(server, "GET", "/api/tracks/0/audio", headers={"Range": f"bytes={size}-"})
        assert response.status == 416 and response.getheader("Content-Range") == f"bytes */{size}"
        response, _ = _http(server, "GET", "/api/tracks/0/audio", cookie=False)
        assert response.status == 403
        response, _ = _http(server, "GET", "/api/tracks/7/audio")
        assert response.status == 404
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_web_plays_alac_through_a_flac_copy(cfg, tmp_path, monkeypatch):
    alac = tmp_path / "a.m4a"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:a", "alac", str(alac)], check=True)
    aac = tmp_path / "b.m4a"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1",
                    "-c:a", "aac", str(aac)], check=True)
    session = web.WebSession(tmp_path, [alac, aac], tagging.MetadataCache())
    assert session.track(0)["playback"]["mode"] == "converted"
    assert session.track(1)["playback"]["mode"] == "direct"   # AAC plays as it is
    copy, content_type = session.audio(0)
    assert content_type == "audio/flac" and copy.read_bytes()[:4] == b"fLaC"
    assert session.audio(0)[0] == copy                         # converted once
    assert session.audio(1) == (aac, "audio/mp4")
    session.close()
    assert not copy.exists()

    monkeypatch.setattr(web.shutil, "which", lambda name: None)
    assert session.track(0)["playback"]["mode"] == "none"
    with pytest.raises(web.ApiError) as missing:
        session.audio(0)
    assert missing.value.status == 415 and "ffmpeg" in missing.value.message


def test_web_romanizes_like_the_terminal(cfg):
    choices = web.WebSession.romanizers("[00:01.00]사랑해")
    assert choices["detected"] == "ko" and not choices["below"]
    assert {o["key"] for o in choices["options"]} == set(text.ROMANIZERS)
    with pytest.raises(web.ApiError):
        web.WebSession.romanize("x", "klingon", "zh", False)
    if text.HAS_KOREAN:
        result = web.WebSession.romanize("[00:01.00]사랑해", "ko", "zh", keep_original=True)["lyrics"]
        assert result.startswith("[00:01.00]사랑해\n[00:01.00]sa")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_lrc_editor_functions():
    # The editor's shifting and stamping run in the browser; Node runs their tests.
    script = Path(__file__).with_name("lrc.test.mjs")
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


# --- settings, aliases and bulk runs from the page ---------------------------------
@pytest.fixture
def config_home(tmp_path, monkeypatch):
    home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return home / "lyrembed" / "config.json"


def test_saved_settings_keep_the_rest_of_the_file(cfg, config_home):
    config_home.parent.mkdir(parents=True)
    config_home.write_text('{"_note": "mine", "settings": {"search_cache_entries": 9, "_why": "x"}, "aliases": [["A", "B"]]}',
                           encoding="utf-8")
    cfg["_source"] = str(config_home)
    assert config.save_settings({"settings": {"write_sidecar_lrc": True}, "api": {"enable_qqmusic": False}}) == config_home
    saved = json.loads(config_home.read_text(encoding="utf-8"))
    assert saved == {"_note": "mine", "settings": {"search_cache_entries": 9, "_why": "x", "write_sidecar_lrc": True},
                     "aliases": [["A", "B"]], "api": {"enable_qqmusic": False}}
    assert config.get("settings", "write_sidecar_lrc") is True and config.get("api", "enable_qqmusic") is False
    assert json.loads(config_home.with_name("config.json.bak").read_text(encoding="utf-8"))["settings"]["search_cache_entries"] == 9
    # What load_config would throw out is refused before anything is written.
    for bad in ({"settings": {"search_cache_entries": 0}}, {"settings": {"search_cache_entries": "9"}}, {"settings": {"nope": 1}}):
        with pytest.raises(config.ConfigError):
            config.save_settings(bad)
    assert json.loads(config_home.read_text(encoding="utf-8")) == saved


def test_a_broken_config_file_is_never_overwritten(cfg, config_home):
    config_home.parent.mkdir(parents=True)
    config_home.write_text('{"settings": {', encoding="utf-8")
    cfg["_source"] = "built-in defaults"          # unusable at start, so the defaults ran
    with pytest.raises(config.ConfigError) as refused:
        config.save_settings({"settings": {"auto_romanize": True}})
    assert "not overwritten" in str(refused.value)
    assert config_home.read_text(encoding="utf-8") == '{"settings": {'


def test_settings_page_lists_every_setting_once(cfg):
    listed = [(s["section"], s["key"]) for g in web.WebSession.settings()["groups"] for s in g["settings"]]
    expected = {(sec, k) for sec in ("settings", "api", "cleaning") for k in config.DEFAULT_CONFIG[sec]} - {("api", "user_agent")}
    assert len(listed) == len(set(listed)) and set(listed) == expected
    layout = next(s for g in web.WebSession.settings()["groups"] for s in g["settings"] if s["key"] == "romanization_layout")
    assert layout["choices"] == ["replace", "below"] and layout["help"]


def test_aliases_from_the_page(cfg, config_home, tmp_path):
    session = web.WebSession(tmp_path, [], tagging.MetadataCache())
    with pytest.raises(web.ApiError):
        session.save_aliases([["Lonely"]])
    result = session.save_aliases([["Test Artist", " テスト歌手 "], []])
    assert result["user"] == [["Test Artist", "テスト歌手"]]
    assert ("Test Artist", "テスト歌手") in config.alias_groups()
    assert json.loads(config_home.read_text(encoding="utf-8"))["aliases"] == [["Test Artist", "テスト歌手"]]


def _wait_for_job(session, timeout=10.0):
    deadline = time.monotonic() + timeout
    while session.job.running:
        assert time.monotonic() < deadline, "the bulk run did not finish"
        time.sleep(0.02)
    return session.job_view(0)


def test_bulk_run_in_the_background_reports_and_stops_between_tracks(cfg, tmp_path, monkeypatch):
    from mutagen.flac import FLAC

    files = []
    for name in ("a", "b", "c"):
        path = _make_flac(tmp_path / f"{name}.flac")
        audio = FLAC(path)
        audio["title"], audio["artist"] = name.upper(), "Band"
        audio.save()
        files.append(path)
    cfg["api"]["batch_delay_seconds"] = 0
    release = threading.Event()

    def find(meta):
        found = _built(provider="LRCLIB", artist="Band", title=meta.title, duration=10, track_duration=10,
                       text=f"[00:01.00]{meta.title}")
        if meta.title == "B":
            release.wait(5)       # the second search takes a while
        return providers.SearchReport(candidates=[found]), found

    monkeypatch.setattr(core, "find_automatic", find)
    session = web.WebSession(tmp_path, files, tagging.MetadataCache())
    first = session.start_job("fetch", [0, 1, 2])
    assert first["running"] and first["total"] == 3
    with pytest.raises(web.ApiError) as busy:
        session.start_job("fetch", [0])
    assert busy.value.status == 409
    deadline = time.monotonic() + 5
    while session.job_view(0)["processed"] < 1:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    session.stop_job()                            # while B's search runs
    release.set()
    view = _wait_for_job(session)
    assert view["stopped"] and view["tally"]["done"] == 2   # B's search finishes; C is never started
    assert [r["index"] for r in view["rows"]] == [0, 1]
    assert FLAC(files[1])["LYRICS"] == ["[00:01.00]B"] and "LYRICS" not in FLAC(files[2])
    assert session.job_view(1)["rows"] == view["rows"][1:]   # only rows the page has not seen


def test_bulk_delete_from_the_page_backs_up(cfg, tmp_path, state_dir):
    track = _flac_with_lyrics(tmp_path / "a.flac", "[00:01.00]old")
    session = web.WebSession(tmp_path, [track], tagging.MetadataCache())
    session.start_job("delete", [0])
    view = _wait_for_job(session)
    assert view["tally"] == {"done": 1, "skipped": 0, "failed": 0, "backedUp": 1} and view["backupRoot"]
    assert tagging.read_metadata(track).lyrics == ""
    assert [p.read_text(encoding="utf-8") for p in state_dir.rglob("*.lrc")] == ["[00:01.00]old"]
    with pytest.raises(web.ApiError):
        session.start_job("explode", [0])


# --- covers and the preview ---------------------------------------------------------
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 16


def _flac_with_cover(path: Path, *pictures: tuple[int, bytes]) -> Path:
    from mutagen.flac import FLAC, Picture

    _make_flac(path)
    audio = FLAC(path)
    audio["title"], audio["artist"] = path.stem, "Band"
    for kind, data in pictures:
        picture = Picture()
        picture.type, picture.mime, picture.data = kind, "image/whatever", data
        audio.add_picture(picture)
    audio.save()
    return path


def test_covers_prefer_the_front_and_fall_back_to_a_file(tmp_path):
    both = _flac_with_cover(tmp_path / "a.flac", (4, JPEG), (3, PNG))   # back cover first, front second
    assert tagging.embedded_cover(both) == PNG
    bare = _flac_with_cover(tmp_path / "b.flac")
    assert tagging.embedded_cover(bare) is None
    assert tagging.cover_file(tmp_path) is None
    (tmp_path / "Cover.JPG").write_bytes(JPEG)
    assert tagging.cover_file(tmp_path) == tmp_path / "Cover.JPG"
    # The bytes decide the type, not the tag; anything but these four is no cover.
    assert tagging.image_type(PNG) == "image/png" and tagging.image_type(JPEG) == "image/jpeg"
    assert tagging.image_type(b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>") is None


def test_one_cover_for_all_tracks_is_the_albums(cfg, tmp_path):
    a = _flac_with_cover(tmp_path / "a.flac", (3, PNG))
    b = _flac_with_cover(tmp_path / "b.flac", (3, PNG))
    session = web.WebSession(tmp_path, [a, b], tagging.MetadataCache())
    library = session.library()
    assert library["cover"] and [t["cover"] for t in library["tracks"]] == [library["cover"]] * 2

    other = tmp_path / "other"
    other.mkdir()
    c = _flac_with_cover(other / "c.flac", (3, JPEG))
    d = _flac_with_cover(other / "d.flac")                                # no cover at all
    session = web.WebSession(tmp_path, [a, c, d], tagging.MetadataCache())
    library = session.library()
    assert library["cover"] == "" and library["tracks"][0]["cover"] != library["tracks"][1]["cover"]
    assert library["tracks"][2]["cover"] == ""
    assert session.cover(1) == (JPEG, "image/jpeg")
    with pytest.raises(web.ApiError) as missing:
        session.cover(2)
    assert missing.value.status == 404
    (other / "folder.png").write_bytes(PNG)                          # a cover next to the files counts
    assert session.cover(2) == (PNG, "image/png")


def test_cover_route_may_be_cached(cfg, tmp_path):
    track = _flac_with_cover(tmp_path / "a.flac", (3, JPEG))
    session = web.WebSession(tmp_path, [track], tagging.MetadataCache())
    server = web._Server(session, TOKEN)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        response, data = _http(server, "GET", "/api/tracks/0/cover?v=x")
        assert response.status == 200 and data == JPEG and response.getheader("Content-Type") == "image/jpeg"
        assert "max-age" in response.getheader("Cache-Control")
        assert response.getheader("X-Content-Type-Options") == "nosniff"
    finally:
        server.shutdown()
        server.server_close()


# --- behaviour the terminal menus used to test, now on core and the page ---------
def test_a_failed_sidecar_is_a_warning_not_a_failed_save(tmp_path, cfg):
    from mutagen.flac import FLAC

    cfg["settings"]["write_sidecar_lrc"] = True
    src = _make_flac(tmp_path / "song.flac")
    (tmp_path / "song.lrc").mkdir()          # makes the sidecar unwritable
    with pytest.raises(tagging.SidecarError):
        tagging.embed_lyrics(src, "[00:01.00]x")
    warnings = core.write_lyrics(src, "[00:02.00]y")
    assert FLAC(src)["LYRICS"][0] == "[00:02.00]y" and len(warnings) == 1
    (tmp_path / "song.lrc").rmdir()
    assert core.write_lyrics(src, "[00:03.00]z") == []        # kept in sync, so not stale
    assert tagging.stale_sidecar(src) is None


@pytest.mark.skipif(not text.HAS_KOREAN, reason="korean-romanizer required")
def test_bulk_romanize_backs_up_and_is_idempotent(cfg, tmp_path, state_dir):
    cfg["settings"]["romanization_layout"] = "below"
    music = tmp_path / "music"
    music.mkdir()
    korean = _flac_with_lyrics(music / "a.flac", "[00:01.00]사랑해")
    latin = _flac_with_lyrics(music / "b.flac", "[00:01.00]hello there")
    steps = list(core.bulk("romanize", [korean, latin], music, tagging.MetadataCache(), core.backup_directory()))
    assert [s.result for s in steps] == ["done", "skipped"]
    lyrics = tagging.read_metadata(korean).lyrics
    assert lyrics.splitlines()[0] == "[00:01.00]사랑해" and lyrics.splitlines()[1].startswith("[00:01.00]sa")
    assert tagging.read_metadata(latin).lyrics == "[00:01.00]hello there"
    backups = list(state_dir.rglob("a.flac.lrc"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "[00:01.00]사랑해"
    again = list(core.bulk("romanize", [korean], music, tagging.MetadataCache(), core.backup_directory()))
    assert again[0].result == "skipped" and tagging.read_metadata(korean).lyrics == lyrics


def test_bulk_sidecar_export_backs_up_a_differing_sidecar(cfg, tmp_path, state_dir):
    music = tmp_path / "music"
    music.mkdir()
    track = _flac_with_lyrics(music / "a.flac", "[00:01.00]new")
    tagging.sidecar_path(track).write_text("[00:01.00]old", encoding="utf-8")
    steps = list(core.bulk("sidecars", [track], music, tagging.MetadataCache(), core.backup_directory()))
    assert steps[0].result == "done" and steps[0].backed_up
    assert tagging.sidecar_path(track).read_text(encoding="utf-8") == "[00:01.00]new"
    assert [p.read_text(encoding="utf-8") for p in state_dir.rglob("*.lrc")] == ["[00:01.00]old"]


def test_a_tracks_lyrics_are_extracted_to_a_sidecar_from_its_page(cfg, tmp_path, state_dir):
    music = tmp_path / "music"
    music.mkdir()
    track = _flac_with_lyrics(music / "a.flac", "[00:01.00]new")
    bare = _make_flac(music / "b.flac")
    sidecar = tagging.sidecar_path(track)
    session = web.WebSession(music, [track, bare], tagging.MetadataCache())

    first = session.extract(0)
    assert first["message"] == "Wrote a.lrc."
    assert sidecar.read_text(encoding="utf-8") == "[00:01.00]new"
    assert first["track"]["staleSidecar"] == "a.lrc" and first["track"]["hasSidecar"]
    assert session.extract(0)["message"] == "a.lrc is already up to date."

    sidecar.write_text("[00:01.00]old", encoding="utf-8")
    assert session.extract(0)["message"] == "Wrote a.lrc; the previous one was backed up."
    assert sidecar.read_text(encoding="utf-8") == "[00:01.00]new"
    assert [p.read_text(encoding="utf-8") for p in state_dir.rglob("*.lrc")] == ["[00:01.00]old"]

    with pytest.raises(web.ApiError) as nothing:
        session.extract(1)
    assert nothing.value.status == 400 and not tagging.sidecar_path(bare).exists()


def _searchable(tmp_path, title, artist):
    from mutagen.flac import FLAC

    path = _make_flac(tmp_path / f"{title}.flac")
    audio = FLAC(path)
    audio["title"], audio["artist"] = title, artist
    audio.save()
    return web.WebSession(tmp_path, [path], tagging.MetadataCache()), path


def test_results_for_other_songs_are_kept_apart(cfg, tmp_path, monkeypatch):
    filler = _built(artist="朴树", title="平凡之路", duration=300)
    _fake_providers(monkeypatch, [filler], instrumental={"LRCLIB": "exact match"})
    session, _ = _searchable(tmp_path, "Opening", "Orchestra")
    result = session.search(0)
    assert result["candidates"] == [] and result["highlight"] is None
    assert [(c["title"], c["mismatch"]) for c in result["unrelated"]] == [("平凡之路", ["artist", "title"])]
    assert result["instrumental"] == "LRCLIB (exact match)"


def test_search_with_aliases_is_offered_and_runs(cfg, tmp_path, monkeypatch):
    calls = []

    def collect(title, artist, album, duration, quick=False, aliases=None):
        calls.append(aliases)
        return providers.SearchReport(aliases_searched=bool(aliases))

    monkeypatch.setattr(providers, "collect_candidates", collect)
    session, _ = _searchable(tmp_path, "Flashy", "P1Harmony")
    assert "피원하모니" in session.search(0)["unsearchedAliases"]
    assert session.search(0, aliases=True)["unsearchedAliases"] == []
    assert calls == [False, True]


def test_search_stays_cached_after_saving(cfg, tmp_path, monkeypatch):
    found = _built(provider="LRCLIB", artist="A", title="Song", duration=10, track_duration=10)
    _fake_providers(monkeypatch, [found])
    searches = []
    real_collect = providers.collect_candidates
    monkeypatch.setattr(providers, "collect_candidates", lambda *a, **k: searches.append(a) or real_collect(*a, **k))
    session, track = _searchable(tmp_path, "Song", "A")
    session.save(0, session.search(0)["candidates"][0]["text"])
    assert tagging.read_metadata(track).lyrics == found.text
    assert session.search(0)["cached"] is True and len(searches) == 1


def test_candidates_carry_their_source_script_and_length_check(cfg, monkeypatch):
    class FakeEngine:
        @staticmethod
        def search(query, allow_plain_format=True):
            return "[00:01.00]la la"

    monkeypatch.setattr(providers, "HAS_SYNCEDLYRICS", True)
    monkeypatch.setattr(providers, "syncedlyrics", FakeEngine, raising=False)
    scraped = web.candidate_json(providers.query_syncedlyrics("Song", "Artist", providers.SearchReport())[0], 1)
    assert scraped["providerName"] == "syncedlyrics" and scraped["unverified"]
    scripts = [web.candidate_json(providers.Candidate.build(provider="LRCLIB", artist="A", title="t", album="", text=t), 1)["script"]
               for t in ("사랑해 我爱你 ありがとう", "사랑해", "saranghae")]
    assert scripts == ["mixed", "original", "latin"]
    # The difference means something only with both lengths known.
    unknown = web.candidate_json(_cand(has_duration=True, duration=200, track_duration=0), 1)
    assert unknown["diff"] is None and unknown["duration"] == 200 and "duration" not in unknown["description"]
    assert web.candidate_json(_cand(has_duration=True, duration=203, track_duration=200, diff=3), 1)["diff"] == 3


def test_page_exit_codes(tmp_path, monkeypatch, cfg):
    monkeypatch.setattr(config, "load_config", lambda path=None: (cfg, []))
    _make_flac(tmp_path / "a.flac")
    started = []
    monkeypatch.setattr(web, "serve", lambda session, open_browser=True, port=0, unvisited=0: started.append(open_browser) or 0)
    assert cli.main([str(tmp_path), "--no-browser"]) == 0 and started == [False]

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(web, "serve", interrupt)
    assert cli.main([str(tmp_path)]) == 130


def test_settings_of_the_terminal_menus_get_a_note(tmp_path, cfg):
    path = tmp_path / "config.json"
    path.write_text('{"settings": {"default_editor": "vim", "enter_opens_preview": true}}', encoding="utf-8")
    _, warnings = config.load_config(path)
    assert len(warnings) == 2 and all("terminal menus" in w and "unknown" not in w for w in warnings)


# --- app-menu entry and starting without a folder ------------------------------------
from lyrembed import desktop  # noqa: E402


def test_desktop_exec_arguments_are_quoted_and_escaped():
    assert desktop.exec_argument("/usr/bin/lyrembed") == "/usr/bin/lyrembed"
    assert desktop.exec_argument("/home/a b/run.py") == '"/home/a b/run.py"'
    assert desktop.exec_argument("/m/100%") == "/m/100%%"
    # A literal $ is escaped inside the quotes; the string escape then doubles the backslash.
    assert desktop.exec_argument("/x/$y") == '"/x/\\\\$y"'


def test_desktop_entry_starts_the_lyrembed_that_wrote_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    launcher = tmp_path / "check out" / "run.py"
    launcher.parent.mkdir()
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(launcher)])
    monkeypatch.setattr(desktop, "_refresh", lambda folder: None)
    entry, icon, command = desktop.install()
    assert command == [sys.executable, str(launcher)]
    text = entry.read_text(encoding="utf-8")
    assert f'Exec={sys.executable} "{launcher}" %f' in text and f"Icon={icon}" in text
    assert "Terminal=false" in text and icon.read_text(encoding="utf-8").startswith("<svg")
    if shutil.which("desktop-file-validate"):
        checked = subprocess.run(["desktop-file-validate", str(entry)], capture_output=True, text=True)
        assert checked.returncode == 0 and not checked.stdout.strip(), checked.stdout
    assert desktop.remove() == [entry, icon] and not entry.exists() and desktop.remove() == []


def test_desktop_entry_for_an_installed_launcher(tmp_path, monkeypatch):
    shim = tmp_path / "bin" / "lyrembed"
    shim.parent.mkdir()
    shim.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(shim)])
    assert desktop.launch_command() == [str(shim)]


def test_music_folder_comes_from_the_desktop(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Musik").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    assert core.music_folder() == home                          # no user-dirs, no ~/Music
    (home / ".config").mkdir()
    (home / ".config" / "user-dirs.dirs").write_text('XDG_MUSIC_DIR="$HOME/Musik"\n', encoding="utf-8")
    assert core.music_folder() == home / "Musik"
    session = web.WebSession(None, [], tagging.MetadataCache())
    assert session.folders("")["path"] == str(home / "Musik")


def test_started_from_the_menu_the_page_asks_without_a_warning(tmp_path, monkeypatch, cfg):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home)
    monkeypatch.setattr(config, "load_config", lambda path=None: (cfg, []))
    problems = []
    monkeypatch.setattr(web, "serve", lambda session, open_browser=True, port=0, unvisited=0: problems.append(session.problem) or 0)
    assert cli.main([]) == 0                    # the app menu gives no folder
    assert cli.main([str(home)]) == 0           # typed on purpose: say why it is not used
    assert problems[0] == "" and "home folder" in problems[1]


def test_quick_romanization_does_what_batch_mode_does(cfg, monkeypatch):
    assert web.WebSession.auto_romanize("[00:01.00]hello there") == {
        "lyrics": None, "reason": "already Latin", "blocked": "", "language": "", "below": False}
    cfg["settings"]["romanization_layout"] = "below"
    monkeypatch.setitem(text.ROMANIZERS, "ko", ("Korean (Hangul → Romaja)", lambda: False, "korean-romanizer"))
    blocked = web.WebSession.auto_romanize("[00:01.00]사랑해")
    assert blocked["lyrics"] is None and blocked["blocked"] == "'korean-romanizer' is not installed"
    assert blocked["language"].startswith("Korean") and blocked["below"]


@pytest.mark.skipif(not text.HAS_KOREAN, reason="korean-romanizer required")
def test_quick_romanization_keeps_the_original_below(cfg):
    cfg["settings"]["romanization_layout"] = "below"
    result = web.WebSession.auto_romanize("[00:01.00]사랑해")
    assert result["lyrics"].startswith("[00:01.00]사랑해\n[00:01.00]sa") and not result["blocked"]


def test_library_says_how_the_quick_romanization_starts(cfg, tmp_path):
    session = web.WebSession(tmp_path, [_make_flac(tmp_path / "a.flac")], tagging.MetadataCache())
    assert session.library()["autoRomanize"] is False
    cfg["settings"]["auto_romanize"] = True
    assert session.library()["autoRomanize"] is True


# --- opened by another program's page ----------------------------------------------
def test_the_done_button_only_leads_to_a_page_on_this_computer(capsys):
    assert cli.local_page("http://127.0.0.1:41877/") and cli.local_page("http://localhost:8000/x")
    for url in ("https://127.0.0.1:41877/", "http://evil.example:80/", "http://127.0.0.1/", "javascript:alert(1)",
                "http://127.0.0.1.evil.example:80/"):
        assert not cli.local_page(url), url
    for argv in (["--return-url", "http://evil.example:80/"], ["--return-name", "cdport"],
                 ["--auto", "--return-url", "http://127.0.0.1:1/"]):
        with pytest.raises(SystemExit):
            cli.parse_args(argv)
    args = cli.parse_args(["--return-url", "http://127.0.0.1:41877/", "--return-name", "cdport"])
    assert (args.return_url, args.return_name) == ("http://127.0.0.1:41877/", "cdport")


def test_the_page_knows_where_done_goes(tmp_path, monkeypatch, cfg):
    _make_flac(tmp_path / "a.flac")
    monkeypatch.setattr(config, "load_config", lambda path=None: (cfg, []))
    seen = []
    monkeypatch.setattr(web, "serve", lambda session, open_browser=True, port=0, unvisited=0: seen.append(session.library()) or 0)
    assert cli.main([str(tmp_path), "--no-browser", "--return-url", "http://127.0.0.1:41877/", "--return-name", "cdport"]) == 0
    assert cli.main([str(tmp_path), "--no-browser"]) == 0
    assert (seen[0]["returnUrl"], seen[0]["returnName"]) == ("http://127.0.0.1:41877/", "cdport")
    assert seen[1]["returnUrl"] == ""


# --- importing a CD with cdport (called cdimport before 1.3.0) ------------------
def _fake_cdport(folder: Path, help_text: str, run_lines: list[str], pause: float = 0.0,
                 name: str = "cdport") -> Path:
    """A 'cdport' that prints help or, when run, the given lines."""
    script = folder / name
    lines = ";".join(f"print({line!r}, flush=True)" for line in run_lines)
    script.write_text(f"#!{sys.executable}\nimport sys, time\n"
                      f"if '--help' in sys.argv: print({help_text!r}); sys.exit(0)\n"
                      f"{lines.split(';')[0] if lines else ''}\n"
                      f"time.sleep({pause})\n"
                      + "\n".join(lines.split(";")[1:]) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_only_a_cdport_that_can_lead_back_is_offered(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    assert web.find_cdport() is None
    _fake_cdport(tmp_path, "  --web  --no-browser", [], name="cdimport")         # cdimport 0.5
    assert web.find_cdport() is None
    old = _fake_cdport(tmp_path, "  --web  --return-url URL", [], name="cdimport")
    assert web.find_cdport() == str(old)
    # cdport installs a cdimport command too; the new name comes first.
    script = _fake_cdport(tmp_path, "  --web  --return-url URL", [])
    assert web.find_cdport() == str(script)


def test_both_names_lines_are_read():
    for name in ("cdport", "cdimport"):
        page = web._CDPORT_PAGE.search(f"{name} is running at http://127.0.0.1:5555/?token=a_B-9")
        assert page.group(1) == "http://127.0.0.1:5555/?token=a_B-9"
        assert web._CDPORT_DONE.match(f"{name} finished: /m/A B/Album: Live").group(1) == "/m/A B/Album: Live"
    assert not web._CDPORT_PAGE.search("lyrembed is running at http://127.0.0.1:1/?token=x")


def test_an_import_opens_the_album_it_brought(cfg, tmp_path, monkeypatch):
    album = tmp_path / "lib" / "Night Lines"
    album.mkdir(parents=True)
    _make_flac(album / "01.flac")
    script = _fake_cdport(tmp_path, "--return-url", ["cdport is running at http://127.0.0.1:5555/?token=abc",
                                                     f"cdport finished: {album}"], pause=0.3)
    session = web.WebSession(None, [], tagging.MetadataCache())
    session._cdport = (True, str(script))
    with pytest.raises(web.ApiError):
        session.start_import()                                                  # no page address yet
    session.page_url = "http://127.0.0.1:41000/"
    assert session.start_import() == {"url": "http://127.0.0.1:5555/?token=abc"}
    assert session.importing.command[1:] == ["--web", "--no-browser", "--return-url", "http://127.0.0.1:41000/",
                                             "--return-name", "lyrembed"]
    assert session.import_away() and session.library()["importing"]
    session.importing.process.wait(5)
    deadline = time.monotonic() + 5
    while session.importing.ended_at is None:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    library = session.library()
    assert library["imported"] == "Night Lines" and library["folder"] == str(album)
    assert session.library()["imported"] == ""                                 # said once
    assert session.import_away()                                               # the tab is on its way back
    monkeypatch.setattr(web, "IMPORT_GRACE", 0.0)
    assert not session.import_away()


def test_import_is_not_offered_to_a_page_cdport_opened(cfg, tmp_path):
    session = web.WebSession(None, [], tagging.MetadataCache())
    session._cdport = (True, "/usr/bin/cdport")
    assert session.library()["cdport"] is True
    session.return_url = "http://127.0.0.1:41877/"
    assert session.library()["cdport"] is False                              # no loop between the two


def test_two_pages_on_one_host_keep_their_own_cookie(web_server):
    # Cookies belong to the host, not the port: a second lyrembed (cdport's
    # lyrics step starts one) overwrote the first one's token under one name.
    other = web.cookie_name(web_server.port + 1)
    assert other != web.cookie_name(web_server.port)
    response, _ = _http(web_server, "GET", "/api/library", cookie=False, headers={"Cookie": f"{other}={TOKEN}"})
    assert response.status == 403


# --- review of 2026-10-26 ----------------------------------------------------
def test_windows_line_ends_become_plain_ones(cfg, tmp_path):
    track = _flac_with_lyrics(tmp_path / "a.flac", "[00:01.00]one\r\n[00:02.00]two\r\n")
    assert tagging.MetadataCache().get(track).lyrics == "[00:01.00]one\n[00:02.00]two"
    assert "\r" not in _built(artist="A", title="S", text="[00:01.00]one\r\n[00:02.00]two").text
    core.write_lyrics(track, "[00:01.00]a\r\n[00:02.00]b")
    from mutagen.flac import FLAC

    assert FLAC(track)["LYRICS"] == ["[00:01.00]a\n[00:02.00]b"]


def test_of_two_copies_of_a_text_the_one_that_fits_the_length_stays(cfg):
    # LRCLIB's /get without the duration still counts as exact; its record
    # of a 60 s longer version hid the fitting copy, and batch mode took neither.
    lyrics = "[00:01.00]la la\n[00:03.00]na na"
    longer = _built(provider="LRCLIB", artist="A", title="Song", text=lyrics, duration=260, exact=True)
    fitting = _built(provider="LRCLIB", artist="A", title="Song", text=lyrics, duration=200)
    related, _ = providers._classify([fitting, longer], "Song", "A")
    assert related == [fitting]
    assert providers.pick_automatic(providers.SearchReport(candidates=related), 200) is fitting


def test_an_off_vocal_track_gets_its_own_cache_slot(cfg):
    key = providers.SearchCache.key
    assert key("Song", "A", "", 200) != key("Song (カラオケ)", "A", "", 200)
    assert key("Song", "A", "", 200) != key("Song (伴奏)", "A", "", 200)
    assert key("Song (Inst.)", "A", "", 200) == key("song (inst.)", "a", "", 200)


def test_sidecar_export_leaves_a_sidecar_that_is_not_utf8(cfg, tmp_path, state_dir):
    music = tmp_path / "music"
    music.mkdir()
    first = _flac_with_lyrics(music / "a.flac", "[00:01.00]新しい")
    second = _flac_with_lyrics(music / "b.flac", "[00:01.00]second")
    old = "[00:01.00]古い".encode("shift_jis")
    tagging.sidecar_path(first).write_bytes(old)
    steps = list(core.bulk("sidecars", [first, second], music, tagging.MetadataCache(), core.backup_directory()))
    outcomes = [s for s in steps if isinstance(s, core.Outcome)]
    assert [o.result for o in outcomes] == ["failed", "done"]     # the run goes on
    assert "not UTF-8" in outcomes[0].message
    assert tagging.sidecar_path(first).read_bytes() == old


def test_mixed_romanization_names_the_missing_packages(cfg, monkeypatch):
    lyrics = "[00:01.00]사랑해요\n[00:02.00]ありがとう"
    for name in ("HAS_KOREAN", "HAS_KAKASI", "HAS_PYPINYIN", "HAS_ANYASCII"):
        monkeypatch.setattr(text, name, False)
    assert core.auto_romanize(lyrics) == (None, "'korean-romanizer' and 'pykakasi' are not installed")
    assert web.WebSession.auto_romanize(lyrics)["blocked"]
    monkeypatch.setattr(text, "HAS_KOREAN", True)
    # Half-romanized lyrics are worse than none.
    assert core.auto_romanize(lyrics) == (None, "'pykakasi' is not installed")
    with pytest.raises(web.ApiError):
        web.WebSession.romanize(lyrics, "mixed", "zh", False)


@pytest.mark.skipif(not text.HAS_KAKASI, reason="pykakasi required")
def test_a_kanji_pykakasi_cannot_read_does_not_make_japanese_lyrics_chinese(cfg):
    assert not text._kakasi_reads("唸")                 # in JIS X 0208, not in pykakasi
    assert text.detect_script("[00:01.00]唸る声 君の夢\n[00:02.00]夜の街") == "ja"
    # Without Kana it still points to Chinese, and simplified forms always do.
    assert text.detect_script("我爱你\n东京") == "zh"
    assert text.detect_script("我爱你\n君の夢") == "mixed"


@pytest.mark.skipif(not (text.HAS_KAKASI and text.HAS_PYPINYIN), reason="pykakasi and pypinyin required")
def test_kanji_between_punctuation_on_a_japanese_line_reads_as_japanese(cfg):
    out = text.romanize_lyrics("「夢」、そして\n我爱你", "mixed")
    assert out.splitlines() == ["「yume」、soshite", "wǒ ài nǐ"]


def test_saved_settings_and_aliases_are_used_by_the_next_search(cfg, config_home, tmp_path, monkeypatch):
    session, _ = _searchable(tmp_path, "Song", "Artist")
    calls = []

    def collect(title, artist, album, duration, aliases=False):
        calls.append(title)
        return providers.SearchReport()

    monkeypatch.setattr(providers, "collect_candidates", collect)
    closed = []
    monkeypatch.setattr(providers, "close_session", lambda: closed.append(True))
    session.search(0)
    session.search(0)
    assert len(calls) == 1                       # cached
    session.save_settings([{"section": "settings", "key": "prefer_latin", "value": True}])
    session.search(0)
    assert len(calls) == 2 and not closed
    session.save_aliases([["Artist", "Other Name"]])
    session.search(0)
    assert len(calls) == 3
    session.save_settings([{"section": "api", "key": "retry_total", "value": 4}])
    assert closed                                # the HTTP session takes the new retries


def test_no_other_folder_while_a_bulk_run_goes(cfg, tmp_path):
    import types

    other = tmp_path / "other"
    other.mkdir()
    _make_flac(other / "x.flac")
    session = web.WebSession(tmp_path, [], tagging.MetadataCache())
    session.job = types.SimpleNamespace(running=True)
    with pytest.raises(web.ApiError) as busy:
        session.open_folder(str(other))
    assert busy.value.status == 409 and session.base_dir == tmp_path


def test_a_track_removed_from_disk_is_not_an_internal_error(cfg, tmp_path):
    track = _make_flac(tmp_path / "a.flac")
    session = web.WebSession(tmp_path, [track], tagging.MetadataCache())
    session.track(0)
    track.unlink()
    with pytest.raises(web.ApiError) as gone:
        session.audio(0)
    assert gone.value.status == 404 and "a.flac" in gone.value.message


def test_home_reached_through_a_link_and_the_folders_above_it_are_refused(tmp_path, monkeypatch):
    real = tmp_path / "var" / "home"
    (real / "user").mkdir(parents=True)
    (tmp_path / "home").symlink_to(real)               # Fedora Atomic: /home -> /var/home
    monkeypatch.setenv("HOME", str(tmp_path / "home" / "user"))
    for folder in (real / "user", real, tmp_path / "home" / "user"):
        _, problem = core.check_music_directory(folder.resolve())
        assert problem is not None and "home folder" in problem.message, folder


def test_unusable_patterns_ratios_and_language_codes_are_reported(tmp_path, cfg):
    path = tmp_path / "config.json"
    path.write_text(
        '{"aliases": [["BTS", "Bangtan Boys"], ["IU", 5]],'
        ' "cleaning": {"feature_regex": "(unclosed"},'
        ' "settings": {"non_latin_ratio_threshold": 2.0, "uslt_language": "de"}}',
        encoding="utf-8",
    )
    merged, warnings = config.load_config(path)
    joined = " ".join(warnings)
    assert merged["cleaning"]["feature_regex"] == config.DEFAULT_CONFIG["cleaning"]["feature_regex"]
    assert "not a valid regular expression" in joined
    assert merged["settings"]["non_latin_ratio_threshold"] == 0.1 and "at most 1" in joined
    assert merged["settings"]["uslt_language"] == "eng" and "three-letter" in joined
    assert merged["aliases"] == [["BTS", "Bangtan Boys"]]     # the warning says "ignoring"
    assert config.setting_problem("cleaning", "feature_regex", "[") is not None
    assert config.setting_problem("cleaning", "feature_regex", "") is None
    assert config.setting_problem("settings", "uslt_language", "jpn") is None


def test_the_launcher_names_the_package_files_there_are():
    source = (Path(__file__).resolve().parent.parent / "run.py").read_text(encoding="utf-8")
    listed = set(re.findall(r'"([^"]+\.py)"', re.search(r"modules = \[(.*?)\]", source, re.S).group(1)))
    package = Path(__file__).resolve().parent.parent / "lyrembed"
    assert listed == {p.name for p in package.glob("*.py")}


# --- a newer lyrembed ----------------------------------------------------------
def _fake_ls_remote(monkeypatch, stdout="", code=0, stderr=""):
    asked = []

    def run(cmd, **kwargs):
        asked.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)
    monkeypatch.setattr(selfupdate.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(selfupdate.subprocess, "run", run)
    return asked


def test_update_check_takes_the_newest_release_tag(monkeypatch):
    refs = "".join(f"abc\trefs/tags/{tag}\n" for tag in ("0.9.0", "1.0.0", "1.10.0", "1.2.0", "2.0.0-rc1", "v3.0.0"))
    asked = _fake_ls_remote(monkeypatch, refs)
    assert REAL_LATEST() == "1.10.0"
    cmd, kwargs = asked[0]
    assert cmd[-1] == selfupdate.REPO and kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    _fake_ls_remote(monkeypatch, code=128, stderr="fatal: Repository not found.\n")
    with pytest.raises(selfupdate.UpdateError, match="^Repository not found"):
        REAL_LATEST()


def _pipx_venv(tmp_path, url):
    venv = tmp_path / ("venv" + str(len(list(tmp_path.glob("venv*")))))
    venv.mkdir()
    (venv / "pipx_metadata.json").write_text(json.dumps({
        "main_package": {"package": "lyrembed", "package_or_url": url, "pip_args": []}, "venv_args": []}))
    return venv


def test_update_keeps_the_extras_and_a_pinned_release(tmp_path):
    how = selfupdate.how_installed("1.1.0", _pipx_venv(tmp_path, "lyrembed[all] @ git+https://github.com/sobakah/lyrembed.git"))
    assert how.command == "pipx upgrade lyrembed"
    how = selfupdate.how_installed("1.1.0", _pipx_venv(tmp_path, "lyrembed[all] @ git+https://github.com/sobakah/lyrembed.git@1.0.0"))
    assert how.argv[1:] == ["install", "--force", "lyrembed[all] @ git+https://github.com/sobakah/lyrembed.git@1.1.0"]
    top = tmp_path / "checkout"
    (top / ".git").mkdir(parents=True)
    how = selfupdate.how_installed("1.1.0", tmp_path / "none", top / "lyrembed")
    assert how.kind == "checkout" and how.argv is None


def test_update_runs_pipx_and_keeps_its_output(tmp_path):
    log = tmp_path / "state" / "update.log"
    code, tail = selfupdate.install(["sh", "-c", "echo one; echo two >&2; exit 3"], log)
    assert code == 3 and "one" in tail and "two" in tail and log.read_text().startswith("$ sh -c")
    assert selfupdate.install(["/nonexistent/pipx"], log)[0] == 127


def test_the_page_looks_for_updates_at_every_start(cfg, tmp_path, monkeypatch):
    """Not once a day: a release made a minute ago shows at the next start
    (the check runs in a thread, so the start doesn't wait for it)."""
    monkeypatch.setattr(selfupdate, "latest", lambda timeout=30: config.__version__)
    session = web.WebSession(tmp_path, [], tagging.MetadataCache())
    assert web.wants_update_check(session)
    session.updates.check(quiet=True)
    _wait_for(session.updates, "current")
    assert web.wants_update_check(web.WebSession(tmp_path, [], tagging.MetadataCache()))   # again at once
    session.return_url = "http://127.0.0.1:9/"            # opened by cdport: it doesn't look
    assert not web.wants_update_check(session)


def _wait_for(updates, *states):
    deadline = time.monotonic() + 5
    while updates.view()["state"] not in states:
        assert time.monotonic() < deadline, updates.view()
        time.sleep(0.01)
    return updates.view()


def test_the_page_finds_installs_and_restarts_a_newer_lyrembed(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(selfupdate, "latest", lambda timeout=30: "9.0.0")
    monkeypatch.setattr(selfupdate, "how_installed", lambda version: selfupdate.Install(
        "pipx", "pipx upgrade lyrembed", ["/usr/bin/pipx", "upgrade", "lyrembed"]))
    runs = []
    monkeypatch.setattr(selfupdate, "install", lambda argv, log: (runs.append(argv) or 1, "no network"))
    session = web.WebSession(tmp_path, [], tagging.MetadataCache())
    with pytest.raises(web.ApiError, match="no update"):
        session.updates.install()
    session.updates.check()
    view = _wait_for(session.updates, "available")
    assert view["version"] == "9.0.0" and view["canInstall"] and view["notes"].endswith("/tag/9.0.0")
    with pytest.raises(web.ApiError, match="No new lyrembed"):
        session.request_restart()
    session.updates.install()
    view = _wait_for(session.updates, "install-failed")
    assert view["message"] == "no network" and view["log"].endswith("update.log")
    monkeypatch.setattr(selfupdate, "install", lambda argv, log: (runs.append(argv) or 0, ""))
    session.updates.install()  # try again
    _wait_for(session.updates, "installed")
    assert runs == [["/usr/bin/pipx", "upgrade", "lyrembed"]] * 2
    session.job = type("RunningJob", (), {"running": True})()
    with pytest.raises(web.ApiError, match="bulk run"):
        session.request_restart()
    session.job = None
    assert session.request_restart() == {"ok": True} and session.restart_requested


def test_update_api_and_restart_stop_the_server(web_server, monkeypatch):
    response, data = _http(web_server, "GET", "/api/update")
    assert response.status == 200 and json.loads(data) == {"state": "idle", "current": config.__version__}
    response, data = _http(web_server, "POST", "/api/ping", {})
    assert json.loads(data)["update"]["state"] == "idle"
    response, data = _http(web_server, "POST", "/api/update/restart", {})
    assert response.status == 409 and not web_server.stop.is_set()
    web_server.session.updates._set(state="installed", version="9.0.0")
    response, _ = _http(web_server, "POST", "/api/update/restart", {})
    assert response.status == 200 and web_server.stop.wait(2)


def test_a_failed_check_is_reported_to_the_page(cfg, tmp_path):
    session = web.WebSession(tmp_path, [], tagging.MetadataCache())
    session.updates.check(quiet=True)
    view = _wait_for(session.updates, "failed")
    assert view["quiet"] and view["message"] == "no network in tests"


def test_restart_hands_over_the_address(monkeypatch):
    monkeypatch.setenv(cli.RESTART_ENV, "8123:abc")
    assert cli._take_restart() == (8123, "abc") and cli.RESTART_ENV not in os.environ
    assert cli._take_restart() is None
    monkeypatch.setenv(cli.RESTART_ENV, "nonsense")
    assert cli._take_restart() is None
    started = []
    monkeypatch.setattr(cli.os, "execvp", lambda file, argv: started.append((argv, os.environ[cli.RESTART_ENV])))
    monkeypatch.setattr(selfupdate, "restart_argv", lambda: ["/home/u/.local/bin/lyrembed", "~/Music/Album"])
    cli._restart(8123, "abc")
    assert started == [(["/home/u/.local/bin/lyrembed", "~/Music/Album"], "8123:abc")]
    monkeypatch.delenv(cli.RESTART_ENV)


# --- review of 2026-10-31 ----------------------------------------------------
import types  # noqa: E402
from pathlib import PureWindowsPath  # noqa: E402


def _flac_with_tags(path: Path, **tags) -> Path:
    from mutagen.flac import FLAC

    audio = FLAC(_make_flac(path))
    audio.update(tags)
    audio.save()
    return path


def test_kanji_only_lines_do_not_make_a_japanese_song_mixed():
    song = "[00:01.00]君を探している\n[00:05.00]約束\n[00:09.00]東京物語"
    assert text.detect_script(song) == "ja"
    assert text.missing_packages(song) == ([] if text.HAS_KAKASI else ["pykakasi"])
    # Still Chinese without Kana, and still mixed with proof of Chinese.
    assert text.detect_script("約束\n未来") == "zh"
    assert text.detect_script("君を探している\n我爱你中国") == "mixed"


@pytest.mark.skipif(not text.HAS_KAKASI, reason="pykakasi not installed")
def test_kanji_only_lines_of_a_japanese_song_are_romanized_as_japanese():
    result, reason = core.auto_romanize("[00:01.00]君を探している\n[00:05.00]約束")
    assert reason == "" and "yakusoku" in result and "yuē" not in result


def test_a_title_of_symbols_does_not_match_every_title():
    assert not text.matches_title("???", "Anything At All")
    assert not text.matches_title("♥", "Hello")
    assert text.matches_title("???", " ??? ")
    assert text.matches_title("", "Unknown Song")                               # unknown still counts


def test_one_word_of_a_longer_title_is_another_song():
    assert not text.matches_title("Stay", "Stay With Me")
    assert not text.matches_title("Love", "I Don't Love You")
    assert not text.matches_title("I Don't Love You", "Love")
    assert text.matches_title("Hello", "Hello (Live)")
    assert text.matches_title("Paper Harbour", "Paper Harbour Remastered")


def test_a_result_without_an_artist_is_not_embedded_unattended():
    nameless = providers.Candidate("NETEASE", "", "Song", "", "[00:01.00]x", synced=True)
    named = providers.Candidate("LRCLIB", "Band", "Song", "", "[00:01.00]x", synced=True)
    report = providers.SearchReport()
    report.candidates = [nameless]
    assert providers.pick_automatic(report, 0) is None
    report.candidates = [nameless, named]
    assert providers.pick_automatic(report, 0) is named


def test_a_credit_gets_the_aliases_of_each_artist_it_names():
    aliases = text.get_artist_aliases("IU, BTS")
    solo_iu, solo_bts = text.get_artist_aliases("IU"), text.get_artist_aliases("BTS")
    assert set(aliases) & set(solo_iu) and set(aliases) & set(solo_bts)
    # About as many searches as one artist's: each alias is a request. The
    # credit may search a name alone, but a solo artist's own name is no alias.
    assert len(aliases) <= max(len(solo_iu), len(solo_bts)) + 1
    assert "IU" not in solo_iu


def test_a_rate_limit_is_waited_out_after_a_match_from_another_provider(cfg, tmp_path, monkeypatch):
    tracks = [_flac_with_tags(tmp_path / f"{n}.flac", title=f"Song {n}", artist="Band") for n in (1, 2, 3)]
    limited = providers.SearchReport()
    limited.rate_limited = {"LRCLIB": 30.0}
    limited.candidates = [providers.Candidate("NETEASE", "Band", "Song", "", "[00:01.00]x", synced=True)]
    free = providers.SearchReport()
    free.candidates = limited.candidates
    answers = iter([(limited, limited.candidates[0]), (free, free.candidates[0]), (free, free.candidates[0])])
    monkeypatch.setattr(core, "find_automatic", lambda meta: next(answers))
    pauses = [s for s in core.batch(tracks, tmp_path, tagging.MetadataCache(), overwrite=False, dry_run=True,
                                    backup_root=None) if isinstance(s, core.Pause)]
    assert [(p.seconds, p.limited) for p in pauses] == [(30.0, ["LRCLIB"]), (cfg["api"]["batch_delay_seconds"], [])]


def test_an_import_counts_as_running_until_its_output_is_read(cfg, tmp_path):
    script = tmp_path / "ends_at_once.py"
    script.write_text("print('cdport finished: /album', flush=True)\n", encoding="utf-8")
    release = threading.Event()

    class LateReader(web.Import):
        def _read(self):
            release.wait(5)
            super()._read()

    run = LateReader([sys.executable, str(script)])
    run.process.wait(5)
    assert run.running and run.album is None                                    # the page keeps asking
    release.set()
    deadline = time.monotonic() + 5
    while run.running:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert run.album == Path("/album")


def test_a_conversion_for_listening_holds_up_nothing_else(cfg, tmp_path, monkeypatch):
    track = _make_flac(tmp_path / "a.m4a")
    session = web.WebSession(tmp_path, [_make_flac(tmp_path / "b.flac")], tagging.MetadataCache())
    session.files = [track]
    monkeypatch.setattr(web, "_is_alac", lambda path: True)
    monkeypatch.setattr(web.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    started, finish = threading.Event(), threading.Event()
    calls = []

    def convert(command, **kwargs):
        calls.append(command)
        started.set()
        assert finish.wait(5)
        Path(command[-1]).write_bytes(b"fLaC")

    monkeypatch.setattr(web.subprocess, "run", convert)
    copies = []
    threads = [threading.Thread(target=lambda: copies.append(session.audio(0)[0])) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert started.wait(5)
    answered = threading.Event()

    def other_request():
        with session.lock:
            answered.set()

    threading.Thread(target=other_request, daemon=True).start()
    assert answered.wait(2)                                                     # the page goes on
    finish.set()
    for thread in threads:
        thread.join(5)
    assert len(calls) == 1 and copies[0] == copies[1]                           # converted once
    session.close()


def test_a_changed_cover_file_shows_without_a_changed_track(cfg, tmp_path):
    track = _make_flac(tmp_path / "a.flac")
    session = web.WebSession(tmp_path, [track], tagging.MetadataCache())
    assert session._cover(track) is None
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"first")
    first = session._cover(track)
    assert first is not None
    stamp = cover.stat()
    cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"second")
    os.utime(cover, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
    assert session._cover(track)["id"] != first["id"]
    cover.unlink()
    assert session._cover(track) is None


def test_a_page_that_went_away_mid_answer_is_no_error(web_server, monkeypatch, caplog):
    def gone():
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(web_server.session, "library", gone)
    with pytest.raises(http.client.HTTPException):
        _http(web_server, "GET", "/api/library")
    assert "web request failed" not in caplog.text


def test_an_import_opening_its_album_reads_the_tracks_once(cfg, tmp_path, monkeypatch):
    album = tmp_path / "Album"
    album.mkdir()
    _make_flac(album / "01.flac")
    session = web.WebSession(None, [], tagging.MetadataCache())
    session.importing = types.SimpleNamespace(running=False, album=album)
    answers = []
    real = session._summary
    monkeypatch.setattr(session, "_summary", lambda *a: (answers.append(a), real(*a))[1])
    assert session.library()["imported"] == "Album"
    assert len(answers) == 1


def test_a_stuck_provider_does_not_keep_a_finished_lyrembed_alive(tmp_path):
    # Python joins an executor's threads at exit; syncedlyrics has no timeout.
    script = tmp_path / "stuck.py"
    script.write_text(f"""
import sys, time, types
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
from lyrembed import config, providers
config.CONFIG["api"]["syncedlyrics_timeout_seconds"] = 0.1
config.CONFIG["api"]["enable_syncedlyrics"] = True
providers.HAS_SYNCEDLYRICS = True
providers.syncedlyrics = types.SimpleNamespace(search=lambda query, **kw: time.sleep(30))
report = providers.SearchReport()
providers.query_syncedlyrics("Song", "Band", report)
print(report.errors)
""", encoding="utf-8")
    done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=10)
    assert "Timeout" in done.stdout, done.stderr


def test_restart_finds_the_launcher_as_windows_starts_it(monkeypatch):
    # The launcher's sys.argv[0] has lost its .exe, so only which() finds the file.
    monkeypatch.setattr(sys, "argv", [r"C:\pipx\venvs\lyrembed\Scripts\lyrembed", "--flag"])
    monkeypatch.setattr(selfupdate.shutil, "which", lambda name: name + ".exe")
    monkeypatch.setattr(selfupdate, "Path", PureWindowsPath)
    assert selfupdate.restart_argv() == [r"C:\pipx\venvs\lyrembed\Scripts\lyrembed.exe", "--flag"]


# --- romanize --stdio (for Gatefold) ----------------------------------------

def _romanize_lines(*requests: str) -> list[dict]:
    import io
    import json
    out = io.StringIO()
    cli.romanize_stdio(io.StringIO("".join(r + "\n" for r in requests)), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_romanize_stdio_answers_every_line_and_survives_bad_ones():
    answers = _romanize_lines('{"id": 7, "lyrics": "just english"}', "", "not json",
                              '{"id": 8}', '{"id": "x", "lyrics": "[00:01.00]Hello"}')
    assert [a["id"] for a in answers] == [7, None, None, "x"]
    assert answers[0] == {"id": 7, "lyrics": None, "language": None, "reason": "already Latin"}
    assert "error" in answers[1] and "error" in answers[2]
    assert answers[3]["lyrics"] is None


def test_romanize_help_names_stdio_which_gatefold_looks_for(capsys):
    with pytest.raises(SystemExit):
        cli.main(["romanize", "--help"])
    assert "--stdio" in capsys.readouterr().out


@pytest.mark.skipif(not text.HAS_KOREAN, reason="korean-romanizer not installed")
def test_romanize_stdio_puts_the_romanization_below_whatever_the_layout(cfg):
    cfg["settings"]["romanization_layout"] = "replace"
    [answer] = _romanize_lines('{"id": 1, "lyrics": "[00:01.00]사랑해요\\r\\n[00:03.00]Hello"}')
    assert answer["language"] == "ko"
    assert answer["lyrics"] == "[00:01.00]사랑해요\n[00:01.00]saranghaeyo\n[00:03.00]Hello"
    # Already in that form: nothing to do.
    again = core.romanize_for_display(answer["lyrics"])
    assert again == (None, "ko", "already romanized")


@pytest.mark.skipif(not (text.HAS_KAKASI and text.HAS_PYPINYIN),
                    reason="needs pykakasi and pypinyin")
def test_a_japanese_hint_reads_kanji_only_lyrics_as_japanese():
    alone, language, _ = core.romanize_for_display("[00:01.00]約束")
    assert language == "zh" and "yakusoku" not in alone
    hinted, language, _ = core.romanize_for_display("[00:01.00]約束", "ja")
    assert language == "ja"
    assert hinted == "[00:01.00]約束\n[00:01.00]yakusoku"
    # Chinese evidence wins over the hint: 爱 is in no Japanese standard.
    chinese, language, _ = core.romanize_for_display("[00:01.00]我爱你", "ja")
    assert language == "zh" and "ài" in chinese



# --- the D-Bus interface, for Gatefold ------------------------------------------
import itertools  # noqa: E402
import signal  # noqa: E402

from lyrembed import dbus_service  # noqa: E402


class FakeRun:
    """A run's process: prints *lines*, then runs until released (or
    stopped, as with Ctrl+C: 130)."""

    def __init__(self, lines=(), code=0, hold=True):
        self.lines, self.code, self.signals = list(lines), code, []
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.stdout = self._out()

    def _out(self):
        yield from (line + "\n" for line in self.lines)
        self.release.wait(10)

    def wait(self):
        return self.code

    def send_signal(self, sig):
        self.signals.append(sig)
        self.code = 130
        self.release.set()


PAGE_URL = "http://127.0.0.1:43210/?token=abc_DEF-1"
PAGE_LINE = web.RUNNING_LINE.format(url=PAGE_URL)


def bus_service(*processes, clock=None, opens=True):
    """A service with stand-in processes and browser; .started holds the
    commands, .opened the addresses given to the browser."""
    started, opened = [], []

    def spawn(command):
        started.append(command)
        return processes[len(started) - 1]

    def open_url(url):
        opened.append(url)
        return opens
    service = dbus_service.Service(spawn=spawn, open_url=open_url, **({"clock": clock} if clock else {}))
    service.started, service.opened = started, opened
    return service


def drain(service, count=1, timeout=5.0):
    """What the service sends, once *count* items came (or the time is up)."""
    out, deadline = [], time.monotonic() + timeout
    while len(out) < count and time.monotonic() < deadline:
        out += service.outgoing()
        time.sleep(0.01)
    return out


@pytest.fixture
def music(tmp_path, monkeypatch):
    folder = tmp_path / "Musik"
    (folder / "Artist" / "Album").mkdir(parents=True)
    monkeypatch.setattr(core, "music_folder", lambda: folder)
    return folder


def test_dbus_info_says_what_gatefold_read_from_the_config(config_home, music):
    config_home.parent.mkdir(parents=True)
    config_home.write_text('{"settings": {"romanization_layout": "below", "max_file_count": 400}}', encoding="utf-8")
    service = bus_service()
    signature, (info,) = service.call("GetInfo", "", (), None)
    assert signature == "a{sv}" and info["Version"] == ("s", config.__version__)
    assert info["InterfaceVersion"] == ("u", 1) and info["MusicFolder"] == ("s", str(music.resolve()))
    assert info["RomanizationLayout"] == ("s", "below") and info["MaxFileCount"] == ("u", 400)
    assert info["Romanizes"][0] == "as" and set(info["Romanizes"][1]) <= {"ko", "ja", "zh", "any"}


def test_dbus_info_fits_its_types(config_home, monkeypatch):
    # A uint32 cannot carry more; jeepney would fail to send the reply.
    config_home.parent.mkdir(parents=True)
    config_home.write_text('{"settings": {"max_file_count": 5000000000}}', encoding="utf-8")
    monkeypatch.setattr(core, "music_folder", Path.home)          # no music folder: falls back to home
    info = bus_service().info()
    assert info["MaxFileCount"] == ("u", 2**32 - 1) and info["MusicFolder"] == ("s", "")


def test_dbus_signatures_come_from_the_introspection_data():
    assert dbus_service._SIGNATURES == {"GetInfo": "", "ArtistAliases": "", "Romanize": "ss", "OpenPage": "s",
                                        "ShowPage": "s", "FetchLyrics": "sa{sv}", "Runs": "", "Stop": "s"}


def test_dbus_aliases_are_the_ones_in_effect(config_home):
    config_home.parent.mkdir(parents=True)
    config_home.write_text('{"aliases": [["Night Owls", "夜猫"]]}', encoding="utf-8")
    signature, (groups,) = bus_service().call("ArtistAliases", "", (), None)
    assert signature == "aas" and ["Night Owls", "夜猫"] in groups
    assert len(groups) == len(config.builtin_aliases()) + 1


def test_dbus_romanize_answers_as_romanize_stdio_does():
    service = bus_service()
    assert service.call("Romanize", "ss", ("[00:01.00]Hello", ""), "latin") is None   # answered by the romanizer
    expected = [("return", "latin", "sss", ("", "", "already Latin"))]
    if text.HAS_KOREAN:
        service.call("Romanize", "ss", ("[00:01.00]안녕", "ja"), "korean")
        lyrics, language, _ = core.romanize_for_display("[00:01.00]안녕", "ja")
        expected.append(("return", "korean", "sss", (lyrics, language, "")))
    assert drain(service, len(expected)) == expected and not service.busy()
    for hint, lyrics in (("ko", "x"), ("", "x" * (dbus_service.MAX_LYRICS + 1))):
        with pytest.raises(dbus_service.ServiceError) as refused:
            service.call("Romanize", "ss", (lyrics, hint), "call")
        assert refused.value.name.endswith("InvalidArgs")


def test_dbus_page_opens_in_the_browser_and_its_address_stays_here(tmp_path):
    # The token opens the page's whole API: a sandboxed caller must not get it.
    process = FakeRun([PAGE_LINE, "Open that address in your browser."])
    service = bus_service(process)
    assert service.call("OpenPage", "s", (str(tmp_path),), "the call") is None
    assert service.started[0][-6:] == [str(tmp_path.resolve()), "--no-browser", "--color", "never",
                                       "--unvisited-timeout", "600"]
    assert drain(service) == [("return", "the call", "sb", ("1", True))] and service.opened == [PAGE_URL]
    assert service.call("Runs", "", (), None) == ("a(sss)", ([("1", "page", str(tmp_path.resolve()))],))
    # The same folder again: the page already open is shown, not a second one.
    assert service.call("OpenPage", "s", (str(tmp_path),), "again") is None
    assert drain(service) == [("return", "again", "sb", ("1", True))] and len(service.started) == 1
    assert service.call("ShowPage", "s", ("1",), "show") is None
    assert drain(service) == [("return", "show", "b", (True,))] and service.opened == [PAGE_URL] * 3
    process.release.set()
    assert drain(service) == [("signal", "Finished", "si", ("1", 0))]
    assert service.runs == {} and not service.busy()
    with pytest.raises(dbus_service.ServiceError) as gone:
        service.call("ShowPage", "s", ("1",), "show")
    assert gone.value.name.endswith("UnknownRun")


def test_dbus_page_says_when_no_browser_opened(tmp_path):
    service = bus_service(FakeRun([PAGE_LINE]), opens=False)
    service.call("OpenPage", "s", ("",), "call")
    assert service.started[0][-5:] == ["--no-browser", "--color", "never", "--unvisited-timeout", "600"]
    assert drain(service) == [("return", "call", "sb", ("1", False))]


def test_dbus_show_page_needs_a_page_that_is_up(tmp_path, music):
    service = bus_service(FakeRun(), FakeRun())
    (tmp_path / "elsewhere").mkdir()
    service.call("OpenPage", "s", (str(tmp_path / "elsewhere"),), "call")
    service.call("FetchLyrics", "sa{sv}", (str(music / "Artist" / "Album"), {}), "call")
    for run, error in (("1", "NotUp"), ("2", "UnknownRun")):
        with pytest.raises(dbus_service.ServiceError) as refused:
            service.call("ShowPage", "s", (run,), "show")
        assert refused.value.name.endswith(error)


def test_dbus_fetches_lyrics_only_inside_the_music_folder(tmp_path, music):
    album = music / "Artist" / "Album"
    process = FakeRun(["Searching...", PAGE_LINE])          # batch output is not a page
    service = bus_service(process, FakeRun())
    for elsewhere, error in ((str(tmp_path), "NotInMusicFolder"), ("/etc", "NotInMusicFolder"),
                             (str(album / ".." / ".." / ".."), "NotInMusicFolder"),
                             ("Artist/Album", "InvalidArgs"), (str(music / "Nothing"), "NoFolder")):
        with pytest.raises(dbus_service.ServiceError) as refused:
            service.call("FetchLyrics", "sa{sv}", (elsewhere, {}), "call")
        assert refused.value.name.endswith(error), elsewhere
    assert service.call("FetchLyrics", "sa{sv}", (str(album), {"Romanize": ("b", True)}), "call") == ("s", ("1",))
    assert service.started[0][-5:] == [str(album.resolve()), "--auto", "--color", "never", "--romanize"]
    with pytest.raises(dbus_service.ServiceError) as busy:           # nor a page on it meanwhile
        service.call("OpenPage", "s", (str(album),), "call")
    assert busy.value.name.endswith("Busy")
    with pytest.raises(dbus_service.ServiceError) as busy:
        service.call("FetchLyrics", "sa{sv}", (str(music / "Artist"), {}), "call")
    assert busy.value.name.endswith("Busy") and len(service.started) == 1
    assert service.call("Runs", "", (), None) == ("a(sss)", ([("1", "fetch", str(album.resolve()))],))
    process.release.set()
    assert drain(service) == [("signal", "Finished", "si", ("1", 0))] and service.opened == []


def test_dbus_fetches_nothing_without_a_music_folder(tmp_path, monkeypatch):
    # core.music_folder falls back to the home folder: far too wide.
    album = Path.home() / "Album-that-is-not-there"
    monkeypatch.setattr(core, "music_folder", Path.home)
    monkeypatch.setattr(dbus_service.Service, "_folder", staticmethod(lambda folder: album))
    with pytest.raises(dbus_service.ServiceError) as refused:
        bus_service().call("FetchLyrics", "sa{sv}", (str(album), {}), "call")
    assert refused.value.name.endswith("NotInMusicFolder") and "no music folder" in str(refused.value)


def test_dbus_fetch_refuses_options_it_does_not_know(music):
    service = bus_service()
    album = str(music / "Artist" / "Album")
    for options in ({"Command": ("s", "rm")}, {"Romanize": ("s", "yes")}, {"Overwrite": ("b", True)}):
        with pytest.raises(dbus_service.ServiceError) as refused:
            service.call("FetchLyrics", "sa{sv}", (album, options), "call")
        assert refused.value.name.endswith("InvalidArgs")
    with pytest.raises(dbus_service.ServiceError) as wrong:
        service.call("Stop", "", (), None)
    assert wrong.value.name.endswith("InvalidArgs")
    with pytest.raises(dbus_service.ServiceError) as unknown:
        service.call("Run", "s", ("ls",), None)
    assert unknown.value.name.endswith("UnknownMethod") and service.started == []


def test_dbus_stop_is_ctrl_c_for_the_run(music):
    process = FakeRun()
    service = bus_service(process)
    service.call("FetchLyrics", "sa{sv}", (str(music / "Artist" / "Album"), {}), "call")
    assert service.call("Stop", "s", ("1",), None) == ("", ())
    assert service.call("Stop", "s", ("1",), None) == ("", ())    # one Ctrl+C, not two
    assert process.signals == [signal.SIGINT]
    assert drain(service) == [("signal", "Finished", "si", ("1", 130))]
    with pytest.raises(dbus_service.ServiceError) as gone:
        service.call("Stop", "s", ("1",), None)
    assert gone.value.name.endswith("UnknownRun")


def test_dbus_page_that_ends_before_its_address_is_an_error_and_finished(tmp_path):
    service = bus_service(FakeRun(["Warning: ..."], code=2, hold=False))
    service.call("OpenPage", "s", (str(tmp_path),), "call")
    [(kind, caller, name, message), finished] = drain(service, 2)
    assert (kind, caller) == ("error", "call") and name.endswith("Failed") and "exit code 2" in message
    assert finished == ("signal", "Finished", "si", ("1", 2))       # for whoever saw it in Runs


def test_dbus_gives_up_on_a_page_that_does_not_come(tmp_path):
    now = [0.0]
    process = FakeRun()
    service = bus_service(process, clock=lambda: now[0])
    service.call("OpenPage", "s", (str(tmp_path),), "call")
    service.call("OpenPage", "s", (str(tmp_path),), "second")     # waits for the same page
    assert service.outgoing() == []
    now[0] = dbus_service.URL_TIMEOUT + 1
    out = service.outgoing()
    assert [(o[0], o[1]) for o in out] == [("error", "call"), ("error", "second")] and "not up" in out[0][3]
    assert process.signals == [signal.SIGINT]
    assert drain(service) == [("signal", "Finished", "si", ("1", 130))]


def test_dbus_service_file_starts_this_lyrembed(tmp_path, monkeypatch, capsys, cfg):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(dbus_service, "_reload_bus", lambda: None)
    shim = tmp_path / "my bin" / "lyrembed"
    shim.parent.mkdir()
    shim.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(shim)])
    assert cli.main(["--install-dbus-service"]) == 0
    path = tmp_path / "data" / "dbus-1" / "services" / "io.github.sobakah.lyrembed.service"
    assert path.read_text(encoding="utf-8") == (
        f"[D-BUS Service]\nName=io.github.sobakah.lyrembed\nExec='{shim}' --dbus\n")
    assert cli.main(["--remove-dbus-service"]) == 0 and not path.exists()
    assert "There was no" not in capsys.readouterr().out
    assert cli.main(["--remove-dbus-service"]) == 0 and "There was no" in capsys.readouterr().out


def test_dbus_takes_no_other_options():
    assert cli.parse_args(["--dbus", "-v"]).dbus
    for argv in (["~/Music/Album", "--dbus"], ["--dbus", "--auto"], ["--dbus", "--config", "my.json"]):
        with pytest.raises(SystemExit):
            cli.parse_args(argv)


def test_dbus_runs_start_the_same_lyrembed_in_a_checkout(monkeypatch, tmp_path):
    launcher = tmp_path / "run.py"
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(launcher), "--dbus"])
    assert dbus_service.page_command("/m/Album") == [
        sys.executable, str(launcher), "/m/Album", "--no-browser", "--color", "never", "--unvisited-timeout", "600"]
    assert dbus_service.fetch_command("/m/Album", False) == [
        sys.executable, str(launcher), "/m/Album", "--auto", "--color", "never", "--no-romanize"]


def test_dbus_reads_exactly_the_line_the_page_prints():
    assert dbus_service._PAGE.match(PAGE_LINE).group(1) == PAGE_URL
    assert not dbus_service._PAGE.match("cdport is running at http://127.0.0.1:1/?token=x")
    assert not dbus_service._PAGE.match("lyrembed is running at https://evil.example/?token=x")
    assert dbus_service.URL_TIMEOUT < 25   # GDBus' and libdbus' default reply timeout


def test_page_runs_print_their_address_without_colour(cfg, tmp_path, monkeypatch, capsys):
    # A config with "color": "always" wrapped the line in escape codes, and
    # the service never saw the page come up.
    monkeypatch.setattr(desktop, "launch_command", lambda: ["lyrembed"])
    cfg["settings"]["color"] = "always"
    cfg["settings"]["check_for_updates"] = False
    args = dbus_service.page_command(str(tmp_path))[1:]
    monkeypatch.setattr(config, "load_config", lambda path=None: (cfg, []))
    monkeypatch.setattr(web, "serve", lambda session, **kwargs: ui.info(web.RUNNING_LINE.format(url=PAGE_URL)) or 0)
    assert cli.main(args) == 0
    assert capsys.readouterr().out.splitlines()[0] == PAGE_LINE


def test_a_page_nobody_opens_ends_when_asked_to(cfg, tmp_path, capsys):
    session = web.WebSession(tmp_path, [], tagging.MetadataCache())
    cfg["settings"]["check_for_updates"] = False
    started = time.monotonic()
    assert web.serve(session, open_browser=False, unvisited=0.2) == 0
    assert time.monotonic() - started < 5 and "Nobody opened the page" in capsys.readouterr().out
    for argv in (["--auto", "--unvisited-timeout", "5"], ["--unvisited-timeout", "-1"]):
        with pytest.raises(SystemExit):
            cli.parse_args(argv)


class FakeBus:
    """Just enough of a jeepney connection for dbus_service._answer: the
    bus answers RequestName and ReleaseName; *before_reply* comes before
    RequestName's answer, *after* before ReleaseName's; then *end*."""

    def __init__(self, before_reply=(), after=(), end=OSError("the bus went away")):
        self.outgoing_serial = itertools.count(1)
        self.sent, self.before_reply, self.after, self.end = [], list(before_reply), list(after), end
        self.answered = 0

    def send(self, message, serial=None):
        message.header.serial = serial or next(self.outgoing_serial)
        message.serialise()   # as the real one does: fails on values out of range
        self.sent.append(message)

    def _bus_call(self):
        from jeepney import HeaderFields

        calls = [m for m in self.sent if m.header.fields.get(HeaderFields.destination) == "org.freedesktop.DBus"]
        return calls[self.answered] if self.answered < len(calls) else None

    def receive(self, timeout=None):
        from jeepney import new_method_return

        if self.before_reply:
            return self.before_reply.pop(0)
        if self.answered == 0 and self._bus_call() is not None:   # RequestName: "now the owner"
            self.answered += 1
            return new_method_return(self._bus_call() or self.sent[0], "u", (1,))
        if self.after:
            return self.after.pop(0)
        if (release := self._bus_call()) is not None:              # ReleaseName
            self.answered += 1
            return new_method_return(release, "u", (1,))
        raise self.end


def _calls(*methods):
    from jeepney import DBusAddress, new_method_call

    target = DBusAddress(dbus_service.OBJECT_PATH, dbus_service.BUS_NAME, dbus_service.INTERFACE)
    messages = []
    for serial, method in enumerate(methods, start=7):
        messages.append(new_method_call(target, method))
        messages[-1].header.serial = serial
    return messages


def test_dbus_survives_a_failing_call_and_keeps_the_one_that_started_it(capsys):
    pytest.importorskip("jeepney")
    from jeepney import MessageType

    messages = _calls("Runs", "GetInfo", "GetInfo", "Runs")
    bus = FakeBus(before_reply=messages[:1], after=messages[1:])   # the first before RequestName's reply
    service = dbus_service.Service()
    infos = iter([PermissionError(13, "Permission denied"), {"Version": ("u", -1)}])   # then one jeepney cannot send

    def info():
        answer = next(infos)
        if isinstance(answer, Exception):
            raise answer
        return answer
    service.info = info
    assert dbus_service._answer(bus, service, idle_exit=60) == 0   # ended by the bus going away
    replies = bus.sent[1:]
    assert [m.header.message_type for m in replies] == [MessageType.method_return, MessageType.error,
                                                        MessageType.error, MessageType.method_return]
    assert "PermissionError" in replies[1].body[0] and "Traceback" in capsys.readouterr().err


def test_dbus_answers_what_came_while_it_let_go_of_its_name():
    # Idle, the service releases its name before it ends: a call the bus
    # routed to it just before is still answered, the next one starts a new one.
    pytest.importorskip("jeepney")
    from jeepney import HeaderFields, MessageType

    bus = FakeBus(after=_calls("Runs"), end=TimeoutError())
    assert dbus_service._answer(bus, dbus_service.Service(), idle_exit=-1) == 0
    members = [m.header.fields.get(HeaderFields.member) for m in bus.sent]
    assert members[:2] == ["RequestName", "ReleaseName"]
    assert bus.sent[2].header.message_type == MessageType.method_return
    assert bus.sent[2].header.fields[HeaderFields.reply_serial] == 7


def test_dbus_waits_for_its_runs_before_it_ends(tmp_path):
    process = FakeRun([PAGE_LINE])
    service = bus_service(process)
    service.call("OpenPage", "s", (str(tmp_path),), "call")
    drain(service)
    threading.Timer(0.3, process.release.set).start()
    started = time.monotonic()
    dbus_service._outlive_runs(service)
    assert service.runs == {} and time.monotonic() - started >= 0.25


def test_dbus_ctrl_c_reaches_the_runs_too(tmp_path, monkeypatch):
    pytest.importorskip("jeepney")
    import contextlib
    import jeepney.io.blocking

    process = FakeRun([PAGE_LINE])
    service = bus_service(process)
    service.call("OpenPage", "s", (str(tmp_path),), "call")
    drain(service)
    monkeypatch.setattr(jeepney.io.blocking, "open_dbus_connection", lambda bus: contextlib.nullcontext())

    def interrupted(conn, service, idle_exit):
        raise KeyboardInterrupt
    monkeypatch.setattr(dbus_service, "_answer", interrupted)
    assert dbus_service.serve(service) == 130
    # Its own session keeps the terminal's Ctrl+C from the run: the service passes it on.
    assert process.signals == [signal.SIGINT] and service.runs == {}


def test_dbus_on_a_real_bus_started_by_the_bus(tmp_path, monkeypatch):
    """A private dbus-daemon with lyrembed's service file: the bus starts
    lyrembed --dbus at the first call, as the session bus does for Gatefold."""
    pytest.importorskip("jeepney")
    daemon = shutil.which("dbus-daemon")
    if daemon is None:
        pytest.skip("no dbus-daemon")
    from jeepney import DBusAddress, MatchRule, MessageType, new_method_call
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection

    services = tmp_path / "services"
    services.mkdir()
    run_py = Path(__file__).resolve().parent.parent / "run.py"
    (services / f"{dbus_service.BUS_NAME}.service").write_text(
        dbus_service.service_file_text([sys.executable, str(run_py)]), encoding="utf-8")
    (tmp_path / "bus.conf").write_text(f"""<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <servicedir>{services}</servicedir>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/><allow own="*"/>
  </policy>
</busconfig>
""", encoding="utf-8")
    # What the bus starts inherits this environment: its own config (no
    # update check), home and music folder, no PATH to find git or a
    # browser with, and no display to open one on.
    home = tmp_path / "home"
    album = home / "Music" / "Album"
    album.mkdir(parents=True)
    _make_flac(album / "01.flac")
    (home / "xdg" / "lyrembed").mkdir(parents=True)
    (home / "xdg" / "lyrembed" / "config.json").write_text('{"settings": {"check_for_updates": false}}',
                                                           encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    monkeypatch.setenv("PATH", str(tmp_path / "no-tools"))
    for name in ("BROWSER", "DISPLAY", "WAYLAND_DISPLAY", "TERM"):
        monkeypatch.delenv(name, raising=False)
    bus = subprocess.Popen([daemon, f"--config-file={tmp_path / 'bus.conf'}", "--nofork", "--print-address"],
                           stdout=subprocess.PIPE, text=True)
    try:
        address = bus.stdout.readline().strip()
        lyrembed_obj = DBusAddress(dbus_service.OBJECT_PATH, dbus_service.BUS_NAME, dbus_service.INTERFACE)
        with open_dbus_connection(bus=address) as conn:
            def call(method, signature=None, body=()):
                reply = conn.send_and_get_reply(new_method_call(lyrembed_obj, method, signature, body), timeout=30)
                assert reply.header.message_type == MessageType.method_return, reply.body
                return reply.body

            info = call("GetInfo")[0]
            assert info["Version"] == ("s", config.__version__)
            assert info["MusicFolder"] == ("s", str(home / "Music"))
            assert len(call("ArtistAliases")[0]) == len(config.builtin_aliases())
            assert call("Romanize", "ss", ("[00:01.00]Hello", "")) == ("", "", "already Latin")
            finished = MatchRule(type="signal", interface=dbus_service.INTERFACE, member="Finished")
            conn.send_and_get_reply(message_bus.AddMatch(finished), timeout=10)
            with conn.filter(finished) as signals:
                run, opened = call("OpenPage", "s", (str(album),))
                assert opened is False                            # no browser here, and no address either
                assert call("Runs") == ([(run, "page", str(album))],)
                assert call("ShowPage", "s", (run,)) == (False,)
                call("Stop", "s", (run,))
                ended = conn.recv_until_filtered(signals, timeout=30)
            assert ended.body == (run, 130)   # stopped before the page's first visit
            refused = conn.send_and_get_reply(new_method_call(
                lyrembed_obj, "FetchLyrics", "sa{sv}", ("/etc", {})), timeout=10)
            assert refused.header.message_type == MessageType.error and "NotInMusicFolder" in str(refused.header.fields)
            intro = conn.send_and_get_reply(new_method_call(
                DBusAddress(dbus_service.OBJECT_PATH, dbus_service.BUS_NAME, "org.freedesktop.DBus.Introspectable"),
                "Introspect"), timeout=10)
            assert 'name="ShowPage"' in intro.body[0]
    finally:
        bus.terminate()
        bus.wait(10)
