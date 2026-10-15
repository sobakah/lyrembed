# lyrembed

Vibecoded interactive CLI utility for Linux, macOS and Windows to search, inspect, romanize and embed synchronized (`.lrc`) and plain lyrics into FLAC, MP3, OGG Vorbis, Opus and M4A files with a focus on east asian music.

Version 0.7.0 · Python 3.10+ · MIT

Formerly called **lrckit**; see the [changelog](CHANGELOG.md) for moving an existing setup.

---

## Features

* **Multi-format tagging** — FLAC, MP3, OGG Vorbis, Opus and M4A, plus optional companion `.lrc` sidecar files.
* **Concurrent multi-provider search** — LRCLIB, NetEase Cloud Music and QQ Music are queried in parallel on a pooled HTTP session with retry and backoff; Megalobiz (community-synced lyrics, often several versions per song), the artist's aliases and the optional `syncedlyrics` engine follow only when that finds no synced lyrics.
* **Exact-match lookups** — LRCLIB's `/get` endpoint is queried with title, artist, album and duration; fuzzy `/search` queries run alongside as a fallback.
* **Confidence-aware ranking** — ordered by exact match, synchronization, script preference and duration agreement.
* **Smart tag cleaning & aliases** — strips features (`feat. …`), filters localized bracketed subtitles out of queries, and resolves group acronyms (*Tomorrow X Together* ↔ *TXT*) with word-boundary matching.
* **Romanization** with automatic script detection, preserving LRC timestamps: Hangul → Romaja, Kanji/Kana → Hepburn Rōmaji, Hanzi → Pīnyīn, and any other script → Latin via `anyascii`. Lines containing several scripts at once are split into runs and converted per run, and text that is already Latin is never touched — so passes can be combined without destroying each other's output.
* **Batch mode** — `--auto` tags every track with a confident match; `--dry-run` reports without writing, `--overwrite` backs up the lyrics it replaces.
* **Search results are cached for the session** — leaving a track and coming back to it reuses the previous result instead of querying the providers again. `R` forces a fresh search.
* **Safeguards** — metadata cached by modification time, recursion depth and file-count caps, Tab-completing path prompt, colour output honouring `NO_COLOR` and non-TTY pipes.

---

## Installation

### pipx (recommended)

```bash
pipx install git+https://github.com/sobakah/lyrembed.git
# with romanization and the extra provider engine:
pipx install "lyrembed[all] @ git+https://github.com/sobakah/lyrembed.git"
```

This installs a `lyrembed` command on your `PATH`.

> **fish users:** `pipx` and `pip install --user` place their launchers in `~/.local/bin`. On Fedora that directory is added to `PATH` by `~/.bash_profile`, which fish never reads — so the install succeeds but `lyrembed` is not found. Add it once with `fish_add_path ~/.local/bin` (fish 3.2+); it persists across sessions. In a virtual environment, use `source venv/bin/activate.fish` rather than `activate`, and set the editor with `set -gx EDITOR nvim` rather than `export`.

### From a checkout

```bash
git clone https://github.com/sobakah/lyrembed.git
cd lyrembed
pip install -e ".[all]"
```

The checkout also runs without installation. `run.py` is only a launcher — it needs the sibling `lyrembed/` package directory next to it:

```
run.py
lyrembed/
    __init__.py  cli.py  app.py  providers.py
    tagging.py   text.py editor.py ui.py config.py
```

```bash
chmod +x run.py
./run.py ~/Music/Artist/Album
```

### Dependencies

Required: `mutagen`, `requests`. On Windows also `pyreadline3`, which supplies the `readline` API that Windows lacks — without it the program still runs, but Tab completion, history and prefilled prompts are disabled.

```bash
pip install mutagen requests          # Linux/macOS
pip install mutagen requests pyreadline3   # Windows
```

Optional. A missing package only disables that one feature and is reported where it is used:

```bash
pip install --user pykakasi pypinyin korean-romanizer anyascii syncedlyrics
```

