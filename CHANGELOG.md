# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] — 2026-09-13

The project is now called **lrckit**. Everything the old name touched moved with
it, so this release needs a few manual steps — see *Upgrading* below.

### Fixed

* **The `exact` flag was assigned at random when a record came back from both
  LRCLIB endpoints.** `/get` and `/search` frequently return the same track,
  and since the queries run concurrently the first response to arrive decided
  the flag. Exactness is now merged across all responses for a record, so the
  same search no longer produces a different marking from run to run.

### Added

* **Session-wide search cache.** Results are kept per query, so stepping away
  from a track and coming back reuses them instead of querying the providers
  again; the highlighted entry is restored along with them. `R` forces a fresh
  search, and a successful save drops the entry. Capped by
  `settings.search_cache_entries` (default 64, least recently used evicted).
* **Confirm the highlighted entry with Enter.** When a search returns a match
  from LRCLIB's exact endpoint, that entry is highlighted on arrival and
  `Enter` saves it without further keystrokes. The `Enter` line is only shown
  when an entry is actually highlighted.

### Changed

* **Renamed from `fetch-lyrics` to `lrckit`.** The command, the Python package,
  the launcher, the PyPI project name, the User-Agent sent to LRCLIB and the
  configuration directory all follow the new name.
* **The selected entry is visible.** The highlighted row is marked with `▶`
  and its number and title are emphasised; the `Enter` line names it in full
  with artist, title, provider and properties, so the selection is readable
  without colour.
* **Returning from an inspected entry keeps it highlighted.** Looking at an
  entry and going back with `b` moves the highlight to that entry, so it is
  clear where you left off and `Enter` confirms it.
* **Menus are grouped into labelled columns.** Every menu is rendered by one
  layout function that arranges options in aligned columns under headings such
  as *Edit*, *File* and *Navigate*. Column count and width follow the terminal,
  so the list reflows instead of overflowing on a narrow window, and key
  colours now follow meaning — green for actions, yellow for navigation, red
  for quitting and deleting.
* The action `Enter` would take is printed on its own line above the menu.
* Batch mode names the entry it applied or would apply, instead of reporting
  only the provider.

### Upgrading from 0.1.x

* Reinstall under the new name: `pipx uninstall fetch-lyrics && pipx install
  git+https://github.com/sobakah/lrckit.git`, or `pip install -e ".[all]"`
  in a fresh checkout.
* Move your configuration: `mv ~/.config/fetch-lyrics ~/.config/lrckit`.
* The command is now `lrckit`; in a checkout the launcher is `./lrckit.py`.
* Nothing changes in your audio files. Tags written by earlier versions are
  read and written identically.

## [0.1.0] — 2026-09-13

Romanization of songs that mix several writing systems. Every fix below was
reproduced against real lyrics before and after the change.

First packaged release, replacing the earlier single-file script.

See the README for the full feature set. Highlights: concurrent queries against
LRCLIB, NetEase and `syncedlyrics`; confidence-aware ranking; batch mode with
`--auto` and `--dry-run`; recursive configuration merging; and a pytest suite.

### Fixed

* **The `exact` flag was assigned at random when a record came back from both
  LRCLIB endpoints.** `/get` and `/search` frequently return the same track,
  and since the queries run concurrently the first response to arrive decided
  the flag. Exactness is now merged across all responses for a record, so the
  same search no longer produces a different marking from run to run.
* **A second romanization pass destroyed the first one.** Tone marks are not
  ASCII, so pykakasi split them into separate tokens and `wǒ ài nǐ` came back
  as `w  w  a i n  i n`. Romanization now works on script runs and never
  touches text that is already Latin, so passes can be combined freely.
* **Kanji lost the context of its Kana.** Splitting a line by script handed
  pykakasi a bare `見`, which reads *ken*, instead of `見つけた`, which reads
  *mitsu*. Adjacent runs with the same target language are merged before
  conversion. `見つけたよ ココロが安らぐ` was `ken tsuketayo kokoro ga an ragu`
  and is now `mitsuketayo kokoro ga yasuragu`.
* **Okurigana was broken apart by spaces.** pykakasi cuts `見つけた` into
  `見つ` + `けたよ`; joining every token with a space produced `itoshi teru` for
  `愛してる`. Verb and adjective endings are now glued back on while real word
  boundaries keep their space. Known trade-off: a particle after a Kana-ending
  compound may be glued too, so `ずっと一緒にいたい` yields `zutto isshoniitai`.
