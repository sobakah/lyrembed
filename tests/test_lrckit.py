"""Regression tests for lrckit."""

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lrckit import config, editor, providers, tagging, text  # noqa: E402
from lrckit import app, ui  # noqa: E402
from lrckit.app import natural_key  # noqa: E402


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
    assert config.DEFAULT_CONFIG["aliases"][0][0] == "tomorrow x together"


# --- artist matching ---------------------------------------------------------
def test_alias_lookup_respects_word_boundaries():
    # "Liu Yifei" used to match the IU alias group via a naive substring test.
    assert text.get_artist_aliases("Liu Yifei") == []
    assert "이지은" in text.get_artist_aliases("IU")
    assert "tomorrow x together" in text.get_artist_aliases("TXT")


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


def test_exact_match_wins():
    exact = _cand(synced=False, exact=True, has_duration=True)
    fuzzy = _cand(synced=True, exact=False, has_duration=True)
    assert sorted([fuzzy, exact], key=providers.rank_key)[0] is exact


def test_prefer_latin_is_configurable():
    original = _cand(synced=True, latin=False, has_duration=True)
    romanized = _cand(synced=True, latin=True, has_duration=True)

    config.CONFIG["settings"]["prefer_latin"] = True
    assert sorted([original, romanized], key=providers.rank_key)[0] is romanized

    config.CONFIG["settings"]["prefer_latin"] = False
    assert sorted([romanized, original], key=providers.rank_key)[0] is original
    config.CONFIG["settings"]["prefer_latin"] = True


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

from lrckit import cli  # noqa: E402


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
    # Named lrckit.py, the launcher imported itself when lrckit/ was missing
    # and the explanation below was unreachable.
    launcher = tmp_path / "run.py"
    shutil.copy(Path(__file__).resolve().parent.parent / "run.py", launcher)
    (tmp_path / "cli.py").write_text("", encoding="utf-8")
    result = subprocess.run([sys.executable, str(launcher)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "package was not found" in result.stderr
    assert "mv cli.py" in result.stderr


def test_project_url_is_not_a_placeholder():
    assert "<" not in config.PROJECT_URL
    assert "<" not in config.DEFAULT_CONFIG["api"]["user_agent"]


# config
def test_user_config_comes_before_a_checkout_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_search_paths()[0] == tmp_path / "lrckit" / "config.json"


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
    assert merged["settings"]["prefer_latin"] is True
    assert merged["api"]["batch_delay_seconds"] == 2      # an int is a valid float
    joined = " ".join(warnings)
    assert "supported_extensions" in joined and "preview_lines" in joined
    assert "settings.colour" in joined                    # typo reported, not ignored


def test_example_config_matches_the_defaults():
    import json

    example = json.loads((Path(__file__).resolve().parent.parent / "config.example.json")
                         .read_text(encoding="utf-8"))
    expected = copy.deepcopy(config.DEFAULT_CONFIG)
    del expected["api"]["user_agent"]
    assert example == expected


# command line
def test_batch_only_flags_require_auto(capsys):
    for argv in (["--dry-run"], ["--overwrite"], ["--sidecar", "--no-sidecar"], ["--max-files", "-1"]):
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
    assert text.detect_script("[al:七裏香]\n[00:01.00]我爱你") == "zh"
    assert text.detect_script("[al:峯]\n[00:01.00]我爱你") == "zh"


@pytest.mark.skipif(not (text.HAS_KAKASI and text.HAS_PYPINYIN),
                    reason="pykakasi and pypinyin required")
def test_japanese_only_characters_need_kana_somewhere_in_the_text():
    # Without any Kana, a "Japanese only" character is more likely a variant
    # the character sets lack; the run follows the Han default instead.
    assert text.romanize_lyrics("[al:峯]\n[00:01.00]我爱你", "mixed") == "[al:fēng]\n[00:01.00]wǒ ài nǐ"
    # With Kana in the song, shinjitai still mark a Kanji-only line as Japanese.
    assert text.romanize_lyrics("[00:01.00]駅前\n[00:02.00]ありがとう", "mixed") == \
        "[00:01.00]ekimae\n[00:02.00]arigatou"
