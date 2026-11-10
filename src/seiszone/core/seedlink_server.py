"""SeisZone's own SeedLink 3 server: every record in the DataHub is offered again to other
programs (other viewers, SeisComP, ObsPy, Swarm, ...), by default on port 18010.

Protocol (SeedLink 3.1, as libslink and standard servers use it):

    HELLO                         -> 2 lines: software banner, organization
    CAT                           -> "NET STA description" lines, then "END"
    CAPABILITIES EXTREPLY ...     -> OK (then errors are sent as "ERROR <CODE> <text>")
    BATCH                         -> OK; afterwards STATION/SELECT/DATA/FETCH/TIME are not answered
    STATION sta [net]             -> OK; SELECT/DATA/FETCH/TIME now apply to this station
    SELECT [!]LLCCC[.T] ...       -> OK (no pattern: clear the selectors)
    DATA [seq [begin [end]]]      -> OK (multi-station) / start streaming (uni-station)
    FETCH [seq [begin]]           -> like DATA, but ends with "END" once the held data is sent
    TIME begin [end]              -> like DATA from a time ("YYYY,MM,DD,hh,mm,ss")
    END                           -> start streaming the configured stations
    INFO ID|CAPABILITIES|STATIONS|STREAMS|CONNECTIONS  -> XML in SLINFO packets
    BYE                           -> close

Data packets are b"SL%06X" % seq + 512-byte record. "DATA n" starts AT packet n (libslink
sends last received + 1), so a client that was away gets exactly what it missed while the
ring still holds it. An n that is not held (pushed out of the ring, or a number of an earlier
run - the engine keeps the numbers unique across runs) starts at the oldest packet held (that
ends after the begin time, if one was given): resending is harmless, live only would lose
data. Without a sequence number streaming is live only, unless a begin time was given (then
from the oldest held packet ending after it). A client that cannot keep up and falls out of
the ring continues at the oldest packet held. Commands may end in CR, LF or CR LF (libslink
and ObsPy send a bare CR). Everything runs in the engine's asyncio loop and never blocks it:
packets are sent in batches with a yield in between, a client that takes no data for
DRAIN_TIMEOUT is dropped, one that sends no command for IDLE_TIMEOUT while negotiating is
closed, and TCP keep-alive notices peers that vanished while no data flows.
"""

import asyncio
import collections
import datetime as dt
import fnmatch
import logging
import re
import socket
import struct
import time
from xml.sax.saxutils import escape

from .. import VERSION
from . import mseed

log = logging.getLogger("seiszone")

SOFTWARE = f"SeisZone v{VERSION}"
MASK = 0xFFFFFF
PACKET = 520                 # "SL" + 6 hex digits + 512-byte record
BATCH_PACKETS = 500          # packets taken from the ring per step (then the loop gets a turn)
DRAIN_TIMEOUT = 120          # seconds a client may refuse data before it is dropped
WAIT_TIMEOUT = 30            # re-check interval while waiting for new packets
IDLE_TIMEOUT = 300           # seconds a negotiating client may stay silent
CACHE_SIZE = 4000            # re-encoded (non-512-byte) records kept for other clients
CODE_RE = re.compile(r"^[A-Z0-9?*]{1,5}$")
SELECTOR_RE = re.compile(r"^!?[A-Z0-9?*\-]{0,5}(\.[A-Z?*])?$")


def _iso(t):
    """SeedLink INFO time format: 2026/11/07 12:00:00.0000 (UTC)."""
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y/%m/%d %H:%M:%S.%f")[:-2]


def _parse_sl_time(s):
    """SeedLink command time "YYYY,MM,DD,hh,mm,ss" (later fields optional) -> epoch seconds."""
    parts = s.split(",")
    if not 1 <= len(parts) <= 6:
        raise ValueError(f"bad SeedLink time {s!r}")
    nums = [int(p) for p in parts[:5]] + ([float(parts[5])] if len(parts) == 6 else [])
    while len(nums) < 6:
        nums.append(1 if len(nums) < 3 else 0)
    y, mo, d, h, mi, se = nums
    return dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp() + se


def _attr(v):
    return escape(str(v), {'"': "&quot;"})


def _wild(pat, value):
    return fnmatch.fnmatchcase(value, pat)


