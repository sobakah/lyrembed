# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.5.0] — 2026-10-06

Search results now have to fit the track, instrumental tracks are recognised,
several actions run over many tracks at once, NetEase finds songs again with
fewer requests, and synced lyrics rank first.

### Fixed

* **NetEase returned only unrelated songs.** Its `/api/search/get` endpoint
  answers anonymous clients with popular filler, which the new result filter
  hid, so NetEase looked empty. The default is now `/api/search/pc`, which
  returns the right songs for the same queries. Checked on P1Harmony's *DUH!*:
  NetEase now has synced lyrics for five of six tracks, before for none. A
  config that sets `api.netease_search_url` to `…/api/search/get` should drop
  the key.
* **NetEase throttling went unnoticed.** Filler answers and refusals (code
  -462) are now reported as provider notices; `R` retries.
* **Unrelated results filled the list.** NetEase answers a search without good
  hits with popular songs, and when no result matched the artist, lrckit
  listed all of them - often a page of Chinese pop for an instrumental track.
  The title is now checked as well as the artist (for a title in another
  language, the length counts instead), and results that do not fit are
  hidden behind `u` and marked `≠artist` / `≠title`. Their lyrics are not even
  fetched from NetEase any more. Checked live: *Opening* by Studio Ghibli
  Orchestra listed nine Chinese pop songs, now none; *Blueming* and *LOVE
  SCENARIO* are still found and picked automatically.
* **NetEase's instrumental notice was listed as lyrics.** `纯音乐，请欣赏`
  ("pure music, please enjoy") showed up as Chinese lyrics.
* **The sung version was offered for instrumental tracks.** LRCLIB's exact
  lookup ignores `(Inst.)` and returned the vocal *Blueming* for *Blueming
  (Inst.)*, highlighted for Enter. Version notes now count, also for exact
  matches.

### Added

* Alias P1Harmony = 피원하모니.
* **Instrumental tracks are recognised** before and during the search: by a
  version note in the title (`(Inst.)`, `- Instrumental`, `(Off Vocal)`,
  `(MR)`, `(Karaoke)`, `オフボーカル`, `伴奏` …), by the language tag `zxx`,
  by LRCLIB's `instrumental` and NetEase's `pureMusic` flags, and optionally
  by MusicBrainz (`api.enable_musicbrainz`, off by default) for files with a
  MusicBrainz recording ID. A `♪` notice says why; batch mode and the bulk
  fetch skip such tracks, without searching when the tags already say so.
* **Bulk actions** (`a` in the tree overview) for all tracks or a selection
  such as `1-5,8`: fetch lyrics for tracks without, romanize the embedded
  lyrics (detected script, `romanization_layout`), write `.lrc` sidecars, and
  delete lyrics. Replaced lyrics and overwritten sidecars are backed up first.
* **About 200 built-in artist alias groups** in `lrckit/aliases.json`, up from
  11: K-pop groups and soloists, Japanese and Chinese artists, soundtrack
  composers, and spellings with and without diacritics. `--list-aliases`
  prints every group in effect.
* `config.example.json` explains every setting next to it, and says that it
  is a reference lrckit does not read. Keys starting with `_` are comments in
  any config file.

### Changed

* **Synced lyrics rank above plain ones**, also above LRCLIB's exact match
  when that has plain lyrics only. Such an exact match used to lead the list
  and be what Enter saved, while synced versions of the same song sat further
  down. After a search, Enter now saves the entry batch mode would pick
  (synced, artist and title fit, length within tolerance); only without one
  an exact match on top. `settings.prefer_synced: false` restores the old
  order. Checked on P1Harmony's *DUH!*, *Flashy* and *Work*: the synced
  version is on top and highlighted, the plain one below.
* **Enter saves.** In the inspection view Enter applies the lyrics (`y` still
  works), and "Save changes?" / "Save romanized lyrics?" default to yes.
  Delete and discard prompts still default to no.
* **Searches go in rounds.** LRCLIB and NetEase are asked first with the
  tag's artist only. The artist's aliases and the `syncedlyrics` engine
  follow only when that finds no synced lyrics that fit, and never for
  instrumental tracks. `a` in the search results searches the aliases on
  demand; `settings.search_all_aliases` and `api.syncedlyrics_as_fallback`
  bring back the old behaviour.
* **Fewer requests.** NetEase searches run one after another, and lyrics are
  fetched for at most the three best-fitting songs. LRCLIB gets four queries
  instead of six: the `Romanized` query found nothing new in a test of eight
  tracks, and the exact lookup without duration only runs when the one with
  duration fails. `syncedlyrics` no longer queries LRCLIB and NetEase a second
  time (`api.syncedlyrics_providers`).
* Provider labels are upper case: `[NETEASE]`, and `[SYNCEDLYRICS]` instead of
  the unclear `[MULTI]`.
* **`settings.prefer_latin` is off by default**, and off now leaves the script
  out of the ranking instead of preferring the original.
* **Your `aliases` extend the built-in groups** instead of replacing all of
  them; a group sharing a name with a built-in one is merged into it.
