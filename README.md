# lrckit

Vibecoded interactive CLI utility for Linux, macOS and Windows to search, inspect, romanize and embed synchronized (`.lrc`) and plain lyrics into FLAC, MP3, OGG Vorbis, Opus and M4A files with a focus on east asian music.

Version 0.3.0 · Python 3.10+ · MIT

---

## Features

* **Multi-format tagging** — FLAC, MP3, OGG Vorbis, Opus and M4A, plus optional companion `.lrc` sidecar files.
* **Concurrent multi-provider search** — LRCLIB, NetEase Cloud Music and the optional `syncedlyrics` engine are queried in parallel on a pooled HTTP session with retry and backoff.
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
pipx install git+https://github.com/sobakah/lrckit.git
# with romanization and the extra provider engine:
pipx install "lrckit[all] @ git+https://github.com/sobakah/lrckit.git"
```

This installs a `lrckit` command on your `PATH`.

> **fish users:** `pipx` and `pip install --user` place their launchers in `~/.local/bin`. On Fedora that directory is added to `PATH` by `~/.bash_profile`, which fish never reads — so the install succeeds but `lrckit` is not found. Add it once with `fish_add_path ~/.local/bin` (fish 3.2+); it persists across sessions. In a virtual environment, use `source venv/bin/activate.fish` rather than `activate`, and set the editor with `set -gx EDITOR nvim` rather than `export`.

### From a checkout

```bash
git clone https://github.com/sobakah/lrckit.git
cd lrckit
pip install -e ".[all]"
```

The checkout also runs without installation. `run.py` is only a launcher — it needs the sibling `lrckit/` package directory next to it:

```
run.py
lrckit/
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
| `syncedlyrics` | Multi-provider scraping engine (Megalobiz, Deezer, …) |
| `korean-romanizer` | Hangul → Revised Romanization of Korean |
| `pykakasi` | Kanji and Kana → Hepburn Rōmaji |
| `pypinyin` | Hanzi → Pīnyīn with tone marks |
| `anyascii` | Any non-Latin Unicode script → ASCII |

> **PEP 668 / externally-managed-environment:** if your package manager blocks global pip installs, use a virtual environment or append `--break-system-packages`.

---

## Usage

```bash
lrckit                              # interactive, current directory
lrckit ~/Music/Artist/Album         # interactive, specific album
lrckit ~/Music/Artist --auto        # tag everything with a confident match
lrckit ~/Music/Artist --auto --dry-run
```

| Option | Effect |
|---|---|
| `path` | Music directory (default: current directory) |
| `--version` | Print the version and exit |
| `-c`, `--config FILE` | Use this config file instead of the search paths; an unusable file is an error |
| `--max-files N` | Scan at most `N` files, `0` for no limit (overrides `settings.max_file_count`) |
| `--auto` | Batch mode: tag confident matches without prompting |
| `--overwrite` | With `--auto`, also replace existing lyrics; the old ones are backed up to `$XDG_STATE_HOME/lrckit/backups/<time>/` (default `~/.local/state/…`) |
| `--dry-run` | With `--auto`, report changes without writing |
| `--color {auto,always,never}` | Override colour handling |
| `-v`, `-vv` | Increase log verbosity (on stderr) |
| `--sidecar` / `--no-sidecar` | Force companion `.lrc` files on or off |

`--auto` only applies a candidate when it is synchronized, the artist matches, and the duration is within `api.duration_tolerance_seconds` — or the match came from LRCLIB's exact `/get` endpoint. Results from the `syncedlyrics` engine are never applied automatically, because it does not say which recording its lyrics belong to. Anything less confident is left for interactive review.

To go easy on LRCLIB, a free community service, batch mode first asks only its exact `/get` endpoint and runs the full search across all providers just for tracks that are not settled that way, and it waits `api.batch_delay_seconds` between tracks. Batch mode never prompts: a path it cannot use ends the run with exit code 2.

Exit codes: `0` success or quit, `1` some files failed in batch mode, `2` unusable path, config or arguments, `130` aborted with Ctrl+C.

---

## Keybindings

**Tree overview (entry screen)** — `1`–`N` jump to track · `Enter` start with the first · `c` change directory · `q` quit

**Search results**

| Key | Action |
|---|---|
| `Enter` | Save the highlighted entry — shown only when one is highlighted |
| `1`–`N` | Inspect and select a candidate |
| `m` | Adjust the search query (pre-filled) |
| `R` | Repeat the search, bypassing the cache |
| `Ctrl+C` | While searching: cancel the search and stay on the track |
| `n` | Create lyrics from scratch in your editor |
| `p` / `s` | Previous / next track |
| `t` | Back to the tree overview |
| `q` | Quit |

`✓exact` marks a record LRCLIB returned as a direct match rather than a search hit; the duration column shows separately whether the length was verified (`Duration OK` / `±Ns`), or just the candidate's own length when the track's length is unknown. `?unverified` marks `syncedlyrics` results, which carry no artist or title to check against. NetEase results may come in a second variant marked `user romanization` — the romanized lyrics NetEase users submitted.

The entry `Enter` would save is marked `▶` and named in full above the menu — the exact match after a search, or the one you last inspected.

**Inspection** (candidate or manual entry)

| Key | Action |
|---|---|
| `v` | Full preview (all lines) |
| `e` | Open in your editor |
| `r` | Romanize (detected script offered as default) |
| `y` | Apply and embed |
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