| Package | Functionality |
|---|---|
| `syncedlyrics` | Scraping engine for Musixmatch and Genius (neither answers as of October 2026) |
| `korean-romanizer` | Hangul → Revised Romanization of Korean |
| `pykakasi` | Kanji and Kana → Hepburn Rōmaji |
| `pypinyin` | Hanzi → Pīnyīn with tone marks |
| `anyascii` | Any non-Latin Unicode script → ASCII |

> **PEP 668 / externally-managed-environment:** if your package manager blocks global pip installs, use a virtual environment or append `--break-system-packages`.

---

## Usage

```bash
lyrembed                              # interactive, current directory
lyrembed ~/Music/Artist/Album         # interactive, specific album
lyrembed ~/Music/Artist --auto        # tag everything with a confident match
lyrembed ~/Music/Artist --auto --dry-run
```

| Option | Effect |
|---|---|
| `path` | Music directory (default: current directory) |
| `--version` | Print the version and exit |
| `-c`, `--config FILE` | Use this config file instead of the search paths; an unusable file is an error |
| `--list-aliases` | Print the artist alias groups in effect (built-in and your own) and exit |
| `--max-files N` | Scan at most `N` files, `0` for no limit (overrides `settings.max_file_count`) |
| `--auto` | Batch mode: tag confident matches without prompting |
| `--overwrite` | With `--auto`, also replace existing lyrics; the old ones are backed up to `$XDG_STATE_HOME/lyrembed/backups/<time>/` (default `~/.local/state/…`) |
| `--dry-run` | With `--auto`, report changes without writing |
| `--romanize` / `--no-romanize` | With `--auto`, romanize the lyrics it embeds, or not; overrides `settings.auto_romanize` for this run |
| `--color {auto,always,never}` | Override colour handling |
| `-v`, `-vv` | Increase log verbosity (on stderr) |
| `--sidecar` / `--no-sidecar` | Force companion `.lrc` files on or off |

`--auto` only applies a candidate when it is synchronized, the artist matches, and the duration is within `api.duration_tolerance_seconds` — or the match came from LRCLIB's exact `/get` endpoint. Results from the `syncedlyrics` engine are never applied automatically, because it does not say which recording its lyrics belong to. Anything less confident is left for interactive review.

To go easy on LRCLIB, a free community service, batch mode first asks only its exact `/get` endpoint and runs the full search across all providers just for tracks that are not settled that way, and it waits `api.batch_delay_seconds` between tracks. Batch mode never prompts: a path it cannot use ends the run with exit code 2.

Exit codes: `0` success or quit, `1` some files failed in batch mode, `2` unusable path, config or arguments, `130` aborted with Ctrl+C.

---

## Keybindings

**Tree overview (entry screen)** — `1`–`N` jump to track · `Enter` start with the first track, or after `t` continue with the current one · `a` bulk actions · `c` change directory · `q` quit

**Bulk actions** (`a` in the tree overview) run one action over all tracks or a selection such as `1-5,8`, without asking per track:

| Key | Action |
|---|---|
| `1` | Fetch lyrics for tracks without lyrics — the same confident-match rules as `--auto` |
| `2` | Romanize embedded lyrics: detected script per track, Han characters read as Chinese unless the context says Japanese, layout from `settings.romanization_layout`. Latin lyrics and lyrics romanized before are skipped |
| `3` | Write `.lrc` sidecars from the embedded lyrics |
| `4` | Delete embedded lyrics (asks once), optionally with their sidecars |

Lyrics that `2` or `4` replace, and sidecars that `3` overwrites, are backed up to `$XDG_STATE_HOME/lyrembed/backups/<time>/` first. Ctrl+C stops the run; the remaining tracks are left untouched.

**Search results**

| Key | Action |
|---|---|
| `Enter` | Save the highlighted entry — shown only when one is highlighted. With `settings.enter_opens_preview` it opens the entry's preview and options instead, like its number |
| `1`–`N` | Inspect and select a candidate |
| `m` | Adjust the search query (pre-filled) |
| `R` | Repeat the search, bypassing the cache |
| `u` | Show / hide results for other titles or artists — offered only when some were hidden |
| `a` | Search again with the artist's aliases too — offered when they were not searched yet |
| `Ctrl+C` | While searching: cancel the search and stay on the track |
| `n` | Create lyrics from scratch in your editor |
| `p` / `s` | Previous / next track |
| `t` | Back to the tree overview |
| `q` | Quit |

