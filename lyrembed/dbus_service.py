"""lyrembed on the D-Bus session bus, for Jewelcase and other programs.

Why: a sandboxed program (Jewelcase as a Flatpak) can neither start host
programs nor read lyrembed's config, but it may talk to a bus name it is
allowed (``--talk-name=io.github.sobakah.lyrembed``). The bus starts this
service when it is called (a service file in
``~/.local/share/dbus-1/services``, written by ``--install-dbus-service``),
so lyrembed can stay a pipx program on the host. cdport has the same
(its ``dbus_service.py``); the loop and the service file are copied from
there, not imported: the two programs stay independent.

Everything Jewelcase does through the command line and lyrembed's config file
has a call. Romanizing runs in this process, as ``romanize --stdio`` does;
a page and a search for lyrics run as child processes (``lyrembed DIR
--no-browser`` and ``lyrembed DIR --auto``), whose ends are signalled.

It must be no way out of a sandbox. No call runs a command. The page's
address never leaves this side: its token opens the page's whole API (any
folder, settings, updates), and a Flatpak shares the host's 127.0.0.1, so
the service opens the browser itself (OpenPage, ShowPage) instead of
handing the address over. A page may then open any folder, since the user
decides on it what is written; an unattended search only one inside the
music folder, which a sandboxed caller can see anyway.

The bus name is the app ID lyrembed would have as a Flatpak, whose own
service file would then take over.

Interface ``io.github.sobakah.lyrembed1`` on ``/io/github/sobakah/lyrembed``:

- ``GetInfo() -> a{sv}``: Version, InterfaceVersion, MusicFolder ("" when
  there is none), RomanizationLayout and MaxFileCount (the config's),
  Romanizes (the languages whose packages are installed: ko, ja, zh, any).
- ``ArtistAliases() -> aas``: the alias groups in effect, built-in and the
  user's, as ``--list-aliases`` prints them.
- ``Romanize(s lyrics, s han_hint) -> (s lyrics, s language, s reason)``: as
  ``romanize --stdio``; han_hint is "", "ja" or "zh". "" stands for its
  null: no lyrics when they were left alone, and why in reason.
- ``OpenPage(s folder) -> (s run, b opened)``: lyrembed's page on folder
  ("": the folder picker), opened in the default browser once it is up;
  opened is false when no browser could be started. A page already open
  on that folder is shown again instead of a second one.
- ``ShowPage(s run) -> b``: open a page run in the browser again (a lost
  tab).
- ``FetchLyrics(s folder, a{sv} options) -> s run``: ``--auto`` on folder,
  which must be inside the music folder; option ``Romanize`` (b, else the
  config's auto_romanize).
- ``Runs() -> a(sss)``: the runs still going: run, kind ("page" or
  "fetch"), the folder it was started on ("" for the picker; the user may
  switch folders on a page).
- ``Stop(s run)``: as Ctrl+C; only the first Stop of a run sends it.
- Signal ``Finished(s run, i exit_code)``: a run ended, with lyrembed's exit
  code (0 done, 1 some files failed, 2 unusable folder, 130 stopped).
"""

from __future__ import annotations

import itertools
import os
import queue
import re
import shlex
import signal
import subprocess
import threading
import time
import traceback
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config, container, core, text, ui, web

BUS_NAME = "io.github.sobakah.lyrembed"
OBJECT_PATH = "/io/github/sobakah/lyrembed"
INTERFACE = "io.github.sobakah.lyrembed1"
INTERFACE_VERSION = 1
ERROR = INTERFACE + ".Error"
# Without runs or calls for this long, the service ends; the bus starts it
# again at the next call. As long as Jewelcase keeps its romanize helper, so
# a song or two apart do not each pay the start (~1 s with pykakasi); not
# longer, so an old version does not stay after pipx updated lyrembed.
IDLE_EXIT = 300.0
# How long OpenPage waits for the page before giving up on the run: below
# the 25 s D-Bus clients (GDBus, libdbus) wait for a reply by default, else
# the caller gives up first and a page starts nobody sees.
URL_TIMEOUT = 20.0
# A page nobody opened ends after this long (web.serve's unvisited): the
# browser may not have come up.
UNVISITED_TIMEOUT = 600.0
# Lyrics are a few kilobytes; this only keeps one call from holding the
# romanizer, and every call after it, for minutes.
MAX_LYRICS = 200_000
_UINT32_MAX = 2**32 - 1

