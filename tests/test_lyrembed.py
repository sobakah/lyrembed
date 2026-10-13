"""Regression tests for lyrembed."""

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lyrembed import config, editor, providers, tagging, text  # noqa: E402
from lyrembed import app, ui  # noqa: E402
from lyrembed.app import natural_key  # noqa: E402


# --- config merging ----------------------------------------------------------
def test_partial_user_config_keeps_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"settings": {"preview_lines": 40}}', encoding="utf-8")
    merged, warnings = config.load_config(path)
    assert warnings == []
    assert merged["settings"]["preview_lines"] == 40
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


def test_candidate_list_shows_the_mixed_badge(cfg, capsys):
    cfg["settings"]["color"] = "never"
    ui.apply_color_settings()
    found = [providers.Candidate.build(provider="LRCLIB", artist="A", title=t, album="", text=lyrics)
             for t, lyrics in (("one", "사랑해 我爱你 ありがとう"), ("two", "사랑해"), ("three", "saranghae"))]
    app.print_candidates(found)
    lines = capsys.readouterr().out.splitlines()
    assert "[MIXED]" in next(l for l in lines if "one" in l)
    assert "[ORIG ]" in next(l for l in lines if "two" in l)
    assert "[LATIN]" in next(l for l in lines if "three" in l)


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
def test_editor_command_splits_arguments(monkeypatch):
    monkeypatch.setenv("EDITOR", "code --wait")
    assert editor.editor_command() == ["code", "--wait"]


def test_editor_roundtrip(monkeypatch, tmp_path):
    script = tmp_path / "fake_editor.py"
    script.write_text(
        "import sys\nopen(sys.argv[1], 'w', encoding='utf-8').write('edited')\nsys.exit(1)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")
    # A non-zero exit code must not abort the session.
    assert editor.open_editor("original") == "edited"


def test_missing_editor_raises_editor_error(monkeypatch):
    monkeypatch.setenv("EDITOR", "definitely-not-an-editor-binary")
    with pytest.raises(editor.EditorError):
        editor.open_editor("x")


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
    assert report.candidates[app.default_highlight(report, 200) - 1] is synced
    only_plain = providers.SearchReport(candidates=[plain])
    assert app.default_highlight(only_plain, 200) == 1


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
    app.find_automatic(meta)
    assert set(searched) == {"Member A, Member B"}          # off by default

    searched.clear()
    cfg["settings"]["prefer_album_artist"] = True
    app.find_automatic(meta)
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


def test_menu_lists_every_key_and_group(capsys):
    ui.print_menu(_menu_groups())
    out = capsys.readouterr().out
    for key in ("e", "r", "v", "p", "s", "q"):
        assert f"[{key}]" in out
    assert "Edit" in out and "Navigate" in out


def test_menu_columns_adapt_to_terminal_width(monkeypatch, capsys):
    def rows_for(width):
        monkeypatch.setattr(ui, "terminal_width", lambda default=80: width)
        ui.print_menu(_menu_groups())
        out = capsys.readouterr().out
        return [ln for ln in out.splitlines() if "[" in ln]

    wide = rows_for(200)
    narrow = rows_for(30)
    # A narrow terminal must break into more rows rather than overflow.
    assert len(narrow) > len(wide)


def test_menu_never_exceeds_the_terminal_width(monkeypatch, capsys):
    for width in (30, 60, 100):
        monkeypatch.setattr(ui, "terminal_width", lambda default=80, w=width: w)
        ui.print_menu(_menu_groups())
        for line in capsys.readouterr().out.splitlines():
            plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
            assert len(plain) <= width, (width, plain)


def test_menu_handles_a_single_group(capsys):
    ui.print_menu([("Navigate", [("q", "Quit")])])
    assert "[q]" in capsys.readouterr().out


# --- candidate selection UI --------------------------------------------------
def test_describe_candidate_names_the_entry():
    cand = _cand(artist="IU", title="Love Wins All", synced=True, exact=True,
                 has_duration=True, diff=0, duration=200, track_duration=200)
    line = app.describe_candidate(1, cand)
    assert line.startswith("#1 IU - Love Wins All")
    assert "exact" in line and "synced" in line
    assert "duration OK" in line and "±0s" not in line


def test_describe_candidate_without_duration():
    line = app.describe_candidate(3, _cand(artist="", title="Song", synced=False))
    assert line.startswith("#3 Song")
    assert "plain" in line and "duration" not in line


