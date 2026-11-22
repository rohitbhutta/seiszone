"""SeisZone's own GCF data server: another computer on the network pulls every channel SeisZone
receives, over UDP, the way it pulls from a Guralp digitiser or from the earlier viewer's own
server (config["gcf_server"], default port 1567, off until the user switches it on - the earlier
viewer on the same PC uses that port too, and only one program can own it).

Protocol (the server side of core/gcf_link.py; core/gcf.py describes the blocks):

    client -> server  GCFPING        server -> client  GCFACKN
    client -> server  GCFSEND:B      the client is added (or refreshed); from now on it gets every
                                     data block, one block per datagram, plus a status block per
                                     station at once and every STATUS_EVERY s
    client -> server  GCFSTOP        server -> client  GCFACKN; the client is removed

A client that sends nothing for CLIENT_TIMEOUT s is removed (pull clients refresh their
GCFSEND:B every 20 s). Channels above config["gcf_server"]["max_rate"] sps are not sent (the
"sps transmitted block limit" of the earlier viewer's server).

The data comes from the DataHub as miniSEED records and is cut again into GCF blocks per channel:
a block starts on a whole second (on a multiple of 1/den s for the rates above 250 sps), holds at
most BLOCK_SECONDS of data (250 samples) and the smallest differences that fit (8, 16 or 32
bit). Records that continue a channel are joined; records that do not (the first one, after a
gap, or a SeedLink backfill arriving between live records) start a piece of their own, so live
and backfilled data never mix (MAX_PIECES pieces per channel; a piece not continued for
PIECE_IDLE s is sent as it is). A piece starts at its first sample on the block-start grid: up
to one second at the very start of a piece is not sent, and times are rounded to the nearest
sample (at most half a sample). Rates GCF has no code for (below 1 sps other than 0.1 / 0.125 /
0.2 / 0.25 / 0.5) are not sent.

GCF names: a channel received over GCF keeps its digitiser's system id and stream id. Any other
channel gets the station code as system id and band + instrument + location + component + tap
as stream id (KA.KAND.0J.HNZ -> system KAND, stream HN0JZ0). The ids are kept in the State
("gcf_server_ids"), so they stay the same across restarts; the tap (last character) is raised
when two channels of a station would get the same stream id. The status block of a station
says "SeisZone <version> <NET.STA> <site> seedlink=<port>" and lists its channels, so a
SeisZone that pulls from here can connect to the SeedLink server instead (exact names, and
missed data fetched again after an outage).

Threading: everything runs in the engine's asyncio loop (hub listeners too).
"""

import asyncio
import logging
import math
import re
import time

import numpy as np

from .. import VERSION
from . import gcf

log = logging.getLogger("seiszone")

CLIENT_TIMEOUT = 180.0       # s without a request before a client is removed
MAX_CLIENTS = 32
STATUS_EVERY = 300.0         # s between the status blocks of a station
BLOCK_SECONDS = 2            # longest block (at most 250 samples)
MAX_PIECES = 4               # pieces of one channel open at once (live + backfill)
PIECE_IDLE = 30.0            # s: a piece not continued for this long is sent as it is
CONTROL_LINES = 200          # control log lines kept for the user interface
STATE_KEY = "gcf_server_ids"
B36 = re.compile(r"[^0-9A-Z]")
TAPS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _clean(text):
    return B36.sub("", str(text).upper())