Pick **Mixed scripts** in the `r` menu; it is preselected when two different Asian scripts share the text. A line like `사랑해 我爱你 ありがとう` becomes `saranghae wǒ ài nǐ arigatou` in one pass. After choosing the script you choose the layout: replace each line, or keep it and add the romanized line below with the same timestamp. `settings.romanization_layout` sets which of the two Enter picks.

Romanization never drops characters: pykakasi silently deletes characters it cannot read (and sometimes the one after them), so those are converted with pypinyin — or anyascii — instead.

### Limitations

* **Runs of purely shared characters cannot be resolved automatically.** `月光下的思念` and `東京物語` are valid in either reading and fall back to your choice. Roughly four in five runs resolve on their own in typical lyrics; traditional Chinese resolves less often than simplified.
* **Japanese word spacing is approximate.** Verb and adjective endings stay attached (`愛してる` → `itoshiteru`), but a particle following a Kana-ending compound may be attached too: `ずっと一緒にいたい` → `zutto isshoniitai`.
* **Kanji readings come from pykakasi** and are not context-aware for names or unusual readings — `君` becomes `kun`, not `kimi`.
* **Provider credit headers** such as NetEase's `作词 : …` are removed from NetEase lyrics. Typed into other lyrics by hand, they are ignored when detecting scripts but still passed through the conversion.

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

Lookup order: `--config FILE` → `$XDG_CONFIG_HOME/lrckit/config.json` (falling back to `~/.config/lrckit/config.json`) → `config.json` in the root of a source checkout.

User values are merged **recursively** over the built-in defaults, so a file with only the keys you care about is valid:

```json
{
  "settings": {
    "prefer_latin": false,
    "write_sidecar_lrc": true
  }
}
```

An invalid file found on the search paths produces a warning and the defaults are used; a file given with `--config` must be valid, or the run stops. Values of the wrong type and unknown keys (typos) are reported, and the default is used for them. `config.example.json` contains every key with its default value — copy only what you want to change.

**`aliases`** — groups of equivalent artist spellings, matched at word boundaries.

**`cleaning`** — `feature_regex`, `trailing_feature_regex` and the `korean_`/`japanese_`/`chinese_bracket_regex` patterns removed from search queries, plus `ignore_words_artist_match`.

**`api`**

| Key | Meaning |
|---|---|
| `lrclib_url`, `netease_search_url`, `netease_lyric_url` | Provider endpoints. NetEase's former `/api/search/get/web` now answers encrypted; a config still naming it gets a provider notice |
| `timeout_seconds` | Per-request timeout |
| `netease_search_limit` | Results requested per NetEase query |
| `user_agent` | Sent to LRCLIB; identify your fork by name, version and URL. Defaults to the current version, so leave it out unless you change it |
| `max_workers` | Parallel HTTP requests per provider stage |
| `retry_total`, `retry_backoff` | Automatic retries on 429 and 5xx |
| `duration_tolerance_seconds` | Maximum length difference accepted by `--auto` |
| `syncedlyrics_timeout_seconds` | How long to wait for the `syncedlyrics` engine, which has no timeout of its own |
| `batch_delay_seconds` | Pause between tracks in `--auto` mode |
| `enable_lrclib`, `enable_netease`, `enable_syncedlyrics` | Turn individual providers off |

**`settings`**

| Key | Meaning |
|---|---|
| `supported_extensions` | File types included in a scan; only `.flac`, `.mp3`, `.ogg`, `.opus` and `.m4a` can be tagged, others are ignored with a warning |
| `default_editor` | Fallback when `$EDITOR` and `$VISUAL` are unset |
| `preview_lines` | Lines shown in the short preview |
| `non_latin_ratio_threshold` | Share of non-Latin letters above which lyrics count as original-script |
| `max_search_depth` | Directory levels to descend. `3` collects files up to three levels below the start directory; deeper directories are not entered. |
| `max_file_count` | Abort the scan above this many files and ask for a narrower path; `0` for no limit |
| `search_cache_entries` | How many past searches to keep in memory for the session |
| `prefer_latin` | `true` ranks romanized versions first; `false` prefers the original script |
| `sort_by_tags` | `true` orders by disc/track number; `false` uses natural filename order (`Track 2` before `Track 10`) |
| `write_sidecar_lrc` | Also write a companion `.lrc` next to each track |
| `id3_v2_version` | ID3v2 version for MP3s that have no tag yet: `4` (default) or `3` for players that only read ID3v2.3. Existing tags keep their version |
| `uslt_language` | ISO-639-2 code stored in the MP3 `USLT` frame |
| `romanization_layout` | What Enter picks at the romanization layout prompt: `replace` (default) or `below` to keep the original line with the romanized one below it |
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
lrckit/
  cli.py                 Argument parsing and start-up
  app.py                 Directory scanning, navigation, menus, batch mode
  providers.py           LRCLIB / NetEase / syncedlyrics, ranking
  tagging.py             Metadata reading, writing, caching
  text.py                Tag cleaning, artist matching, romanization
  editor.py              External editor handling
  ui.py                  Colours, prompts, previews, path completion
  config.py              Defaults and recursive config merging
tests/                   pytest regression suite
```

```bash
pip install -e ".[all,dev]"
pytest -q
python -m pyflakes lrckit/ tests/ run.py
```

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## License

MIT. Free for private and commercial use, modification and distribution.
