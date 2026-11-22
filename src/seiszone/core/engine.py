"""Engine - owns everything that runs in the background (plan.md 5.14):

  * one thread "seiszone-engine" with its own asyncio loop,
  * one Link task per enabled source (SeedLinkClient, GcfLink), each with its own stop Event,
  * the Gecko listeners (Kelunji Gecko recorders connect IN, config["gecko"]["ports"]),
  * SeisZone's own SeedLink server (config["seedlink_server"]),
  * SeisZone's own GCF server over UDP (config["gcf_server"], off by default, port 1567),
  * the DataHub, the RecordingManager (a hub listener) and the ResponseStore.

The user interface calls the public methods from its own thread. They change config.json at
once (the config is the truth) and then ask the loop to bring the running links in line with
it, so calls in quick succession (disable, enable, edit) always end in the configured state.
A listener port that is busy (another program, an old SeisZone still closing) is shown in
status() with the program that holds it ("owner": the old Gecko SeedLink service or another
program, see core/ports.py) and tried again every LISTEN_RETRY seconds (with the settings the
listeners were opened with: a settings change is applied by apply_settings only), so a port
that becomes free is taken within a few seconds; it never stops the engine.

The SeedLink server's packet numbers continue from the previous run (state "server_seq" or the
newest packet in the disk buffer, plus SEQ_SAFETY_GAP for the packets numbered after the last
save), so a number a downstream client saved is never given to another packet after SeisZone
restarts. The disk buffer (core/packet_store.py, config seedlink_server.buffer_days) keeps the
packets for days: SeedLink clients of this PC backfill from it, and the viewers read its
per-station files like a recording folder.
"""

import asyncio
import errno
import logging
import os
import threading
import time

from .. import config as cfg_mod
from .. import paths
from . import ports, utc_clock
from .history import RecordedData
from .gcf_link import GcfLink
from .gecko import GeckoReceiver
from .hub import DataHub
from .links import RECEIVING
from .packet_store import PacketStore
from .recorder import RecordingManager
from .seedlink_client import SeedLinkClient
from .gcf_server import GcfServer
from .seedlink_server import SeedLinkServer
from .stationxml import ResponseStore

log = logging.getLogger("seiszone")

LINK_CLASSES = {"seedlink": SeedLinkClient, "gcf-udp": GcfLink, "gcf-tcp": GcfLink}
START_TIMEOUT = 10        # s start() waits for the listeners
LINK_STOP_WAIT = 3        # s a link gets to say goodbye (BYE, GCFSTOP) before it is cancelled
SERVER_CLOSE_WAIT = 2     # s for a listener and its connections to close
TICK = 1.0                # s between housekeeping steps
STATUS_LOG_EVERY = 60.0   # s between one-line status messages in the log
LISTEN_RETRY = 3.0        # s between new tries of a listener port that could not be opened (cheap)
ADDR_IN_USE = {errno.EADDRINUSE, 10048}   # 10048 = WSAEADDRINUSE
ADDR_DENIED = {errno.EACCES, 10013}       # 10013 = WSAEACCES (port excluded / reserved by Windows)
SEQ_SAFETY_GAP = 100000   # server packet numbers skipped at start (numbered after the last save)
STORE_FLUSH_EVERY = 5.0   # s between flushes of the disk buffer
STORE_PURGE_EVERY = 600.0  # s between removals of what is older than the buffer keeps


def listen_error(port, exc, owner=None):
    """A busy or blocked port in words the user understands (owner: see ports.describe_owner)."""
    if exc.errno in ADDR_IN_USE:
        if owner == "old-service":
            return f"port {port} is used by the old Gecko SeedLink service"
        return f"port {port} is in use by another program"
    if exc.errno in ADDR_DENIED:
        return f"port {port} is blocked (reserved by Windows or another program)"
    return f"port {port} cannot be opened ({getattr(exc, 'strerror', None) or exc})"


def busy_port_owner(port, exc):
    """(owner, pid) of a port whose bind failed: only "address in use" has an owner to look for
    (a read-only query of this PC's listener table, a few milliseconds)."""
    if exc.errno not in ADDR_IN_USE:
        return None, None
    try:
        return ports.describe_owner(port)
    except Exception:  # never let the lookup break the listener retry
        log.debug("owner of port %s not found", port, exc_info=True)
        return None, None


