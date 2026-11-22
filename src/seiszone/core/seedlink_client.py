"""SeedLink 3 client: receives miniSEED records from a SeedLink server (Guralp Fortimus on
18000, any public or private SeedLink server, another SeisZone) and hands them to the hub.

Negotiation (multi-station mode, one block per configured station):

    HELLO                       -> 2 lines (software, organization)
    STATION sta net             -> OK | ERROR (station not on this server: skipped, noted)
    SELECT x                    -> OK | ERROR, one per selector of the station
    DATA <last seq + 1> <time>  -> OK | ERROR (some servers refuse it: DATA <seq>, then DATA)

Live first (version 1.2.1, multi-station mode). A SeedLink server sends what a client missed
oldest first, so after SeisZone had been closed for hours the newest sample it showed stayed
hours old until the whole gap had come through a slow VPN ("latency 7 h 28 min" while the
instrument was fine). Now, when the resume position is older than LIVE_FIRST seconds (or there
is none yet: a new station gets its last day, BACKFILL_SECONDS), the connection asks for plain
DATA - live data at once - and the gap is noted in the State ("seedlink_gaps": begin time per
station). A second connection (_backfill) then asks for the same stations with TIME <begin>,
feeds what was missed into the hub (recordings and the viewers' history get it; the hub drops
what it already has) and closes when it reaches the time the live data began. The begin time
moves forward as it goes, so a restart continues the fetch where it stopped. The fetch waits
(at most LIVE_WAIT s) for the live connection's first record of its stations, so it knows where
to stop, and decodes the records of each network read together (mseed.decode_many).
    END                         -> the server streams "SL" + 6 hex digits + 512-byte record

With no stations configured the uni-station form is used (SELECTs, then DATA starts the
stream at once). The last sequence number received per station and the end time of that
record are kept in State, so after a restart of SeisZone or a lost VPN the server sends
exactly what was missed (while it still holds it); the time ("DATA n begin", as libslink
sends it) lets a server whose numbers no longer match start at the right packet. Keep-alive "INFO ID" after KEEPALIVE seconds without data; the connection is
dropped and made again after SILENT_RECONNECT seconds without any byte. run() never raises.
"""

import asyncio
import fnmatch
import logging
import re
import time

from . import mseed
from .links import CONNECTED, CONNECTING, ERROR, STOPPED, Link

log = logging.getLogger("seiszone")

PACKET = 520                 # "SL" + 6 hex digits + 512-byte record
BACKFILL_SECONDS = 24 * 3600  # history asked for the first time a station is read (TIME)
LIVE_FIRST = 120             # a resume position older than this: live data first, the gap behind it
GAPS = "seedlink_gaps"       # State key: {"<source id>|<station>": begin time of data still to fetch}
BACKFILL_MARGIN = 300        # s the saved begin time stays behind the fetch (long low-rate records)
BACKFILL_IDLE = 90           # s without a packet before a fetch counts as finished
BACKFILL_RETRY = 30          # s before a failed fetch is tried again
LIVE_WAIT = 20               # s a fetch waits for the live connection's first record of its stations
MASK = 0xFFFFFF
CONNECT_TIMEOUT = 15
REPLY_TIMEOUT = 30           # one answer during negotiation
KEEPALIVE = 60               # seconds without data -> INFO ID
SILENT_RECONNECT = 180       # seconds without any byte -> reconnect
POLL = 5                     # read timeout, so the silence checks run
UNI = "*"                    # State key of the resume position in uni-station mode
HEX6 = re.compile(rb"[0-9A-Fa-f]{6}")


class SeedLinkError(Exception):
    """The server is no SeedLink server, or refused what was asked."""


def _ok(reply, command):
    """True for OK, False for ERROR; anything else is a protocol error."""
    up = reply.upper()
    if up.startswith("OK"):
        return True
    if up.startswith("ERROR"):
        return False
    raise SeedLinkError(f"unexpected answer {reply!r} to {command}")