* Romanizing with the `below` layout twice no longer doubles the lines.

### Upgrading from 0.4.x

* To keep romanized results first, set `"prefer_latin": true`.
* If your config has an `aliases` list that copied the old built-in groups,
  you can remove the copies; `lrckit --list-aliases` shows what is in effect.

## [0.4.0] — 2026-10-05

### Fixed

* **Korean and Chinese lyrics were labelled LATIN.** The script badge counted
  letters, but one Hangul or Han character holds a whole syllable, so an
  English chorus outweighed the Korean verses. LRC tags (`[ar:…]`, `[ti:…]`)
  and NetEase credit lines, mostly Latin metadata, were counted as well. Lines
  are counted now, without tags and credits. In a live check 25 of 80 search
  results changed from LATIN to ORIG, all Korean songs with much English
  (*Super Shy*: 7% non-Latin letters, 26% non-Latin lines); none changed the
  other way. Ranking with `prefer_latin` uses the same check and improves
  with it.
* LRC tags no longer count towards the suggested romanization, so a Chinese
  `[al:…]` tag does not make a Korean song look mixed.

### Added

* **`[MIXED]` badge** for lyrics that combine several Asian scripts (Korean
  with Japanese, say), which need the *Mixed scripts* romanization. Asian
  script with English stays `[ORIG]`.
* **`cleaning.strip_all_brackets`** removes everything in `(…)` and `[…]`
  from title, artist and album before searching, for tags such as
  `Group ("Member A", "Member B")`. Off by default.
* **`settings.prefer_album_artist`** searches with the album artist
  (`albumartist`, ID3 `TPE2`, MP4 `aART`) instead of the track artist, for
  tags that list every member or guest per track. Compilations (`Various
  Artists`) and tracks without an album artist keep the track artist. Off by
  default.

### Changed

* `settings.non_latin_ratio_threshold` is now a share of lyric lines, with a
  default of `0.1` (was a share of letters, `0.25`).
* The selected choice has yellow brackets: the highlighted search result, the
  detected romanization, the default Han reading and layout, and the track the
  overview starts with.

### Upgrading from 0.3.x

* If your config sets `non_latin_ratio_threshold`, check the value: it now
  means lines, not letters. Removing it gives the new default.

## [0.3.0] — 2026-10-05

A correctness and safety release, prompted by a full code review. Romanization
could silently lose characters, NetEase had stopped returning results without
saying so, and several paths could crash, hang in batch mode or report the
wrong outcome. Every fix below comes with a regression test that fails without
it, and the lossy romanization was checked against real lyrics.

**If you romanized Chinese lyrics with *Mixed scripts* (or *Japanese*) in an
earlier version, check them** — see *Upgrading* below. Korean and Japanese
songs, and Chinese songs romanized with *Mandarin*, are not affected.

### Fixed

* **Romanization lost characters.** pykakasi does not only return an empty
  reading for a character it does not know; it can also swallow the next one.
  The old guard caught only a completely empty result, so partial loss went
  through: `区别` was saved as `ku`, `鄉間的歌謠` as `teki kayou`. Unreadable
  characters are now cut out before pykakasi sees the text and converted with
  pypinyin (or anyascii), and every conversion is checked for completeness.
* **Chinese was read as Japanese.** The hand-kept list of "Japanese-only"
  characters contained `問 間 師 級 傷`, which Traditional Chinese uses, and
  `区 医 欠 芸`, which Simplified Chinese uses. `時間` became `jikan`, `老師`
  became `roushi`. Which writing systems use a character is now looked up in
  the GB2312, CP950 (Big5) and JIS character sets that ship with Python, and a
  Japanese-only character only counts when the text contains Kana somewhere —
  so rare Chinese variants such as `裏` are not misread either.
* **NetEase returned nothing.** Its search endpoint now answers with an
  encrypted string, which the provider silently treated as "no results". The
  default endpoint is `/api/search/get`, and an unreadable response is
  reported as a provider notice.
* **NetEase credit headers were embedded.** `作词 : …`, `作曲 : …` and similar
  lines are removed from NetEase lyrics.
* **`syncedlyrics` results passed as matches.** The engine does not say which
  recording its lyrics belong to, but its results carried the searched artist
  and passed the artist filter. They are now marked `?unverified`, ranked after
  verified results and never applied by `--auto`. Its wait is capped by
  `api.syncedlyrics_timeout_seconds`; a slow scraper no longer stalls a search.
* **"Duration OK" was shown when the track's length was unknown.** With
  nothing to compare, the column now shows the candidate's own length.
* **A file type added to `supported_extensions` crashed the program.** Only
  the five supported containers are scanned; others are ignored with a
  warning, and an unreadable file is reported on its track.
* **Batch mode could hang or exit with 0.** With a path that was too broad,
  the home directory or empty, `--auto` prompted for another path — blocking
  under cron, or reading end-of-file and exiting successfully. Batch mode
  never prompts now and exits with code 2.