def test_highlight_marks_only_the_selected_row(capsys):
    candidates = [_cand(title="A", exact=True), _cand(title="B"), _cand(title="C")]
    app.print_candidates(candidates, highlight=2)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "[" in ln]
    marked = [ln for ln in lines if ln.startswith("\u25b6")]
    assert len(marked) == 1
    assert "B" in marked[0]


def test_no_highlight_marks_nothing(capsys):
    app.print_candidates([_cand(title="A"), _cand(title="B")], highlight=None)
    assert "\u25b6" not in capsys.readouterr().out


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


def _scripted_input(monkeypatch, answers):
    """Feed menu answers to app.safe_input; running out ends the program."""
    feed = iter(answers)

    def fake(prompt="", default_text=""):
        try:
            return next(feed)
        except StopIteration:
            raise ui.QuitRequested(99) from None

    monkeypatch.setattr(app, "safe_input", fake)


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
        '{"settings": {"supported_extensions": ".flac", "preview_lines": "many",'
        ' "prefer_latin": 1, "colour": "never"}, "api": {"batch_delay_seconds": 2}}',
        encoding="utf-8",
    )
    merged, warnings = config.load_config(path)
    assert merged["settings"]["supported_extensions"] == config.DEFAULT_CONFIG["settings"]["supported_extensions"]
    assert merged["settings"]["preview_lines"] == 16
    assert merged["settings"]["prefer_latin"] is False
    assert merged["api"]["batch_delay_seconds"] == 2      # an int is a valid float
    joined = " ".join(warnings)
    assert "supported_extensions" in joined and "preview_lines" in joined
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

    def no_prompt(*args, **kwargs):
        raise AssertionError("batch mode prompted")

    monkeypatch.setattr(app, "safe_input", no_prompt)
    assert cli.main(["--auto", str(tmp_path / "missing")]) == 2
    assert cli.main(["--auto", str(tmp_path)]) == 2       # exists, but empty


def test_quit_and_interrupt_exit_codes(tmp_path, monkeypatch, cfg):
    monkeypatch.setattr(config, "load_config", lambda path=None: (cfg, []))
    _make_flac(tmp_path / "a.flac")

    def quit_now(*args, **kwargs):
        raise ui.QuitRequested(0)

    monkeypatch.setattr(app, "run_interactive", quit_now)
    assert cli.main([str(tmp_path)]) == 0

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(app, "run_interactive", interrupt)
    assert cli.main([str(tmp_path)]) == 130


def test_end_of_input_quits_instead_of_exiting_the_interpreter(monkeypatch):
    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(ui.QuitRequested) as raised:
        ui.safe_input("> ")
    assert raised.value.code == 0


# scanning and tagging
def test_unsupported_configured_extension_is_skipped_not_fatal(tmp_path, cfg):
    cfg["settings"]["supported_extensions"] = [".flac", ".wav"]
    (tmp_path / "song.wav").write_bytes(b"RIFF")
    _make_flac(tmp_path / "song.flac")
    files, err = app.find_music_files(tmp_path)
    assert err is None
    assert [f.name for f in files] == ["song.flac"]
    # Reading a file of that type directly is an error on the track, not a crash.
    assert "Unsupported" in tagging.read_metadata(tmp_path / "song.wav").error


def test_max_file_count_zero_means_unlimited(tmp_path, cfg):
    for n in range(3):
        _make_flac(tmp_path / f"{n}.flac")
    cfg["settings"]["max_file_count"] = 2
    assert app.find_music_files(tmp_path) == (None, "TOO_MANY_FILES")
    cfg["settings"]["max_file_count"] = 0
    files, err = app.find_music_files(tmp_path)
    assert err is None and len(files) == 3


def test_path_completion_handles_brackets(tmp_path):
    (tmp_path / "Album [FLAC]" / "CD1").mkdir(parents=True)
    match = ui.complete_path(str(tmp_path / "Album [FLAC]" / "C"), 0)
    assert match == str(tmp_path / "Album [FLAC]" / "CD1") + os.sep


def test_failed_sidecar_is_not_a_failed_embed(tmp_path, cfg):
    from mutagen.flac import FLAC

    cfg["settings"]["write_sidecar_lrc"] = True
    src = _make_flac(tmp_path / "song.flac")
    (tmp_path / "song.lrc").mkdir()          # makes the sidecar unwritable
    with pytest.raises(tagging.SidecarError):
        tagging.embed_lyrics(src, "[00:01.00]x")
    assert FLAC(src)["LYRICS"][0] == "[00:01.00]x"
    assert app.save_lyrics(src, "[00:02.00]y") is True


