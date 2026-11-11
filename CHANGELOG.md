# Changelog

All notable changes to lyrembed are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] — 2026-11-11

The first release. lyrembed finds synchronized and plain lyrics for an album, checks that they
belong to the track, romanizes Korean, Japanese and Chinese lyrics, and embeds them into FLAC, MP3,
OGG Vorbis, Opus and M4A files. You work on a page in your browser that only your own computer can
reach; `--auto` tags a whole folder in the terminal without asking.

### Features

- **A page in your browser.** The folder's tracks with their covers and lyrics status, search
  results next to a preview, an editor, bulk actions and every setting. It runs on `127.0.0.1`
  only, opens only with the secret address lyrembed prints, and loads nothing from the internet.
- **Several lyrics providers.** LRCLIB (its exact lookup by title, artist, album and length, and
  its search), NetEase Cloud Music and QQ Music are asked in parallel; Megalobiz, the artist's
  aliases and the optional `syncedlyrics` engine follow only when that finds no synced lyrics that
  fit. Rate limits and providers that pad their answers with unrelated songs are reported.
- **Only results that fit the track.** The artist must match (aliases included), and the title
  too, or the length for a title in another language. Synced lyrics rank first, and the result
  batch mode would pick is marked **Best match**; the rest sits behind a click.
- **Instrumental tracks are recognised** by version notes in the title (`(Inst.)`, `(Off Vocal)`,
  `伴奏` …), the language tag `zxx`, the providers' instrumental flags and, optionally, MusicBrainz;
  batch mode and the bulk fetch skip them.
- **An editor that plays the track** and lights up the current line: shift all lines, start a
  line at the current position (which also turns plain lyrics into synced ones), switch between
  search results while the song keeps playing, romanize, or edit the text. Apple Lossless tracks
  play through a temporary FLAC copy made with ffmpeg.
- **Romanization** of Korean (Revised Romanization), Japanese (Hepburn), Chinese (Pinyin with
  tones) and any other script, preserving timestamps. Lyrics that mix scripts are converted per
  run in one pass, Latin text is never touched, and no character is lost. Replace each line or
  keep it with the romanized line below; **Romanize when saving** does it without the editor.
- **Batch mode.** `lyrembed DIR --auto` tags every track with a confident match, also from cron:
  `--dry-run` reports without writing, `--overwrite` backs up the lyrics it replaces, `--romanize`
  romanizes what it embeds. It never prompts and ends with a clear exit code.
- **Bulk actions on the page**: fetch lyrics automatically, romanize embedded lyrics, extract them
  to `.lrc` files, or delete them, for a chosen set of tracks, with each track's result as it
  comes. Lyrics and `.lrc` files they replace are backed up first.
- **Tagging** of FLAC, OGG Vorbis and Opus (`LYRICS`), MP3 (`USLT`, synced `SYLT` read and
  replaced) and M4A (`©lyr`), optionally with a `.lrc` file next to each track. Covers are only
  read.
- **Settings and artist aliases on the page**, checked before saving and written key by key into
  the config file, which keeps its other keys and comments. About 200 built-in alias groups
  (K-pop, Japanese and Chinese artists, spellings with and without diacritics) can be extended.
- **Import a CD** with [cdport](https://github.com/sobakah/cdport) from the page: cdport rips and
  tags the disc in the same tab and brings it back with the new album open.
- **For other programs**: `lyrembed romanize --stdio` romanizes lyrics for display, a D-Bus
  interface (`io.github.sobakah.lyrembed1`) lets a player such as
  [Jewelcase](https://github.com/sobakah/jewelcase) romanize, open pages and fetch lyrics, also
  from a Flatpak, and `--return-url` sends the tab back to the page that opened lyrembed.
- **Updates from the page**: lyrembed looks for a newer release when its page starts, installs it
  with pipx and restarts on the same address.
- **An app-menu entry** with `lyrembed --install-desktop-entry` (Linux and BSD desktops).
- **Installing**: `pipx install "lyrembed[all] @ git+https://github.com/sobakah/lyrembed.git"`;
  Python 3.10 or newer, ffmpeg optional for Apple Lossless.

[Unreleased]: https://github.com/sobakah/lyrembed/compare/1.0.0...HEAD
[1.0.0]: https://github.com/sobakah/lyrembed/releases/tag/1.0.0
