# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Extract to .lrc** on a track's page writes its embedded lyrics to a `.lrc` file next to it, as the bulk action, now called **Extract embedded lyrics to .lrc files**, does for many tracks. A differing `.lrc` file is backed up first.

### Fixed

- The lists of the page's dropdowns, such as the setting **romanization layout**, are readable in dark mode.

## [1.1.0] — 2026-10-31

### Changed

- **The page has a new look, shared with cdimport**, in libadwaita's design language: a header with lyrembed's workflow in the middle (Folder, Tracks, Lyrics, Edit, which you can click to go back), a centred column, softer buttons, grouped settings with switches, and floating messages. The two programs differ by colour and logo.
- A small logo for the header and the browser tab, where the full icon can't be read. The full icon stays in the app menu and appears on the page's larger screens.
- Icons on the action buttons. The list's **Open** buttons became arrows, and the folder's path moved under the album's name.

### Fixed

- **Japanese songs are no longer romanized as Chinese.** A line of Kanji alone (`約束`) made a Japanese song count as mixed, so such lines came out in Pinyin, without asking in batch mode and bulk runs. They are now read as Japanese when the song has Kana and nothing proves Chinese.
- **Batch mode is pickier about titles and artists.** Titles of symbols alone (`???`) no longer match every song, one word no longer matches a much longer title (`Stay` and `Stay With Me`), and a result without an artist is no longer embedded unattended. The page still lists them.
- Batch mode waits out a provider's rate limit even when another provider found the lyrics.
- An album imported with cdimport is no longer missed when cdimport ends at the wrong moment.
- A credit naming several artists searches the aliases of each of them, without more requests than one artist's.
- A slow search no longer keeps lyrembed from ending after Quit or Ctrl+C.
- Converting Apple Lossless for listening no longer holds up the rest of the page.
- A cover file that was changed, added or removed shows without restarting lyrembed.
- Restarting after an update finds lyrembed on Windows.
- No error messages in the terminal when the browser leaves in the middle of an answer.
- Settings opens when no folder is open; it showed the folder picker instead.
- No sideways scrolling at phone width.

## [1.0.1] — 2026-10-30

### Changed

- **lyrembed looks for a newer version every time the page starts**, in the background, instead of once a day, so a new release shows at the next start.
- A more playful icon, drawn like cdimport's: a speech bubble of lyrics, its first line lit like karaoke, with words flying out of it into a music file. Run `lyrembed --install-desktop-entry` again to show it in the app menu.

## [1.0.0] — 2026-10-27

The first stable release. lyrembed finds synchronized and plain lyrics for
an album (LRCLIB, NetEase, QQ Music, Megalobiz), checks that they belong
to the track, romanizes Korean, Japanese and Chinese lyrics, and embeds
them into FLAC, MP3, OGG Vorbis, Opus and M4A files. You work on a page in
your browser, served by lyrembed on this computer: the track list with
covers, search results next to a preview, an editor that plays the track,
bulk actions, settings and artist aliases. `--auto` tags a whole folder in
the terminal without asking, also from cron. With cdimport installed, a
CD can be imported from the page. From now on the page tells you about new
versions and installs them.

### Added

* **Updates:** the page looks for a newer lyrembed when it starts (at most
  once a day; setting `check_for_updates`) and with **Check for updates**
  in Settings. Installed with pipx, **Update** installs it and **Restart
  lyrembed** starts the new version on the same address, so the open tab
  just reloads; otherwise the page shows the command. The check uses git,
  with the same access to the repository as `pipx install`. Batch mode and
  a lyrembed opened by cdimport never look.

### Fixed

* **Synced lyrics with Windows line ends** showed as untimed in the
  editor, and shifting or stamping them failed. Lyrics now get plain line
  ends when read from a file, taken from a provider or saved.
* **A fitting result could be thrown out.** Of several results with the
  same text, the one whose length fits the track is now kept; before, an
  exact match of a longer version won, and batch mode then took neither.
* **An off-vocal track showed the sung track's results** when both have
  the same length: `Song (カラオケ)` and `Song` shared a cached search.