def test_stale_sidecar_is_reported(tmp_path, cfg, capsys):
    src = _make_flac(tmp_path / "song.flac")
    assert tagging.stale_sidecar(src) is None
    (tmp_path / "song.lrc").write_text("old", encoding="utf-8")
    assert tagging.stale_sidecar(src) == tmp_path / "song.lrc"
    app.save_lyrics(src, "new")
    assert "song.lrc" in capsys.readouterr().out
    cfg["settings"]["write_sidecar_lrc"] = True
    assert tagging.stale_sidecar(src) is None   # kept in sync, so not stale


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
    target = app.backup_lyrics(tmp_path / "backup", base, track, "[00:01.00]old")
    assert target == tmp_path / "backup" / "Album" / "01.flac.lrc"
    assert target.read_text(encoding="utf-8") == "[00:01.00]old"


# navigation
def test_stepping_back_skips_an_unreadable_track_backwards(monkeypatch, capsys, cfg):
    files = [Path(f"/music/{n}.flac") for n in (1, 2, 3)]
    metas = {
        files[0]: tagging.TrackMeta(path=files[0], title="One"),        # no artist
        files[1]: tagging.TrackMeta(path=files[1], error="broken"),
        files[2]: tagging.TrackMeta(path=files[2], title="Three"),
    }

    class FakeCache:
        def get(self, path):
            return metas[path]

    # Jump to track 3, step back: the unreadable track 2 must lead to track 1.
    _scripted_input(monkeypatch, ["3", "p", "q"])
    with pytest.raises(ui.QuitRequested):
        app.run_interactive(files, Path("/music"), FakeCache())
    banners = re.findall(r"\[(\d)/3\]", capsys.readouterr().out)
    assert banners == ["3", "2", "1"]


def test_ctrl_c_cancels_only_the_search(monkeypatch, capsys, cfg):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(providers, "collect_candidates", interrupted)
    _scripted_input(monkeypatch, ["s"])
    meta = tagging.TrackMeta(path=Path("/m/a.flac"), title="T", artist="A")
    cache = providers.SearchCache()
    assert app.select_lyrics(meta, cache) == (app.Action.NEXT, None)
    out = capsys.readouterr().out
    assert "Search cancelled" in out and "No matching results" not in out
    assert len(cache) == 0                        # nothing half-finished cached


# providers
def test_duration_ok_needs_both_lengths():
    unknown_track = _cand(has_duration=True, duration=200, track_duration=0)
    assert app.duration_label(unknown_track) == "3:20"
    assert "duration" not in app.describe_candidate(1, unknown_track)
    known = _cand(has_duration=True, duration=200, track_duration=200)
    assert app.duration_label(known) == "Duration OK"
    assert app.duration_label(_cand(has_duration=True, duration=203, track_duration=200, diff=3)) == "±3s"


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
    assert app.find_automatic(meta)[1] is exact
    assert calls == [True]


# editor
def test_visual_takes_precedence_over_editor(monkeypatch):
    monkeypatch.setenv("EDITOR", "nano")
    monkeypatch.setenv("VISUAL", "code --wait")
    assert editor.editor_command() == ["code", "--wait"]


def test_detached_gui_editor_is_reported(monkeypatch, tmp_path):
    script = tmp_path / "detached.py"
    script.write_text("pass\n", encoding="utf-8")        # returns at once, no edit
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")
    with pytest.raises(editor.EditorError, match="--wait"):
        editor.open_editor("original")


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


def test_selected_candidate_has_yellow_brackets(colors, capsys):
    found = [providers.Candidate.build(provider="LRCLIB", artist="A", title=t, album="", text="la")
             for t in ("one", "two", "three")]
    app.print_candidates(found, highlight=2)
    assert _yellow_brackets(capsys.readouterr().out) == ["2"]
    app.print_candidates(found)
    assert _yellow_brackets(capsys.readouterr().out) == []


def test_preselected_choices_have_yellow_brackets(cfg, colors, monkeypatch, capsys):
    _scripted_input(monkeypatch, ["c", "c", "c"])
    app.prompt_layout()
    assert _yellow_brackets(capsys.readouterr().out) == ["1"]
    cfg["settings"]["romanization_layout"] = "below"
    app.prompt_layout()
    assert _yellow_brackets(capsys.readouterr().out) == ["2"]
    app.prompt_han_default()
    assert _yellow_brackets(capsys.readouterr().out) == ["1"]