class Selector:
    """One SeedLink selector: [!]LLCCC[.T], CCC (any location), LL (every channel of a
    location); "?" / "*" wildcards; "--" stands for the empty location."""

    def __init__(self, text):
        self.text = text
        self.neg = text.startswith("!")
        t = text[1:] if self.neg else text
        self.type = None
        if "." in t:
            t, self.type = t.split(".", 1)
        if len(t) >= 5:
            loc, cha = t[:-3], t[-3:]
        elif len(t) == 3:
            loc, cha = None, t
        elif len(t) == 2:
            loc, cha = t, "*"
        else:
            loc, cha = None, t or "*"
        self.loc = "" if loc == "--" else loc
        self.cha = cha

    def match(self, rec):
        if self.loc is not None:
            if self.loc == "":
                if rec.loc:
                    return False
            elif not (_wild(self.loc, rec.loc.ljust(2)) or _wild(self.loc.strip(), rec.loc)):
                return False
        if not _wild(self.cha, rec.cha):
            return False
        return self.type in (None, "D", "?", "*")  # the hub only holds data records


class StationReq:
    """What a client asked for one station (or, in uni-station mode, for everything)."""

    def __init__(self, sta, net):
        self.sta, self.net = sta, net
        self.selectors = []
        self.seq = None        # DATA/FETCH sequence number: start at this packet
        self.begin = None      # TIME / DATA n begin
        self.end = None
        self.fetch = False
        self.delta = 0         # packets after the stream start where this request begins
        self.active = False

    @property
    def label(self):
        return f"{self.net}.{self.sta}"

    def match(self, rec):
        # SeedLink '?' also matches the space padding of short codes (e.g. "?????" vs "AB1")
        sta_ok = _wild(self.sta, rec.sta) or _wild(self.sta, rec.sta.ljust(5))
        net_ok = not self.net.strip() or _wild(self.net, rec.net) or _wild(self.net, rec.net.ljust(2))
        if not (sta_ok and net_ok):
            return False
        if self.begin is not None and rec.end <= self.begin:
            return False
        if self.end is not None and rec.start >= self.end:
            return False
        pos = [s for s in self.selectors if not s.neg]
        if pos and not any(s.match(rec) for s in pos):
            return False
        return not any(s.match(rec) for s in self.selectors if s.neg)


