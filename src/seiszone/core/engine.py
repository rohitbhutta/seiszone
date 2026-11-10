"""Engine - owns everything that runs in the background (plan.md 5.14):

  * one thread "seiszone-engine" with its own asyncio loop,
  * one Link task per enabled source (SeedLinkClient, GcfLink), each with its own stop Event,
  * the Gecko listeners (Kelunji Gecko recorders connect IN, config["gecko"]["ports"]),
  * SeisZone's own SeedLink server (config["seedlink_server"]),
  * the DataHub, the RecordingManager (a hub listener) and the ResponseStore.

The user interface calls the public methods from its own thread. They change config.json at
once (the config is the truth) and then ask the loop to bring the running links in line with
it, so calls in quick succession (disable, enable, edit) always end in the configured state.
A listener port that is busy (another program, an old SeisZone still closing) is shown in
status() and tried again every LISTEN_RETRY seconds (with the settings the listeners were
opened with: a settings change is applied by apply_settings only); it never stops the engine.

The SeedLink server's packet numbers continue from the previous run (state "server_seq" plus
SEQ_SAFETY_GAP for the packets numbered after the last save), so a number a downstream client
saved is never given to another packet after SeisZone restarts.
"""

import asyncio
import errno
import logging
import threading
import time

from .. import config as cfg_mod
from .gcf_link import GcfLink
from .gecko import GeckoReceiver
from .hub import DataHub
from .links import RECEIVING
from .recorder import RecordingManager
from .seedlink_client import SeedLinkClient
from .seedlink_server import SeedLinkServer
from .stationxml import ResponseStore

log = logging.getLogger("seiszone")

LINK_CLASSES = {"seedlink": SeedLinkClient, "gcf-udp": GcfLink, "gcf-tcp": GcfLink}
START_TIMEOUT = 10        # s start() waits for the listeners
LINK_STOP_WAIT = 3        # s a link gets to say goodbye (BYE, GCFSTOP) before it is cancelled
SERVER_CLOSE_WAIT = 2     # s for a listener and its connections to close
TICK = 1.0                # s between housekeeping steps
STATUS_LOG_EVERY = 60.0   # s between one-line status messages in the log
LISTEN_RETRY = 30.0       # s between new tries of a listener port that could not be opened
ADDR_IN_USE = {errno.EADDRINUSE, 10048}   # 10048 = WSAEADDRINUSE
ADDR_DENIED = {errno.EACCES, 10013}       # 10013 = WSAEACCES (port excluded / reserved by Windows)
SEQ_SAFETY_GAP = 100000   # server packet numbers skipped at start (numbered after the last save)


def listen_error(port, exc):
    """A busy or blocked port in words the user understands."""
    if exc.errno in ADDR_IN_USE:
        return f"port {port} is in use by another program"
    if exc.errno in ADDR_DENIED:
        return f"port {port} is blocked (reserved by Windows or another program)"
    return f"port {port} cannot be opened ({getattr(exc, 'strerror', None) or exc})"


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
        saved = self.state.get("server_seq")
        if isinstance(saved, int) and not self.hub.packets:
            self.hub.seq = (saved + SEQ_SAFETY_GAP) & 0xFFFFFF
        self._saved_seq = None        # hub.seq last written to the state
        self.responses = ResponseStore()
        self.recorder = RecordingManager(self.hub, self.state, self.config.get("recording", "folder"))
        self._recorder_open = True
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
        self._gecko_cfg = None        # (enabled, bind, ports) the listeners were opened with
        self._sl_server = None        # asyncio.Server of the SeedLink server
        self._sl_port = None          # port it really listens on (differs from config for port 0)
        self._sl_error = ""
        self._sl_cfg = None           # (enabled, bind, port) it was opened with
        self._last_status_log = 0.0
        self._last_retry = 0.0

    # ================================================================== life cycle
    def start(self):
        """Start the engine thread; returns once the listeners are open (or have failed)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not self._recorder_open:   # started again after stop(): a fresh recorder
                self.recorder = RecordingManager(self.hub, self.state, self.config.get("recording", "folder"))
                self._recorder_open = True
            self._loop = asyncio.new_event_loop()
            self._quit = asyncio.Event()
            self._ready.clear()
            self.started = time.time()
            self._thread = threading.Thread(target=self._run, name="seiszone-engine", daemon=True)
            self._thread.start()
        if not self._ready.wait(START_TIMEOUT):
            log.error("engine: listeners not ready after %d s", START_TIMEOUT)

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
        with self._lock:
            if self._recorder_open:
                self._recorder_open = False
                try:
                    self.recorder.close()
                except Exception:
                    log.exception("recorder close failed")
        self.state.flush()

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
                for src in self.config.sources():
                    self.hub.descriptions.update(station_descriptions(src))
                    if src.get("enabled", True):
                        self._start_link(src)
            n = sum(1 for s in self.config.sources() if s.get("enabled", True))
            log.info("engine started: %d source(s), Gecko %s, SeedLink server %s", n,
                     self._gecko_text(), self._server_text())
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
        self._save_seq()
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
        return {
            "sources": sources,
            "gecko": {"enabled": bool(g.get("enabled", True)), "ports": list(g.get("ports") or []),
                      "listening": self._gecko_ports(), "error": "; ".join(self._gecko_errors.values()),
                      "links": [dict(x) for x in list(self.gecko.links.values())]},
            "server": {"enabled": bool(s.get("enabled", True)), "bind": s.get("bind", "0.0.0.0"),
                       "port": self._sl_port or s.get("port"), "listening": self._sl_server is not None,
                       "error": self._sl_error, "clients": srv.clients if srv else 0,
                       "peers": [dict(p) for p in list(srv.peers)] if srv else []},
            "started": self.started if self.running else None,
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
            port = int(s.get("port", 18010))
        except (TypeError, ValueError):
            port = 18010
        return bool(s.get("enabled", True)), s.get("bind") or "0.0.0.0", port

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
                log.info("Gecko receiver listening on %s:%d", bind, self._gecko_servers[port].sockets[0].getsockname()[1])
            except OSError as e:
                text = listen_error(port, e)
                if self._gecko_errors.get(port) != text:
                    log.error("Gecko receiver: %s (%s)", text, e)
                self._gecko_errors[port] = text

    async def _close_gecko(self, ports):
        servers = [self._gecko_servers.pop(p) for p in ports if p in self._gecko_servers]
        for p in ports:
            self._gecko_errors.pop(p, None)
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
            text = listen_error(port, e)
            if self._sl_error != text:
                log.error("SeedLink server: %s (%s)", text, e)
            self._sl_error = text
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

    # ================================================================== housekeeping
    async def _housekeeping(self):
        while True:
            await asyncio.sleep(TICK)
            try:
                self._save_seq()
                self.state.maybe_flush()
                now = time.time()
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
        if not (self._gecko_errors or self._sl_error):
            return
        async with self._changes:
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
                f"recordings {len(sessions)}")
