# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`lyrembed` is an interactive terminal program that searches, inspects, romanizes
and embeds synchronized (`.lrc`) and plain lyrics into FLAC, MP3, OGG Vorbis,
Opus and M4A files. Python 3.10+, no runtime framework.

```
run.py                   Launcher for checkouts (thin wrapper)
lyrembed/
  cli.py                 Argument parsing, config loading, start-up
  app.py                 Directory scanning, navigation, menus, batch mode
  providers.py           LRCLIB / NetEase / QQ Music / Megalobiz / syncedlyrics, ranking, search cache
  tagging.py             Metadata reading, writing, mtime-keyed cache
  text.py                Tag cleaning, artist matching, script handling, romanization
  editor.py              External editor handling
  ui.py                  Colours, prompts, previews, menus, path completion
  config.py              Defaults, setting explanations, config merging, alias loading
  aliases.json           Built-in artist alias groups (package data)
tests/test_lyrembed.py     pytest suite
```

## Commands

```bash
pip install -e ".[all,dev]"     # all optional backends plus pytest/pyflakes
pytest -q                       # 179 tests; 21 skip without the optional backends
python -m pyflakes lyrembed/ tests/ run.py
python run.py --version
python run.py ~/Music/Album --auto --dry-run --color never
```

Always run both `pytest -q` and `pyflakes` before finishing a change.

## Non-obvious constraints

These were found by debugging against real data. Reverting any of them
reintroduces a bug that is hard to spot.

**Never `import readline` unconditionally.** It is POSIX-only; Windows has it
through `pyreadline3` or not at all. Go through the shim in `ui.py` and check
`ui.HAS_READLINE`.

**pykakasi deletes characters it cannot read — and sometimes the next one.**
`区别` comes back as `ku`, `时候` as the single token `时`: the characters are
missing even from the token list, so checking for empty readings is not
enough. `text._romanize_japanese` probes each Han character on its own
(`_kakasi_reads`), cuts unreadable ones out before pykakasi sees the text, and
verifies that the tokens' `orig` text adds up to the input. Route every
pykakasi conversion through it; test partial loss, not just total loss.

**Han evidence comes from data, not from hand-kept lists.** `_charset_evidence`
asks the GB2312, CP950 and EUC-JP codecs which standards contain a character.
A memorised "Japanese only" list contained `問 間 師 級 傷` (Traditional
Chinese) and `区 医 欠 芸` (Simplified Chinese). Disjointness of two lists says
nothing about their correctness. pypinyin is no source either: it has readings
for shinjitai such as `駅` and `気`. CP950, not plain Big5 (lacks the variant
`裏`) nor Big5-HKSCS (contains `駅`, `円`). Japanese-only evidence counts only
when the text has Kana (`trust_ja`): rarer Hong Kong variants such as `峯` are
in no Chinese standard, and Japanese lyrics practically always contain Kana.

**Romanization works on script runs, never on whole lines.** Handing a full
line to one backend destroys what another produced (tone marks are not ASCII
and pykakasi splits them into separate tokens). But adjacent runs with the
*same* target language must be merged before conversion: a bare `見` reads
`ken`, while `見つけた` reads `mitsu`. `text._plan_runs` does both; route new
conversion paths through it.

**Non-letter characters are `KEEP`.** `text._char_class` tests `isalpha()`, not
`isascii()`. Typographic punctuation (`…`, em dashes, curly quotes) must not
count as a script, or purely Korean lyrics get reported as mixed. The
katakana middle dot `・` is `KEEP` too (Chinese and Korean lyrics use it in
names), and `ー` counts as Kana only next to Kana (`text._char_classes`).

**Latin vs. original is counted per lyric line, not per letter.** One Hangul
or Han character holds a whole syllable, so a letter count let an English
chorus outweigh the Korean verses (*Super Shy* had 7% non-Latin letters but
26% non-Latin lines). `text._lyric_lines` also drops LRC tags and credit
lines, which are mostly Latin metadata. `[MIXED]` means two *Asian* scripts
(`detect_script == "mixed"`), not Asian plus English.

