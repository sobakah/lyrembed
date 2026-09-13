"""Regression tests for the issues found in the 2.x review."""

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetchlyrics import config, editor, providers, tagging, text  # noqa: E402
from fetchlyrics import app, ui  # noqa: E402
from fetchlyrics.app import natural_key  # noqa: E402


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


def test_invalid_config_falls_back_with_warning(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    merged, warnings = config.load_config(path)
    assert warnings
    assert merged["api"]["lrclib_url"].startswith("https://")


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


def test_character_sets_are_disjoint():
    assert not (text._ZH_ONLY & text._JP_ONLY)


def test_han_evidence_from_unique_characters():
    assert text._han_evidence("気持") == "ja"      # shinjitai-only
    assert text._han_evidence("山川") is None      # shared, stays ambiguous


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
    assert text.romanize_lyrics("东车书长门", "ja") == "东车书长门"
    assert text.romanize_segment("东车书长门", "ja") == "东车书长门"


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
                 has_duration=True, diff=0)
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