* **Exit codes were inconsistent.** Ctrl+C at a prompt exited with 0, elsewhere
  with 130. Now: `0` quit, `1` failures in batch mode, `2` unusable path,
  config or arguments, `130` Ctrl+C.
* **A failed sidecar was reported as a failed save.** The lyrics were already
  in the file; batch mode counted the track as failed and the tag menu kept
  showing "unsaved". It is now a warning.
* **MP3 tags were converted to ID3v2.4** on every save, and deleting lyrics
  rewrote files that had none. The existing version is kept, and files are
  only rewritten when there is something to remove.
* **Tab completion failed in folders with brackets** such as `Album [FLAC]/`.
* **Stepping back onto an unreadable track jumped forward again**, so earlier
  tracks could not be reached.
* **GUI editors started without their wait flag lost every edit**, because the
  file was read back and deleted at once. Such an editor is now detected and
  the fix suggested (`code --wait`). `$VISUAL` takes precedence over
  `$EDITOR`, as is conventional.
* **CJK lines were cut at the wrong width** in previews: characters were
  counted instead of terminal columns.
* **The user's config was ignored in a checkout**, because the shipped
  `config.json` was found first.
* **A broken `--config` file was silently replaced by the defaults.** It is now
  an error. Values of the wrong type and unknown keys in any config file are
  reported, and the default is used for them.
* **The launcher's help for a broken checkout was unreachable.** Named
  `lrckit.py`, it imported itself when the package directory was missing.
* A title written only in bracketed CJK (`[自由]`) produced an empty query.
* The User-Agent sent to LRCLIB contained a `<username>` placeholder.

### Added

* **`settings.romanization_layout`** chooses what Enter picks at the layout
  prompt after romanizing: `replace` (default) replaces each line, `below`
  keeps the original and adds the romanized line below it. Typing `1` or `2`
  still picks a layout for a single song.
* **Backups for `--overwrite`.** Lyrics replaced in batch mode are saved to
  `$XDG_STATE_HOME/lrckit/backups/<time>/` first; a track whose lyrics cannot
  be backed up is not overwritten.
* **Keep the original line when romanizing.** After choosing the script, choose
  between replacing each line and adding the romanized line below it with the
  same timestamp.
* **NetEase user romanizations** are offered as an additional candidate, marked
  `user romanization`.
* **Synchronized MP3 lyrics (`SYLT`)** are read and replaced on write.
* **Ctrl+C cancels a running search** and stays on the track; `R` starts it
  again.
* **`--max-files N`** overrides `settings.max_file_count`; `0` means no limit.
* **Stale sidecar warning.** Without `--sidecar`, an existing `<track>.lrc` is
  left alone but reported, since many players prefer it; deleting a track's
  lyrics offers to delete it too.
* Config keys `api.syncedlyrics_timeout_seconds` (default 15) and
  `api.batch_delay_seconds` (default 1).

### Changed

* **Batch mode goes easy on LRCLIB.** It asks only the exact `/get` endpoint
  first and runs the full search across all providers just for tracks that
  are not settled that way, and pauses between tracks. A large library used to
  cost a dozen requests per track to a free community service.
* **The checkout launcher is `run.py`.**
* **`config.json` became `config.example.json`** and is no longer loaded
  automatically. Lookup order: `--config`, then
  `~/.config/lrckit/config.json`, then a `config.json` in a source checkout.
* `--overwrite` and `--dry-run` require `--auto`; `--sidecar` and
  `--no-sidecar` exclude each other. Both used to be silently ignored.
* `settings.id3_v2_version` applies to MP3s without an ID3v2 tag only.
* The version is defined once, in `lrckit/config.py`.

### Removed

* `requirements.txt` and `requirements-optional.txt` — they duplicated
  `pyproject.toml`. Use `pip install -e ".[all]"`.

### Upgrading from 0.2.x

* **Check Chinese songs you romanized with *Mixed scripts*.** Earlier versions
  replaced the original text, so lost characters cannot be recovered from the
  file. Re-romanize them: open the track, `o` to search online, pick the
  matching entry, `r` → *Mixed scripts*. Signs of an affected song are words
  without tone marks among the pinyin — Japanese readings such as `teki`,
  `ware`, `jikan`.
* In a checkout, start the program with `./run.py`.
* If you edited the checkout's `config.json`, move your changes to
  `~/.config/lrckit/config.json`; `git pull` will report a conflict on that
  file until you do.
* If your config sets `api.netease_search_url` to `…/api/search/get/web`,
  remove the key or change it to `https://music.163.com/api/search/get`.

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

[0.5.0]: https://github.com/sobakah/lyrembed/releases/tag/0.5.0
[0.4.0]: https://github.com/sobakah/lyrembed/releases/tag/0.4.0
[0.3.0]: https://github.com/sobakah/lyrembed/releases/tag/0.3.0
[0.2.0]: https://github.com/sobakah/lyrembed/releases/tag/0.2.0
[0.1.0]: https://github.com/sobakah/lyrembed/releases/tag/0.1.0