* **pykakasi silently deleted text it could not read.** Running the Japanese
  romanizer over Chinese lyrics returned an empty string — `东车书长门` simply
  vanished, and the loss only surfaced after saving. A romanizer that would
  produce nothing now returns the original text.
* **NetEase credit headers were treated as lyrics.** Files from that provider
  start with `作词 : …`, `作曲 : …` and `编曲 : …`. Being Chinese metadata, they
  made every Korean and Japanese song from NetEase look like mixed-script text.
  Credit lines are recognised by a whitelist of known labels and ignored during
  detection; a lyric line that merely contains a colon is unaffected.
* **Typographic punctuation counted as a foreign script.** `…`, em dashes and
  curly quotes are not ASCII, so a purely Korean line containing one was
  reported as mixed. Character classification now tests for letters rather
  than for ASCII.
* **Chinese next to Japanese was absorbed into Japanese.** Kana anywhere on a
  line marked every Han run on it as Kanji. Script detection now uses the same
  per-run evidence as the converter, so `我爱你 ありがとう` is reported as mixed.
* **Kana separated by a space no longer breaks Kanji detection.** `東京 の 夜`
  fell through to the fallback. Spaced Kana counts as context when nothing else
  on the line points to another language — which keeps Chinese runs on a
  language-switching line from being captured.
* **HTTP errors dumped full request URLs** into the batch output, making every
  line unreadable. They are reported as `HTTP 403 (Forbidden)` instead.

### Added

* **Session-wide search cache.** Results are kept per query, so stepping away
  from a track and coming back reuses them instead of querying the providers
  again; the highlighted entry is restored along with them. `R` forces a fresh
  search, and a successful save drops the entry. Capped by
  `settings.search_cache_entries` (default 64, least recently used evicted).
* **Confirm the highlighted entry with Enter.** When a search returns a match
  from LRCLIB's exact endpoint, that entry is highlighted on arrival and
  `Enter` saves it without further keystrokes. The `Enter` line is only shown
  when an entry is actually highlighted.
* **Change directory from the tree overview.** `c` opens a path prompt with Tab
  completion; an empty answer keeps the current folder. The overview now shows
  which folder it is listing.
* **Mixed-script romanization.** A new *Mixed scripts* entry in the `r` menu
  converts Korean, Japanese and Chinese in a single pass. It is preselected
  only when two different Asian scripts actually share the text.
* **Choice of reading for ambiguous Han runs.** When starting a mixed
  conversion you decide whether characters that both writing systems share are
  read as Chinese or as Japanese.

### Changed

* **The selected entry is visible.** The highlighted row is marked with `▶`
  and its number and title are emphasised; the `Enter` line names it in full
  with artist, title, provider and properties, so the selection is readable
  without colour.
* **Returning from an inspected entry keeps it highlighted.** Looking at an
  entry and going back with `b` moves the highlight to that entry, so it is
  clear where you left off and `Enter` confirms it.
* **Menus are grouped into labelled columns.** Every menu is rendered by one
  layout function that arranges options in aligned columns under headings such
  as *Edit*, *File* and *Navigate*. Column count and width follow the terminal,
  so the list reflows instead of overflowing on a narrow window, and key
  colours now follow meaning — green for actions, yellow for navigation, red
  for quitting and deleting.
* The action `Enter` would take is printed on its own line above the menu.
* Batch mode names the entry it applied or would apply, instead of reporting
  only the provider.
* Distinguishing Hanzi from Kanji no longer relies on a hand-curated character
  list. A Han run is fed to pykakasi and the share of characters it has a
  reading for is measured; since its dictionary contains no simplified forms,
  incomplete coverage proves the run is not Japanese. This probes the whole
  dictionary rather than 55 selected characters, and raised the share of runs
  resolved without asking from 5/14 to 11/14 on a sample of typical lyric
  lines.
* Text written purely in characters both systems share — `月光下的思念`,
  `東京物語` — remains undecidable and follows the choice made at conversion
  time. Traditional Chinese resolves less often than simplified for this
  reason.

[0.2.0]: https://github.com/sobakah/lyrembed/releases/tag/0.2.0
[0.1.0]: https://github.com/sobakah/lyrembed/releases/tag/0.1.0