def wire_selector(sel):
    """A selector as sent to the server. SeedLink selectors are [LL]CCC[.T]: a two-character
    location or none. Units whose location code is one character ("1", padded "1 " in the data:
    Minimus Lite and older Minimus) were given "1HH?", which they refuse ("refused selector 1HH?",
    every reconnect of Tuljapur until 1.6.0). The location's second character becomes "?", which
    matches the padding: "1HH?" -> "1?HH?" (checked read-only on Tuljapur: OK, its HH? channels)."""
    sel = str(sel).strip()
    neg = "!" if sel.startswith("!") else ""
    base, dot, typ = sel[len(neg):].partition(".")
    if len(base) == 4:
        base = base[0] + "?" + base[1:]
    return neg + base + dot + typ


def _span(seconds):
    s = max(0, int(seconds))
    if s >= 3600:
        return f"{s // 3600} h {s % 3600 // 60:02d} min"
    return f"{s // 60} min" if s >= 60 else f"{s} s"


class SeedLinkClient(Link):
    kind = "seedlink"

    def __init__(self, src, hub, state=None):
        super().__init__(src, hub, state)
        self.name = src.get("name") or self.address
        self.server = ""          # banner line of the server
        self.organization = ""
        self.bad = 0              # packets whose record did not decode (skipped)
        self.garbage = 0          # bytes skipped while looking for the next packet
        self.missing = []         # configured stations the server does not have
        self._keys = {}           # "NET.STA" of a record -> State key (configured station)
        self._patterns = []       # [(net, sta, key)] of the stations accepted by the server
        self._uni_retry = []      # uni-station: DATA commands to send after an ERROR
        self._last_data = self._last_byte = self._last_keepalive = 0.0
        self._live_start = {}     # State key -> start time of the first live record of this connection
        self.backfilled = 0       # records fetched by the background fetch

    def set_state(self, state, detail=""):
        """Keep the note about stations missing on the server visible while receiving."""
        if not detail and self.missing and state != STOPPED:
            detail = self._missing_text()
        super().set_state(state, detail)

    def _missing_text(self):
        if len(self.missing) == 1:
            return f"station {self.missing[0]} not on this server"
        return f"stations {', '.join(self.missing)} not on this server"

    # ------------------------------------------------------------------ run loop
    async def run(self, stop):
        """Connect, receive, reconnect - until `stop` is set. A set `kick` drops the
        connection and connects again at once."""
        while not stop.is_set():
            self.kick.clear()
            session = asyncio.ensure_future(self._session())
            stop_t = asyncio.ensure_future(stop.wait())
            kick_t = asyncio.ensure_future(self.kick.wait())
            try:
                await asyncio.wait({session, stop_t, kick_t}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                stop_t.cancel()
                kick_t.cancel()
                if not session.done():
                    session.cancel()
                    try:
                        await session
                    except BaseException:  # its own cancellation / error: reported below
                        pass
            err = session.exception() if session.done() and not session.cancelled() else None
            if err is not None:
                self._report(err)
            if stop.is_set():
                break
            if self.kick.is_set():
                log.info("SeedLink %s: reconnecting to %s on request", self.name, self.address)
                continue
            if await self.pause(stop):
                break
        self.set_state(STOPPED)
        self._flush_state()

    def _report(self, err):
        self.failures += 1
        if isinstance(err, TimeoutError) and not str(err):
            text = "timed out"
        elif isinstance(err, (OSError, SeedLinkError, ConnectionError, TimeoutError)):
            text = str(err) or type(err).__name__
        else:
            log.error("SeedLink %s: unexpected error", self.name, exc_info=err)
            text = f"internal error: {err}"
        self.set_state(ERROR, text)
        if self.failures == 1:
            log.warning("SeedLink %s (%s): %s", self.name, self.address, text)
        else:
            log.debug("SeedLink %s (%s): %s (attempt %d)", self.name, self.address, text, self.failures)

    def _flush_state(self):
        if self.state_store is not None:
            self.state_store.flush()

    # ------------------------------------------------------------------ one connection
    async def _session(self):
        host, port = self.src["host"], int(self.src["port"])
        self.missing = []
        self.set_state(CONNECTING)
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)
        except TimeoutError:
            raise TimeoutError(f"no answer from {self.address} within {CONNECT_TIMEOUT} s") from None
        self.since = time.time()
        log.info("SeedLink %s: connected to %s", self.name, self.address)
        reason = "closed"
        fetch = None
        try:
            self._live_start = {}
            await self._negotiate(reader, writer)
            if self.pending_gaps():
                fetch = asyncio.ensure_future(self._backfill())
            await self._receive(reader, writer)
        except asyncio.CancelledError:
            reason = "stopped" if not self.kick.is_set() else "reconnect requested"
            raise
        except Exception as e:
            reason = str(e) or type(e).__name__
            raise
        finally:
            if fetch is not None and not fetch.done():
                fetch.cancel()
                try:
                    await fetch
                except BaseException:
                    pass
            try:
                writer.write(b"BYE\r\n")
                writer.close()
            except Exception:
                pass
            log.info("SeedLink %s: disconnected from %s (%s)", self.name, self.address, reason)
            self._flush_state()

    async def _command(self, reader, writer, text, lines=1):
        writer.write(text.encode("ascii") + b"\r\n")
        await writer.drain()
        out = []
        for _ in range(lines):
            line = await asyncio.wait_for(reader.readline(), REPLY_TIMEOUT)
            if not line:
                raise ConnectionError(f"{self.address} closed the connection after {text.split()[0]}")
            out.append(line.decode("latin-1", "replace").strip())
        return out[0] if lines == 1 else out

    def _data_commands(self, key):
        """DATA commands to try in turn: with the resume sequence number and the end time of the
        last record, with the number only, plain DATA (servers that refuse the arguments)."""
        store = self.state_store
        seq = store.get_seq(self.id, key) if store is not None else None
        now = time.time()
        if key != UNI and store is not None and BACKFILL_SECONDS > 0:
            # live first: plain DATA now, the missed time is fetched by _backfill
            t = store.get_seq_time(self.id, key)
            begin = None
            if seq is None:
                begin = now - BACKFILL_SECONDS            # a new station: its last day
            elif t is not None and now - t > LIVE_FIRST:
                begin = float(t)
            pending = self._gap(key)
            if pending is not None:
                begin = pending if begin is None else min(begin, pending)
            if begin is not None:
                self._set_gap(key, begin)
                return ["DATA"]
        if seq is None:
            if BACKFILL_SECONDS <= 0:
                return ["DATA"]
            begin = time.strftime("%Y,%m,%d,%H,%M,%S", time.gmtime(now - BACKFILL_SECONDS))
            return [f"TIME {begin}", "DATA"]
        cmd = f"DATA {(int(seq) + 1) & MASK:06X}"
        t = store.get_seq_time(self.id, key)
        if t is None:
            return [cmd, "DATA"]
        stamp = time.strftime("%Y,%m,%d,%H,%M,%S", time.gmtime(int(t)))
        return [f"{cmd} {stamp}", cmd, "DATA"]

    # ------------------------------------------------------------------ gaps (live first)
    def _gap(self, key):
        gaps = self.state_store.get(GAPS) if self.state_store is not None else None
        v = (gaps or {}).get(f"{self.id}|{key}")
        return float(v) if isinstance(v, (int, float)) else None

    def _set_gap(self, key, begin):
        """Note (begin: a time) or clear (None) the data still to fetch for a station."""
        if self.state_store is None:
            return
        gaps = dict(self.state_store.get(GAPS) or {})
        k = f"{self.id}|{key}"
        if begin is None:
            if k not in gaps:
                return
            del gaps[k]
        else:
            gaps[k] = float(begin)
        self.state_store.set(GAPS, gaps)

    def pending_gaps(self):
        """{State key: begin time} of this connection's stations with missed data to fetch."""
        out = {}
        for _net, _sta, key in self._patterns:
            g = self._gap(key)
            if g is not None:
                out[key] = g
        return out

    async def _backfill(self):
        """Fetch the missed data of the stations with a gap over a second connection, while the
        main one receives live. Tried again after a failure for as long as the session lives."""
        while self.pending_gaps():
            try:
                await self._backfill_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.info("SeedLink %s: fetching missed data from %s failed (%s); again in %d s",
                         self.name, self.address, str(e) or type(e).__name__, BACKFILL_RETRY)
            if self.pending_gaps():
                await asyncio.sleep(BACKFILL_RETRY)
        if self.detail.startswith("live"):
            self.detail = ""

    async def _backfill_once(self):
        gaps = self.pending_gaps()
        # The fetch stops where the live data begin (the first record of the live connection).
        # Started before that record has come, it could not know where that is: it took the
        # first live records itself (counted as missed data; the hub kept them once) and read on
        # past the live start. So it waits for it first - a quiet station at most LIVE_WAIT s.
        deadline = time.monotonic() + LIVE_WAIT
        while any(k not in self._live_start for k in gaps) and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        host, port = self.src["host"], int(self.src["port"])
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)
        try:
            await self._command(reader, writer, "HELLO", lines=2)
            asked = {}
            for st in self.src.get("stations") or []:
                key = f"{st['net']}.{st['sta']}"
                if key not in gaps:
                    continue
                cmd = f"STATION {st['sta']} {st['net']}"
                if not _ok(await self._command(reader, writer, cmd), cmd):
                    continue
                for sel in st.get("selectors") or []:
                    await self._command(reader, writer, f"SELECT {wire_selector(sel)}")
                stamp = time.strftime("%Y,%m,%d,%H,%M,%S", time.gmtime(int(gaps[key])))
                cmd = f"TIME {stamp}"
                if _ok(await self._command(reader, writer, cmd), cmd):
                    asked[key] = gaps[key]
                else:   # a server without TIME: nothing to fetch this way
                    log.warning("SeedLink %s: %s refused %s - the data missed for %s is not fetched",
                                self.name, self.address, cmd, key)
                    self._set_gap(key, None)
            if not asked:
                return
            writer.write(b"END\r\n")
            await writer.drain()
            first = min(asked.values())
            log.info("SeedLink %s: live; fetching the data missed since %s in the background", self.name,
                     mseed.fmt_time(first))
            done, buf, n, saved_at = set(), bytearray(), 0, time.time()
            newest = dict(asked)
            while len(done) < len(asked):
                try:
                    chunk = await asyncio.wait_for(reader.read(65536), BACKFILL_IDLE)
                except TimeoutError:
                    break                       # nothing more: caught up
                if not chunk:
                    break
                self.bytes += len(chunk)
                buf += chunk
                batch = []
                while len(buf) >= PACKET:
                    if not (buf.startswith(b"SL") and HEX6.fullmatch(bytes(buf[2:8]))):
                        nxt = buf.find(b"SL", 1)
                        del buf[:nxt if nxt > 0 else len(buf)]
                        continue
                    body = bytes(buf[8:PACKET])
                    del buf[:PACKET]
                    res = mseed.parse_record(body, 0, check=False)
                    if not isinstance(res, tuple):
                        continue
                    rec = res[0]
                    if rec.nsamp == 0 or rec.rate <= 0:
                        continue
                    key = self._key(rec.station, rec.net, rec.sta)
                    if key in asked and key not in done:   # (others are not decoded at all)
                        batch.append(rec)
                # the records of this read decoded together: numpy over all of them is 7-10x
                # faster than word by word per record (a day of history is ~100 000 records);
                # a damaged record gets None and is dropped, as parse_record(check=True) did
                for rec, samples in zip(batch, mseed.decode_many(batch)):
                    if samples is None:
                        continue
                    rec._samples = samples
                    key = self._key(rec.station, rec.net, rec.sta)
                    if key not in asked or key in done:
                        continue
                    live = self._live_start.get(key)
                    if live is not None and rec.start >= live:
                        done.add(key)           # reached what the live connection has
                        continue
                    if self.hub.add_record(rec, self.id):
                        self.packets += 1
                        self.backfilled += 1
                        n += 1
                    newest[key] = max(newest[key], rec.start)
                now = time.time()
                if now - saved_at >= 5:
                    saved_at = now
                    for key in asked:
                        if key not in done:
                            self._set_gap(key, max(asked[key], newest[key] - BACKFILL_MARGIN))
                    left = max((self._live_start.get(k, now) - newest[k]) for k in asked if k not in done) \
                        if len(done) < len(asked) else 0
                    self.detail = f"live - fetching missed data, {_span(left)} to go"
            for key in asked:
                self._set_gap(key, None)
            log.info("SeedLink %s: the missed data is fetched (%d records since %s)", self.name, n,
                     mseed.fmt_time(first))
        finally:
            try:
                writer.write(b"BYE\r\n")
                writer.close()
            except Exception:
                pass
            self._flush_state()

    async def _negotiate(self, reader, writer):
        banner = await self._command(reader, writer, "HELLO", lines=2)
        if "seedlink" not in banner[0].lower():
            raise SeedLinkError(f"{self.address} is not a SeedLink server (answer {banner[0][:60]!r})")
        self.server, self.organization = banner
        self._patterns, self._keys = [], {}
        self._uni_retry = []
        stations = self.src.get("stations") or []
        if not stations:
            await self._negotiate_uni(reader, writer)
            return
        for st in stations:
            net, sta = st["net"], st["sta"]
            key = f"{net}.{sta}"
            cmd = f"STATION {sta} {net}"
            if not _ok(await self._command(reader, writer, cmd), cmd):
                log.warning("SeedLink %s: station %s is not on %s", self.name, key, self.address)
                self.missing.append(key)
                continue
            for sel in st.get("selectors") or []:
                cmd = f"SELECT {wire_selector(sel)}"
                if not _ok(await self._command(reader, writer, cmd), cmd):
                    log.warning("SeedLink %s: %s refused selector %s for %s", self.name, self.address,
                                wire_selector(sel), key)
            accepted = False
            for cmd in self._data_commands(key):
                if accepted:
                    break
                accepted = _ok(await self._command(reader, writer, cmd), cmd)
                if not accepted and cmd != "DATA":
                    log.info("SeedLink %s: %s refused %s for %s; asking again with less", self.name,
                             self.address, cmd, key)
            if not accepted:
                log.warning("SeedLink %s: %s refused DATA for %s", self.name, self.address, key)
                self.missing.append(key)
                continue
            self._patterns.append((net, sta, key))
        if not self._patterns:
            raise SeedLinkError(self._missing_text())
        writer.write(b"END\r\n")
        await writer.drain()
        self.set_state(CONNECTED, self.server)

    async def _negotiate_uni(self, reader, writer):
        for sel in self.src.get("selectors") or []:
            cmd = f"SELECT {wire_selector(sel)}"
            if not _ok(await self._command(reader, writer, cmd), cmd):
                log.warning("SeedLink %s: %s refused selector %s", self.name, self.address, wire_selector(sel))
        cmd, *self._uni_retry = self._data_commands(UNI)
        writer.write(cmd.encode("ascii") + b"\r\n")   # no answer: the data follows at once
        await writer.drain()
        self.set_state(CONNECTED, self.server)

    # ------------------------------------------------------------------ receiving
    async def _receive(self, reader, writer):
        buf = bytearray()
        self._last_data = self._last_byte = self._last_keepalive = time.time()
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(65536), POLL)
            except TimeoutError:
                chunk = None
            now = time.time()
            if chunk is not None:
                if not chunk:
                    raise ConnectionError(f"{self.address} closed the connection")
                self.bytes += len(chunk)
                self._last_byte = now
                buf += chunk
                self._consume(buf, writer)
            if now - self._last_byte > SILENT_RECONNECT:
                raise TimeoutError(f"nothing received for {SILENT_RECONNECT} s")
            if now - max(self._last_data, self._last_keepalive) > KEEPALIVE:
                writer.write(b"INFO ID\r\n")
                self._last_keepalive = now
            await writer.drain()

    def _consume(self, buf, writer):
        """Take every complete packet (and reply line) from the front of buf; partial ones
        stay for the next read. Bytes that are neither are skipped up to the next "SL"."""
        pos, n = 0, len(buf)
        while pos < n:
            rest = n - pos
            if buf.startswith(b"SL", pos):
                if rest < 8:
                    break
                head = bytes(buf[pos + 2:pos + 8])
                if head.startswith(b"INFO") or HEX6.fullmatch(head):
                    if rest < PACKET:
                        break
                    if not head.startswith(b"INFO"):   # INFO packets: keep-alive answers, ignored
                        self._packet(int(head, 16), bytes(buf[pos + 8:pos + PACKET]))
                    pos += PACKET
                    continue
            elif buf.startswith((b"OK", b"ERROR"), pos):
                nl = buf.find(b"\n", pos, pos + 200)
                if nl >= 0:
                    self._text(bytes(buf[pos:nl]).decode("latin-1").strip(), writer)
                    pos = nl + 1
                    continue
                if rest < 200:
                    break
            elif rest < 5 and any(t.startswith(bytes(buf[pos:])) for t in (b"SL", b"OK", b"ERROR")):
                break   # the beginning of a packet or reply; the rest is on its way
            nxt = buf.find(b"SL", pos + 1)
            end = nxt if nxt >= 0 else (n - 1 if buf.endswith(b"S") else n)
            end = max(end, pos + 1)
            if not self.garbage:
                log.warning("SeedLink %s: unexpected bytes from %s skipped", self.name, self.address)
            self.garbage += end - pos
            pos = end
        del buf[:pos]

    def _text(self, line, writer):
        if line.upper().startswith("ERROR") and self._uni_retry:
            cmd = self._uni_retry.pop(0)
            log.info("SeedLink %s: %s refused the resume position; sending %s",
                     self.name, self.address, cmd)
            writer.write(cmd.encode("ascii") + b"\r\n")
        else:
            log.debug("SeedLink %s: server says %r", self.name, line)

    def _packet(self, seq, body):
        self._uni_retry = []
        self._last_data = time.time()
        res = mseed.parse_record(body, 0, check=True)
        if not isinstance(res, tuple):
            if not self.bad:
                log.warning("SeedLink %s: packet %06X from %s is no valid miniSEED record; skipped",
                            self.name, seq, self.address)
            self.bad += 1
            return
        rec = res[0]
        if rec.nsamp == 0 or rec.rate <= 0:
            return  # log / event records: no time series
        self.feed(rec)
        if self.failures:
            self.failures = 0
        key = self._key(rec.station, rec.net, rec.sta)
        if key not in self._live_start:
            self._live_start[key] = rec.start
        if self.state_store is not None:
            self.state_store.set_seq(self.id, key, seq, rec.end)

    def _key(self, station, net, sta):
        """State key of a record: the configured station it belongs to (wildcards allowed)."""
        key = self._keys.get(station)
        if key is None:
            key = UNI
            for pnet, psta, pkey in self._patterns:
                if fnmatch.fnmatchcase(sta, psta) and fnmatch.fnmatchcase(net, pnet):
                    key = pkey
                    break
            self._keys[station] = key
        return key