* **Japanese lyrics with a rare kanji** (one pykakasi has no reading for,
  such as 唸) counted as mixed with Chinese, and their kanji came out as
  Pinyin. Kanji between punctuation on a Japanese line (`「夢」、そして`)
  are read as Japanese too.
* **Mixed-script romanization without its packages** said "already
  romanized"; it now names the missing packages instead of converting
  only part of the lyrics.
* **Exporting sidecars** stopped the whole run at an `.lrc` file that is
  not UTF-8; that file is now left alone and reported.
* **The page:** saved settings and aliases apply to the next search
  instead of a cached one; a failed update no longer freezes the bulk
  run's progress; clicking a repeated line with an offset or shift jumps
  to the right repeat; no other folder opens while a bulk run goes; a
  track removed from disk gives a clear message instead of an internal
  error.
* **Folder check:** the folders above your home folder are refused too,
  also when `/home` is a link (Fedora Atomic).
* **Config:** an invalid `cleaning` regex, a `non_latin_ratio_threshold`
  above 1 and a `uslt_language` that is not three letters are reported
  and replaced by the default; the settings page refuses them. Before,
  a broken regex was skipped without a word.

## [0.9.0] — 2026-10-21

### Added

* **Romanize when saving**, a checkbox in the preview of a search result:
  both Save buttons then romanize first, as batch mode does with
  `settings.auto_romanize` (detected script, configured layout), without
  the editor. It starts as that setting says and stays as set while
  working through an album; with the needed package missing nothing is
  saved and the page says why.
* **Import a CD**, when cdimport 0.6.0 or newer is installed: cdimport's
  page opens in the same tab, its lyrics step opens the album on a
  lyrembed page of its own, and **Done – back to lyrembed** at the end
  brings the tab back with the imported album open.
* **`--return-url URL --return-name NAME`**, for another program's page
  that opens lyrembed: **Done – back to NAME** replaces Quit, ends
  lyrembed and sends the tab back. Only addresses on this computer are
  accepted.
* **An app-menu entry**: `lyrembed --install-desktop-entry` adds lyrembed
  with an icon to the menu of Linux and BSD desktops, starting the
  lyrembed that wrote it (a pipx installation or a checkout);
  `--remove-desktop-entry` removes it. Started without a folder, the page
  opens the folder picker in the desktop's music folder instead of
  warning about the home folder.

### Fixed

* **Two pages on this computer no longer log each other out.** Browser
  cookies belong to the host, not the port, so a second lyrembed (or
  cdimport) overwrote the first one's token; the cookie's name now
  carries the port.

## [0.8.0] — 2026-10-21

### Changed

* **lyrembed works in your browser now.** `lyrembed DIR` opens a page
  instead of the terminal menus, which are gone. It runs on a small
  server on `127.0.0.1` that only this computer can reach, and only with
  the secret address lyrembed prints; it loads nothing from the internet.
  Quit on the page, press Ctrl+C, or close the tab. `--auto` (batch mode)
  works as before, without a page; its options and exit codes are
  unchanged.
* **Programs that start `lyrembed DIR`**, such as cdimport, now get the
  page; lyrembed still exits when you are done, so they continue as
  before.
* **Windows no longer needs `pyreadline3`**: there are no prompts left.

### Added

* **The track list** with covers (one shared by every track is shown with
  the folder) and each track's lyrics status. Every track can be played
  from the list, and a folder picker opens another album.
* **Search results next to a preview**, with provider notices, the
  results for other songs behind a click, other search terms, a fresh
  search, alias search, and lyrics typed or pasted in.
* **An editor that plays the track** and lights up the current line:
  shift all lines in 50/250 ms steps, start a single line at the current
  position (which also turns plain lyrics into synced ones), switch
  between search results while the song keeps playing, romanize, or edit
  the text. A file's `[offset:]` is folded into the timestamps when the
  timing changes. Apple Lossless (ALAC) tracks play through a temporary
  FLAC copy made with ffmpeg, since browsers do not play ALAC.
* **Bulk actions with live progress**: fetch lyrics automatically (also
  as a dry run, replacing existing lyrics, or romanizing), romanize,
  write `.lrc` files, delete. Stop takes effect after the current track.