def test_tree_marks_the_track_enter_starts_with(colors, tmp_path, capsys):
    files = [_make_flac(tmp_path / f"{n}.flac") for n in ("a", "b")]
    app.display_tree(files, tmp_path, tagging.MetadataCache())
    assert _yellow_brackets(capsys.readouterr().out) == ["1"]


def test_detected_romanization_has_yellow_brackets(colors, monkeypatch, capsys):
    _scripted_input(monkeypatch, ["c"])
    app.prompt_romanization("[00:01.00]사랑해")
    expected = str(list(text.ROMANIZERS).index("ko") + 1)
    assert _yellow_brackets(capsys.readouterr().out) == [expected]


def test_layout_setting_decides_what_enter_picks(cfg, monkeypatch):
    _scripted_input(monkeypatch, ["", "1"])
    assert app.prompt_layout() is False               # default: replace
    cfg["settings"]["romanization_layout"] = "below"
    _scripted_input(monkeypatch, ["", "1"])
    assert app.prompt_layout() is True                # Enter keeps the original
    assert app.prompt_layout() is False               # an explicit 1 still replaces


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


def test_selection_parsing():
    assert app.parse_selection("", 5) == [0, 1, 2, 3, 4]
    assert app.parse_selection("1-3, 5", 5) == [0, 1, 2, 4]
    assert app.parse_selection("4-2 2", 5) == [1, 2, 3]
    assert app.parse_selection("0", 5) is None
    assert app.parse_selection("6", 5) is None
    assert app.parse_selection("1;x", 5) is None


@pytest.mark.skipif(not (text.HAS_KOREAN or text.HAS_ANYASCII),
                    reason="korean-romanizer or anyascii required")
def test_keeping_the_original_twice_does_not_double_lines():
    once = text.romanize_lyrics("[00:01.00]사랑해\n[00:02.00]hello", "ko", keep_original=True)
    assert text.romanize_lyrics(once, "ko", keep_original=True) == once