`✓exact` marks a record LRCLIB returned as a direct match rather than a search hit; the duration column shows separately whether the length was verified (`Duration OK` / `±Ns`), or just the candidate's own length when the track's length is unknown. `[MEGALOBIZ]` results come from Megalobiz, a site where users sync lyrics themselves: often several versions of one song, also for little-known songs, of varying quality. It is only asked when LRCLIB, NetEase and QQ Music find no synced lyrics that fit, because its free API allows 30 requests a minute and 1,000 a day (`api.megalobiz_api_key` raises that); after a refusal it is skipped until its limit resets. Its page for the lyrics is shown when you inspect them. `[SYNCEDLYRICS]` results come from the `syncedlyrics` engine, which tries Musixmatch and Genius (`api.syncedlyrics_providers`) and returns bare text; `?unverified` marks them, since they carry no artist or title to check against. By default the engine only runs when LRCLIB, NetEase and QQ Music find no synced lyrics that fit (`api.syncedlyrics_as_fallback`). As of October 2026 neither source answers through syncedlyrics 1.0.1, its latest release: Musixmatch returns the same unrelated songs for every search, Genius refuses the requests. NetEase results may come in a second variant marked `user romanization` — the romanized lyrics NetEase users submitted. `[QQMUSIC]` results come from QQ Music, whose catalogue is strong in Chinese, Korean and Japanese music; it often has lyrics when LRCLIB and NetEase do not.

Only results that fit the track are listed: the artist must match (aliases included), and the title too — or, for a title in another language such as `사랑을 했다` vs `LOVE SCENARIO`, the length. The rest is hidden behind `u` and marked `≠artist` / `≠title` when shown; NetEase and QQ Music in particular pad searches without good hits with unrelated popular songs, whose lyrics are not even fetched; it does the same when it throttles a client that sent too many requests, which lyrembed then reports as a provider notice (`R` retries). To stay below that limit, NetEase and QQ Music get one search at a time and at most three lyrics requests per round. Version notes count: the sung version is no match for `Song (Inst.)`, even as LRCLIB's exact match, and an instrumental version is no match for the sung track even when its length fits.

**Instrumental tracks.** A `♪` notice says when a track probably has no lyrics, and why:

* the title carries a version note such as `(Inst.)`, `- Instrumental`, `(Off Vocal)`, `(MR)`, `(Karaoke)`, `オフボーカル`, `伴奏`;
* the language tag is `zxx` ("no lyrics", written by MusicBrainz Picard);
* LRCLIB marks the track's record as instrumental, NetEase marks it as `pureMusic` (its "lyrics" are then only the notice `纯音乐，请欣赏`, which is no longer listed), or QQ Music answers with its notice `此歌曲为没有填词的纯音乐，请您欣赏`;
* with `api.enable_musicbrainz`, MusicBrainz says so for the file's recording ID (tagged by Picard): an instrumental performance, or a work whose lyrics language is "no lyrics". Its data is incomplete, so silence there means nothing.

Batch mode and the bulk fetch skip such tracks and report them as instrumental; tracks recognised by their tags are not even searched.

The entry `Enter` would save is marked `▶` and named in full above the menu — after a search the one batch mode would pick (synced, artist and title fit, length within tolerance), otherwise an exact match on top; later the one you last inspected. Synced lyrics are listed above plain ones, even above LRCLIB's exact match when that has plain lyrics only (`settings.prefer_synced`).

**Inspection** (candidate or manual entry)

| Key | Action |
|---|---|
| `v` | Full preview (all lines) |
| `e` | Open in your editor |
| `r` | Romanize (detected script offered as default) |
| `Enter` | Apply and embed (`y` works too) |
| `b` | Back to the candidate list (cached, no new requests) |
| `q` | Quit |

