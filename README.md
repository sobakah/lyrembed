# lyrembed

Vibecoded tool for Linux, macOS and Windows to search, check, romanize and embed synchronized (`.lrc`) and plain lyrics into FLAC, MP3, OGG Vorbis, Opus and M4A files, with a focus on East Asian music. You work on a page in your browser that only your own computer can reach; batch mode runs in the terminal.

---

## Features

* **A page in your browser** — the folder's tracks with their covers and lyrics status, search results side by side with a preview, an editor, bulk actions and every setting. It runs on `127.0.0.1` only, needs no internet access of its own and installs nothing in the browser.
* **Check lyrics while listening** — the editor plays the track and lights up the current line. Shift all lines by a few milliseconds, or set a single line's start while it plays; that also turns plain lyrics into synced ones. Switch between search results while the song keeps playing.
* **Multi-format tagging** — FLAC, MP3, OGG Vorbis, Opus and M4A, plus optional companion `.lrc` sidecar files.
* **Concurrent multi-provider search** — LRCLIB, NetEase Cloud Music and QQ Music are queried in parallel on a pooled HTTP session with retry and backoff; Megalobiz (community-synced lyrics, often several versions per song), the artist's aliases and the optional `syncedlyrics` engine follow only when that finds no synced lyrics.
* **Exact-match lookups** — LRCLIB's `/get` endpoint is queried with title, artist, album and duration; fuzzy `/search` queries run alongside as a fallback.
* **Confidence-aware ranking** — ordered by exact match, synchronization, script preference and duration agreement.
* **Smart tag cleaning & aliases** — strips features (`feat. …`), filters localised bracketed subtitles out of queries, and resolves group acronyms (*Tomorrow X Together* ↔ *TXT*) with word-boundary matching.
* **Romanization** with automatic script detection, preserving LRC timestamps: Hangul → Romaja, Kanji/Kana → Hepburn Rōmaji, Hanzi → Pīnyīn, and any other script → Latin via `anyascii`. Lines containing several scripts at once are split into runs and converted per run, and text that is already Latin is never touched — so passes can be combined without destroying each other's output.
* **Batch mode** — `--auto` tags every track with a confident match; `--dry-run` reports without writing, `--overwrite` backs up the lyrics it replaces.
* **Works with the other programs** — **Import a CD** hands the tab to [cdport](https://github.com/sobakah/cdport) and back; a player such as [Jewelcase](https://github.com/sobakah/jewelcase) gets romanization, pages and lyrics searches over [D-Bus](#d-bus).
* **Search results are cached for the session** — leaving a track and coming back to it reuses the previous result instead of querying the providers again.
* **Safeguards** — metadata cached by modification time, recursion depth and file-count caps, backups of every lyrics they replace, colour output honouring `NO_COLOR` and non-TTY pipes.

---

## Installation

### pipx (recommended)

```bash
pipx install git+https://github.com/sobakah/lyrembed.git
# with romanization and the extra provider engine:
pipx install "lyrembed[all] @ git+https://github.com/sobakah/lyrembed.git"
```

This installs a `lyrembed` command on your `PATH`. The page tells you about a newer release and installs it for you (see `check_for_updates` under [Configuration](#configuration)); `pipx upgrade lyrembed` does the same by hand.

> **fish users:** `pipx` and `pip install --user` place their launchers in `~/.local/bin`. On Fedora that folder is added to `PATH` by `~/.bash_profile`, which fish never reads — so the install succeeds but `lyrembed` is not found. Add it once with `fish_add_path ~/.local/bin` (fish 3.2+); it persists across sessions. In a virtual environment, use `source venv/bin/activate.fish` rather than `activate`.

### From a checkout

```bash
git clone https://github.com/sobakah/lyrembed.git
cd lyrembed
pip install -e ".[all]"
```

The checkout also runs without installation: `./run.py ~/Music/Artist/Album`. `run.py` is only a launcher and needs the `lyrembed/` package folder next to it.

### Fedora Silverblue, Bluefin and other image-based systems

lyrembed runs on the host: Silverblue's image has Python, git and ffmpeg, only pipx is missing.
pipx installs itself without changing the image:

```bash
curl -LO https://github.com/pypa/pipx/releases/latest/download/pipx.pyz
python3 pipx.pyz install pipx && rm pipx.pyz
~/.local/bin/pipx install "lyrembed[all] @ git+https://github.com/sobakah/lyrembed.git"
```

(`rpm-ostree install pipx` and a restart work too.) cdport runs in a toolbox there (its README
says why). To import CDs from lyrembed's page, make lyrembed's app menu entry and D-Bus service
in cdport's toolbox (`lyrembed --install-desktop-entry`, `lyrembed --install-dbus-service`
inside it): they then start lyrembed in the container (`toolbox run --container NAME …`), where
*Import a CD* finds cdport, and its page opens in the host's browser. pipx's folders are in the
home folder, which the host and its toolboxes share, so one install serves both while they run
the same Fedora version.

### Dependencies

Required: Python 3.10 or newer, `mutagen`, `requests`, and a current browser (Firefox, Chrome, Edge, Safari). `jeepney` is installed with them for the [D-Bus interface](#d-bus); without it only that is missing.

```bash
pip install mutagen requests jeepney
```

Optional. A missing package only disables that one feature and is reported where it is used:

```bash
pip install --user pykakasi pypinyin korean-romanizer anyascii syncedlyrics
```

| Package | Functionality |
|---|---|
| `syncedlyrics` | Scraping engine for Musixmatch and Genius (currently neither answers through it) |
| `korean-romanizer` | Hangul → Revised Romanization of Korean |
| `pykakasi` | Kanji and Kana → Hepburn Rōmaji |
| `pypinyin` | Hanzi → Pīnyīn with tone marks |
| `anyascii` | Any non-Latin Unicode script → ASCII |
| `ffmpeg` (program, not a Python package) | Listening to Apple Lossless (ALAC) tracks: browsers don't play ALAC, so lyrembed plays a temporary FLAC copy. AAC in M4A plays without it |

> **PEP 668 / externally-managed-environment:** if your package manager blocks global pip installs, use a virtual environment or append `--break-system-packages`.

---

## Usage

```bash
lyrembed                              # the page, current folder
lyrembed ~/Music/Artist/Album         # the page, specific album
lyrembed ~/Music/Artist --auto        # tag everything with a confident match
lyrembed ~/Music/Artist --auto --dry-run
```

Without `--auto`, lyrembed opens its page in your default browser and prints the page's address. Only this computer can open it, and only with the secret address lyrembed prints. Quit on the page or press Ctrl+C in the terminal; closing the tab ends lyrembed too, after a short while. If the folder can't be used (it doesn't exist, has no music, or is your home folder or a folder above it), the page asks for another one. Started without a folder in your home folder, as the app menu does, the folder picker opens in your music folder.

| Option | Effect |
|---|---|
| `path` | Music folder (default: the current folder) |
| `--version` | Print the version and exit |
| `-c`, `--config FILE` | Use this config file instead of the search paths; an unusable file is an error |
| `--list-aliases` | Print the artist alias groups in effect (built-in and your own) and exit |
| `--max-files N` | Scan at most `N` files, `0` for no limit (overrides `settings.max_file_count`) |
| `--no-browser` | Print the page's address instead of opening it |
| `--return-url URL` / `--return-name NAME` | For another program's page that opens lyrembed (cdport does): **Done – back to NAME** replaces Quit, ends lyrembed and sends the tab back to `URL`, which must be on this computer (`http://127.0.0.1:PORT/…`) |
| `--install-desktop-entry` | Add lyrembed to the app menu (Linux and BSD desktops), then exit; see [App menu](#app-menu) |
| `--remove-desktop-entry` | Remove it again, then exit |
| `--install-dbus-service` | Let the session bus start lyrembed when a program calls it, then exit; see [D-Bus](#d-bus) |
| `--remove-dbus-service` | Remove that again, then exit |
| `--dbus` | Serve the D-Bus interface until idle (what the session bus starts) |
| `--auto` | Batch mode: tag confident matches without a page and without prompting |
| `--overwrite` | With `--auto`, also replace existing lyrics; the old ones are backed up to `$XDG_STATE_HOME/lyrembed/backups/<time>/` (default `~/.local/state/…`) |
| `--dry-run` | With `--auto`, report changes without writing |
| `--romanize` / `--no-romanize` | With `--auto`, romanize the lyrics it embeds, or not; overrides `settings.auto_romanize` for this run |
| `--color {auto,always,never}` | Override colour handling |
| `-v`, `-vv` | Increase log verbosity (on stderr) |
| `--sidecar` / `--no-sidecar` | Force companion `.lrc` files on or off |

`--auto` only applies a candidate when it is synchronized, the artist matches, and the duration is within `api.duration_tolerance_seconds` — or the match came from LRCLIB's exact `/get` endpoint. Results from the `syncedlyrics` engine are never applied automatically, because it does not say which recording its lyrics belong to. Anything less confident is left for the page.

To go easy on LRCLIB, a free community service, batch mode first asks only its exact `/get` endpoint and runs the full search across all providers just for tracks that are not settled that way, and it waits `api.batch_delay_seconds` between tracks. Batch mode never prompts: a path it cannot use ends the run with exit code 2.

Exit codes: `0` success or quit, `1` some files failed in batch mode, `2` unusable path, config or arguments, `130` aborted with Ctrl+C.

### App menu

```bash
lyrembed --install-desktop-entry
```

adds lyrembed to the app menu of GNOME, KDE and other freedesktop.org desktops: `~/.local/share/applications/lyrembed.desktop` and an icon under `~/.local/share/icons/`. The entry starts the lyrembed that wrote it — a pipx installation, or the Python and `run.py` of a checkout — so run the command again after installing lyrembed somewhere else. Started from the menu, the page opens in your default browser with the folder picker in your music folder (`XDG_MUSIC_DIR`). `--remove-desktop-entry` removes both files.

---

## The page

**Library** — the folder's tracks with their length, format and lyrics status (`Synced`, `Plain`, `No lyrics`, `INSTRUMENTAL?`). A cover shared by every track is shown with the folder; otherwise each track, or each disc of a multi-disc album, shows its own. Every track can be played from the list; a bar at the bottom keeps playing while you move between tracks. **Change folder…** lists folders to choose from, since a browser can't hand out paths.

**A track** shows its embedded lyrics (**Check and edit**, **Search for other versions**, **Delete lyrics…**) or, without lyrics, the search results right away. Notices from the providers, and searches beyond the first round, are shown above the results.

* **Best match** marks the entry batch mode would pick (synced, artist and title fit, length within tolerance); otherwise **Exact match** marks a record LRCLIB returned as a direct match rather than a search hit. Synced lyrics are listed above plain ones, even above LRCLIB's exact match when that has plain lyrics only (`settings.prefer_synced`).
* Each result shows whether its length was verified (`length OK` / `±N s`), or just its own length when the track's length is unknown.
* **Megalobiz** results come from a site where users sync lyrics themselves: often several versions of one song, also for little-known songs, of varying quality. It is only asked when LRCLIB, NetEase and QQ Music find no synced lyrics that fit, because its free API allows 30 requests a minute and 1,000 a day (`api.megalobiz_api_key` raises that); after a refusal it is skipped until its limit resets. The preview links to the lyrics' page there.
* **syncedlyrics** results come from the `syncedlyrics` engine, which tries Musixmatch and Genius (`api.syncedlyrics_providers`) and returns bare text; they are marked *not verified*, since they carry no artist or title to check against. By default the engine only runs when LRCLIB, NetEase and QQ Music find no synced lyrics that fit (`api.syncedlyrics_as_fallback`). Currently neither source answers through syncedlyrics: Musixmatch returns the same unrelated songs for every search, Genius refuses the requests.
* NetEase results may come in a second variant marked `user romanization` — the romanized lyrics NetEase users submitted. **QQ Music** is strong in Chinese, Korean and Japanese music; it often has lyrics when LRCLIB and NetEase do not.
* **Romanize when saving**, in the preview, romanizes before both Save buttons the way batch mode does with `settings.auto_romanize` (detected script, layout from `settings.romanization_layout`), without opening the editor. It starts as that setting says and stays as set while you work through the album.
* **Search with other terms** searches for another title or artist, **Search again, without the cache** asks the providers again, **Also search as …** adds the artist's aliases, and **Type or paste lyrics yourself** opens an empty editor.

Only results that fit the track are listed: the artist must match (aliases included), and the title too — or, for a title in another language (a Korean title in the tags, its English one at the provider), the length. The rest sits behind **Show N results for other titles or artists** and is marked `≠ artist` / `≠ title`. NetEase and QQ Music in particular pad searches without good hits with unrelated popular songs, whose lyrics are not even fetched; NetEase does the same when it throttles a client that sent too many requests, which lyrembed then reports as a provider notice. To stay below that limit, NetEase and QQ Music get one search at a time and at most three lyrics requests per round. Version notes count: the sung version is no match for `Song (Inst.)`, even as LRCLIB's exact match, and an instrumental version is no match for the sung track even when its length fits.

**Instrumental tracks.** A `♪` notice says when a track probably has no lyrics, and why:

* the title carries a version note such as `(Inst.)`, `- Instrumental`, `(Off Vocal)`, `(MR)`, `(Karaoke)`, `オフボーカル`, `伴奏`;
* the language tag is `zxx` ("no lyrics", written by MusicBrainz Picard);
* LRCLIB marks the track's record as instrumental, NetEase marks it as `pureMusic` (its "lyrics" are then only the notice `纯音乐，请欣赏`, which is no longer listed), or QQ Music answers with its notice `此歌曲为没有填词的纯音乐，请您欣赏`;
* with `api.enable_musicbrainz`, MusicBrainz says so for the file's recording ID (tagged by Picard): an instrumental performance, or a work whose lyrics language is "no lyrics". Its data is incomplete, so silence there means nothing.

Batch mode and the bulk fetch skip such tracks and report them as instrumental; tracks recognised by their tags are not even searched.

**The editor** (**Check and edit**) plays the track and lights up the line being sung; click a line to jump there. Nothing is written to the file until you save.

* **Timing** moves all lines by −250, −50, +50 or +250 ms (negative is earlier). The shift is written into the timestamps when you save; a file's `[offset:]` tag is folded in and removed, because players treat that tag differently.
* **Now** (or N) starts the chosen line at the current position and selects the next one, so you can fix a late line, or sync plain lyrics, while listening. A romanized line below its original moves with it.
* **Version** switches between the search results while the song keeps playing.
* **Romanize…** and **Edit as text** change the text itself; **Undo changes** returns to where you started.
* Keys: Space plays and pauses, ← and → jump 5 s, N starts the chosen line, Ctrl+S saves. Leaving with unsaved changes asks first.

**Import a CD** appears when [cdport](https://github.com/sobakah/cdport) is installed. It opens cdport's page in the same tab to rip a disc; cdport's lyrics step opens the album on a lyrembed page of its own, and at the end **Done – back to lyrembed** brings the tab back here with the imported album open.

**Bulk actions** run one action on many tracks, picked from a list (all, none, with or without lyrics), and show each track's result as it comes; each result leads to its track. Stop takes effect after the current track.

| Action | What it does |
|---|---|
| Fetch lyrics automatically | The same confident-match rules as `--auto`, optionally replacing existing lyrics, as a dry run, or romanizing what it saves |
| Romanize embedded lyrics | Detected script per track, Han characters read as Chinese unless the context says Japanese, layout from `settings.romanization_layout`. Latin lyrics and lyrics romanized before are skipped |
| Extract embedded lyrics to .lrc files | A `.lrc` file next to each track; **Extract to .lrc** on a track's page does the same for one track |
| Delete embedded lyrics | Asks once, optionally with their `.lrc` files |

Lyrics the actions replace or delete, and `.lrc` files they overwrite, are backed up to `$XDG_STATE_HOME/lyrembed/backups/<time>/` first.

**Settings** lists every setting of the config file with its explanation, checks values before saving, and writes only what you changed (see [Configuration](#configuration)). Your own artist aliases are edited there too, one group per line.

---

## Romanization of mixed-script lyrics

Each line is split into runs of one script, and only non-Latin runs are converted — text that is already Latin is never touched, so passes can be combined without destroying each other's output.

Hangul and Kana are unambiguous. A Han run is read as Chinese when it contains a character that no Japanese character set standard includes, or one pykakasi has no reading for; as Japanese when Kana touches it or it contains a character only Japanese standards include (shinjitai such as `駅` or `気`). Which systems use a character is looked up in the GB2312, Big5 and JIS character sets that ship with Python, not in a hand-kept list. Anything still ambiguous follows the choice you make when starting the conversion.

Pick **Mixed scripts** under **Romanize…** in the editor; it is preselected when two different Asian scripts share the text, and search results with such lyrics carry a `MIXED` badge instead of `ORIG`. A line like `사랑해 我爱你 ありがとう` becomes `saranghae wǒ ài nǐ arigatou` in one pass. After choosing the script you choose the layout: replace each line, or keep it and add the romanized line below with the same timestamp. `settings.romanization_layout` sets which of the two is preselected.

Romanization never drops characters: pykakasi silently deletes characters it cannot read (and sometimes the one after them), so those are converted with pypinyin — or anyascii — instead.

---

## Tagging

Lyrics are written into standard metadata containers; audio streams are never transcoded.

* **FLAC, OGG Vorbis, Opus** — Vorbis comment `LYRICS`. Competing `UNSYNCEDLYRICS` and `SYNCEDLYRICS` fields are removed on write.
* **MP3** — ID3v2 `USLT` frame, UTF-8 (`encoding=3`). The file's ID3v2 version is kept; new tags use `id3_v2_version`. Synchronized `SYLT` frames are read (millisecond timestamps) and replaced on write, so players do not keep showing the old ones.
* **M4A / AAC / ALAC** — MP4 atom `\xa9lyr` (`©lyr`).
* **Sidecar** — with `write_sidecar_lrc` or `--sidecar`, a `<track>.lrc` is written alongside the audio file. Without it, an existing `<track>.lrc` is left alone but reported, since many players prefer it over the embedded tag; deleting a track's lyrics offers to delete it too.
* **Covers** are only read, never written: the front cover embedded in the file, or a `cover`/`folder`/`front` image next to it.

Embedded tags are recognised by Navidrome, Jellyfin, Symfonium, Feishin and foobar2000.

---

## Configuration

Lookup order: `--config FILE` → `$XDG_CONFIG_HOME/lyrembed/config.json` (falling back to `~/.config/lyrembed/config.json`) → `config.json` in the root of a source checkout.

User values are merged **recursively** over the built-in defaults, so a file with only the keys you care about is valid:

```json
{
  "settings": {
    "prefer_latin": true,
    "write_sidecar_lrc": true
  }
}
```

The **Settings** page writes to the file in effect (or creates `~/.config/lyrembed/config.json`): only the keys you change, keeping the rest of the file and its `_` comments, with the previous version kept as `config.json.bak`. It never overwrites a file that is not valid JSON.

An invalid file found on the search paths produces a warning and the defaults are used; a file given with `--config` must be valid, or the run stops. Values of the wrong type or outside their range (`max_workers: 0`, a `color` other than `auto`/`always`/`never`), alias groups that are not lists, and unknown keys (typos) are reported, and the default is used for them. `config.example.json` is a reference, not a config: lyrembed never reads it. It lists every key with its default value and, in the `_key` entry above it, what the key does. Copy only what you want to change into your own file. Keys starting with `_` are treated as comments, so copying a block with its explanations works too.

**`aliases`** — your own groups of names for the same artist, e.g. `[["Hoshino Gen", "星野源"]]`. They are added to the about 200 built-in groups in [`lyrembed/aliases.json`](lyrembed/aliases.json) (K-pop groups and soloists, Japanese, Chinese, and spellings with and without diacritics); a group sharing a name with a built-in one is merged into it. Aliases let a result filed under another name count as the right artist, and add searches under the other names: by default only when the tag's artist finds no synced lyrics that fit, or on **Also search as …** in the search results; with `settings.search_all_aliases` always. Names match whole words, ignoring case. `lyrembed --list-aliases` prints every group in effect.

**`cleaning`** — `feature_regex`, `trailing_feature_regex` and the `korean_`/`japanese_`/`chinese_bracket_regex` patterns removed from search queries, plus `ignore_words_artist_match`. Set `strip_all_brackets` to `true` to drop everything in `(…)` and `[…]` from title, artist and album before searching — useful when tags list individual members behind the group, as in `Group ("Member A", "Member B")`. It also drops parts such as `(Live)` or `[Remastered]`; a title or artist written entirely in brackets is searched as it is.

**`api`**

| Key | Meaning |
|---|---|
| `lrclib_url`, `netease_search_url`, `netease_lyric_url`, `qqmusic_search_url`, `qqmusic_lyric_url`, `megalobiz_url` | Provider endpoints. NetEase's default is `/api/search/pc`: `/api/search/get` answers anonymous clients with unrelated popular songs, and `/api/search/get/web` answers encrypted |
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
| `non_latin_ratio_threshold` | Share of lyric lines (`0.1` = 10%) that must contain non-Latin letters for lyrics to count as original-script. Counted per line because one Hangul or Han character holds a whole syllable; LRC tags and credit lines are ignored |
| `max_search_depth` | Folder levels to descend. `3` collects files up to three levels below the start folder; deeper folders are not entered. |
| `max_file_count` | Refuse a folder with more than this many files and ask for a narrower one; `0` for no limit |
| `search_cache_entries` | How many past searches to keep in memory for the session |
| `prefer_synced` | `true` (default) ranks synced lyrics above plain ones, also above an exact match with plain lyrics only; `false` puts the exact match first |
| `prefer_latin` | `true` ranks romanized versions first; `false` (default) leaves the script out of the ranking |
| `sort_by_tags` | `true` orders by disc/track number; `false` uses natural filename order (`Track 2` before `Track 10`) |
| `write_sidecar_lrc` | Also write a companion `.lrc` next to each track |
| `id3_v2_version` | ID3v2 version for MP3s that have no tag yet: `4` (default) or `3` for players that only read ID3v2.3. Existing tags keep their version |
| `uslt_language` | ISO-639-2 code stored in the MP3 `USLT` frame |
| `search_all_aliases` | `true` searches every alias of the artist right away. `false` (default) searches the tag's artist first and the aliases only when that finds no synced lyrics that fit, or on request — each alias costs several more requests |
| `prefer_album_artist` | `true` searches with the album artist instead of the track artist, for tags that list every member or guest per track. Compilations (`Various Artists`) and tracks without an album artist keep the track artist |
| `romanization_layout` | `replace` (default) or `below` to keep the original line with the romanized one below it: preselected when romanizing on the page, and used by the bulk action and batch mode |
| `auto_romanize` | `true` makes batch mode (`--auto`) romanize the lyrics it embeds, like the bulk action: detected script, layout from `romanization_layout`, Latin lyrics left alone. `false` (default) embeds them as found. `--romanize` / `--no-romanize` override it |
| `color` | Colours in the terminal output: `auto`, `always` or `never` |
| `check_for_updates` | `true` (default): the page looks for a newer lyrembed every time it starts, in the background so the start doesn't wait, through git with the access `pipx install` has; Settings has **Check for updates** for any time. Installed with pipx, **Update** runs `pipx upgrade lyrembed` (or `pipx install --force …@<new tag>` for an install pinned to a release) and **Restart lyrembed** starts the new version on the same address, so the open tab just reloads. Batch mode and a lyrembed opened by cdport never look |

---

## Working with other programs

[cdport](https://github.com/sobakah/cdport) rips and tags CDs; its lyrics step opens the album on a lyrembed page with `--return-url`, so **Done – back to cdport** returns the tab, and lyrembed's **Import a CD** starts cdport the other way round (see [The page](#the-page)).

### Romanizing for other programs

`lyrembed romanize --stdio` romanizes lyrics for display in another program, such as the music player [Jewelcase](https://github.com/sobakah/jewelcase), which shows the romanization under each original line. It reads one JSON object per line from standard input and answers each with one line on standard output, until the input ends:

```
→ {"id": 1, "lyrics": "[00:01.00]사랑해요", "han_hint": null}
← {"id": 1, "lyrics": "[00:01.00]사랑해요\n[00:01.00]saranghaeyo", "language": "ko", "reason": ""}
```

The romanized line always goes below the original, whatever `settings.romanization_layout` says, and timestamps are kept. `han_hint` (`"ja"`, `"zh"` or `null`) is what the program knows about ambiguous Han characters, for example from a `LANGUAGE` tag: with `"ja"`, Han characters that nothing in the lyrics decides are read as Japanese, as Kana elsewhere in the song would make them. Characters only Chinese uses still count as Chinese. Lyrics that need nothing (Latin, already romanized) come back as `null` with the reason, and a line that isn't a request gets an `error`. Nothing is written and nothing goes over the network.

### D-Bus

```bash
lyrembed --install-dbus-service
```

lets the session bus start lyrembed when a program calls it on `io.github.sobakah.lyrembed`. That is how a music player such as Jewelcase can use lyrembed also from inside a Flatpak, which can neither start host programs nor read lyrembed's settings (it needs `--talk-name=io.github.sobakah.lyrembed`). The command writes `~/.local/share/dbus-1/services/io.github.sobakah.lyrembed.service`, starting the lyrembed that wrote it with `--dbus`; run it again after installing lyrembed somewhere else. `--remove-dbus-service` removes it.

The interface (`io.github.sobakah.lyrembed1` on `/io/github/sobakah/lyrembed`):

| Call | What it does |
|---|---|
| `GetInfo() → a{sv}` | `Version`, `InterfaceVersion` (1), `MusicFolder` (empty when there is none), `RomanizationLayout` and `MaxFileCount` from the settings, `Romanizes` (the languages whose packages are installed: `ko`, `ja`, `zh`, `any`) |
| `ArtistAliases() → aas` | the artist alias groups in effect, as `--list-aliases` shows them |
| `Romanize(s lyrics, s han_hint) → (s lyrics, s language, s reason)` | as `romanize --stdio`; `han_hint` is `""`, `"ja"` or `"zh"`, and `""` in the answer stands for `null` |
| `OpenPage(s folder) → (s run, b opened)` | starts lyrembed's page on the folder (`""`: the folder picker) and opens it in your default browser; `opened` is false when no browser could be started. A page already open on that folder is shown again |
| `ShowPage(s run) → b` | opens a page in the browser again, for a lost tab |
| `FetchLyrics(s folder, a{sv} options) → s run` | `--auto` on a folder inside your music folder (`XDG_MUSIC_DIR` or `~/Music`); option `Romanize` (`b`, else `settings.auto_romanize`) |
| `Runs() → a(sss)` | the runs still going: run, kind (`page` or `fetch`), the folder it was started on |
| `Stop(s run)` | as Ctrl+C: the current track finishes first |
| signal `Finished(s run, i exit_code)` | a run ended, with lyrembed's [exit code](#usage) |

It has no call that runs a command, and a page's address never goes over the bus: its secret token opens everything the page can do, so lyrembed opens the browser itself. A page may open any folder, since you decide on it what is written; an unattended `FetchLyrics` only works inside your music folder. So the interface gives a sandboxed program no way out. A page nobody opens ends after ten minutes. Without calls or runs for five minutes, the service ends, and the bus starts it again at the next call.

```bash
gdbus call --session -d io.github.sobakah.lyrembed -o /io/github/sobakah/lyrembed \
  -m io.github.sobakah.lyrembed1.Romanize '[00:01.00]사랑해요' ''
```

---

## Limitations

* **Han runs of purely shared characters cannot be resolved automatically.** `月光下的思念` and `東京物語` are valid in either reading and fall back to your choice. Roughly four in five runs resolve on their own in typical lyrics; traditional Chinese resolves less often than simplified.
* **Japanese word spacing is approximate.** Verb and adjective endings stay attached (`愛してる` → `itoshiteru`), but a particle following a Kana-ending compound may be attached too: `ずっと一緒にいたい` → `zutto isshoniitai`.
* **Kanji readings come from pykakasi** and are not context-aware for names or unusual readings — `君` becomes `kun`, not `kimi`.
* **Provider credit headers** such as NetEase's `作词 : …` are removed from NetEase and QQ Music lyrics, as are the header tags QQ Music and Megalobiz put above their lyrics (`[ti:]`, `[ar:]`, `[al:]`, `[by:]`, `[length:]`, `[kana:]` and the like; a non-zero `[offset:]` stays) and QQ Music's opening `Title - Artist` line. Typed into other lyrics by hand, they are ignored when detecting scripts but still passed through the conversion.

---

## License

MIT. Free for private and commercial use, modification and distribution.