**`detect_script` skips provider credit lines.** NetEase prepends Chinese
headers (`作词 : …`); they are metadata and would make every Korean song look
mixed. Recognised by a whitelist in `text._CREDIT_LABELS`, not by a generic
colon pattern — a lyric line may contain a colon.

**Never index `CONFIG["section"]` directly.** A user config may omit any
section. Use `config.get(section, key, default)`, which falls back to the
built-in defaults. Merging is recursive and deep-copies; do not replace it with
`dict.update`.

**Results must fit the track; there is no fallback to everything.** NetEase
answers a search without good hits with popular songs, which used to fill the
list whenever no artist matched. `providers.is_related` needs the artist and
the title (or, for translated titles, the length); the rest goes to
`SearchReport.unrelated`, shown on `u`. For an instrumental track
(`text.instrumental_marker`) neither length nor LRCLIB's `/get` vouches for a
result: both return the sung version. Instrumental flags (`instrumental` in
LRCLIB records, `pureMusic` in NetEase lyrics) only count for a record of the
searched track, never for a fuzzy hit.

**Keep the request count low, NetEase above all.** After a burst NetEase
answers every search with popular songs (or code -462), which looks exactly
like "not found". Its searches run one after another, lyrics are fetched for
at most `_NETEASE_LYRIC_FETCHES` songs, and aliases are searched in a second
round only when the first finds no synced lyrics that fit
(`collect_candidates`, `settings.search_all_aliases`). Filler and refusals
are reported as provider notices, never silently dropped. The search endpoint is
`/api/search/pc`: `/api/search/get` answers anonymous clients with filler
only (checked 2026-10-06 - same query, filler there, the right song on `pc`).
The syncedlyrics engine runs in the second round too, and never with its own
LRCLIB/NetEase sources (`api.syncedlyrics_providers`), which duplicated
lyrembed's requests. LRCLIB rate-limits per client (HTTP 429 via Cloudflare,
seen 2026-10-15): 429 is not in the session's retry list, a refusal stops the
track's queued LRCLIB queries and later rounds (`SearchReport.rate_limited`),
at most `_LRCLIB_PARALLEL` requests run at once, and batch mode's full search
reuses the quick `/get` result (`after_quick`) instead of asking again.
QQ Music follows NetEase's rules (filler for unknown songs, so `_fit` decides
which lyrics to fetch). It has no instrumental flag: its lyrics are then the
notice `此歌曲为没有填词的纯音乐，请您欣赏`. The syncedlyrics sources were all
dead on 2026-10-15 (Musixmatch filler for every query, Megalobiz 410, Genius
403). Megalobiz is queried directly through its public API instead, and only
in the second round: 30 requests a minute and 1,000 a day per IP. After a 429
it is skipped until `Retry-After` passes (`_megalobiz_blocked_until`) and is
never put into `rate_limited`, so a batch run does not wait for a daily limit.
Its terms ask to link to the lyric's page: `Candidate.url`, shown on inspect.

**Provider results are merged across concurrent responses.** `/get` and
`/search` often return the same LRCLIB record. Attributes derived from which
endpoint answered — currently `exact` — must be merged, not taken from
whichever future finished first, or the flag flips between runs.

**The launcher must not be named `lyrembed.py`.** With the package directory
missing, `import lyrembed` would import the launcher itself, and its
explanation of the expected layout became unreachable. Hence `run.py`;
`test_launcher_explains_a_missing_package` covers it.

**Batch mode never prompts.** It may run from cron, where a prompt blocks or
reads end-of-file. `resolve_music_directory(..., interactive=False)` returns
None instead, and the exit code is 2.

**Quitting goes through `ui.QuitRequested`, not `sys.exit`.** `cli.main`
decides the exit code in one place: 0 quit, 1 batch failures, 2 unusable
input, 130 Ctrl+C.

## Conventions