* **Settings and artist aliases** on the page. Only the changed keys are
  written to the config file; its other keys and comments stay, the
  previous version is kept as `config.json.bak`, and a file that is not
  valid JSON is never overwritten.
* **`--no-browser`** prints the page's address instead of opening it.

### Removed

* **The terminal menus**, the external editor (`$VISUAL`, `$EDITOR`) and
  the path prompt with Tab completion. The settings `default_editor`,
  `preview_lines` and `enter_opens_preview` belonged to them; a config
  file that still has them gets a note that they can be removed.

## [0.7.0] — 2026-10-15

### Added

* **QQ Music** as a third provider, searched together with LRCLIB and
  NetEase. Its catalogue is strong in Chinese, Korean and Japanese music,
  and it often has synced lyrics when the others do not or are
  unavailable. It follows NetEase's rules: one search at a time, lyrics
  for at most three fitting songs. Its instrumental notice marks a track
  as instrumental, and its credit lines and opening `Title - Artist` line
  are removed, also when the song's name carries a version note, such as
  `Lemon (Live)` with the line `Lemon - 米津玄師`.
  `api.enable_qqmusic` turns it off.
* **Megalobiz** as a fourth provider, through its public API. Users sync
  lyrics there themselves: often several versions of one song, also for
  little-known songs, of varying quality. It is only asked when LRCLIB,
  NetEase and QQ Music find no synced lyrics that fit, because its free
  limits are small (30 requests a minute, 1,000 a day; an optional
  `api.megalobiz_api_key` raises them). After a refusal it is skipped
  until the limit resets, without holding up a batch run. When you
  inspect its lyrics, their Megalobiz page is shown.