**Tag management** (track already has lyrics)

| Key | Action |
|---|---|
| `v` / `e` / `r` | Full preview · editor · romanize |
| `w` | Save pending changes to the file |
| `z` | Revert to the embedded version |
| `d` | Delete the lyrics tag from the file |
| `o` | Ignore the existing tag and search online |
| `p` / `s` / `t` | Previous track · next track · tree overview |
| `q` | Quit |

Unsaved edits are marked `(modified, unsaved)` in the header; navigating away asks before discarding them.

---

## Romanization of mixed-script lyrics

Each line is split into runs of one script, and only non-Latin runs are converted — text that is already Latin is never touched, so passes can be combined without destroying each other's output.

Hangul and Kana are unambiguous. A Han run is read as Chinese when it contains a character that no Japanese character set standard includes, or one pykakasi has no reading for; as Japanese when Kana touches it or it contains a character only Japanese standards include (shinjitai such as `駅` or `気`). Which systems use a character is looked up in the GB2312, Big5 and JIS character sets that ship with Python, not in a hand-kept list. Anything still ambiguous follows the choice you make when starting the conversion.

Pick **Mixed scripts** in the `r` menu; it is preselected when two different Asian scripts share the text, and search results with such lyrics carry a `[MIXED]` badge instead of `[ORIG]`. A line like `사랑해 我爱你 ありがとう` becomes `saranghae wǒ ài nǐ arigatou` in one pass. After choosing the script you choose the layout: replace each line, or keep it and add the romanized line below with the same timestamp. `settings.romanization_layout` sets which of the two Enter picks.

Romanization never drops characters: pykakasi silently deletes characters it cannot read (and sometimes the one after them), so those are converted with pypinyin — or anyascii — instead.

### Limitations

* **Runs of purely shared characters cannot be resolved automatically.** `月光下的思念` and `東京物語` are valid in either reading and fall back to your choice. Roughly four in five runs resolve on their own in typical lyrics; traditional Chinese resolves less often than simplified.
* **Japanese word spacing is approximate.** Verb and adjective endings stay attached (`愛してる` → `itoshiteru`), but a particle following a Kana-ending compound may be attached too: `ずっと一緒にいたい` → `zutto isshoniitai`.
* **Kanji readings come from pykakasi** and are not context-aware for names or unusual readings — `君` becomes `kun`, not `kimi`.
* **Provider credit headers** such as NetEase's `作词 : …` are removed from NetEase and QQ Music lyrics, as are the header tags QQ Music and Megalobiz put above their lyrics (`[ti:]`, `[ar:]`, `[al:]`, `[by:]`, `[length:]`, `[kana:]` and the like; a non-zero `[offset:]` stays) and QQ Music's opening `Title - Artist` line. Typed into other lyrics by hand, they are ignored when detecting scripts but still passed through the conversion.

---

## Editor

Taken from `$VISUAL`, then `$EDITOR`, then `settings.default_editor`. Commands with arguments are supported:

```bash
export EDITOR="code --wait"     # or: nvim, "subl -w", …
```
```powershell
$env:EDITOR = "notepad"
```

A non-zero exit status (for example `:cq` in Vim) is treated as a normal user action and the file is still read back. If the editor cannot be launched, you get a message and stay in the menu. An editor that returns within a second without changes is reported too: that is usually a GUI editor started without its wait flag (`code --wait`, `subl -w`), whose edits would otherwise be lost.

---

## Configuration

Lookup order: `--config FILE` → `$XDG_CONFIG_HOME/lyrembed/config.json` (falling back to `~/.config/lyrembed/config.json`) → `~/.config/lrckit/config.json` from before the rename, with a reminder to move it → `config.json` in the root of a source checkout.

User values are merged **recursively** over the built-in defaults, so a file with only the keys you care about is valid:

```json
{
  "settings": {
    "prefer_latin": true,
    "write_sidecar_lrc": true
  }
}
```