* **Errors are reported, never swallowed.** No bare `except: pass`. File
  operations raise `TaggingError`; provider failures go into
  `SearchReport.add_error` so the user sees which provider failed.
* **Writing to a file must not crash the session.** Wrap it and print the
  error; the user may have hundreds of tracks queued. Use `app.save_lyrics`,
  which also treats a failed sidecar (`tagging.SidecarError`) as the partial
  success it is.
* **Optional dependencies stay optional.** A missing package disables exactly
  one feature and says so where it is used. Never import one at module scope
  without a guarded fallback.
* **All terminal output goes through `ui`.** Colours via `StyleUI` (they are
  empty strings when the output is piped or `NO_COLOR` is set), menus via
  `ui.print_menu`, the Enter action via `ui.print_primary_action`. Do not build
  menus with bare `print`.
* **Expensive objects are built once.** The `requests.Session`, the pykakasi
  converter and the compiled regexes are cached; metadata is cached by file
  mtime, searches by query. Do not add per-line or per-redraw construction.
* **Type hints and docstrings** on public functions. Docstrings say *why*, not
  what the code already shows.

## Tests

* Anything touching an optional backend needs
  `@pytest.mark.skipif(not text.HAS_KAKASI, ...)` or the equivalent; the suite
  must pass with no optional packages installed.
* **Never hit the network.** Monkeypatch `providers.get_session` and, where
  useful, `providers._lrclib_queries`. See
  `test_exact_flag_does_not_depend_on_response_order` for the pattern.
* Audio fixtures use the base64 FLAC in `MINIMAL_FLAC_B64` written to `tmp_path`
  — no binary files in the repo.
* A bug fix comes with a test that fails without it. Prefer asserting the
  observable result (`"mitsuketayo" in out`) over internal calls.

## Verify claims, do not assert them

Behaviour here depends on third-party dictionaries and real provider
responses. Before stating that something works — in a commit message, a
changelog entry or a reply — run it. Several past "fixes" were wrong in ways
only a quick script revealed.

## Releases and documentation

`README.md`, `CHANGELOG.md` and the commit message are **only** updated for an
actual commit or release. Routine code changes do not touch them, except when
a change makes existing documentation factually wrong (a renamed command, a new
config key).

Commit messages and release notes do not belong in the repository. They are
drafted in a notes folder outside the checkout (`COMMIT_MSG_<version>.txt`,
`RELEASE_NOTES_<version>.md`); ask the maintainer where if it is not known.
The repo's `.gitignore` excludes them should one be created here by mistake.
Release tags carry no `v` prefix and always have three parts (`0.9.0`,
`1.0.0`), like the version itself.

The project was called fetchlyrics (0.1.0) and lrckit (0.2.0 to 0.5.0).
It was renamed to lyrembed in 0.6.0 because `lrc-kit` exists on PyPI;
`config.legacy_config_path` keeps reading the old config location.

A version bump touches two places:

1. `lyrembed/config.py` — `__version__` (`pyproject.toml` reads it via
   `[tool.setuptools.dynamic]`, and `api.user_agent` is derived from it)
2. `README.md` — the version line under the title

`config.example.json` is generated by `config.example_config()`: the defaults
without `api.user_agent` (a copied value would freeze the version), each key
preceded by an `_key` explanation from `config.SETTING_HELP`. Keys starting
with `_` are comments that `load_config` drops. A new setting needs a
`SETTING_HELP` entry; regenerate the file after changing a default or a text.
The example tests fail until you do. Keep any documented key in sync with the
README table.

```bash
python -c "from pathlib import Path; from lyrembed import config; config.write_example(Path('config.example.json'))"
```

Built-in artist aliases live in `lyrembed/aliases.json`, not in code, so that
what is in effect can be read (`lyrembed --list-aliases`). Every name must be
unambiguous: not an ordinary word, not shared by several artists, not inside
another group's name (`Mayday` would join *Mayday Parade* with 五月天).
`test_builtin_aliases_are_unambiguous` checks the last two. User groups from
`config.json` are merged into the built-in ones by shared name.