* **QQ Music and Megalobiz lyrics start with the lyrics.** The header tags
  both put above them (`[ti:]`, `[ar:]`, `[al:]`, `[by:]`, `[length:]`,
  `[re:]`, QQ Music's long `[kana:]` reading list) are removed; the file's
  own tags already hold that information. A non-zero `[offset:]` stays,
  since it shifts the timing.

### Changed

* **Gentler on LRCLIB's rate limit.** LRCLIB now often refuses requests
  with HTTP 429 (Too Many Requests). lyrembed sent no more requests than
  before, but made a limit worse:
  * A refused request is no longer retried automatically; each retry
    counted against the limit again. Server errors (5xx) are still retried.
  * After a refusal, the remaining LRCLIB queries for the track are not
    sent, and later search rounds skip LRCLIB. NetEase and syncedlyrics
    carry on. The refusal is shown as a provider notice.
  * Batch mode waits after a refused track (at least 10 seconds or what
    LRCLIB asks for, doubling while the limit persists, at most 2 minutes)
    and tries the track once more instead of skipping it.
  * At most two LRCLIB requests run at the same time, instead of all of a
    track's queries at once (up to eleven with aliases).
  * Batch mode's full search reuses the result of the quick `/get` lookup
    instead of sending it again: two requests fewer per track that the
    quick lookup does not settle.
* **`api.syncedlyrics_providers` defaults to Musixmatch and Genius**;
  Megalobiz is now asked directly. As of October 2026 neither source
  answers through syncedlyrics 1.0.1: Musixmatch returns the same
  unrelated songs for every search, Genius refuses the requests.
* **Configuration values are checked for range, not only type.** Values
  that cannot work, such as `max_workers: 0` (which silently switched
  LRCLIB off), a negative `preview_lines` or an unknown `color`, are
  reported and replaced by the default. An `aliases` entry that is not a
  list of names, such as a flat `["BTS", "Bangtan Boys"]`, is reported
  instead of being dropped silently.
* **The tree overview continues where you were.** After `t`, Enter goes
  back to the current track instead of starting over at track 1.
* **Search results stay cached after saving.** Choosing "search online"
  on a track tagged in the same session shows the earlier results without
  new requests.
* **An editor closed right away without changes** is shown as a note
  ("No changes"), not as an error. The hint about GUI editors without a
  wait flag stays.

### Fixed

* **Identical lyrics from several uploads** no longer depend on which
  request finished first. The upload that fits the track best is kept: a
  cover with the same text no longer hides the original, and a fuzzy hit
  no longer hides LRCLIB's exact match.
* **An instrumental version no longer fits a sung track by its length.**
  `BIZNESS (纯音乐)` was listed for `BIZNESS` because both are equally
  long; the length only vouches for a title in another language.
* **The middle dot `・`** in Chinese and Korean lyrics no longer makes them
  count as Japanese: they were marked `[MIXED]`, and Han characters next
  to the dot were romanized with Japanese readings. `ー` counts as Kana
  only next to Kana.
* **Romanized lines below the original** no longer duplicate lines that
  needed no romanization but contained double or trailing spaces, nor LRC
  headers such as `[al:]`.
* **Tab completion** of the folder prompt works in folders with spaces in
  their names, and with a quoted path.
* **In the menu for embedded lyrics**, closing the editor without changes
  after an unsaved romanization no longer asks "Save changes?" again.
* **An instrumental note in Japanese or Chinese brackets** (`(インスト)`,
  `(伴奏)`) now counts when a provider's instrumental flag is checked, and
  tracks whose titles are entirely bracketed no longer share cached search
  results.

## [0.6.0] — 2026-10-13

lrckit is now called **lyrembed**: the name lrckit was already taken on
PyPI by `lrc-kit`, a lyrics parser.

### Added

* **Romanization in batch mode.** `settings.auto_romanize` makes `--auto`
  romanize the lyrics it embeds, the same way the bulk action does: the
  script is detected per song, the layout comes from
  `settings.romanization_layout`, and Latin or already romanized lyrics are
  embedded as they are. Off by default. `--romanize` and `--no-romanize`
  override the setting for one run. If a romanization package is missing,
  the track is still tagged with the lyrics as found, and the batch output
  says why they were not romanized.
* **`settings.enter_opens_preview`** makes Enter on the highlighted search
  result open its preview and options first, like typing its number, instead
  of saving at once. Off by default.

### Changed

* **Renamed to lyrembed.** The command, the Python package and the paths
  follow: `lyrembed` instead of `lrckit`, `~/.config/lyrembed/config.json`,
  and backups in `~/.local/state/lyrembed/backups/`. The repository moved to
  <https://github.com/sobakah/lyrembed>; GitHub forwards the old address.

### Fixed

* **Albums with sung and instrumental versions of the same song.** Searching
  for `1&Only` also finds the record of `1&Only (Inst.)`, which LRCLIB flags
  as instrumental. Because version notes were ignored when comparing titles,
  that flag was applied to the sung track, and the interactive view reported
  it as instrumental. NetEase's instrumental flag had the same flaw. A flag
  now counts only when the record and the track agree on being an
  instrumental version.

### Upgrading from lrckit

* Install under the new name and remove the old command:

  ```bash
  pipx uninstall lrckit
  pipx install "lyrembed[all] @ git+https://github.com/sobakah/lyrembed.git"
  ```

* Move `~/.config/lrckit/config.json` to `~/.config/lyrembed/config.json`.
  Until then the old file is still read, with a reminder at start-up.
* Backups made by lrckit stay in `~/.local/state/lrckit/backups/`.

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

[1.1.0]: https://github.com/sobakah/lyrembed/releases/tag/1.1.0
[1.0.1]: https://github.com/sobakah/lyrembed/releases/tag/1.0.1
[1.0.0]: https://github.com/sobakah/lyrembed/releases/tag/1.0.0
[0.9.0]: https://github.com/sobakah/lyrembed/releases/tag/0.9.0
[0.8.0]: https://github.com/sobakah/lyrembed/releases/tag/0.8.0
[0.7.0]: https://github.com/sobakah/lyrembed/releases/tag/0.7.0
[0.6.0]: https://github.com/sobakah/lyrembed/releases/tag/0.6.0
[0.5.0]: https://github.com/sobakah/lyrembed/releases/tag/0.5.0
[0.4.0]: https://github.com/sobakah/lyrembed/releases/tag/0.4.0
[0.3.0]: https://github.com/sobakah/lyrembed/releases/tag/0.3.0
[0.2.0]: https://github.com/sobakah/lyrembed/releases/tag/0.2.0
[0.1.0]: https://github.com/sobakah/lyrembed/releases/tag/0.1.0