def _block_samples(rate, den):
    """Samples per full block: whole steps of the block-start grid, at most 250 samples and
    BLOCK_SECONDS."""
    g = int(round(rate / den))
    k = max(1, min(250 // g, BLOCK_SECONDS * den)) if g <= 250 else 1
    return g * k, g


class _Piece:
    __slots__ = ("t", "buf", "next", "used")

    def __init__(self, t, buf, nxt, now):
        self.t = t           # time of buf[0] (on the block-start grid)
        self.buf = buf       # int32 samples not sent yet
        self.next = nxt      # true end time of the last record joined (continuity check)
        self.used = now


class _Channel:
    __slots__ = ("sid", "rate", "code", "den", "n", "grid", "pieces", "ids")

    def __init__(self, sid, rate, ids):
        self.sid, self.rate, self.ids = sid, rate, ids
        self.code, self.den = gcf.rate_code(rate)
        self.n, self.grid = _block_samples(rate, self.den)
        self.pieces = []


class GcfServer(asyncio.DatagramProtocol):
    """The UDP endpoint and the GCF encoder. open() / close() run in the engine loop."""

    def __init__(self, hub, state=None, max_rate=1000.0, seedlink_port=None, gcf_names=None):
        self.hub = hub
        self.state = state
        self.max_rate = float(max_rate or 0) or 1e9
        self.seedlink_port = seedlink_port          # told to SeisZone clients in the status blocks
        self.gcf_names = dict(gcf_names or {})      # sid -> (system id, stream id) of GCF sources
        self.transport = None
        self.clients = {}            # (ip, port) -> {"params", "since", "last", "blocks"}
        self.control = []            # [text] like the earlier viewer's Control box
        self.blocks_sent = 0
        self._channels = {}          # sid -> _Channel (None: not sent, with the reason in _skipped)
        self._skipped = {}           # sid -> why it is not sent
        self._status_sent = {}       # NET.STA -> wall time of its last status block
        self._ids = dict((state.get(STATE_KEY) if state is not None else None) or {})
        self._ids_dirty = False
        self._house = None

    # ------------------------------------------------------------------ life cycle
    async def open(self, bind, port):
        loop = asyncio.get_running_loop()
        self.transport, _p = await loop.create_datagram_endpoint(lambda: self, local_addr=(bind, port))
        self.hub.add_listener(self.on_record)
        self._house = asyncio.ensure_future(self._housekeeping())
        return self.transport.get_extra_info("sockname")[1]

    def close(self):
        self.hub.remove_listener(self.on_record)
        if self._house is not None:
            self._house.cancel()
            self._house = None
        self._save_ids()
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        self.clients.clear()
        self._channels.clear()

    # ------------------------------------------------------------------ protocol
    def datagram_received(self, data, addr):
        cmd = bytes(data[:64]).strip().upper()
        now = time.time()
        if cmd.startswith(b"GCFPING"):
            self._send(b"GCFACKN", addr)
            if addr in self.clients:
                self.clients[addr]["last"] = now
        elif cmd.startswith(b"GCFSEND"):
            params = cmd.partition(b":")[2].decode("latin-1", "replace") or "B"
            c = self.clients.get(addr)
            if c is not None:
                c["last"], c["params"] = now, params
                return
            if len(self.clients) >= MAX_CLIENTS:
                self._send(b"GCFNOSV", addr)
                self._note(f"Refused client {addr[0]}:{addr[1]} ({MAX_CLIENTS} clients already)")
                return
            self.clients[addr] = {"params": params, "since": now, "last": now, "blocks": 0}
            self._note(f"Added client {addr[0]}:{addr[1]}")
            log.info("GCF server: client %s:%d added (%d now)", addr[0], addr[1], len(self.clients))
            self._status_sent.clear()          # the new client gets every station's status at once
        elif cmd.startswith(b"GCFSTOP"):
            self._send(b"GCFACKN", addr)
            if self.clients.pop(addr, None) is not None:
                self._note(f"Removed client {addr[0]}:{addr[1]} (GCFSTOP)")
                log.info("GCF server: client %s:%d stopped (%d left)", addr[0], addr[1], len(self.clients))
                if not self.clients:
                    self._channels.clear()

    def error_received(self, exc):
        # Windows reports an ICMP "port unreachable" (a client that went away) on the next receive
        log.debug("GCF server: %s", exc)

    def _send(self, data, addr):
        if self.transport is not None:
            try:
                self.transport.sendto(data, addr)
            except OSError as e:
                log.debug("GCF server: sending to %s failed: %s", addr, e)

    def _note(self, text):
        self.control.append(f"{time.strftime('%d-%m-%Y %H:%M:%S')}  {text}")
        del self.control[:-CONTROL_LINES]

    def peers(self):
        now = time.time()
        return [{"address": f"{a[0]}:{a[1]}", "params": c["params"], "since": c["since"],
                 "idle": now - c["last"], "blocks": c["blocks"]} for a, c in list(self.clients.items())]

    # ------------------------------------------------------------------ data
    def on_record(self, rec, source_id=""):
        """Hub listener: cut the record into GCF blocks and send them to every client."""
        if not self.clients or self.transport is None:
            return
        ch = self._channel(rec)
        if ch is None:
            return
        try:
            y = np.asarray(rec.samples)
        except Exception:  # noqa: BLE001 - the hub decoded it already; never break the engine
            return
        if y.dtype.kind == "f":
            y = np.round(y)
        y = y.astype(np.int64)
        now = time.time()
        tol = 0.5 / ch.rate
        piece = None
        for p in ch.pieces:
            if abs(rec.start - p.next) <= tol:
                piece = p
                break
        if piece is None:
            if len(ch.pieces) >= MAX_PIECES:
                oldest = min(ch.pieces, key=lambda q: q.used)
                self._flush(ch, oldest)
                ch.pieces.remove(oldest)
            # the first sample on the block-start grid (rounded to the nearest sample)
            step = 1.0 / ch.den
            first = math.ceil((rec.start - tol) / step - 1e-9) * step
            k = max(0, int(round((first - rec.start) * ch.rate)))
            if k >= len(y):
                return
            piece = _Piece(first, y[k:], rec.end, now)
            ch.pieces.append(piece)
        else:
            piece.buf = np.concatenate([piece.buf, y])
            piece.next = rec.end
            piece.used = now
        self._emit(ch, piece)

    def _emit(self, ch, piece, everything=False):
        while len(piece.buf) >= ch.n or (everything and len(piece.buf)):
            n = min(ch.n, len(piece.buf))
            self._broadcast(gcf.encode_block(ch.ids[0], ch.ids[1], piece.t, ch.rate, piece.buf[:n]))
            piece.buf = piece.buf[n:]
            piece.t += n / ch.rate

    def _flush(self, ch, piece):
        self._emit(ch, piece, everything=True)

    def _broadcast(self, block):
        for addr, c in list(self.clients.items()):
            self._send(block, addr)
            c["blocks"] += 1
        self.blocks_sent += 1

    def _channel(self, rec):
        sid = rec.sid
        ch = self._channels.get(sid)
        if ch is not None and abs(ch.rate - rec.rate) < 1e-9:
            return ch
        if sid in self._skipped and ch is None:
            return None
        if rec.rate > self.max_rate:
            self._skip(sid, f"{rec.rate:g} sps is above the limit of {self.max_rate:g} sps")
            return None
        if gcf.rate_code(rec.rate) is None:
            self._skip(sid, f"GCF has no code for {rec.rate:g} sps")
            return None
        ch = self._channels[sid] = _Channel(sid, rec.rate, self.ids_for(sid))
        return ch

    def _skip(self, sid, why):
        self._skipped[sid] = why
        self._channels.pop(sid, None)
        self._note(f"{sid} not sent: {why}")
        log.info("GCF server: %s not sent: %s", sid, why)

    # ------------------------------------------------------------------ names
    def ids_for(self, sid):
        """(system id, stream id) of a channel: kept in the State so it never changes."""
        got = self._ids.get(sid)
        if got:
            return tuple(got)
        if sid in self.gcf_names:
            ids = tuple(self.gcf_names[sid])
        else:
            net, sta, loc, cha = (sid.split(".") + ["", "", "", ""])[:4]
            system = (_clean(sta).lstrip("0") or _clean(net).lstrip("0") or "SZ")[:6]
            if int(system, 36) >= 0x80000000:      # does not fit the 6-character form
                system = system[:5]
            cha = _clean(cha).ljust(3, "X")
            loc = _clean(loc).rjust(2, "X")[-2:]
            base = cha[:2] + loc + cha[2]          # HN 0J Z: never starts with a digit 0
            if base[0] == "0":
                base = "X" + base[1:]
            used = {tuple(v) for k, v in self._ids.items() if k != sid}
            ids = None
            for tap in TAPS:
                if (system, base + tap) not in used:
                    ids = (system, base + tap)
                    break
            if ids is None:
                ids = (system, base + "Z")
        self._ids[sid] = list(ids)
        self._ids_dirty = True
        return ids

    def _save_ids(self):
        if self._ids_dirty and self.state is not None:
            self.state.set(STATE_KEY, dict(self._ids))
            self._ids_dirty = False

    def status_text(self, station):
        """Status block text of one station: who sends, its SeedLink port and its channels."""
        site = self.hub.descriptions.get(station, "")
        chans = sorted((sid, ch.ids) for sid, ch in self._channels.items()
                       if ch is not None and sid.startswith(station + "."))
        text = f"SeisZone {VERSION} {station}" + (f" {site}" if site else "")
        if self.seedlink_port:
            text += f" seedlink={self.seedlink_port}"
        for sid, ids in chans:
            text += f"\r\n{ids[0]} {ids[1]} = {sid}"
        return text

    # ------------------------------------------------------------------ housekeeping
    async def _housekeeping(self):
        while True:
            await asyncio.sleep(1.0)
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("GCF server housekeeping failed")

    def tick(self, now=None):
        now = time.time() if now is None else now
        for addr, c in list(self.clients.items()):
            if now - c["last"] > CLIENT_TIMEOUT:
                del self.clients[addr]
                self._note(f"Removed client {addr[0]}:{addr[1]} (no request for {CLIENT_TIMEOUT:.0f} s)")
                log.info("GCF server: client %s:%d timed out", addr[0], addr[1])
        if not self.clients:
            self._channels.clear()
            self._save_ids()
            return
        for ch in list(self._channels.values()):
            for p in list(ch.pieces):
                if now - p.used > PIECE_IDLE:
                    self._flush(ch, p)
                    ch.pieces.remove(p)
        for station in sorted({".".join(sid.split(".")[:2]) for sid in self._channels}):
            if now - self._status_sent.get(station, 0.0) >= STATUS_EVERY:
                self._status_sent[station] = now
                sid = next(s for s in self._channels if s.startswith(station + "."))
                system = self._channels[sid].ids[0]
                self._broadcast(gcf.encode_status(system, system[:4] + "00", now, self.status_text(station)))
        self._save_ids()