@pytest.mark.skipif(not text.HAS_KOREAN, reason="korean-romanizer required")
def test_bulk_romanize_backs_up_and_is_idempotent(cfg, tmp_path, state_dir):
    cfg["settings"]["romanization_layout"] = "below"
    music = tmp_path / "music"
    music.mkdir()
    korean = _flac_with_lyrics(music / "a.flac", "[00:01.00]사랑해")
    latin = _flac_with_lyrics(music / "b.flac", "[00:01.00]hello there")
    cache = tagging.MetadataCache()

    app.run_bulk_action("2", [korean, latin], music, cache)
    lyrics = tagging.read_metadata(korean).lyrics
    assert lyrics.splitlines()[0] == "[00:01.00]사랑해"
    assert lyrics.splitlines()[1].startswith("[00:01.00]sa")
    assert tagging.read_metadata(latin).lyrics == "[00:01.00]hello there"
    backups = list(state_dir.rglob("a.flac.lrc"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "[00:01.00]사랑해"

    app.run_bulk_action("2", [korean], music, tagging.MetadataCache())
    assert tagging.read_metadata(korean).lyrics == lyrics


def test_tree_bulk_delete_with_selection(cfg, tmp_path, state_dir, monkeypatch):
    music = tmp_path / "music"
    music.mkdir()
    files = [_flac_with_lyrics(music / f"{n}.flac", f"[00:01.00]{n}") for n in ("a", "b", "c")]
    # tree: a = bulk, 4 = delete, tracks 1 and 3, confirm; then quit
    _scripted_input(monkeypatch, ["a", "4", "1,3", "y"])
    monkeypatch.setattr(ui, "safe_input", app.safe_input)   # confirm() reads through ui
    with pytest.raises(ui.QuitRequested):
        app.run_interactive(files, music, tagging.MetadataCache())
    assert [tagging.read_metadata(f).lyrics for f in files] == ["", "[00:01.00]b", ""]
    assert len(list(state_dir.rglob("*.lrc"))) == 2


def test_bulk_sidecar_export_backs_up_a_differing_sidecar(cfg, tmp_path, state_dir):
    music = tmp_path / "music"
    music.mkdir()
    track = _flac_with_lyrics(music / "a.flac", "[00:01.00]new")
    tagging.sidecar_path(track).write_text("[00:01.00]old", encoding="utf-8")

    app.run_bulk_action("3", [track], music, tagging.MetadataCache())
    assert tagging.sidecar_path(track).read_text(encoding="utf-8") == "[00:01.00]new"
    assert [p.read_text(encoding="utf-8") for p in state_dir.rglob("*.lrc")] == ["[00:01.00]old"]


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
    def lrclib(title, artist, album, duration, report, quick=False, aliases="none"):
        for provider, detail in (instrumental or {}).items():
            report.mark_instrumental(provider, detail)
        return list(found)
    monkeypatch.setattr(providers, "query_lrclib", lrclib)
    monkeypatch.setattr(providers, "query_netease", lambda *a, **k: [])
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
    assert app.run_batch([inst, zxx], tmp_path, tagging.MetadataCache(), False, False) == 0
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
    assert app.instrumental_hint(meta) is None              # off by default
    cfg["api"]["enable_musicbrainz"] = True
    assert app.instrumental_hint(meta) == "MusicBrainz: work without lyrics"


def test_unrelated_results_are_shown_on_request(cfg, monkeypatch, capsys):
    filler = _built(artist="朴树", title="平凡之路", duration=300)
    _fake_providers(monkeypatch, [filler], instrumental={"LRCLIB": "exact match"})
    _scripted_input(monkeypatch, ["u", "s"])
    meta = tagging.TrackMeta(path=Path("x.flac"), title="Opening", artist="Orchestra", duration=200)
    action, _ = app.select_lyrics(meta, providers.SearchCache())
    out = capsys.readouterr().out
    assert action is app.Action.NEXT
    assert "Marked as instrumental by LRCLIB (exact match)" in out
    first, _, second = out.partition("hidden - 'u' shows them")
    assert "平凡之路" not in first and "平凡之路" in second and "≠artist" in second


def test_syncedlyrics_results_are_labelled_by_name(cfg, monkeypatch, capsys):
    # They used to be labelled "MULTI", which said nothing about the source.
    class FakeEngine:
        @staticmethod
        def search(query, allow_plain_format=True):
            return "[00:01.00]la la"

    monkeypatch.setattr(providers, "HAS_SYNCEDLYRICS", True)
    monkeypatch.setattr(providers, "syncedlyrics", FakeEngine, raising=False)
    found = providers.query_syncedlyrics("Song", "Artist", providers.SearchReport())
    cfg["settings"]["color"] = "never"
    ui.apply_color_settings()
    app.print_candidates(found)
    out = capsys.readouterr().out
    assert "[SYNCEDLYRICS]" in out and "?unverified" in out and "MULTI" not in out


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

    def lrclib(title, artist, album, duration, report, quick=False, aliases="none"):
        rounds.append(aliases)
        return list(first_round) if aliases != "only" else []
    monkeypatch.setattr(providers, "query_lrclib", lrclib)
    monkeypatch.setattr(providers, "query_netease", lambda *a, **k: [])
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


def test_search_menu_offers_a_search_with_aliases(cfg, monkeypatch, capsys):
    calls = []

    def collect(title, artist, album, duration, quick=False, aliases=None):
        calls.append(aliases)
        return providers.SearchReport(aliases_searched=bool(aliases))
    monkeypatch.setattr(providers, "collect_candidates", collect)
    _scripted_input(monkeypatch, ["a", "s"])
    meta = tagging.TrackMeta(path=Path("x.flac"), title="Flashy", artist="P1Harmony", duration=189)
    app.select_lyrics(meta, providers.SearchCache())
    out = capsys.readouterr().out
    assert calls == [None, True]
    assert "Search with aliases too" in out and "피원하모니" in out


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

    def lrclib(title, artist, album, duration, report, quick=False, aliases="none"):
        rounds.append(("lrclib", aliases))
        return list(first_round) if aliases != "only" else []

    def scrapers(title, artist, report, aliases="none"):
        rounds.append(("syncedlyrics", aliases))
        return []
    monkeypatch.setattr(providers, "query_lrclib", lrclib)
    monkeypatch.setattr(providers, "query_netease", lambda *a, **k: [])
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
    assert seen == [["Musixmatch", "Megalobiz", "Genius"]]


def test_enter_applies_inspected_lyrics(monkeypatch):
    _scripted_input(monkeypatch, [""])
    assert app.inspect_and_confirm("[00:01.00]la") == "[00:01.00]la"


def test_enter_saves_edited_lyrics(cfg, tmp_path, monkeypatch):
    track = _flac_with_lyrics(tmp_path / "a.flac", "[00:01.00]old")
    monkeypatch.setattr(app, "open_editor", lambda text: "[00:01.00]new")
    # e = edit, Enter = save, s = next track
    _scripted_input(monkeypatch, ["e", "", "s"])
    monkeypatch.setattr(ui, "safe_input", app.safe_input)
    meta = tagging.read_metadata(track)
    assert app.manage_existing_lyrics(meta, tagging.MetadataCache()) is app.Action.NEXT
    assert tagging.read_metadata(track).lyrics == "[00:01.00]new"


def test_enter_on_a_result_can_open_its_preview_first(cfg, monkeypatch, capsys):
    synced = _built(provider="LRCLIB", artist="A", title="Song", duration=200)
    _fake_providers(monkeypatch, [synced])
    meta = tagging.TrackMeta(path=Path("x.flac"), title="Song", artist="A", duration=200)

    _scripted_input(monkeypatch, [""])                     # default: saves at once
    assert app.select_lyrics(meta, providers.SearchCache()) == (app.Action.APPLY, synced.text)

    cfg["settings"]["enter_opens_preview"] = True
    _scripted_input(monkeypatch, ["", "b", "s"])           # Enter = inspect, b = back
    assert app.select_lyrics(meta, providers.SearchCache()) == (app.Action.NEXT, None)
    out = capsys.readouterr().out
    assert "[Enter] Inspect #1" in out and "Apply these lyrics" in out


def test_config_from_before_the_rename_is_still_read(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = tmp_path / "lrckit" / "config.json"
    legacy.parent.mkdir()
    legacy.write_text('{"settings": {"preview_lines": 5}}', encoding="utf-8")
    merged, warnings = config.load_config(None)
    assert merged["settings"]["preview_lines"] == 5
    assert any("move it to" in w and "lyrembed" in w for w in warnings)

    current = tmp_path / "lyrembed" / "config.json"
    current.parent.mkdir()
    current.write_text('{"settings": {"preview_lines": 7}}', encoding="utf-8")
    merged, warnings = config.load_config(None)
    assert merged["settings"]["preview_lines"] == 7 and warnings == []


# batch romanization
def _batch_track(tmp_path: Path, monkeypatch, text_found: str) -> Path:
    """A taggable FLAC whose automatic search finds text_found."""
    from mutagen.flac import FLAC

    path = _make_flac(tmp_path / "track.flac")
    audio = FLAC(path)
    audio["title"], audio["artist"] = "Love wins all", "IU"
    audio.save()
    found = _cand(synced=True, exact=True, text=text_found)
    monkeypatch.setattr(app, "find_automatic",
                        lambda meta: (providers.SearchReport(candidates=[found]), found))
    return path


@pytest.mark.skipif(not text.HAS_KOREAN, reason="korean-romanizer not installed")
def test_batch_romanizes_only_when_asked(cfg, tmp_path, monkeypatch, capsys):
    from mutagen.flac import FLAC

    path = _batch_track(tmp_path, monkeypatch, "[00:01.00]사랑해")
    assert app.run_batch([path], tmp_path, tagging.MetadataCache(), False, False) == 0
    assert FLAC(path)["LYRICS"] == ["[00:01.00]사랑해"]  # off by default

    path.unlink()
    path = _batch_track(tmp_path, monkeypatch, "[00:01.00]사랑해")
    cfg["settings"]["auto_romanize"] = True
    cfg["settings"]["romanization_layout"] = "below"
    assert app.run_batch([path], tmp_path, tagging.MetadataCache(), False, False) == 0
    saved = FLAC(path)["LYRICS"][0]
    assert saved.startswith("[00:01.00]사랑해") and "saranghae" in saved  # original kept, line below
    assert ", romanized" in capsys.readouterr().out


def test_batch_romanization_reports_a_missing_backend(cfg, tmp_path, monkeypatch, capsys):
    from mutagen.flac import FLAC

    path = _batch_track(tmp_path, monkeypatch, "[00:01.00]사랑해")
    cfg["settings"]["auto_romanize"] = True
    monkeypatch.setattr(app, "auto_romanize", lambda lyrics: (None, "'korean-romanizer' is not installed"))
    assert app.run_batch([path], tmp_path, tagging.MetadataCache(), False, False) == 0
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