An invalid file found on the search paths produces a warning and the defaults are used; a file given with `--config` must be valid, or the run stops. Values of the wrong type or outside their range (`max_workers: 0`, a `color` other than `auto`/`always`/`never`), alias groups that are not lists, and unknown keys (typos) are reported, and the default is used for them. `config.example.json` is a reference, not a config: lyrembed never reads it. It lists every key with its default value and, in the `_key` entry above it, what the key does. Copy only what you want to change into your own file. Keys starting with `_` are treated as comments, so copying a block with its explanations works too.

**`aliases`** — your own groups of names for the same artist, e.g. `[["Hoshino Gen", "星野源"]]`. They are added to the about 200 built-in groups in [`lyrembed/aliases.json`](lyrembed/aliases.json) (K-pop groups and soloists, Japanese, Chinese, and spellings with and without diacritics); a group sharing a name with a built-in one is merged into it. Aliases let a result filed under another name count as the right artist, and add searches under the other names: by default only when the tag's artist finds no synced lyrics that fit, or when you press `a` in the search results; with `settings.search_all_aliases` always. Names match whole words, ignoring case. `lyrembed --list-aliases` prints every group in effect.

**`cleaning`** — `feature_regex`, `trailing_feature_regex` and the `korean_`/`japanese_`/`chinese_bracket_regex` patterns removed from search queries, plus `ignore_words_artist_match`. Set `strip_all_brackets` to `true` to drop everything in `(…)` and `[…]` from title, artist and album before searching — useful when tags list individual members behind the group, as in `Group ("Member A", "Member B")`. It also drops parts such as `(Live)` or `[Remastered]`; a title or artist written entirely in brackets is searched as it is.

**`api`**

| Key | Meaning |
|---|---|
| `lrclib_url`, `netease_search_url`, `netease_lyric_url`, `qqmusic_search_url`, `qqmusic_lyric_url`, `megalobiz_url` | Provider endpoints. NetEase's `/api/search/pc` is the default: `/api/search/get` (the earlier default) answers anonymous clients with unrelated popular songs, and the older `/api/search/get/web` answers encrypted |
| `timeout_seconds` | Per-request timeout |
| `netease_search_limit` | Results requested per NetEase query |
| `user_agent` | Sent to LRCLIB; identify your fork by name, version and URL. Defaults to the current version, so leave it out unless you change it |
| `max_workers` | Parallel HTTP requests per provider stage |
| `retry_total`, `retry_backoff` | Automatic retries on 5xx server errors. A rate limit (429) is not retried but reported; batch mode then waits and tries the track once more |
| `duration_tolerance_seconds` | Maximum length difference accepted by `--auto` |
| `syncedlyrics_timeout_seconds` | How long to wait for the `syncedlyrics` engine, which has no timeout of its own |
| `syncedlyrics_as_fallback` | `true` (default) runs the engine only when LRCLIB, NetEase and QQ Music find no synced lyrics that fit; `false` with every search |
| `syncedlyrics_providers` | The engine's sources, default `["Musixmatch", "Genius"]`. LRCLIB, NetEase and Megalobiz are left out because lyrembed asks them itself; `[]` allows all |
| `batch_delay_seconds` | Pause between tracks in `--auto` mode |
| `enable_lrclib`, `enable_netease`, `enable_qqmusic`, `enable_megalobiz`, `enable_syncedlyrics` | Turn individual providers off |
| `megalobiz_api_key` | Optional free key from megalobiz.com/developers; raises Megalobiz's limits from 30 requests a minute and 1,000 a day per IP |
| `enable_musicbrainz` | `true` asks MusicBrainz whether a track is instrumental, for files with a MusicBrainz recording ID. Off by default: one extra request per track, at most one per second |
| `musicbrainz_url` | MusicBrainz API base URL |

**`settings`**

