# lyrembed – notes for Claude Code

lyrembed searches, checks, romanizes and embeds synchronized (`.lrc`) and plain lyrics into FLAC,
MP3, OGG Vorbis, Opus and M4A files. The owner works on a page in the browser, served by lyrembed
itself on 127.0.0.1; batch mode (`--auto`) runs in the terminal. Python 3.10+, no web framework,
no build step for the page (Preact and htm, copied in). User docs: `README.md`; there are no
separate design documents. lyrembed owns the family's shared page look (`static/base.css`,
`static/icons.js`): its rules are under Conventions below, and the other repositories point here.

## The program family

<!-- This section is the same in all six repositories. Change it in all of them or none. -->

Six programs by the same owner, each in its own private repository `sobakah/<name>` with its own
CLAUDE.md. Other sessions work in the other repositories: change one only when the owner asks.

| Program | What it does | Works with |
|---|---|---|
| picktag | Tags and searches pictures, videos and documents (GTK 4, libadwaita) | reads keepbest's and media-optimizer's file journals; starts keepbest for *Find Duplicates* |
| keepbest | Finds duplicate pictures and videos and keeps the best copy (page in the browser) | hands its tab to media-optimizer; writes a file journal |
| media-optimizer | Converts pictures, GIFs and videos to smaller formats (page in the browser) | started from keepbest; writes a file journal |
| cdport | Rips CDs, tags them and adds covers and lyrics (page in the browser) | starts lyrembed; D-Bus service for Jewelcase |
| lyrembed | Finds lyrics, embeds them in music files, romanizes them (page in the browser) | started from cdport; D-Bus service for Jewelcase |
| Jewelcase | A GNOME music player for an album library, with Spotify (GTK 4, libadwaita) | uses cdport and lyrembed; never writes music files |

