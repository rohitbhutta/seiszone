"""GcfLink - pulls GCF data from a Guralp digitiser / Scream server and turns it into miniSEED.

UDP ("gcf-udp", Scream protocol, default port 1567), the way Scream's own UDP pull works:
    GCFPING + GCFSEND:B, GCFSEND:B again every REFRESH s (servers drop pull clients that do
    not refresh), a fresh start after SILENT_RESTART s without blocks, GCFSTOP twice on stop
    (UDP may lose one; without it the instrument keeps sending to a port nobody reads).
TCP ("gcf-tcp"): the instrument streams BTP frames or bare blocks; every BTP frame is
    acknowledged, otherwise the instrument resends it.

Every data block becomes 512-byte Steim2 records named with src["gcf"]["net"/"sta"] and
src["gcf"]["map"] {gcf stream id: [loc, cha]}, with a sequence counter per channel; repeated
blocks (refreshes, BTP resends) are dropped before encoding. A non-empty map is the user's
channel selection: streams not in it are not used. Only with an empty map (a source added
without a channel list) are the names made by gcf.default_seed_name. A source belongs to one
digitiser: src["gcf"]["system_id"] (or, for older configs, the one the probe found, else the
first one seen) - blocks of other digitisers behind the same Scream server are dropped, so two
units with the same stream names never mix. Status blocks carry text from the instrument and
are logged.
"""

import asyncio
import collections
import logging
import socket
import time

from . import gcf, mseed
from .links import CONNECTED, CONNECTING, ERROR, STOPPED, WAITING, Link

log = logging.getLogger("seiszone")

REFRESH = 20            # s between GCFSEND:B refreshes
SILENT_RESTART = 60     # s without blocks before the pull is started again / TCP reconnected
CONNECT_TIMEOUT = 15
POLL = 0.5              # s; how quickly stop and kick are noticed
RECLEN = 512
SEEN_MAX = 20000        # remembered (stream, start) keys for dropping repeated blocks
REFUSED = "server refused (GCFNOSV)"


def block_records(names, b, seqs):
    """miniSEED Records for one GCF data block. `names` is src["gcf"]; `seqs` holds the next
    record sequence number per "loc.cha" and is updated."""
    mapped = (names.get("map") or {}).get(b.stream_id)
    loc, cha = mapped if mapped else gcf.default_seed_name(b.stream_id, b.rate)
    rfac, rmul = mseed.seed_rate(b.rate)
    key = f"{loc}.{cha}"
    data, seqs[key] = mseed.encode_segment(names.get("net", "XX"), names.get("sta", "GCF"), loc, cha,
                                           rfac, rmul, b.start, b.samples, seq_start=seqs.get(key, 1),
                                           reclen=RECLEN)
    return [mseed.parse_record(data, i, check=False)[0] for i in range(0, len(data), RECLEN)]


class _UdpProtocol(asyncio.DatagramProtocol):
    """Replies (GCFACKN / GCFNOSV) change the link state; everything else is queued as data."""

    def __init__(self, link, queue):
        self.link = link
        self.queue = queue

    def datagram_received(self, data, addr):
        link = self.link
        if data.startswith(b"GCFACKN"):
            if link.state in (CONNECTING, REFUSED):
                link.set_state(CONNECTED, "GCFACKN received, waiting for blocks")
                log.info("GCF %s answered GCFPING", link.label)
        elif data.startswith(b"GCFNOSV"):
            if link.state != REFUSED:
                log.warning("GCF %s: the server refused the pull (GCFNOSV)", link.label)
            link.set_state(REFUSED)
        else:
            try:
                self.queue.put_nowait(data)
            except asyncio.QueueFull:
                pass  # the engine is far behind; the blocks come again after the next refresh

    def error_received(self, exc):
        # Windows reports an ICMP "port unreachable" as an error on the next receive
        log.debug("GCF %s: %s", self.link.label, exc)