| Key | Meaning |
|---|---|
| `supported_extensions` | File types included in a scan; only `.flac`, `.mp3`, `.ogg`, `.opus` and `.m4a` can be tagged, others are ignored with a warning |
| `default_editor` | Fallback when `$EDITOR` and `$VISUAL` are unset |
| `preview_lines` | Lines shown in the short preview |
| `non_latin_ratio_threshold` | Share of lyric lines (`0.1` = 10%) that must contain non-Latin letters for lyrics to count as original-script. Counted per line because one Hangul or Han character holds a whole syllable; LRC tags and credit lines are ignored |
| `max_search_depth` | Directory levels to descend. `3` collects files up to three levels below the start directory; deeper directories are not entered. |
| `max_file_count` | Abort the scan above this many files and ask for a narrower path; `0` for no limit |
| `search_cache_entries` | How many past searches to keep in memory for the session |
| `enter_opens_preview` | `true` makes Enter on the highlighted search result open its preview and options first; `false` (default) saves it at once |
| `prefer_synced` | `true` (default) ranks synced lyrics above plain ones, also above an exact match with plain lyrics only; `false` puts the exact match first |
| `prefer_latin` | `true` ranks romanized versions first; `false` (default) leaves the script out of the ranking |
| `sort_by_tags` | `true` orders by disc/track number; `false` uses natural filename order (`Track 2` before `Track 10`) |
| `write_sidecar_lrc` | Also write a companion `.lrc` next to each track |
| `id3_v2_version` | ID3v2 version for MP3s that have no tag yet: `4` (default) or `3` for players that only read ID3v2.3. Existing tags keep their version |
| `uslt_language` | ISO-639-2 code stored in the MP3 `USLT` frame |
| `search_all_aliases` | `true` searches every alias of the artist right away. `false` (default) searches the tag's artist first and the aliases only when that finds no synced lyrics that fit, or on `a` — each alias costs several more requests |
| `prefer_album_artist` | `true` searches with the album artist instead of the track artist, for tags that list every member or guest per track. Compilations (`Various Artists`) and tracks without an album artist keep the track artist |
| `romanization_layout` | What Enter picks at the romanization layout prompt: `replace` (default) or `below` to keep the original line with the romanized one below it |
| `auto_romanize` | `true` makes batch mode (`--auto`) romanize the lyrics it embeds, like the bulk action: detected script, layout from `romanization_layout`, Latin lyrics left alone. `false` (default) embeds them as found. `--romanize` / `--no-romanize` override it |
| `color` | `auto`, `always` or `never` |

---

## Tagging

Lyrics are written into standard metadata containers; audio streams are never transcoded.

* **FLAC, OGG Vorbis, Opus** — Vorbis comment `LYRICS`. Competing `UNSYNCEDLYRICS` and `SYNCEDLYRICS` fields are removed on write.
* **MP3** — ID3v2 `USLT` frame, UTF-8 (`encoding=3`). The file's ID3v2 version is kept; new tags use `id3_v2_version`. Synchronized `SYLT` frames are read (millisecond timestamps) and replaced on write, so players do not keep showing the old ones.
* **M4A / AAC / ALAC** — MP4 atom `\xa9lyr` (`©lyr`).
* **Sidecar** — with `write_sidecar_lrc` or `--sidecar`, a `<track>.lrc` is written alongside the audio file. Without it, an existing `<track>.lrc` is left alone but reported, since many players prefer it over the embedded tag; deleting a track's lyrics offers to delete it too.

Embedded tags are recognized by Navidrome, Jellyfin, Symfonium, Feishin and foobar2000.

---

## Development

```
run.py                   Launcher for checkouts
lyrembed/
  cli.py                 Argument parsing and start-up
  app.py                 Directory scanning, navigation, menus, batch mode
  providers.py           LRCLIB / NetEase / QQ Music / Megalobiz / syncedlyrics, ranking
  tagging.py             Metadata reading, writing, caching
  text.py                Tag cleaning, artist matching, romanization
  aliases.json           Built-in artist alias groups
  editor.py              External editor handling
  ui.py                  Colours, prompts, previews, path completion
  config.py              Defaults and recursive config merging
tests/                   pytest regression suite
```

```bash
pip install -e ".[all,dev]"
pytest -q
python -m pyflakes lyrembed/ tests/ run.py
```

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## License

MIT. Free for private and commercial use, modification and distribution.