INTROSPECTION = f"""<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="{INTERFACE}">
    <method name="GetInfo"><arg name="info" type="a{{sv}}" direction="out"/></method>
    <method name="ArtistAliases"><arg name="groups" type="aas" direction="out"/></method>
    <method name="Romanize">
      <arg name="lyrics" type="s" direction="in"/>
      <arg name="han_hint" type="s" direction="in"/>
      <arg name="romanized" type="s" direction="out"/>
      <arg name="language" type="s" direction="out"/>
      <arg name="reason" type="s" direction="out"/>
    </method>
    <method name="OpenPage">
      <arg name="folder" type="s" direction="in"/>
      <arg name="run" type="s" direction="out"/>
      <arg name="opened" type="b" direction="out"/>
    </method>
    <method name="ShowPage">
      <arg name="run" type="s" direction="in"/>
      <arg name="opened" type="b" direction="out"/>
    </method>
    <method name="FetchLyrics">
      <arg name="folder" type="s" direction="in"/>
      <arg name="options" type="a{{sv}}" direction="in"/>
      <arg name="run" type="s" direction="out"/>
    </method>
    <method name="Runs"><arg name="runs" type="a(sss)" direction="out"/></method>
    <method name="Stop"><arg name="run" type="s" direction="in"/></method>
    <signal name="Finished">
      <arg name="run" type="s"/><arg name="exit_code" type="i"/>
    </signal>
  </interface>
  <interface name="org.freedesktop.DBus.Introspectable">
    <method name="Introspect"><arg name="xml" type="s" direction="out"/></method>
  </interface>
  <interface name="org.freedesktop.DBus.Peer">
    <method name="Ping"/>
  </interface>
</node>
"""


def _in_signatures(introspection: str) -> dict[str, str]:
    """Each method's input signature, from the introspection data, so the
    calls are declared once."""
    root = ElementTree.fromstring(introspection.split("\n", 2)[2])   # without the DOCTYPE
    interface = next(i for i in root.iter("interface") if i.get("name") == INTERFACE)
    return {m.get("name"): "".join(a.get("type") for a in m.iter("arg") if a.get("direction") == "in")
            for m in interface.iter("method")}


_SIGNATURES = _in_signatures(INTROSPECTION)

# The page's first line (web.RUNNING_LINE): its address, again after an
# update on the page restarted it. Page runs get --color never, so it is
# never wrapped in colour codes.
_PAGE = re.compile("^" + re.escape(web.RUNNING_LINE).replace(
    re.escape("{url}"), r"(http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]+)") + "$")

NEEDS_JEEPNEY = ("The D-Bus service needs the Python package jeepney, which pipx installs with lyrembed "
                 "(in a checkout: pip install jeepney).")


class ServiceError(Exception):
    """A call that cannot be answered: becomes a D-Bus error reply."""

    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name


def _invalid(message: str) -> ServiceError:
    return ServiceError("org.freedesktop.DBus.Error.InvalidArgs", message)


@dataclass
class Run:
    id: str
    kind: str                     # "page" or "fetch"
    process: Any                  # subprocess.Popen, or a test's stand-in
    started: float
    folder: str = ""
    url: str = ""                 # never sent over the bus (see the module)
    waiting: list = field(default_factory=list)   # OpenPage calls waiting for the page
    stopping: bool = False        # SIGINT sent: never a second one


def page_command(folder: str = "") -> list[str]:
    """The page of the lyrembed this service runs from (pipx launcher or
    checkout), like Jewelcase's `lyrembed DIR --no-browser`."""
    from .desktop import launch_command

    return [*launch_command(), *([folder] if folder else []), "--no-browser", "--color", "never",
            "--unvisited-timeout", f"{UNVISITED_TIMEOUT:.0f}"]