The file journal format is specified in picktag's `docs/JOURNAL_SPEC.md`. The programs with a page
share its look (libadwaita's design language): `static/base.css` and `static/icons.js` are
lyrembed's, copied unchanged into cdport, keepbest and media-optimizer; change them in lyrembed and
copy them back. Setup details of the owner's machine are in `CLAUDE.local.md` (not in git).

On image-based systems (Fedora Silverblue, Bluefin) the programs run on the host where the image
has what they need (pipx from its zipapp, or layered with `rpm-ostree`); cdport runs in a toolbox,
where cyanrip can be built, and Jewelcase comes as a Flatpak bundle on its releases. In a toolbox
or distrobox, what the host starts (app-menu entry, D-Bus service) enters the container, and what
a program opens in other apps (browser, Flatpaks, file manager) leaves it: `container.py`,
lyrembed's, copied unchanged into cdport, keepbest and media-optimizer; picktag's
`core/desktop.py`. The suites of the four make no container unless a test asks (an autouse
fixture), so they run alike in a toolbox. Each README has a section *Fedora Silverblue, Bluefin
and other image-based systems* under its installation.

### Working rules

- **Commits**: commit finished work once its tests pass, locally, on the default branch;
  experiments stay uncommitted. **Push, tags and releases only when the owner says so**; a request
  to release includes commit, tag and push. Pushed history is never rewritten; a mistake in a
  release is fixed with a new version.
- **Commit messages**: English; a subject of at most 72 characters saying what changed; a body
  wrapped at 72 saying why and what was verified. Written directly, no draft files.
- **Before pushing**, check the diff for personal data: the owner's name, home paths, file and
  folder names or contents from the owner's libraries, details of the owner's machine. Those
  belong in `CLAUDE.local.md`.
- **Verify, don't assert.** Run the code before saying something works; say what was tested and
  what wasn't. Outside reviews have been wrong before: check each claim.
- **Every bug fix comes with a test that fails without it.**
- **Tests never touch the owner's data** (libraries, music, settings, journals, the trash), the
  network or real hardware; they use temporary folders and generated media.
- **Ask first** before installing system packages, deleting or overwriting the owner's files, or
  changing a default the owner chose.
- Stop processes by PID, never with `pkill -f`. Commands for the owner to paste are written for
  fish; examples in the docs use bash.

### Versions and releases

- Versions have three parts and tags no `v` (`1.2.0`). New features raise the minor version. A
  release of a few changes that fix bugs is a patch, also when a fix changes behaviour. The
  version lives in one place (see Releasing below).
- Every user-visible change goes under `## [Unreleased]` at the top of `CHANGELOG.md` when it is
  committed. Don't propose a release after every change: say what is waiting and suggest one when
  a feature or a few fixes have gathered.
- A release: the version raised, `## [Unreleased]` becomes `## [X.Y.Z] — YYYY-MM-DD`, the full test
  suite passes, one commit `<program> X.Y.Z: <what it brings>`, an annotated tag
  `git tag -a X.Y.Z -m "<program> X.Y.Z"`, push of the branch and the tag, then a GitHub release
  titled `<program> X.Y.Z` whose notes are that version's CHANGELOG section.
- Installed copies look for newer releases by their tags, so a tag above the current version must
  never exist. Pre-releases below 1.0.0 on GitHub are an archive; they are not mentioned in the
  repository.

### Documentation

- `README.md` is for people using the program: what it does, installing, using it, settings,
  working with the other programs, limits. Current state only.
- `CHANGELOG.md` follows Keep a Changelog, newest first: `### Added`, `### Changed`, `### Fixed`,
  bullets in the user's terms (a bold lead-in for the important ones), no internal reasons, no
  commit hashes.
- `CLAUDE.md` is for Claude sessions: the same section order in every repository. Keep a reason
  only where it stops a session from undoing a decision.
- Update README and CHANGELOG in the commit that changes the behaviour.

### Wording

British spelling (colour, behaviour, analyse); names of code, GTK and GNOME stay as they are.
Program names are written as above (lower case, except Jewelcase). Terms used in all six:

| Use | Not |
|---|---|
| folder | directory (except in code and option names) |
| the page (the program's page in the browser) | web UI, frontend, site |
| settings | options, preferences (except GNOME's *Preferences* window in the GTK apps) |
| dry run (`--dry-run`) | simulation, test run |
| the trash; *move to the trash* | recycle bin, wastebin |
| the owner (in CLAUDE.md) | the user, the maintainer, a name |
| you (in README) | the user |
| Enter, Esc, Ctrl+C | ENTER, Return, ^C |

## Layout

```
run.py                   Launcher for checkouts (thin wrapper)
lyrembed/
  cli.py                 Argument parsing, config loading, start-up, romanize --stdio
  core.py                Scanning, saving, backups, batch and bulk runs; no output, no prompts
  web.py                 The page's local HTTP server and JSON API (the default without --auto)
  batch.py               Batch mode (--auto): prints core's steps in the terminal
  static/                The page: index.html, app.js, style.css, lrc.js (editor's LRC logic),
                         base.css and icons.js (the family's shared look), icon.svg, logo.svg,
                         vendor/preact-htm.js
  providers.py           LRCLIB / NetEase / QQ Music / Megalobiz / syncedlyrics, ranking, search cache
  tagging.py             Metadata and cover reading, writing, mtime-keyed cache
  text.py                Tag cleaning, artist matching, script handling, romanization
  ui.py                  Terminal colours, messages and the status line
  selfupdate.py          A newer lyrembed: release tags via git, the pipx command, the restart
  config.py              Version, defaults, setting explanations, config merging, alias loading
  desktop.py             The app-menu entry (freedesktop.org), written on request
  dbus_service.py        The D-Bus interface (--dbus) and its service file
  container.py           Toolbox and distrobox: what the host starts enters the container, what
                         lyrembed opens leaves it; copied unchanged into cdport, keepbest and
                         media-optimizer (see the family block)
  aliases.json           Built-in artist alias groups (package data)
config.example.json      Generated from config.py (see Conventions)
tests/test_lyrembed.py   pytest suite
tests/lrc.test.mjs       Node tests for static/lrc.js (run by the pytest suite when node exists)
```

## Commands

```bash
pip install -e ".[all,dev]"     # all optional backends plus pytest/pyflakes
python -m pytest -q             # about 250 tests, under 10 s; tests needing an optional backend,
                                # jeepney, dbus-daemon, ffmpeg or node skip without them
python -m pyflakes lyrembed/ tests/ run.py
python run.py --version
python run.py ~/Music/Album --auto --dry-run --color never
python run.py ~/Music/Album --no-browser         # the page; prints its address
python -c "from pathlib import Path; from lyrembed import config; config.write_example(Path('config.example.json'))"
```

Always run both pytest and pyflakes before finishing a change.

## Rules that must not break

Each of these was found by debugging against real data; reverting one brings back a bug that is
hard to spot.

**Romanization**

- **Route every pykakasi conversion through `text._romanize_japanese`.** pykakasi deletes
  characters it cannot read, sometimes the next one too, and they are missing even from the token
  list (`区别` → `ku`). It probes each Han character (`_kakasi_reads`), cuts unreadable ones out
  first and checks that the tokens' `orig` text adds up to the input. Test partial loss, not just
  total loss.
- **Han evidence comes from codecs, not hand-kept lists.** `_charset_evidence` asks GB2312, CP950
  and EUC-JP which standards contain a character (CP950: plain Big5 lacks `裏`, Big5-HKSCS has `駅`;
  pypinyin is no source, it reads shinjitai). Japanese-only evidence counts only when the text has
  Kana (`trust_ja`); an unreadable Han character is Chinese evidence only without Kana (pykakasi
  lacks 17 JIS X 0208 kanji). On a line with Kana, a Han run separated by punctuation or spaces
  counts as Japanese. A line of shared characters alone (`約束`) says nothing: `_asian_languages`
  lets the rest decide, Japanese when the text has Kana and no proof of Chinese.
- **Romanization works on script runs, never whole lines** (a second backend destroys the first's
  output), but adjacent runs with the same target language are merged first (`見` reads `ken`,
  `見つけた` reads `mitsu`). `text._plan_runs` does both; route new paths through it.
- **Non-letters are `KEEP`**: `text._char_class` tests `isalpha()`, not `isascii()`. `・` is `KEEP`;
  `ー` counts as Kana only next to Kana (`_char_classes`).
- **Latin vs. original is counted per lyric line**, not per letter (one Hangul or Han character is
  a syllable). `text._lyric_lines` drops LRC tags and credit lines. `[MIXED]` means two Asian
  scripts (`detect_script == "mixed"`), not Asian plus English.
- **`detect_script` skips provider credit lines** by the whitelist `text._CREDIT_LABELS`, not a
  colon pattern: a lyric line may contain a colon.
- **`lyrembed romanize --stdio` and D-Bus `Romanize` are Jewelcase's interface.** Jewelcase finds
  `--stdio` in `lyrembed romanize --help` and keeps one process running: the JSON shape (`id`,
  `lyrics`, `language`, `reason`, `error`) and one flushed answer per line must stay. `cli.main`
  checks for `romanize` before the normal parser (else it is a folder name). The layout is always
  "below" (`core.romanize_for_display`). Both use the built-in settings, not the owner's file, so
  they give the same answer. `han_hint` only settles undecided Han runs, as Kana would, and never
  outweighs Chinese evidence (`我爱你` must not come out in Rōmaji).

**Searching**

- **Results must fit the track; there is no fallback to everything.** `text.matches_title` wants
  the shorter title to cover at least half of the longer one and compares symbol-only titles
  (`???`) as written; only a missing title matches. A record without an artist is listed but never
  embedded unattended (`pick_automatic`). `providers.is_related` needs artist and title (or the
  length, for a translated title); the rest goes to `SearchReport.unrelated`. For an instrumental
  track (`text.instrumental_marker`) neither length nor LRCLIB's `/get` vouches for a result.
  Instrumental flags (LRCLIB `instrumental`, NetEase `pureMusic`) count only for a record of the
  searched track.
- **Keep the request count low, NetEase above all.** NetEase searches run one after another,
  lyrics are fetched for at most `_NETEASE_LYRIC_FETCHES` songs, aliases are searched in a second
  round only when the first finds no synced lyrics that fit (`collect_candidates`,
  `settings.search_all_aliases`). Filler and refusals are reported as provider notices, never
  dropped. The syncedlyrics engine runs in the second round and never with its own LRCLIB/NetEase
  sources (`api.syncedlyrics_providers`). A credit naming several artists gets each one's aliases
  in turn, no more than the longest group has (`get_artist_aliases`). QQ Music follows NetEase's
  rules (`_fit` decides which lyrics to fetch).
- **LRCLIB 429**: not in the session's retry list; a refusal stops the track's queued LRCLIB
  queries and later rounds (`SearchReport.rate_limited`); at most `_LRCLIB_PARALLEL` requests at
  once; batch mode's full search reuses the quick `/get` result (`after_quick`), and a refusal is
  waited out even when another provider found the lyrics (`core.batch`, `limited`).
- **Megalobiz** is asked directly, second round only. After a 429 it is skipped until
  `Retry-After` (`_megalobiz_blocked_until`) and never put into `rate_limited`, so a batch run does
  not wait for a daily limit. Its terms ask to link the lyric's page: `Candidate.url`.
- **Provider threads are daemons** (`providers._DaemonPool`): a ThreadPoolExecutor's threads are
  joined at exit, and a request nobody waits for (syncedlyrics has no timeout) kept lyrembed alive.
- **Provider results are merged across concurrent responses**: `/get` and `/search` often return
  the same record; endpoint-derived attributes (`exact`) are merged, not taken from whichever
  future finished first.

**Program structure**

- **Never index `CONFIG["section"]` directly**; use `config.get(section, key, default)`. Merging is
  recursive and deep-copies; don't replace it with `dict.update`.
- **`core` never prints, prompts or sleeps.** Problems come back as values (`DirectoryProblem`,
  the warnings `write_lyrics` returns). Batch and bulk runs are generators of steps (`Searching`,
  `Pause`, `Notice`, `Outcome`) counted by the caller's `Tally`; a search runs when the step after
  `Searching` is requested; waiting is the caller's job (`Pause`), so Stop on the page can end it.
- **Batch mode never prompts** (cron): `batch.open_music_directory` returns None with the reason,
  exit code 2. Without `--auto` an unusable folder is no error: the page opens with the picker.
- **`cli.main` decides the exit code in one place**: 0 done or quit, 1 batch failures, 2 unusable
  input, 130 Ctrl+C. Programs that start `lyrembed DIR` wait for it, so the page must end the
  process: Quit, closing the tab, or Ctrl+C.
- **The launcher must not be named `lyrembed.py`**: with the package missing it would import
  itself (`test_launcher_explains_a_missing_package`).
- **Settings are written key by key to the config file in effect** (`config.save_settings`): other
  keys and `_` comments stay, the previous file becomes `config.json.bak`, invalid JSON is never
  overwritten, values pass `config.setting_problem` (the checks `load_config` applies).
  `api.user_agent` is not offered: a saved copy would freeze the version.
- **Retired settings get a note, not "unknown key"**: `config._RETIRED` (the terminal menus'
  `default_editor`, `preview_lines`, `enter_opens_preview`).

**The page and its server**

- **The web server answers only its own page.** 127.0.0.1 only; the token in the printed address
  becomes an HttpOnly, SameSite=Strict cookie and leaves the address bar; the `Host` header must be
  127.0.0.1 or localhost with the port (DNS rebinding); POSTs need the page's own `Origin` and a
  JSON content type; static files resolve inside `static/`; the page has a CSP. Lyrics are only
  rendered as text, never as markup.
- **The token cookie's name carries the port** (`web.cookie_name`): cookies belong to a host, and a
  second lyrembed (cdport's lyrics step starts one) overwrote the first one's token.
- **Timing**: `/api/quit` sets the stop signal only after its reply is written; the printed address
  is flushed (behind a pipe it stayed buffered); the page counts as gone after a `closing` beacon
  plus 10 s (a reload sends the beacon too) or 90 s without a ping (Chrome runs a hidden tab's
  timers once a minute).
- **What shows after the server stopped is in the page from the start**: the "has stopped" overlay
  is only hidden, or its icon is requested from a server that is gone.
- **The editor's text is the one source of truth.** `static/lrc.js` parses it on every change; the
  line view, the current line and **Now** derive from it. The timing shift stays separate until
  saved or the text view opens. `applyShift` folds `[offset:]` in and drops it. A positive offset
  shows lyrics earlier (play time = timestamp − offset). Stamping a line moves the lines below that
  share its timestamp. Lyrics get LF line ends where they enter (`text.unix_newlines`), and `lrc.js`
  splits on CRLF too.
- **Audio needs byte ranges, and ALAC a copy.** Chrome and Firefox refuse ALAC in M4A (AAC plays):
  `WebSession.audio` converts once per file version to a temporary FLAC with ffmpeg, outside
  `self.lock` with a lock per file; `close()` removes the copies.
- **Covers are judged by their bytes**: `tagging.image_type` accepts JPEG, PNG, GIF and WebP by
  magic bytes only (tags name wrong MIME types; an SVG could carry script). The page gets a digest
  per cover and the address carries `?v=<digest>`. Embedded covers are cached by the track's date,
  cover files by their own.
- **Bulk runs record every step before stopping.** `web.Job` drives `core.batch`/`core.bulk`; an
  `Outcome` arrives after its file was written, so the loop records it first and only then checks
  the stop flag. Closing the page or Ctrl+C lets the current track finish (`WebSession.close`).
- **Updates** (`selfupdate`, cdport has a copy, not an import): `latest` takes the highest plain
  `x.y.z` tag from `git ls-remote --tags` (git never prompts: `GIT_TERMINAL_PROMPT=0`, no askpass).
  `serve` checks at every start, in a thread, never in batch mode or with `--return-url`. pipx
  replaces `static/` under the running server, so the page offers **Restart lyrembed** at once:
  `cli._restart` `exec`s the start command with `LYREMBED_RESTART=PORT:TOKEN`, which the new process
  removes from its environment at once (a cdport it starts must not see it) and uses to listen on
  the same port with the same token. Not while a bulk run goes on.
- **The app-menu entry starts the lyrembed that wrote it** (`desktop.launch_command`: a pipx
  launcher or `python run.py`). `Exec` arguments follow the spec's two escaping layers
  (`exec_argument`). Started without a folder in the home folder, `cli` opens the picker in
  `core.music_folder()` without the home-folder warning.

**cdport**

- **Another program's page can hand lyrembed the tab**: `--return-url URL --return-name NAME`
  replaces Quit with "Done – back to NAME". Only local addresses (`cli.local_page`: http,
  127.0.0.1 or localhost, a port). cdport and the D-Bus service find lyrembed's page by the first
  line `serve` prints, "lyrembed is running at <url>" (`web.RUNNING_LINE`; cdport's `web._HANDOFF`,
  `dbus_service._PAGE` here): keep that wording.
- **Import a CD** (`web.Import`) runs `cdport --web --no-browser --return-url <its page>
  --return-name lyrembed` (`web.find_cdport`), sends the tab to the address cdport prints
  (`_CDPORT_PAGE`) and reads cdport's output to the end for "cdport finished: <folder>"
  (`_CDPORT_DONE`, `_take_imported`). `importing` stays true until the output is read
  (`Import.running`), not until the process is gone, or the last line is missed. While cdport runs,
  and `IMPORT_GRACE` after, lyrembed's page does not count as gone (`import_away`). Not offered when
  lyrembed was opened with `--return-url` (no loop).

**D-Bus** (`dbus_service`; loop, service file and safety rules are copied from cdport's, not
imported; the calls are in its docstring and the README)

- Bus name `io.github.sobakah.lyrembed`, object `/io/github/sobakah/lyrembed`, interface
  `io.github.sobakah.lyrembed1`; a change that breaks callers gets a `lyrembed2` next to it
  (`GetInfo`'s `InterfaceVersion`). Declare a call once, in `INTROSPECTION` (`_SIGNATURES`).
- **No way out of a sandbox**: the page's address never goes over the bus (its token opens the
  whole API, and a Flatpak shares the host's 127.0.0.1), so the service opens the browser itself
  (`webbrowser.open` in a thread) and `Runs` lists no addresses. No call runs a command. A page may
  open any existing absolute folder; `FetchLyrics` only inside the music folder
  (`dbus_service.music_folder`, never the home-folder fallback). Options are checked by name and
  type; `--dbus` refuses other options.
- `Romanize` runs in one worker thread, answering later; `""` stands for `null`. The service ends
  after `IDLE_EXIT` (5 min) so an old version does not stay after a pipx update.
- `OpenPage`/`FetchLyrics` start child processes of the same lyrembed (`desktop.launch_command`) in
  their own session, home folder as working folder. A page run gets `--color never` (else the
  address line is wrapped in escape codes and misses `_PAGE`) and the hidden
  `--unvisited-timeout 600`. The same folder's page is shown again, not started twice; overlapping
  folders get `Busy` (a guard on the folder a run started with, not a lock).
- `Stop` sends SIGINT once per run. A run that ends is always signalled. The service never ends
  while runs go on (`_outlive_runs`: under dbus-broker it is a systemd unit whose end kills its
  cgroup). Ctrl+C on a service started by hand passes on to the runs, exit 130. Anything but a
  `ServiceError`, or a reply jeepney cannot serialise, is answered with
  `org.freedesktop.DBus.Error.Failed`, traceback on stderr.
- Idle, the loop sends `ReleaseName` and ends only once that is answered and nothing came
  meanwhile (a call routed here just before would go unanswered).
- `--install-dbus-service` writes `~/.local/share/dbus-1/services/io.github.sobakah.lyrembed.service`
  (`Exec=` the launcher plus `--dbus`) and asks the bus to `ReloadConfig` (dbus-broker needs it).

## Conventions

- **Errors are reported, never swallowed.** No bare `except: pass`. File operations raise
  `TaggingError`; provider failures go into `SearchReport.add_error`.
- **Writing to a file must not crash the session**: use `core.write_lyrics`, which reports a failed
  sidecar (`tagging.SidecarError`) as a warning.
- **Optional dependencies stay optional**: a missing package disables exactly one feature and says
  so where it is used; never import one at module scope without a guarded fallback. jeepney is a
  dependency, imported only for `--dbus` and the bus reload.
- **All terminal output goes through `ui`** (`StyleUI` colours are empty strings when piped or
  with `NO_COLOR`). Everything the owner decides happens on the page, never in a prompt.
- **Expensive objects are built once**: the `requests.Session`, the pykakasi converter, compiled
  regexes; metadata cached by mtime, searches by query.
- **Type hints and docstrings** on public functions; docstrings say why, not what the code shows.
- **Settings**: a new setting needs a `config.SETTING_HELP` entry and a row in the README table.
  `config.example.json` is generated by `config.example_config()` (the defaults without
  `api.user_agent`, each key preceded by its `_key` explanation); regenerate it after changing a
  default or a text, or the example tests fail. Keys starting with `_` are comments.
- **Artist aliases** live in `lyrembed/aliases.json` (shown by `--list-aliases`). Every name must be
  unambiguous: not an ordinary word, not shared by several artists, not inside another group's name
  (`Mayday` would join *Mayday Parade* with 五月天); `test_builtin_aliases_are_unambiguous` checks
  the last two. User groups are merged into built-in ones by shared name.

**The family's page look** (the other repositories point here)

- `static/base.css` (tokens, buttons, fields, header bar, steps, status pages, toasts) and
  `static/icons.js` are lyrembed's and copied unchanged into cdport, keepbest and media-optimizer,
  like `vendor/`. Change them here, then copy them there (in a session for that repository). What
  only one page has goes in its own `style.css`.
- libadwaita's design language. The header bar is always: program, workflow steps in the middle
  (icons, no numbers), actions. The content is a centred column (`main`, `.narrow`, `.wide`).
  Programs differ by accent (lyrembed blue, cdport teal) and small icon.
- Accents are a shade darker than libadwaita's (white on libadwaita's own is 3.8:1; ours 4.8:1 and
  more).
- `icon.svg` is unreadable below about 64 px: small places (header, browser tab) use `logo.svg`;
  the full icon appears only at 112 px on status pages.
- Here the steps navigate (`FLOW` in `app.js`); Bulk actions and Settings lie beside the flow.
- No build step, nothing loaded from the internet. `static/vendor/preact-htm.js` is htm 3.1.1's
  `preact/standalone.module.js`, unchanged; the npm checksum and both licenses are in
  `vendor/LICENSES.txt`. To update it, replace the file and the checksum, nothing else.

## Testing

- Anything touching an optional backend needs `@pytest.mark.skipif(not text.HAS_KAKASI, ...)` or
  the equivalent; the suite must pass with no optional packages installed.
- **Never hit the network.** Monkeypatch `providers.get_session` and, where useful,
  `providers._lrclib_queries` (pattern: `test_exact_flag_does_not_depend_on_response_order`).
- Tests that save settings set `XDG_CONFIG_HOME` to `tmp_path` (fixture `config_home`).
- Audio fixtures: the base64 FLAC in `MINIMAL_FLAC_B64`, written to `tmp_path`; no binary files in
  the repo.
- Prefer asserting the observable result (`"mitsuketayo" in out`) over internal calls.
- The page is covered by API and session tests. Clicking through it is a manual check (headless
  Chrome via Playwright, outside the repo) with faked providers and generated tracks.
- `test_dbus_on_a_real_bus_started_by_the_bus` starts a private dbus-daemon (skipped without
  jeepney or dbus-daemon); its config needs `<allow eavesdrop="true"/>` or replies are rejected
  and the client hangs.
- Behaviour depends on third-party dictionaries and real provider responses: run a quick script
  before claiming a romanization or search fix works.

## Releasing

The version lives only in `lyrembed/config.py` (`__version__`); `pyproject.toml` reads it
(`[tool.setuptools.dynamic]`) and `api.user_agent` is derived from it. Then follow the release
steps in the shared block above.

## Gotchas

- NetEase: after a burst it answers every search with popular songs (or code -462), which looks
  like "not found". `/api/search/get` answers anonymous clients with filler only; the default is
  `/api/search/pc`.
- QQ Music has no instrumental flag: its lyrics are then the notice
  `此歌曲为没有填词的纯音乐，请您欣赏`.
- LRCLIB rate-limits per client (HTTP 429 via Cloudflare).
- Megalobiz's public API: 30 requests a minute and 1,000 a day per IP.
- The syncedlyrics engine's sources (Musixmatch, Genius) currently return filler or refuse.
- jeepney: use `receive`/`send` only after start-up and wait for `RequestName` by hand
  (`_request_name`): `send_and_get_reply` drops every message that is not its reply, and the call
  that made the bus start lyrembed can come first. An activated service answers on
  `DBUS_STARTER_ADDRESS`.

## Open items

- D-Bus activation by dbus-broker itself (service file installed, nothing started by hand) is not
  known to have been checked; a private dbus-daemon and a hand-started `--dbus` were.