def owner_note(owner, pid):
    """', process id N' for the log line of another program's port (never a program name)."""
    return f", process id {pid}" if owner == "other" and pid else ""


def station_descriptions(src):
    """{"NET.STA": description} configured for a source (shown by the SeedLink server)."""
    out = {}
    for st in src.get("stations") or []:
        if st.get("desc") and st.get("net") and st.get("sta"):
            out[f"{st['net']}.{st['sta']}"] = st["desc"]
    g = src.get("gcf") or {}
    if g.get("desc"):
        out[f"{g.get('net', 'XX')}.{g.get('sta', 'GCF')}"] = g["desc"]
    return out


class _Running:
    """One started link: the Link object, its stop Event and its task."""

    __slots__ = ("link", "stop", "task")

    def __init__(self, link, stop, task):
        self.link, self.stop, self.task = link, stop, task


class Engine:
    def __init__(self, config, state=None, hub=None):
        self.config = config
        self.state = state if state is not None else cfg_mod.State()
        self.hub = hub if hub is not None else DataHub()
        self.hub.set_buffer_seconds(self._buffer_seconds())
        self.store = None             # PacketStore (the SeedLink server's disk buffer) or None
        self._store_cfg = None        # (days, folder) it was opened with
        self._store_error = ""
        self._open_store()
        saved = self.state.get("server_seq")
        last = self.store.last_seq() if self.store is not None else None
        if not self.hub.packets and (isinstance(saved, int) or last is not None):
            base = saved if isinstance(saved, int) else last
            if last is not None and ((last - base) & 0xFFFFFF) < 0x800000:
                base = last               # the disk holds newer packets than the state says
            self.hub.seq = (base + SEQ_SAFETY_GAP) & 0xFFFFFF
        self._saved_seq = None        # hub.seq last written to the state
        self.responses = ResponseStore()
        self.recorder = RecordingManager(self.hub, self.state, self.config.get("recording", "folder"))
        self._recorder_open = True
        self.history = RecordedData(self.recording_folders)   # the viewers' look into the recordings
        self.history_path = self._history_path()
        self._history_loaded = False
        self.gecko = GeckoReceiver(self.hub, rename=self.config.get("gecko", "rename") or {})
        self.server = None            # SeedLinkServer while the listener is open
        self.started = None           # wall time of start()
        self._lock = threading.RLock()
        self._thread = None
        self._loop = None
        self._quit = None             # asyncio.Event: set by stop()
        self._ready = threading.Event()
        self._links = {}              # source id -> _Running (engine thread only)
        self._changes = None          # asyncio.Lock: one change of links / listeners at a time
        self._gecko_servers = {}      # configured port -> asyncio.Server
        self._gecko_errors = {}       # configured port -> error text
        self._gecko_owners = {}       # configured port -> (owner, pid) of a busy port
        self._gecko_cfg = None        # (enabled, bind, ports) the listeners were opened with
        self._sl_server = None        # asyncio.Server of the SeedLink server
        self._sl_port = None          # port it really listens on (differs from config for port 0)
        self._sl_error = ""
        self._sl_owner = (None, None)  # (owner, pid) of its port while that is busy
        self._sl_cfg = None           # (enabled, bind, port) it was opened with
        self.gcf_server = None        # GcfServer while its UDP port is open
        self._gs_port = None
        self._gs_error = ""
        self._gs_cfg = None           # (enabled, bind, port, max_rate) it was opened with
        self._last_status_log = 0.0
        self._last_retry = 0.0
        self._last_flush = 0.0
        self._last_purge = 0.0
        self._purging = False

    # ================================================================== life cycle
    def start(self):
        """Start the engine thread; returns once the listeners are open (or have failed)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not self._recorder_open:   # started again after stop(): a fresh recorder
                self.recorder = RecordingManager(self.hub, self.state, self.config.get("recording", "folder"))
                self._recorder_open = True
            if self.store is None or self.store.closed:
                self._store_cfg = None
                self._open_store()
            if not self._history_loaded:
                self._history_loaded = True
                try:
                    n = self.hub.load_history(self.history_path)
                    if n:
                        log.info("viewer history of the last day: %d channel(s)", n)
                except Exception:
                    log.exception("viewer history not loaded")
            self._loop = asyncio.new_event_loop()
            self._quit = asyncio.Event()
            self._ready.clear()
            self.started = time.time()
            self._start_clock()
            self._thread = threading.Thread(target=self._run, name="seiszone-engine", daemon=True)
            self._thread.start()
        if not self._ready.wait(START_TIMEOUT):
            log.error("engine: listeners not ready after %d s", START_TIMEOUT)

    def _start_clock(self):
        """Check the PC clock against internet time servers (utc_clock), unless the settings
        say "time": {"check": false}. It is process-wide and keeps running after stop()."""
        t = self.config.get("time") or {}
        if t.get("check", True) is False:
            utc_clock.clock.turn_off()
        else:
            utc_clock.clock.start(t.get("servers") or None)

    def stop(self, timeout=5):
        """Stop links (GCFSTOP / BYE are sent), close listeners, close the recorder, save the
        state. Safe to call more than once and from any thread except the engine's own; never
        waits longer than about `timeout` seconds for the engine thread."""
        with self._lock:
            thread, loop, quit_ev = self._thread, self._loop, self._quit
            self._thread = None
        if thread is not None:
            try:
                loop.call_soon_threadsafe(quit_ev.set)
            except RuntimeError:  # the loop has already ended
                pass
            thread.join(timeout)
            if thread.is_alive():
                log.warning("engine thread did not end within %s s - continuing the shutdown", timeout)
            else:
                log.info("engine stopped")
        self._save_seq()
        if self._history_loaded and self.hub.summaries:
            try:
                self.hub.save_history(self.history_path)
            except Exception:
                log.exception("viewer history not saved")
        self.history.stop()
        with self._lock:
            if self._recorder_open:
                self._recorder_open = False
                try:
                    self.recorder.close()
                except Exception:
                    log.exception("recorder close failed")
        self.state.flush()

    def recording_folders(self):
        """Where the viewers look for recorded data: the recording folder setting and the
        folder of every recording there has been."""
        folders = [self.config.get("recording", "folder") or paths.recordings_dir()]
        try:
            folders += [s.get("folder") for s in self.recorder.sessions() if s.get("folder")]
        except Exception:
            pass
        if self.store is not None:   # the SeedLink server's disk buffer: every channel, days back
            folders.append(self.store.folder)
        return list(dict.fromkeys(f for f in folders if f))

    # ================================================================== the disk buffer
    def _store_settings(self):
        s = self.config.get("seedlink_server") or {}
        try:
            days = max(0.0, float(s.get("buffer_days", 2)))
        except (TypeError, ValueError):
            days = 2.0
        folder = str(s.get("buffer_folder") or "").strip()
        if not folder:   # the user's own config: LOCALAPPDATA; another one (a trial, a test): beside it
            path = getattr(self.config, "path", None)
            own = paths.config_file()
            if path and os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(own)):
                folder = os.path.join(os.path.dirname(os.path.abspath(path)), "ServerBuffer")
            else:
                folder = paths.server_buffer_dir()
        return days, folder

    def _open_store(self):
        """Open, re-open or close the disk buffer as the settings say (any thread, before the
        engine runs, or the engine thread)."""
        days, folder = self._store_settings()
        if (days, folder) == self._store_cfg and (self.store is not None or days <= 0):
            return
        old = self.store
        if old is not None and old.folder == folder and days > 0 and not old.closed:
            old.days = days               # only the number of days changed
            self._store_cfg = (days, folder)
            log.info("SeedLink server buffer: keeps %g day(s) in %s", days, folder)
            return
        if old is not None:
            self.hub.store = None
            self.store = None
            old.close()
        self._store_cfg = (days, folder)
        self._store_error = ""
        if days <= 0:
            log.info("SeedLink server buffer: off (packets kept in memory only)")
            return
        try:
            self.store = PacketStore(folder, days)
        except OSError as e:
            self._store_error = f"cannot use {folder} ({e.strerror or e})"
            log.warning("SeedLink server buffer: %s - packets kept in memory only", self._store_error)
            return
        self.hub.store = self.store
        st = self.store.status()
        log.info("SeedLink server buffer: %g day(s) in %s (%s)", days, folder,
                 "empty" if st["oldest"] is None else
                 "from " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(st["oldest"])))

    def _store_status(self):
        days, folder = self._store_cfg or self._store_settings()
        if self.store is None:
            return {"enabled": False, "days": days, "folder": folder, "error": self._store_error,
                    "bytes": None, "oldest": None}
        st = self.store.status()
        st.update({"enabled": True, "error": ""})
        return st

    def _purge_store(self):
        store = self.store
        try:
            if store is not None:
                n = store.purge()
                if n:
                    log.info("SeedLink server buffer: %d file(s) older than %g day(s) removed", n, store.days)
        except Exception:
            log.exception("SeedLink server buffer: clean-up failed")
        finally:
            self._purging = False

    def _history_path(self):
        """The user's own config: LOCALAPPDATA\\SeisZone\\view_history.npz; another config file
        (a field trial, a test): beside it, so it never touches the user's."""
        path = getattr(self.config, "path", None)
        own = paths.config_file()
        if path and os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(own)):
            return os.path.join(os.path.dirname(os.path.abspath(path)), "view_history.npz")
        return os.path.join(paths.local_dir(), "view_history.npz")

    @property
    def running(self):
        t = self._thread
        return t is not None and t.is_alive()

    def _run(self):
        loop = self._loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception:
            log.exception("engine failed")
        finally:
            self._ready.set()
            try:
                self._cancel_leftovers(loop)
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    @staticmethod
    def _cancel_leftovers(loop):
        tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            loop.run_until_complete(asyncio.wait(tasks, timeout=SERVER_CLOSE_WAIT))

    async def _main(self):
        self._changes = asyncio.Lock()
        try:
            async with self._changes:
                await self._open_gecko()
                await self._open_server()
                await self._open_gcf_server()
                for src in self.config.sources():
                    self.hub.descriptions.update(station_descriptions(src))
                    if src.get("enabled", True):
                        self._start_link(src)
            n = sum(1 for s in self.config.sources() if s.get("enabled", True))
            log.info("engine started: %d source(s), Gecko %s, SeedLink server %s, GCF server %s", n,
                     self._gecko_text(), self._server_text(), self._gcf_server_text())
            self._last_status_log = self._last_retry = time.time()
        finally:
            self._ready.set()
        house = asyncio.ensure_future(self._housekeeping())
        try:
            await self._quit.wait()
        finally:
            house.cancel()
            async with self._changes:
                await self._shutdown()

    async def _shutdown(self):
        running = list(self._links.values())
        self._links.clear()
        for r in running:
            r.stop.set()
        await self._wait_links(running)
        await self._close_gecko(list(self._gecko_servers))
        await self._close_server()
        self._close_gcf_server()
        self._save_seq()
        if self.store is not None:
            self.store.close()
        self.state.flush()

    def _save_seq(self):
        """Keep the newest server packet number in the state (see SEQ_SAFETY_GAP)."""
        seq = self.hub.seq
        if seq != self._saved_seq:
            self._saved_seq = seq
            self.state.set("server_seq", seq)

    @staticmethod
    async def _wait_links(running):
        tasks = [r.task for r in running if not r.task.done()]
        if not tasks:
            return
        _done, pending = await asyncio.wait(tasks, timeout=LINK_STOP_WAIT)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=1)

    # ================================================================== thread-safe API
    def call_soon(self, fn, *args):
        """Run fn(*args) in the engine thread (directly when the engine is not running)."""
        loop = self._loop
        if self.running and loop is not None:
            try:
                loop.call_soon_threadsafe(fn, *args)
                return
            except RuntimeError:
                pass
        fn(*args)

    def _submit(self, coro_fn, *args):
        """Run a coroutine in the engine loop; returns its concurrent Future (None when not running)."""
        loop = self._loop
        if self.running and loop is not None:
            try:
                return asyncio.run_coroutine_threadsafe(coro_fn(*args), loop)
            except RuntimeError:
                pass
        return None

    def add_source(self, src):
        """Validate, save and start a source. Returns the saved source dict (with its id)."""
        s = self.config.add_source(src)
        log.info("source added: %s", cfg_mod.source_label(s))
        self._submit(self._sync_source, s["id"])
        return s

    def update_source(self, src):
        """Save a changed source and restart its link."""
        s = self.config.update_source(src)
        self._submit(self._sync_source, s["id"])
        return s

    def remove_source(self, source_id):
        """Stop and forget a source (its recordings stay on disk). True if it existed."""
        removed = self.config.remove_source(source_id)
        if self._submit(self._sync_source, source_id) is None:
            self.state.drop_source(source_id)  # running: dropped once the link has stopped
        if removed:
            log.info("source %s removed", source_id)
        return removed

    def set_enabled(self, source_id, enabled):
        """Switch a source on or off (saved). Returns the source dict, None if unknown."""
        src = self.config.source(source_id)
        if src is None:
            return None
        src["enabled"] = bool(enabled)
        s = self.config.update_source(src)
        log.info("source %s %s", cfg_mod.source_label(s), "enabled" if enabled else "disabled")
        self._submit(self._sync_source, source_id)
        return s

    def reconnect(self, source_id):
        """Drop the connection of a source and connect again at once. False if it is not running."""
        r = self._links.get(source_id)
        if r is None:
            return False
        self.call_soon(r.link.kick.set)
        return True

    def apply_settings(self):
        """Re-read the settings and restart only what changed: Gecko listeners, SeedLink server,
        live buffer length, Gecko rename table, recording folder. Returns a concurrent Future
        that is done when the listeners are restarted (None when the engine is not running)."""
        self.hub.set_buffer_seconds(self._buffer_seconds())
        self.recorder.folder_default = self.config.get("recording", "folder")
        # GeckoReceiver checks and normalises a rename table; the live receiver takes the result,
        # so Geckos that are connected now use the new names from their next record on
        self.gecko.rename = GeckoReceiver(self.hub, self.config.get("gecko", "rename") or {}).rename
        return self._submit(self._apply_listeners)

    def status(self):
        """Snapshot for the user interface (plan.md 5.14); safe from any thread."""
        sources = {}
        for sid, r in list(self._links.items()):
            st = r.link.status()
            st["name"] = r.link.src.get("name", "")
            sources[sid] = st
        g = self.config.get("gecko") or {}
        s = self.config.get("seedlink_server") or {}
        srv = self.server
        g_owner, g_pid = self._gecko_owner()
        s_owner, s_pid = self._sl_owner if self._sl_error else (None, None)
        return {
            "sources": sources,
            "gecko": {"enabled": bool(g.get("enabled", True)), "ports": list(g.get("ports") or []),
                      "listening": self._gecko_ports(), "error": "; ".join(self._gecko_errors.values()),
                      "owner": g_owner, "owner_pid": g_pid,
                      "links": [dict(x) for x in list(self.gecko.links.values())]},
            "server": {"enabled": bool(s.get("enabled", True)), "bind": s.get("bind", "0.0.0.0"),
                       "port": self._sl_port or s.get("port"), "listening": self._sl_server is not None,
                       "error": self._sl_error, "owner": s_owner, "owner_pid": s_pid,
                       "clients": srv.clients if srv else 0,
                       "peers": [dict(p) for p in list(srv.peers)] if srv else [],
                       "control": list(srv.control) if srv else [],
                       "buffer": self._store_status()},
            "gcf_server": self._gcf_server_status(),
            "started": self.started if self.running else None,
            "clock": utc_clock.clock.status(),
        }

    # ================================================================== links (engine thread)
    def _start_link(self, src):
        cls = LINK_CLASSES.get(src.get("kind"))
        if cls is None:
            log.error("source %s has an unknown kind %r - not started", src.get("id"), src.get("kind"))
            return
        link = cls(src, self.hub, self.state)
        stop = asyncio.Event()
        task = asyncio.ensure_future(self._run_link(link, stop))
        self._links[src["id"]] = _Running(link, stop, task)

    @staticmethod
    async def _run_link(link, stop):
        try:
            await link.run(stop)
        except asyncio.CancelledError:
            raise
        except Exception:  # links never raise for network trouble: this is a bug, keep the rest running
            log.exception("link %s ended with an error", link.address)

    async def _sync_source(self, source_id):
        """Bring the running link of one source in line with the config (stop / restart / start)."""
        async with self._changes:
            old = self._links.pop(source_id, None)
            if old is not None:
                old.stop.set()
                await self._wait_links([old])
            src = self.config.source(source_id)
            if src is None:
                # removed: forget its SeedLink resume positions only now, after the link has
                # stopped, so a record arriving meanwhile cannot store them again
                self.state.drop_source(source_id)
            else:
                self.hub.descriptions.update(station_descriptions(src))
                if src.get("enabled", True):
                    self._start_link(src)

    # ================================================================== listeners (engine thread)
    def _buffer_seconds(self):
        try:
            return max(1.0, float(self.config.get("buffer_minutes") or 60) * 60)
        except (TypeError, ValueError):
            return 3600.0

    def _gecko_settings(self):
        g = self.config.get("gecko") or {}
        ports = []
        for p in g.get("ports") or []:
            try:
                ports.append(int(p))
            except (TypeError, ValueError):
                log.error("Gecko port %r is not a number - ignored", p)
        return bool(g.get("enabled", True)), g.get("bind") or "0.0.0.0", tuple(dict.fromkeys(ports))

    def _server_settings(self):
        s = self.config.get("seedlink_server") or {}
        try:
            port = int(s.get("port", 18000))
        except (TypeError, ValueError):
            port = 18000
        return bool(s.get("enabled", True)), s.get("bind") or "0.0.0.0", port

    def _gcf_server_settings(self):
        s = self.config.get("gcf_server") or {}
        try:
            port = int(s.get("port", 1567))
        except (TypeError, ValueError):
            port = 1567
        try:
            max_rate = float(s.get("max_rate", 1000))
        except (TypeError, ValueError):
            max_rate = 1000.0
        return bool(s.get("enabled", False)), s.get("bind") or "0.0.0.0", port, max_rate

    def _gcf_names(self):
        """{sid: (system id, stream id)} of the channels read over GCF: the GCF server sends
        them under the digitiser's own names."""
        out = {}
        for src in self.config.sources():
            g = src.get("gcf") or {}
            system = g.get("system_id") or (src.get("details") or {}).get("system_id")
            if not system:
                continue
            for stream, names in (g.get("map") or {}).items():
                try:
                    loc, cha = names
                except (TypeError, ValueError):
                    continue
                out[f"{g.get('net', 'XX')}.{g.get('sta', 'GCF')}.{loc}.{cha}"] = (system, stream)
        return out

    async def _open_gcf_server(self, cfg=None):
        if cfg is None:
            cfg = self._gs_cfg = self._gcf_server_settings()
        enabled, bind, port, max_rate = cfg
        if not enabled:
            self._gs_error = ""
            return
        if self.gcf_server is not None:
            return
        server = GcfServer(self.hub, self.state, max_rate=max_rate,
                           seedlink_port=self._sl_port if self._sl_server is not None else None,
                           gcf_names=self._gcf_names())
        try:
            self._gs_port = await server.open(bind, port)
        except OSError as e:
            server.close()
            text = listen_error(port, e)
            if e.errno in ADDR_IN_USE:
                text += " (only one program on this PC can use it - is the earlier viewer running?)"
            if self._gs_error != text:
                log.error("GCF server: %s (%s)", text, e)
            self._gs_error = text
            return
        self.gcf_server = server
        self._gs_error = ""
        log.info("GCF server listening on UDP %s:%d (channels up to %g sps)", bind, self._gs_port, max_rate)

    def _close_gcf_server(self):
        srv, self.gcf_server = self.gcf_server, None
        self._gs_port, self._gs_error = None, ""
        if srv is not None:
            srv.close()

    def _gcf_server_status(self):
        enabled, bind, port, max_rate = self._gs_cfg or self._gcf_server_settings()
        srv = self.gcf_server
        return {"enabled": enabled, "bind": bind, "port": self._gs_port or port, "max_rate": max_rate,
                "listening": srv is not None, "error": self._gs_error,
                "clients": len(srv.clients) if srv else 0, "peers": srv.peers() if srv else [],
                "blocks": srv.blocks_sent if srv else 0, "control": list(srv.control) if srv else []}

    def _gcf_server_text(self):
        if not (self._gs_cfg or self._gcf_server_settings())[0]:
            return "off"
        return self._gs_error or f"on UDP {self._gs_port}"

    def _gecko_owner(self):
        """(owner, pid) of the busy Gecko ports: the old service first (the one SeisZone can
        offer to stop), then another program; (None, None) when no port is busy."""
        owners = [self._gecko_owners.get(p, (None, None)) for p in list(self._gecko_errors)]
        for kind in ("old-service", "other"):
            for owner, pid in owners:
                if owner == kind:
                    return owner, pid
        return None, None

    def _gecko_ports(self):
        return [srv.sockets[0].getsockname()[1] for srv in list(self._gecko_servers.values()) if srv.sockets]

    async def _open_gecko(self, only=None, cfg=None):
        """Open the Gecko listeners (all configured ports, or the ports in `only`). With `cfg`
        (a retry) the settings the listeners were opened with are used and kept, so a retry
        never takes over a settings change that apply_settings has still to make."""
        if cfg is None:
            cfg = self._gecko_cfg = self._gecko_settings()
        enabled, bind, ports = cfg
        if not enabled:
            return
        for port in ports:
            if (only is not None and port not in only) or port in self._gecko_servers:
                continue
            try:
                self._gecko_servers[port] = await asyncio.start_server(self.gecko.handle, bind, port)
                self._gecko_errors.pop(port, None)
                self._gecko_owners.pop(port, None)
                log.info("Gecko receiver listening on %s:%d", bind, self._gecko_servers[port].sockets[0].getsockname()[1])
            except OSError as e:
                owner, pid = busy_port_owner(port, e)
                text = listen_error(port, e, owner)
                if self._gecko_errors.get(port) != text:
                    log.error("Gecko receiver: %s (%s%s)", text, e, owner_note(owner, pid))
                self._gecko_errors[port] = text
                self._gecko_owners[port] = (owner, pid)

    async def _close_gecko(self, ports):
        servers = [self._gecko_servers.pop(p) for p in ports if p in self._gecko_servers]
        for p in ports:
            self._gecko_errors.pop(p, None)
            self._gecko_owners.pop(p, None)
        await self._close_servers(servers)

    async def _open_server(self, cfg=None):
        if cfg is None:
            cfg = self._sl_cfg = self._server_settings()
        enabled, bind, port = cfg
        if not enabled:
            self._sl_error = ""      # an old "port in use" does not apply to a server that is off
            return
        if self._sl_server is not None:
            return
        org = self.config.get("seedlink_server", "organization") or "SeisZone"
        server = SeedLinkServer(self.hub, organization=org)
        try:
            self._sl_server = await asyncio.start_server(server.handle, bind, port)
        except OSError as e:
            owner, pid = busy_port_owner(port, e)
            text = listen_error(port, e, owner)
            if self._sl_error != text:
                log.error("SeedLink server: %s (%s%s)", text, e, owner_note(owner, pid))
            self._sl_error = text
            self._sl_owner = (owner, pid)
            return
        self.server = server
        self._sl_port = self._sl_server.sockets[0].getsockname()[1]
        self._sl_error = ""
        log.info("SeedLink server listening on %s:%d", bind, self._sl_port)

    async def _close_server(self):
        srv, self._sl_server = self._sl_server, None
        self.server, self._sl_port, self._sl_error = None, None, ""
        if srv is not None:
            await self._close_servers([srv])

    @staticmethod
    async def _close_servers(servers):
        """Stop listening and drop the connections (a Gecko simply calls again)."""
        for srv in servers:
            srv.close()
            srv.close_clients()
        for srv in servers:
            try:
                await asyncio.wait_for(srv.wait_closed(), SERVER_CLOSE_WAIT)
            except TimeoutError:
                log.warning("a listener did not close within %d s", SERVER_CLOSE_WAIT)

    async def _apply_listeners(self):
        async with self._changes:
            enabled, bind, ports = self._gecko_settings()
            old = self._gecko_cfg
            if old != (enabled, bind, ports):
                if not enabled or old is None or old[1] != bind:
                    await self._close_gecko(list(set(self._gecko_servers) | set(self._gecko_errors)))
                else:  # only ports added / removed: Geckos on the kept ports stay connected
                    await self._close_gecko([p for p in set(self._gecko_servers) | set(self._gecko_errors)
                                             if p not in ports])
                await self._open_gecko()
            if self._server_settings() != self._sl_cfg or (self._sl_cfg[0] and self._sl_server is None):
                await self._close_server()
                await self._open_server()
            elif self.server is not None:
                self.server.organization = self.config.get("seedlink_server", "organization") or "SeisZone"
            if self._gcf_server_settings() != self._gs_cfg or (self._gs_cfg[0] and self.gcf_server is None):
                self._close_gcf_server()
                await self._open_gcf_server()
            elif self.gcf_server is not None:   # the SeedLink port it names may have changed
                self.gcf_server.seedlink_port = self._sl_port if self._sl_server is not None else None
            self._open_store()

    # ================================================================== housekeeping
    async def _housekeeping(self):
        while True:
            await asyncio.sleep(TICK)
            try:
                self._save_seq()
                self.state.maybe_flush()
                now = time.time()
                if self.store is not None and now - self._last_flush >= STORE_FLUSH_EVERY:
                    self._last_flush = now
                    self.store.flush()
                if self.store is not None and not self._purging and now - self._last_purge >= STORE_PURGE_EVERY:
                    self._last_purge = now
                    self._purging = True
                    asyncio.get_running_loop().run_in_executor(None, self._purge_store)
                if now - self._last_retry >= LISTEN_RETRY:
                    self._last_retry = now
                    await self._retry_listeners()
                if now - self._last_status_log >= STATUS_LOG_EVERY:
                    self._last_status_log = now
                    log.info("status: %s", self.status_line())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("engine housekeeping failed")

    async def _retry_listeners(self):
        """Ports that were busy may be free now (e.g. another program has ended)."""
        if not (self._gecko_errors or self._sl_error or self._gs_error):
            return
        async with self._changes:
            if self._gs_error and self._gs_cfg is not None:
                await self._open_gcf_server(cfg=self._gs_cfg)
            if self._gecko_errors and self._gecko_cfg is not None:
                await self._open_gecko(only=set(self._gecko_errors), cfg=self._gecko_cfg)
            if self._sl_error and self._sl_cfg is not None:
                await self._open_server(cfg=self._sl_cfg)

    def _gecko_text(self):
        enabled, _bind, ports = self._gecko_cfg or self._gecko_settings()
        if not enabled:
            return "off"
        if self._gecko_errors:
            return "; ".join(self._gecko_errors.values())
        return "on " + ",".join(str(p) for p in self._gecko_ports())

    def _server_text(self):
        if not (self._sl_cfg or self._server_settings())[0]:
            return "off"
        return self._sl_error or f"on {self._sl_port}"

    def status_line(self):
        """One line for the log: sources receiving, channels, Gecko links, SeedLink clients."""
        st = self.status()
        srcs = list(st["sources"].values())
        rec = sum(1 for s in srcs if s["state"] == RECEIVING)
        sessions = [s for s in self.recorder.sessions() if s.get("state") == "recording"]
        return (f"sources {rec}/{len(srcs)} receiving, {len(self.hub.stream_ids())} channels, "
                f"Gecko links {len(st['gecko']['links'])} ({self._gecko_text()}), "
                f"SeedLink server {self._server_text()} with {st['server']['clients']} client(s), "
                f"GCF server {self._gcf_server_text()} with {st['gcf_server']['clients']} client(s), "
                f"recordings {len(sessions)}")