def fetch_command(folder: str, romanize: bool | None = None) -> list[str]:
    """Batch mode on folder, like Jewelcase's `lyrembed DIR --auto --color never`."""
    from .desktop import launch_command

    flag = [] if romanize is None else ["--romanize" if romanize else "--no-romanize"]
    return [*launch_command(), folder, "--auto", "--color", "never", *flag]


def spawn_run(command: list[str]) -> subprocess.Popen:
    # Its own session, so the service's terminal or process group signals do
    # not reach it. That alone does not protect it under dbus-broker, which
    # runs the service as a systemd unit and stops the unit's cgroup with
    # it: serve therefore never ends while runs are going (_outlive_runs).
    # The home folder as the working folder: a page without a folder then
    # opens the picker in the music folder. stderr goes where the service's
    # goes (the journal, when the bus started it).
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                            start_new_session=True, cwd=Path.home())


def music_folder() -> Path | None:
    """The music folder, or None when there is none: core.music_folder falls
    back to the home folder, which is far too wide for unattended writing."""
    folder = core.music_folder().resolve()
    return None if folder == Path.home().resolve() else folder


def _inside(folder: Path, other: Path) -> bool:
    return folder == other or other in folder.parents or folder in other.parents


class Service:
    """The calls and the runs, without the bus: call() answers a method
    call, outgoing() turns what the runs, the romanizer and the browser
    reported into replies and signals."""

    def __init__(self, spawn: Callable[[list[str]], Any] = spawn_run,
                 clock: Callable[[], float] = time.monotonic,
                 open_url: Callable[[str], bool] = container.open_url) -> None:
        self.runs: dict[str, Run] = {}
        self.events: queue.Queue[tuple] = queue.Queue()
        self._spawn = spawn
        self._clock = clock
        self._open_url = open_url
        self._ids = itertools.count(1)
        self._romanizing: queue.Queue[tuple[Any, str, str | None]] = queue.Queue()
        self._pending = 0           # calls answered later: Romanize, a browser being opened
        self._romanizer: threading.Thread | None = None

    def busy(self) -> bool:
        return bool(self.runs) or self._pending > 0

    # --- method calls -----------------------------------------------------
    def call(self, member: str, signature: str, args: tuple, caller: Any) -> tuple[str, tuple] | None:
        """(signature, body) of the reply, or None when it comes later
        (Romanize, OpenPage, ShowPage). Raises ServiceError."""
        if member not in _SIGNATURES:
            raise ServiceError("org.freedesktop.DBus.Error.UnknownMethod", f"No method {member} in {INTERFACE}.")
        if signature != _SIGNATURES[member]:
            raise _invalid(f"{member} takes ({_SIGNATURES[member]}), not ({signature}).")
        if member == "GetInfo":
            return "a{sv}", (self.info(),)
        if member == "ArtistAliases":
            return "aas", ([list(group) for group in config.alias_groups(self._config())],)
        if member == "Romanize":
            self.romanize(args[0], args[1], caller)
        elif member == "OpenPage":
            self.open_page(args[0], caller)
        elif member == "ShowPage":
            self.show_page(args[0], caller)
        elif member == "FetchLyrics":
            return "s", (self.fetch(args[0], args[1]).id,)
        elif member == "Runs":
            return "a(sss)", ([(r.id, r.kind, r.folder) for r in self.runs.values()],)
        else:
            self.stop(args[0])
            return "", ()
        return None

    @staticmethod
    def _config() -> dict[str, Any]:
        # Read at each call: it may have changed. A broken file is a warning
        # there and gives the defaults, as at lyrembed's start.
        return config.load_config()[0]

    def info(self) -> dict[str, tuple[str, Any]]:
        settings = self._config().get("settings", {})
        try:
            max_files = min(max(0, int(settings.get("max_file_count", 0))), _UINT32_MAX)
        except (TypeError, ValueError):
            max_files = 0
        music = music_folder()
        return {"Version": ("s", config.__version__),
                "InterfaceVersion": ("u", INTERFACE_VERSION),
                "MusicFolder": ("s", str(music) if music else ""),
                "RomanizationLayout": ("s", str(settings.get("romanization_layout") or "")),
                "MaxFileCount": ("u", max_files),
                "Romanizes": ("as", [key for key in ("ko", "ja", "zh", "any") if text.ROMANIZERS[key][1]()])}

    def romanize(self, lyrics: str, han_hint: str, caller: Any) -> None:
        if han_hint not in ("", "ja", "zh"):
            raise _invalid(f'han_hint is "", "ja" or "zh", not {han_hint!r}.')
        if len(lyrics) > MAX_LYRICS:
            raise _invalid(f"Lyrics of {len(lyrics)} characters; at most {MAX_LYRICS} are romanized.")
        if self._romanizer is None:
            # One thread, in order: the romanizers' caches are not built for
            # several at once, and the loop stays free meanwhile.
            self._romanizer = threading.Thread(target=self._romanize_all, name="lyrembed-dbus-romanize",
                                               daemon=True)
            self._romanizer.start()
        self._pending += 1
        self._romanizing.put((caller, lyrics, han_hint or None))

    def _romanize_all(self) -> None:
        while True:
            caller, lyrics, hint = self._romanizing.get()
            try:
                result, language, reason = core.romanize_for_display(lyrics, hint)
                self.events.put(("reply", caller, "sss", (result or "", language or "", reason)))
            except Exception as exc:   # a bug in a romanizer: this call fails, not the service
                traceback.print_exc()
                self.events.put(("fail", caller, ERROR + ".Failed", f"{type(exc).__name__}: {exc}"))

    def _show(self, run: Run, callers: list, signature: str) -> None:
        """Open run's page in the browser, in a thread (a browser can take
        its time to start), and answer callers with whether it worked."""
        def open_it() -> None:
            try:
                opened = bool(self._open_url(run.url))
            except Exception:   # webbrowser has its own ideas; one failed open, not the service
                traceback.print_exc()
                opened = False
            body = (run.id, opened) if signature == "sb" else (opened,)
            for caller in callers:
                self.events.put(("reply", caller, signature, body))
        self._pending += len(callers)
        threading.Thread(target=open_it, name=f"lyrembed-dbus-show-{run.id}", daemon=True).start()

    @staticmethod
    def _folder(folder: str) -> Path:
        path = Path(folder)
        if not path.is_absolute():
            raise _invalid(f"The folder must be an absolute path: {folder}")
        if not path.is_dir():
            raise ServiceError(ERROR + ".NoFolder", f"There is no folder {folder}.")
        return path.resolve()

    def _busy_with(self, folder: Path, kinds: tuple[str, ...]) -> Run | None:
        # The folder a run was started on: the user may switch folders on a
        # page, which the service does not see. A guard, not a lock.
        return next((r for r in self.runs.values()
                     if r.kind in kinds and r.folder and _inside(Path(r.folder), folder)), None)

    def open_page(self, folder: str, caller: Any) -> Run:
        """The page run for folder; caller is answered once it is shown."""
        resolved = self._folder(folder) if folder else None
        if resolved is not None:
            if busy := self._busy_with(resolved, ("fetch",)):
                raise ServiceError(ERROR + ".Busy", f"Lyrics are being fetched for {busy.folder} (run {busy.id}).")
            same = next((r for r in self.runs.values() if r.kind == "page" and r.folder == str(resolved)), None)
            if same is not None:   # shown again, not a second lyrembed on the folder
                if same.url:
                    self._show(same, [caller], "sb")
                else:
                    same.waiting.append(caller)
                return same
        run = self._start("page", page_command(str(resolved or "")), str(resolved or ""))
        run.waiting.append(caller)
        return run

    def show_page(self, run_id: str, caller: Any) -> None:
        run = self.runs.get(run_id)
        if run is None or run.kind != "page":
            raise ServiceError(ERROR + ".UnknownRun", f"No page run {run_id!r} (it may have ended).")
        if not run.url:
            raise ServiceError(ERROR + ".NotUp", f"The page of run {run_id} is not up yet.")
        self._show(run, [caller], "b")

    def fetch(self, folder: str, options: dict[str, tuple[str, Any]]) -> Run:
        for key, (sig, _) in options.items():
            if key != "Romanize":
                raise _invalid(f"Unknown option {key} (known: Romanize).")
            if sig != "b":
                raise _invalid(f"{key} is a boolean (b), not {sig}.")
        resolved = self._folder(folder)
        music = music_folder()
        if music is None:
            raise ServiceError(ERROR + ".NotInMusicFolder",
                               "There is no music folder (XDG_MUSIC_DIR or ~/Music); open the folder as a page.")
        if music not in resolved.parents and resolved != music:
            # Unattended writing only where a sandboxed caller sees anyway.
            raise ServiceError(ERROR + ".NotInMusicFolder",
                               f"{folder} is not inside the music folder ({music}); open it as a page instead.")
        if busy := self._busy_with(resolved, ("page", "fetch")):
            raise ServiceError(ERROR + ".Busy", f"Run {busy.id} is working on {busy.folder}.")
        romanize = options["Romanize"][1] if "Romanize" in options else None
        return self._start("fetch", fetch_command(str(resolved), romanize), str(resolved))

    def _start(self, kind: str, command: list[str], folder: str) -> Run:
        try:
            process = self._spawn(command)
        except OSError as exc:
            raise ServiceError(ERROR + ".Failed", f"Could not start lyrembed: {exc}") from exc
        run = Run(str(next(self._ids)), kind, process, self._clock(), folder=folder)
        self.runs[run.id] = run
        threading.Thread(target=self._read, args=(run,), name=f"lyrembed-dbus-run-{run.id}", daemon=True).start()
        return run

    def stop(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        if run is None:
            raise ServiceError(ERROR + ".UnknownRun", f"No run {run_id!r} (it may have ended).")
        self._interrupt(run)

    @staticmethod
    def _interrupt(run: Run) -> None:
        if run.stopping:
            return   # a second Ctrl+C would cut off the stopping of the first
        run.stopping = True
        try:
            run.process.send_signal(signal.SIGINT)   # as Ctrl+C: the current track finishes
        except (ProcessLookupError, OSError):
            pass   # ended meanwhile; its exit is reported as usual

    # --- what the runs, the romanizer and the browser report -------------------
    def _read(self, run: Run) -> None:
        """In a thread per run: its output, until it ends. Reads to the end
        so a run never blocks on a full pipe."""
        for line in run.process.stdout:
            if run.kind == "page" and (page := _PAGE.match(line.rstrip("\n"))):
                self.events.put(("url", run.id, page.group(1)))
        self.events.put(("exit", run.id, run.process.wait()))

    def outgoing(self) -> list[tuple]:
        """Replies and signals to send now: ("return", caller, signature,
        body), ("error", caller, name, message) or ("signal", member,
        signature, body)."""
        out: list[tuple] = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind in ("reply", "fail"):
                self._pending -= 1
                out.append(("return" if kind == "reply" else "error", *event[1:]))
                continue
            run = self.runs.get(event[1])
            if run is None:
                continue
            if kind == "url":
                run.url = event[2]
                if run.waiting:
                    self._show(run, run.waiting, "sb")
                    run.waiting = []
            else:
                del self.runs[run.id]
                out += [("error", caller, ERROR + ".Failed",
                         f"lyrembed ended (exit code {event[2]}) before its page was up.")
                        for caller in run.waiting]
                out.append(("signal", "Finished", "si", (run.id, int(event[2]))))
        for run in self.runs.values():
            if run.waiting and self._clock() - run.started > URL_TIMEOUT:
                out += [("error", caller, ERROR + ".Failed",
                         f"lyrembed's page was not up after {URL_TIMEOUT:.0f} s; it is stopped.")
                        for caller in run.waiting]
                run.waiting = []
                self._interrupt(run)
        return out


# --- on the bus --------------------------------------------------------------
def serve(service: Service | None = None, idle_exit: float = IDLE_EXIT) -> int:
    """Own the bus name and answer calls until idle; the exit code."""
    try:
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        ui.error(NEEDS_JEEPNEY)
        return 2
    service = service or Service()
    # Started by the bus: answer on the bus that started us.
    bus = os.environ.get("DBUS_STARTER_ADDRESS") or "SESSION"
    try:
        conn = open_dbus_connection(bus=bus)
    except (OSError, KeyError, ValueError) as exc:
        ui.error(f"lyrembed: cannot connect to the D-Bus session bus: {exc}")
        return 1
    with conn:
        try:
            return _answer(conn, service, idle_exit)
        except KeyboardInterrupt:
            # Started by hand: Ctrl+C reaches the runs too, which have
            # sessions of their own and would otherwise be waited for.
            for run in service.runs.values():
                service._interrupt(run)
            return 130
        finally:
            _outlive_runs(service)


def _outlive_runs(service: Service) -> None:
    """Wait for the runs still going, whatever ended the loop: under
    dbus-broker the service's end stops its systemd unit, and every process
    in it, a page or a search half done included."""
    while service.runs:
        service.outgoing()   # drops the ended ones; nobody to tell any more
        time.sleep(0.5)


def _send(conn: Any, make: Callable[[], Any], call: Any = None) -> None:
    """Send one message. One that cannot be built or serialised (a value
    out of range) fails its call with an error, not the service; a bus that
    is gone (OSError) still ends the loop."""
    from jeepney import new_error

    try:
        conn.send(make())
    except OSError:
        raise
    except Exception as exc:
        traceback.print_exc()
        if call is not None:
            conn.send(new_error(call, "org.freedesktop.DBus.Error.Failed", "s",
                                (f"{type(exc).__name__}: {exc}",)))


def _answer(conn: Any, service: Service, idle_exit: float) -> int:
    """The loop: take the name, answer calls, pass on what the runs report.

    Idle, it releases the name first and ends only once the bus confirmed
    that and nothing came meanwhile: a call routed to this process just
    before would otherwise go unanswered, while after the release the bus
    starts a new lyrembed for the next one."""
    from jeepney import DBusAddress, HeaderFields, MessageType, new_error, new_method_return, new_signal
    from jeepney.bus_messages import message_bus

    try:
        owner, inbox = _request_name(conn, message_bus.RequestName(BUS_NAME, 4))  # 4: don't queue
    except (OSError, EOFError, ValueError, TimeoutError) as exc:
        ui.error(f"lyrembed: could not take the name {BUS_NAME}: {exc}")
        return 1
    if owner not in (1, 4):   # 1: now the owner, 4: already was
        ui.error(f"lyrembed: {BUS_NAME} is already taken by another lyrembed.")
        return 0
    emitter = DBusAddress(OBJECT_PATH, interface=INTERFACE)
    last = time.monotonic()
    releasing: int | None = None   # the ReleaseName's serial, once sent
    released = False
    while True:
        for item in service.outgoing():
            if item[0] == "return":
                _send(conn, lambda i=item: new_method_return(i[1], i[2], i[3]), item[1])
            elif item[0] == "error":
                _send(conn, lambda i=item: new_error(i[1], i[2], "s", (i[3],)))
            else:
                _send(conn, lambda i=item: new_signal(emitter, i[1], i[2], i[3]))
        if service.busy():
            last = time.monotonic()
        elif time.monotonic() - last > idle_exit and releasing is None:
            releasing = next(conn.outgoing_serial)
            conn.send(message_bus.ReleaseName(BUS_NAME), serial=releasing)
        try:
            msg = inbox.pop(0) if inbox else conn.receive(timeout=0.25)
        except TimeoutError:
            if released and not service.busy():
                return 0
            continue
        except (OSError, EOFError, ValueError):   # the bus went away (logout)
            return 0
        if releasing is not None and msg.header.fields.get(HeaderFields.reply_serial) == releasing:
            released = True
            continue
        if msg.header.message_type != MessageType.method_call:
            continue   # signals such as NameAcquired
        last = time.monotonic()
        fields = msg.header.fields
        interface = fields.get(HeaderFields.interface)
        member = fields.get(HeaderFields.member)
        signature = fields.get(HeaderFields.signature, "")
        try:
            if interface == "org.freedesktop.DBus.Introspectable" and member == "Introspect":
                answer: tuple[str, tuple] | None = ("s", (INTROSPECTION,))
            elif interface == "org.freedesktop.DBus.Peer" and member == "Ping":
                answer = ("", ())
            elif fields.get(HeaderFields.path) != OBJECT_PATH or interface not in (INTERFACE, None):
                raise ServiceError("org.freedesktop.DBus.Error.UnknownMethod",
                                   f"No {interface}.{member} on {fields.get(HeaderFields.path)}.")
            else:
                answer = service.call(member, signature, msg.body, msg)
        except ServiceError as exc:
            _send(conn, lambda m=msg, e=exc: new_error(m, e.name, "s", (str(e),)))
            continue
        except Exception as exc:   # a bug, or the file system: one failed call, not the service
            traceback.print_exc()
            _send(conn, lambda m=msg, e=exc: new_error(m, "org.freedesktop.DBus.Error.Failed", "s",
                                                       (f"{type(e).__name__}: {e}",)))
            continue
        if answer is not None:
            _send(conn, lambda m=msg, a=answer: new_method_return(m, a[0] or None, a[1]), msg)


def _request_name(conn: Any, request: Any, timeout: float = 10) -> tuple[int, list]:
    """RequestName's answer, and what came before it. Started by the bus,
    the call that started us arrives as soon as the name is ours, which can
    be before the reply: jeepney's send_and_get_reply would drop it."""
    from jeepney import HeaderFields

    serial = next(conn.outgoing_serial)
    conn.send(request, serial=serial)
    early, deadline = [], time.monotonic() + timeout
    while True:
        msg = conn.receive(timeout=max(0.0, deadline - time.monotonic()))
        if msg.header.fields.get(HeaderFields.reply_serial) == serial:
            if msg.header.message_type.name == "error":
                raise ValueError(f"the bus refused: {msg.body}")
            return msg.body[0], early
        early.append(msg)


# --- the service file ---------------------------------------------------------
def service_file_path() -> Path:
    from .desktop import _data_home

    return _data_home() / "dbus-1" / "services" / f"{BUS_NAME}.service"


def service_file_text(command: list[str]) -> str:
    # The bus splits Exec like a shell (quotes and backslashes).
    return "\n".join(["[D-BUS Service]", f"Name={BUS_NAME}",
                      "Exec=" + " ".join(shlex.quote(a) for a in [*command, "--dbus"]), ""])


def install_service_file() -> tuple[Path, list[str], str | None]:
    """Write the service file for the lyrembed that runs this; returns its
    path, the command, and why the bus could not be told (None when it was)."""
    from . import container
    from .desktop import launch_command

    # In a toolbox or distrobox the bus on the host starts the service.
    command = container.on_host(launch_command())
    path = service_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(service_file_text(command), encoding="utf-8")
    return path, command, _reload_bus()


def remove_service_file() -> Path | None:
    path = service_file_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return None
    _reload_bus()
    return path


def _reload_bus() -> str | None:
    """dbus-daemon notices a new service file by itself; dbus-broker reads
    them when told to reload."""
    try:
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return NEEDS_JEEPNEY
    try:
        with open_dbus_connection(bus="SESSION") as conn:
            reply = conn.send_and_get_reply(message_bus.ReloadConfig(), timeout=5)
    except (OSError, KeyError, ValueError, TimeoutError) as exc:
        return str(exc)
    return None if reply.header.message_type.name == "method_return" else str(reply.body)