class GcfLink(Link):
    kind = "gcf-udp"

    def __init__(self, src, hub, state=None):
        super().__init__(src, hub, state)
        self.kind = src.get("kind", "gcf-udp")
        self.names = src.get("gcf") or {}
        self.seqs = {}                           # "loc.cha" -> next miniSEED sequence number
        self.seen = collections.OrderedDict()    # (system id, stream id, start) of blocks already used
        self.blocks = 0
        self.last_block = 0.0
        self.skipped = 0                         # blocks of streams that are not selected
        self.foreign = 0                         # blocks of another digitiser (system id)
        found = str((src.get("details") or {}).get("system_id") or "")
        self.system_id = self.names.get("system_id") or (found if found and "," not in found else None)
        self._noted = set()                      # stream / system ids already logged as skipped
        self._last_error = ""

    @property
    def label(self):
        return f"{self.src.get('name') or self.id} ({self.address})"

    def status(self):
        st = super().status()
        st["blocks"] = self.blocks
        st["last_block"] = self.last_block
        st["skipped"] = self.skipped
        st["foreign"] = self.foreign
        return st

    def handle_block(self, b):
        """Use one decoded block (None = not a block: ignored)."""
        if b is None:
            return
        if self.system_id is None:
            self.system_id = b.system_id
            log.info("GCF %s: using digitiser %s", self.label, b.system_id)
        if b.system_id != self.system_id:
            self.foreign += 1
            if ("sys", b.system_id) not in self._noted:
                self._noted.add(("sys", b.system_id))
                log.info("GCF %s: blocks of digitiser %s ignored (this source is %s)", self.label,
                         b.system_id, self.system_id)
            return
        if b.is_status:
            log.info("GCF %s status %s: %s", self.label, b.stream_id, (b.status_text or "")[:500])
            return
        key = (b.system_id, b.stream_id, round(b.start, 3))
        if key in self.seen:
            self.duplicates += 1
            return
        self.seen[key] = True
        if len(self.seen) > SEEN_MAX:
            self.seen.popitem(last=False)
        self.blocks += 1
        self.last_block = time.time()
        gmap = self.names.get("map") or {}
        if gmap and b.stream_id not in gmap:   # not selected by the user
            self.skipped += 1
            if b.stream_id not in self._noted:
                self._noted.add(b.stream_id)
                log.debug("GCF %s: stream %s is not selected - not used", self.label, b.stream_id)
            return
        for rec in block_records(self.names, b, self.seqs):
            self.feed(rec)

    async def run(self, stop):
        g = self.names
        if g.get("desc"):
            self.hub.descriptions[f"{g.get('net', 'XX')}.{g.get('sta', 'GCF')}"] = g["desc"]
        session = self._udp_session if self.kind == "gcf-udp" else self._tcp_session
        while not stop.is_set():
            before = self.blocks
            try:
                await session(stop)
            except (OSError, TimeoutError) as e:
                self._error(str(e) or "connection timed out")
            except Exception as e:
                log.exception("GCF %s failed", self.label)
                self._error(f"{type(e).__name__}: {e}")
            if stop.is_set():
                break
            self.failures = 0 if self.blocks > before else self.failures + 1
            if self.kick.is_set():  # TCP: the user asked for a new connection now
                self.kick.clear()
                continue
            if await self.pause(stop):
                break
        self.set_state(STOPPED)

    def _error(self, detail):
        self.set_state(ERROR, detail)
        if detail != self._last_error:
            log.warning("GCF %s: %s", self.label, detail)
        self._last_error = detail

    async def _until_stop(self, aw, stop, timeout):
        """Await `aw` unless stop is set first (then None). TimeoutError after `timeout` s."""
        task = asyncio.ensure_future(aw)
        stop_t = asyncio.ensure_future(stop.wait())
        try:
            done, _ = await asyncio.wait({task, stop_t}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_t.cancel()
        if task in done:
            return task.result()
        task.cancel()
        if stop.is_set():
            return None
        raise TimeoutError(f"no answer within {timeout} s")

    # ------------------------------------------------------------------ UDP pull
    async def _udp_session(self, stop):
        loop = asyncio.get_running_loop()
        host, port = self.src["host"], int(self.src["port"])
        self.set_state(CONNECTING, f"GCFPING to {host}:{port}")
        infos = await self._until_stop(
            loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM), stop, CONNECT_TIMEOUT)
        if infos is None:
            return
        addr = infos[0][4]
        queue = asyncio.Queue(maxsize=5000)
        transport, _p = await loop.create_datagram_endpoint(lambda: _UdpProtocol(self, queue),
                                                            local_addr=("0.0.0.0", 0))
        try:
            self.since = time.time()
            self.kick.clear()
            transport.sendto(b"GCFPING", addr)
            transport.sendto(b"GCFSEND:B", addr)
            last_send = started = time.time()
            while not stop.is_set():
                try:
                    data = await asyncio.wait_for(queue.get(), POLL)
                    self.bytes += len(data)
                    self.handle_block(gcf.parse_datagram(data))
                except TimeoutError:
                    pass
                now = time.time()
                if self.kick.is_set():
                    self.kick.clear()
                    transport.sendto(b"GCFPING", addr)
                    transport.sendto(b"GCFSEND:B", addr)
                    last_send = now
                    log.info("GCF %s: GCFPING + GCFSEND:B sent on request", self.label)
                elif now - last_send > REFRESH:
                    transport.sendto(b"GCFSEND:B", addr)
                    last_send = now
                if now - max(self.last_block, started) > SILENT_RESTART:
                    self.set_state(WAITING, f"no data for {SILENT_RESTART} s - asking again")
                    log.warning("GCF %s: no blocks for %d s, starting the pull again", self.label, SILENT_RESTART)
                    return
        finally:
            for _ in range(2):
                transport.sendto(b"GCFSTOP", addr)
            await self._flush(transport)
            transport.close()

    @staticmethod
    async def _flush(transport, timeout=0.5):
        """Let queued datagrams leave before closing (on Windows the second GCFSTOP is still
        queued right after sendto, and close() would drop it)."""
        end = time.monotonic() + timeout
        while transport.get_write_buffer_size() and time.monotonic() < end:
            await asyncio.sleep(0.01)

    # ------------------------------------------------------------------ TCP stream
    async def _tcp_session(self, stop):
        host, port = self.src["host"], int(self.src["port"])
        self.set_state(CONNECTING, f"TCP {host}:{port}")
        conn = await self._until_stop(asyncio.open_connection(host, port), stop, CONNECT_TIMEOUT)
        if conn is None:
            return
        reader, writer = conn
        self.since = time.time()
        self.kick.clear()
        self.set_state(CONNECTED, "waiting for blocks")
        log.info("GCF %s: connected", self.label)
        parser = gcf.StreamParser()
        last_data = time.time()
        try:
            while not stop.is_set():
                if self.kick.is_set():  # TCP has no GCFSEND:B: reconnect at once instead
                    self.set_state(CONNECTING, "reconnecting on request")
                    log.info("GCF %s: reconnecting on request", self.label)
                    return
                try:
                    data = await asyncio.wait_for(reader.read(65536), POLL)
                except TimeoutError:
                    if time.time() - last_data > SILENT_RESTART:
                        self.set_state(WAITING, f"no data for {SILENT_RESTART} s - reconnecting")
                        log.warning("GCF %s: no data for %d s, reconnecting", self.label, SILENT_RESTART)
                        return
                    continue
                if not data:
                    self.set_state(WAITING, "closed by the instrument")
                    log.info("GCF %s: connection closed by the instrument", self.label)
                    return
                last_data = time.time()
                self.bytes += len(data)
                for b, ack in parser.feed(data):
                    if ack:
                        writer.write(ack)
                    self.handle_block(b)
                await writer.drain()
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 2)
            except (OSError, TimeoutError):
                pass