def build_info_records(xml_text, final=True):
    """Pack XML into SeedLink INFO packets (ASCII miniSEED records, station INFO, channel INF);
    "SLINFO *" means more follow, "SLINFO  " is the last one."""
    payload = xml_text.encode("utf-8")
    chunk = 512 - 56
    pieces = [payload[i:i + chunk] for i in range(0, len(payload), chunk)] or [b""]
    now = dt.datetime.now(dt.timezone.utc)
    doy = now.timetuple().tm_yday
    out = []
    for i, p in enumerate(pieces):
        hdr = b"000000D " + b"INFO " + b"  " + b"INF" + b"SL"
        hdr += struct.pack(">HHBBBBHHhh", now.year, doy, now.hour, now.minute,
                           now.second, 0, now.microsecond // 100, len(p), 0, 0)
        hdr += bytes([0, 0, 0, 1]) + struct.pack(">i", 0) + struct.pack(">HH", 56, 48)
        b1000 = struct.pack(">HHBBBB", 1000, 0, 0, 1, 9, 0)
        rec = (hdr + b1000 + p).ljust(512, b"\x00")
        last = (i == len(pieces) - 1) and final
        out.append((b"SLINFO  " if last else b"SLINFO *") + rec)
    return out


async def _read_cmd(reader):
    """One command line ending in CR, LF or CR LF (the LF after a CR then arrives as an empty
    command and is skipped). b"" at the end of the connection."""
    try:
        return await reader.readuntil((b"\r", b"\n"))
    except asyncio.IncompleteReadError as e:
        return e.partial
    except asyncio.LimitOverrunError:
        raise ValueError("command line too long") from None


def _keepalive(writer):
    """TCP keep-alive, so a peer that vanished (VPN down, power cut) is noticed while no data flows."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except (OSError, ValueError, AttributeError):
        return
    # first probe after 60 s of silence, then every 10 s (the system default waits 2 hours).
    # asyncio's TransportSocket has no ioctl(), so the TCP_KEEP* options are used.
    for name, value in (("TCP_KEEPIDLE", 60), ("TCP_KEEPINTVL", 10)):
        opt = getattr(socket, name, None)
        if opt is None:
            continue
        try:
            sock.setsockopt(socket.IPPROTO_TCP, opt, value)
        except (OSError, ValueError, AttributeError):
            pass


def _peer_text(writer):
    p = writer.get_extra_info("peername")
    if isinstance(p, tuple) and len(p) >= 2:
        return f"{p[0]}:{p[1]}"
    return str(p or "?")


class _Conn:
    """Negotiation state of one client connection."""

    def __init__(self, reader, writer, info):
        self.reader, self.writer, self.info = reader, writer, info
        self.reqs = []
        self.cur = None
        self.uni = StationReq("*", "*")
        self.batch = False
        self.extreply = False

    def reply(self, ok, code="ARGUMENTS", text=""):
        """OK / ERROR to STATION, SELECT, DATA, FETCH, TIME (not sent in BATCH mode)."""
        if not self.batch:
            self.writer.write(b"OK\r\n" if ok else self.error_line(code, text))

    def error_line(self, code, text):
        return (f"ERROR {code} {text}\r\n" if self.extreply else "ERROR\r\n").encode("ascii", "replace")


class SeedLinkServer:
    def __init__(self, hub, organization="SeisZone"):
        self.hub = hub
        self.organization = organization
        self.clients = 0
        self.peers = []       # one dict per connection: peer, since, stations, packets
        self._cache = collections.OrderedDict()   # seq -> (Record, [packets]) for re-encoded records
        self._encode_failed = False

    # ------------------------------------------------------------------ INFO / CAT
    def info_xml(self, level):
        level = level.upper()
        hub = self.hub
        head = (f'<?xml version="1.0"?>\n<seedlink software="{SOFTWARE}" '
                f'organization="{_attr(self.organization)}" started="{_iso(hub.started)}"')
        if level == "CAPABILITIES":
            caps = "".join(f'<capability name="{c}"/>' for c in
                           ("dialup", "multistation", "window-extraction", "info:id", "info:capabilities",
                            "info:stations", "info:streams", "info:connections"))
            return head + ">" + caps + "</seedlink>"
        if level == "CONNECTIONS":
            conns = []
            for p in list(self.peers):
                host, _, port = p["peer"].rpartition(":")
                conns.append(f'<connection host="{_attr(host)}" port="{_attr(port)}" ctime="{_iso(p["since"])}" '
                             f'txcount="{p["packets"]}" totBytes="{p["packets"] * PACKET}" '
                             f'stations="{_attr(p["stations"])}"/>')
            return head + ">" + "".join(conns) + "</seedlink>"
        if level not in ("STATIONS", "STREAMS", "ALL"):
            return head + "/>"
        stations = collections.OrderedDict()
        for sid, st in sorted(hub.stats_snapshot().items()):
            stations.setdefault((st["net"], st["sta"]), []).append(st)
        first = hub.first_seq()
        last = (hub.next_seq() - 1) & MASK
        first = last if first is None else first
        body = []
        for (net, sta), sts in stations.items():
            desc = hub.descriptions.get(f"{net}.{sta}") or sta
            attrs = (f'name="{_attr(sta)}" network="{_attr(net)}" description="{_attr(desc)}" '
                     f'begin_seq="{first:06X}" end_seq="{last:06X}" stream_check="enabled"')
            if level == "STATIONS":
                body.append(f"<station {attrs}/>")
                continue
            inner = "".join(
                f'<stream location="{_attr(s["loc"])}" seedname="{_attr(s["cha"])}" type="D" '
                f'begin_time="{_iso(s["first"])}" end_time="{_iso(s["last_end"])}" '
                f'begin_recno="0" end_recno="0" gap_check="disabled" gap_treshold="0"/>'
                for s in sts)
            body.append(f"<station {attrs}>{inner}</station>")
        return head + ">" + "".join(body) + "</seedlink>"

    def cat_text(self):
        seen = sorted({(st["net"], st["sta"]) for st in self.hub.stats_snapshot().values()})
        lines = "".join(f"{n:<2} {s:<5} {self.hub.descriptions.get(f'{n}.{s}') or s}\r\n" for n, s in seen)
        return lines + "END"

    # ------------------------------------------------------------------ connection
    async def handle(self, reader, writer):
        """asyncio.start_server callback: one SeedLink client, from HELLO to disconnect."""
        info = {"peer": _peer_text(writer), "since": time.time(), "stations": "", "packets": 0}
        self.clients += 1
        self.peers.append(info)
        log.info("SeedLink server: client %s connected", info["peer"])
        c = _Conn(reader, writer, info)
        _keepalive(writer)
        reason = "closed"
        try:
            while True:
                try:
                    line = await asyncio.wait_for(_read_cmd(reader), IDLE_TIMEOUT)
                except TimeoutError:
                    reason = f"no command for {IDLE_TIMEOUT} s, closed"
                    break
                if not line:
                    break
                cmd = line.decode("ascii", "replace").strip()
                if cmd and await self._command(c, cmd):
                    break
                await asyncio.wait_for(writer.drain(), DRAIN_TIMEOUT)
        except TimeoutError:
            reason = "too slow (took no data), dropped"
        except (ConnectionError, OSError, ValueError, asyncio.IncompleteReadError) as e:
            reason = str(e) or type(e).__name__
        except Exception:
            log.exception("SeedLink server: client %s failed", info["peer"])
            reason = "internal error"
        finally:
            self.clients -= 1
            if info in self.peers:
                self.peers.remove(info)
            log.info("SeedLink server: client %s disconnected (%s, %d packets sent)",
                     info["peer"], reason, info["packets"])
            try:
                writer.close()
            except Exception:
                pass

    async def _command(self, c, cmd):
        """Handle one command line. True when the connection is finished."""
        parts = cmd.split()
        verb, args = parts[0].upper(), parts[1:]
        log.debug("SeedLink server: %s > %s", c.info["peer"], cmd)
        w = c.writer
        if verb == "HELLO":
            w.write(f"SeedLink v3.1 ({SOFTWARE}) :: SLPROTO:3.1 CAP EXTREPLY NSWILDCARD BATCH WS:13\r\n"
                    f"{self.organization}\r\n".encode("utf-8", "replace"))
        elif verb == "CAPABILITIES":
            if any(a.upper() == "EXTREPLY" for a in args):
                c.extreply = True
            w.write(b"OK\r\n")
        elif verb == "CAT":
            w.write(self.cat_text().encode("utf-8", "replace"))
        elif verb == "BATCH":
            c.batch = True
            w.write(b"OK\r\n")
        elif verb == "STATION":
            codes = [a.upper() for a in args[:2]]
            if not codes or not all(CODE_RE.match(x) for x in codes):
                c.reply(False, "ARGUMENTS", "use STATION sta [net]")
            else:
                c.cur = StationReq(codes[0], codes[1] if len(codes) > 1 else "*")
                c.reqs.append(c.cur)
                c.reply(True)
        elif verb == "SELECT":
            target = c.cur or c.uni
            sels = [a.upper() for a in args]
            if not all(SELECTOR_RE.match(s) for s in sels):
                c.reply(False, "ARGUMENTS", "bad selector")
            else:
                if sels:
                    target.selectors.extend(Selector(s) for s in sels)
                else:
                    target.selectors.clear()
                c.reply(True)
        elif verb in ("DATA", "FETCH", "TIME"):
            target = c.cur or c.uni
            try:
                self._set_start(target, verb, args)
            except ValueError as e:
                c.reply(False, "ARGUMENTS", str(e))
                return False
            if c.cur is None:  # uni-station mode: the data follows at once
                await self._stream(c, [c.uni], fetch=verb == "FETCH")
                return True
            c.reply(True)
        elif verb == "END":
            reqs = c.reqs or [c.uni]
            await self._stream(c, reqs, fetch=all(r.fetch for r in reqs))
            return True
        elif verb == "INFO":
            for pkt in build_info_records(self.info_xml(args[0] if args else "ID")):
                w.write(pkt)
        elif verb == "BYE":
            return True
        else:
            w.write(c.error_line("UNSUPPORTED", f"unknown command {verb}"))
        return False

    @staticmethod
    def _set_start(req, verb, args):
        if verb == "TIME":
            if not args:
                raise ValueError("TIME needs a begin time")
            req.begin = _parse_sl_time(args[0])
            req.end = _parse_sl_time(args[1]) if len(args) > 1 else None
            return
        req.fetch = verb == "FETCH"
        if args:
            seq = int(args[0], 16)
            req.seq = seq & MASK if seq >= 0 else None   # "-1": no sequence number
        if len(args) > 1:
            req.begin = _parse_sl_time(args[1])
        if len(args) > 2:
            req.end = _parse_sl_time(args[2])

    # ------------------------------------------------------------------ streaming
    def _start_seq(self, req, fetch):
        """First packet for one request: its DATA n when held; the oldest held packet when n is
        not held (pushed out of the ring, or a number of an earlier run), when a begin time was
        given or for FETCH; otherwise the next packet to arrive (live)."""
        hub = self.hub
        if req.seq is not None:
            if hub.has_seq(req.seq):
                req.begin = None        # "DATA n begin": the time only counts when n is not held
                return req.seq
            if ((req.seq - hub.next_seq()) & MASK) < 0x800000:
                return hub.next_seq()   # not arrived yet: the client is up to date
        if req.seq is not None or req.begin is not None or req.fetch or fetch:
            first = hub.first_seq()
            return hub.next_seq() if first is None else first
        return hub.next_seq()

    def _wire(self, seq, rec):
        """SeedLink packets for one hub packet. Records of another length than 512 bytes are
        re-encoded (Steim2, integer samples) once and cached for all clients."""
        if rec.reclen == 512:
            return [b"SL%06X" % seq + rec.raw]
        hit = self._cache.get(seq)
        if hit is None or hit[0] is not rec:
            try:
                recs = mseed.encode_records(rec.net, rec.sta, rec.loc, rec.cha, rec.rate, rec.start,
                                            rec.samples, reclen=512)
            except (ValueError, OverflowError, TypeError, struct.error) as e:
                if not self._encode_failed:
                    log.warning("SeedLink server: cannot re-encode %r to 512 bytes (%s); skipped", rec, e)
                    self._encode_failed = True
                recs = []
            hit = (rec, [b"SL%06X" % seq + r.raw for r in recs])
            self._cache[seq] = hit
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(seq)
        return hit[1]

    def _window_done(self, reqs):
        """Time-window extraction (every request has an end time) is finished once the hub
        holds data past the latest end."""
        if any(r.end is None for r in reqs):
            return False
        latest = self.hub.latest_time()
        return latest is not None and latest >= max(r.end for r in reqs)

    async def _watch(self, c, closed, wake):
        """While streaming: answer INFO (keep-alive) requests, notice BYE and disconnects."""
        try:
            while True:
                line = await _read_cmd(c.reader)
                if not line:
                    break
                parts = line.decode("ascii", "replace").split()
                if not parts:
                    continue
                verb = parts[0].upper()
                if verb == "INFO":
                    for pkt in build_info_records(self.info_xml(parts[1] if len(parts) > 1 else "ID")):
                        c.writer.write(pkt)
                elif verb == "BYE":
                    break
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            closed.set()
            wake.set()

    async def _stream(self, c, reqs, fetch):
        hub = self.hub
        first = hub.first_seq()
        starts = [self._start_seq(r, fetch) for r in reqs]
        if first is None:
            base = hub.next_seq()
        else:
            base = min(starts, key=lambda s: (s - first) & MASK)
        for r, s in zip(reqs, starts):
            r.delta = (s - base) & MASK
            r.active = r.delta == 0
        c.info["stations"] = "*" if reqs == [c.uni] else ", ".join(r.label for r in reqs)
        log.info("SeedLink server: client %s %s %s from packet %06X", c.info["peer"],
                 "fetching" if fetch else "streaming", c.info["stations"], base)
        closed = asyncio.Event()
        wake = hub.new_waiter()
        watcher = asyncio.get_running_loop().create_task(self._watch(c, closed, wake))
        next_seq = base
        try:
            while not closed.is_set():
                wake.clear()
                pkts, after = hub.packets_from(next_seq, BATCH_PACKETS)
                if pkts:
                    if pkts[0][0] != next_seq:
                        log.warning("SeedLink server: client %s fell behind; continuing at the oldest "
                                    "packet held (%06X)", c.info["peer"], pkts[0][0])
                        for r in reqs:
                            r.active = True
                    self._send(c, reqs, pkts, base)
                    next_seq = after
                    await asyncio.wait_for(c.writer.drain(), DRAIN_TIMEOUT)
                    await asyncio.sleep(0)
                    continue
                if fetch or self._window_done(reqs):
                    c.writer.write(b"END")
                    await asyncio.wait_for(c.writer.drain(), DRAIN_TIMEOUT)
                    return
                try:
                    await asyncio.wait_for(wake.wait(), WAIT_TIMEOUT)
                except TimeoutError:
                    pass
        finally:
            hub.drop_waiter(wake)
            watcher.cancel()

    def _send(self, c, reqs, pkts, base):
        out = []
        for seq, rec in pkts:
            pos = (seq - base) & MASK
            hit = False
            for r in reqs:
                if not r.active and pos >= r.delta:
                    r.active = True
                if r.active and not hit and r.match(rec):
                    hit = True
            if hit:
                out.extend(self._wire(seq, rec))
        if out:
            c.writer.write(b"".join(out))
            c.info["packets"] += len(out)
