"""Kelunji Gecko receiver and GCF link, driven by bytes captured from real instruments.

    data/gecko_native_capture.bin  what a Gecko SMR03 sent over its USR-W610 link (one direction)
    data/fortimus_udp.bin          Scream UDP datagrams from a real Fortimus, each prefixed with
                                   its length (4 bytes, big-endian)

Everything runs on 127.0.0.1 with ports chosen by the system; no real instrument is contacted.
"""

import asyncio
import os
import random
import socket
import struct
import threading
import time

from seiszone.config import validate_source
from seiszone.core import gcf, gecko, mseed
from seiszone.core.gcf_link import REFUSED, GcfLink, block_records
from seiszone.core.hub import DataHub
from seiszone.core.links import RECEIVING, STOPPED

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
GECKO_SERIAL = 0x02000143
TEDT_MAP = {"ACCZ0": ["0J", "HNZ"], "ACCN0": ["0J", "HNN"], "ACCE0": ["0J", "HNE"],
            "AXLZ0": ["9J", "BNZ"], "AXLN0": ["9J", "BNN"], "AXLE0": ["9J", "BNE"]}
UNIQUE_RECORDS = 234
TEDT_SIDS = ["CI.TEDT.0J.HNE", "CI.TEDT.0J.HNN", "CI.TEDT.0J.HNZ", "CI.TEDT.9J.BNN", "CI.TEDT.9J.BNZ"]


def gecko_capture():
    with open(os.path.join(DATA, "gecko_native_capture.bin"), "rb") as fh:
        return fh.read()


def datagrams():
    with open(os.path.join(DATA, "fortimus_udp.bin"), "rb") as fh:
        raw = fh.read()
    out, i = [], 0
    while i < len(raw):
        n = int.from_bytes(raw[i:i + 4], "big")
        out.append(raw[i + 4:i + 4 + n])
        i += 4 + n
    return out


def data_datagrams():
    return [d for d in datagrams() if gcf.parse_datagram(d) is not None]


def samples_by_sid(records):
    out = {}
    for rec in sorted(records, key=lambda r: r.start):
        out.setdefault(rec.sid, []).extend(int(v) for v in rec.samples)
    return out


def expected_tedt_samples():
    want = {}
    for b in sorted((gcf.parse_datagram(d) for d in data_datagrams()), key=lambda b: b.start):
        loc, cha = TEDT_MAP[b.stream_id]
        want.setdefault(f"CI.TEDT.{loc}.{cha}", []).extend(b.samples)
    return want


async def until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            return False
        await asyncio.sleep(0.02)
    return True


# ============================================================================ Gecko

def test_capture_frames_and_records():
    data = gecko_capture()
    frames = gecko.FrameParser().feed(data)
    p = gecko.FrameParser()  # the same frames when the link delivers odd-sized pieces
    rng, pos, pieces = random.Random(1), 0, []
    while pos < len(data):
        n = rng.randint(1, 3000)
        pieces += p.feed(data[pos:pos + n])
        pos += n
    assert pieces == frames and p.bad == 0
    kinds = [f[0] for f in frames]
    assert kinds[0] == gecko.T_HELLO
    assert {f[1] for f in frames} == {GECKO_SERIAL}
    mseed_frames = [f for f in frames if f[0] == gecko.T_MSEED]
    assert len(mseed_frames) == 244
    recs = [mseed.parse_record(f[4], 0, check=True) for f in mseed_frames]
    assert all(isinstance(r, tuple) and r[1] == 512 for r in recs)
    recs = [r[0] for r in recs]
    found = sorted({(r.station, r.loc, r.cha, r.rate) for r in recs})
    print("Gecko capture:", len(frames), "frames,", len(mseed_frames), "miniSEED;", found)
    assert found == [("SS.SMR03", "00", "HNE", 100.0), ("SS.SMR03", "00", "HNN", 100.0),
                     ("SS.SMR03", "00", "HNZ", 100.0)]
    texts = [f[4] for f in frames if f[0] == gecko.T_TEXT]
    assert texts[0] == b"station_name=SS.00.SMR03"


def test_frame_build_and_damage():
    f = gecko.make_frame(gecko.T_TEXT, 7, 300, b"hello", src=GECKO_SERIAL)
    assert f[0] == 0x55 and len(f) == 21 + 5
    p = gecko.FrameParser()
    assert p.feed(b"\x00\x55junk" + f) == [(gecko.T_TEXT, GECKO_SERIAL, 7, 300 & 0xFF, b"hello")]
    bad = bytearray(f)
    bad[19] ^= 1
    assert p.feed(bytes(bad) + f) == [(gecko.T_TEXT, GECKO_SERIAL, 7, 44, b"hello")]
    assert p.bad == 1
    assert gecko.serial_key("0x02000143") == gecko.serial_key(GECKO_SERIAL) == "2000143"


async def _gecko_session(rename=None, sitename=b'"sitename"="Tank Hill"'):
    """Serve a GeckoReceiver on a free local port; a fake Gecko replays the capture in
    random-sized pieces. Returns (hub, receiver, frames sent to the Gecko, links while connected)."""
    hub = DataHub()
    received = []
    hub.add_listener(lambda rec, source: received.append((rec, source)))
    recv = gecko.GeckoReceiver(hub, rename=rename)
    server = await asyncio.start_server(recv.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    back = bytearray()

    async def collect():
        while True:
            d = await reader.read(65536)
            if not d:
                return
            back.extend(d)
    collector = asyncio.create_task(collect())
    assert await until(lambda: len(back) >= 21)  # the hub greets first, before the Gecko speaks
    data = gecko_capture()
    if sitename:
        data += gecko.make_frame(gecko.T_TEXT, gecko.HUB_ID, 250, sitename, src=GECKO_SERIAL)
    rng, pos = random.Random(5), 0
    while pos < len(data):
        n = rng.randint(1, 4000)
        writer.write(data[pos:pos + n])
        await writer.drain()
        pos += n
        await asyncio.sleep(0.001)
    assert await until(lambda: len(received) >= UNIQUE_RECORDS and recv.links and
                       (not sitename or next(iter(recv.links.values()))["sitename"]))
    links = [dict(v) for v in recv.links.values()]
    writer.close()
    assert await until(lambda: not recv.links)
    await asyncio.wait_for(collector, 5)
    server.close()
    await server.wait_closed()
    return hub, received, gecko.FrameParser().feed(bytes(back)), links


def test_gecko_receiver_end_to_end():
    hub, received, sent, links = asyncio.run(_gecko_session())
    # the capture holds 244 records; 10 are resends of records the Gecko had not seen acknowledged
    assert len(received) == UNIQUE_RECORDS and len(hub.packets) == UNIQUE_RECORDS and hub.duplicates == 10
    assert {src for _r, src in received} == {"gecko:02000143"}
    assert hub.stream_ids() == ["SS.SMR03.00.HNE", "SS.SMR03.00.HNN", "SS.SMR03.00.HNZ"]
    assert hub.descriptions["SS.SMR03"] == "Tank Hill - Kelunji Gecko 02000143"
    st = links[0]
    assert (st["serial"], st["station"], st["sitename"], st["records"]) == ("02000143", "SS.SMR03", "Tank Hill", 244)
    assert st["bytes"] > 130000 and st["since"] > 0 and st["peer"].startswith("127.0.0.1:")

    # what the hub said: hello first, hello reply to the Gecko's hello, start commands, acks
    kinds = [f[0] for f in sent]
    assert kinds[:4] == [gecko.T_HUB_HELLO, gecko.T_HELLO_REPLY, gecko.T_TEXT, gecko.T_TEXT]
    assert sent[0][1] == gecko.HUB_ID and sent[0][2] == 0
    assert all(f[2] == GECKO_SERIAL for f in sent[1:])
    assert [f[4] for f in sent[2:4]] == [b"sampling 1", b'? "sitename"']
    assert set(kinds[4:]) == {gecko.T_ACK}
    data_seqs = [f[3] for f in gecko.FrameParser().feed(gecko_capture()) if f[0] == gecko.T_MSEED]
    acks = [f[3] for f in sent if f[0] == gecko.T_ACK]
    assert len(acks) >= 20 and acks == sorted(acks) and set(acks) <= set(data_seqs)
    assert acks[-1] in data_seqs[-3:]  # every 3 packets / 1 s: at most 2 left unacknowledged
    assert all(f[4] == b"" for f in sent if f[0] == gecko.T_ACK)


def test_gecko_rename():
    rename = {"0x02000143": {"net": "XX", "sta": "TEST", "loc": "10"},
              "ABC": {"sta": "BAD-NAME"}}  # not a valid SEED code: ignored, data kept
    hub, received, _sent, _links = asyncio.run(_gecko_session(rename=rename, sitename=None))
    assert hub.stream_ids() == ["XX.TEST.10.HNE", "XX.TEST.10.HNN", "XX.TEST.10.HNZ"]
    rec = received[0][0]
    assert (rec.raw[8:13], rec.raw[13:15], rec.raw[18:20]) == (b"TEST ", b"10", b"XX")  # bytes carry the name
    assert hub.descriptions["XX.TEST"] == "Kelunji Gecko 02000143"
    originals = [mseed.parse_record(f[4], 0)[0] for f in gecko.FrameParser().feed(gecko_capture())
                 if f[0] == gecko.T_MSEED]
    unique = {(r.start, r.cha): r for r in originals}
    assert len(received) == len(unique)
    assert all(list(r.samples) == list(unique[(r.start, r.cha)].samples) for r, _s in received)


def test_gecko_silent_link_closed(monkeypatch):
    monkeypatch.setattr(gecko, "SILENCE", 0.3)

    async def main():
        recv = gecko.GeckoReceiver(DataHub())
        server = await asyncio.start_server(recv.handle, "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        t0 = time.monotonic()
        got = await asyncio.wait_for(reader.read(), 3)  # until the hub closes the link
        writer.close()
        server.close()
        return time.monotonic() - t0, got, recv
    elapsed, got, recv = asyncio.run(main())
    assert 0.2 < elapsed < 2 and gecko.FrameParser().feed(got)[0][0] == gecko.T_HUB_HELLO
    assert recv.links == {}


# ============================================================================ GCF

def test_block_records_names_and_sequence():
    blocks = [gcf.parse_datagram(d) for d in data_datagrams()]
    names = {"net": "CI", "sta": "TEDT", "map": {"ACCZ0": ["0J", "HNZ"]}}
    seqs = {}
    recs = [r for b in blocks for r in block_records(names, b, seqs)]
    assert all(len(r.raw) == 512 and r.encoding == 11 for r in recs)
    assert {r.sid for r in recs} == {"CI.TEDT.0J.HNZ", "CI.TEDT.00.HNN", "CI.TEDT.00.HNE",
                                     "CI.TEDT.00.BNZ", "CI.TEDT.00.BNN"}  # unmapped: default names
    hnz = [int(r.raw[0:6]) for r in recs if r.cha == "HNZ"]
    assert hnz == list(range(1, len(hnz) + 1))  # one sequence counter per channel
    assert seqs["0J.HNZ"] == len(hnz) + 1


class FakeScream:
    """Local Scream UDP server: GCFACKN (or GCFNOSV) to GCFPING / GCFSTOP, and after GCFSEND:B
    the real Fortimus datagrams every 0.3 s (repeats, as a server does after refreshes)."""

    def __init__(self, refuse=False):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.refuse = refuse
        self.cmds = []
        self.done = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        client, last, dgs = None, 0.0, data_datagrams()
        while not self.done:
            try:
                d, a = self.sock.recvfrom(100)
                self.cmds.append(d)
                if d in (b"GCFPING", b"GCFSTOP"):
                    self.sock.sendto(b"GCFNOSV" if self.refuse else b"GCFACKN", a)
                if d == b"GCFSEND:B" and not self.refuse:
                    client = a
                if d == b"GCFSTOP":
                    client = None
            except (TimeoutError, ConnectionResetError):  # Windows: ICMP 'port unreachable'
                pass
            if client and time.time() - last > 0.3:
                last = time.time()
                for dg in dgs:
                    self.sock.sendto(dg, client)

    def close(self):
        self.done = True
        self.thread.join(2)
        self.sock.close()


def gcf_source(kind, port):
    return validate_source({"id": "fmus", "name": "KUMARI", "kind": kind, "host": "127.0.0.1", "port": port,
                            "gcf": {"net": "CI", "sta": "TEDT", "desc": "Tetar Dam Top", "map": TEDT_MAP}})


def test_gcf_udp_pull():
    fake = FakeScream()

    async def main():
        hub = DataHub()
        received = []
        hub.add_listener(lambda rec, source: received.append((rec, source)))
        link = GcfLink(gcf_source("gcf-udp", fake.port), hub)
        stop = asyncio.Event()
        task = asyncio.create_task(link.run(stop))
        assert await until(lambda: link.blocks == 12)
        await asyncio.sleep(0.8)  # the server repeats its blocks: dropped, not added twice
        status = link.status()
        n_cmds = len(fake.cmds)
        link.kick.set()  # "ask again now": GCFPING + GCFSEND:B on the same socket
        assert await until(lambda: len(fake.cmds) >= n_cmds + 2)
        kicked = fake.cmds[n_cmds:n_cmds + 2]
        t0 = time.monotonic()
        stop.set()
        await asyncio.wait_for(task, 5)
        elapsed = time.monotonic() - t0
        await until(lambda: fake.cmds.count(b"GCFSTOP") >= 2, 1)
        return hub, received, link, status, kicked, elapsed
    try:
        hub, received, link, status, kicked, elapsed = asyncio.run(main())
    finally:
        fake.close()
    assert fake.cmds[:2] == [b"GCFPING", b"GCFSEND:B"]
    assert kicked == [b"GCFPING", b"GCFSEND:B"]
    assert fake.cmds[-2:] == [b"GCFSTOP", b"GCFSTOP"]
    assert elapsed < 2
    assert hub.stream_ids() == TEDT_SIDS
    assert {src for _r, src in received} == {"fmus"}
    assert samples_by_sid([r for r, _s in received]) == expected_tedt_samples()
    assert status["state"] == RECEIVING and status["kind"] == "gcf-udp" and status["blocks"] == 12
    assert status["duplicates"] > 0 and status["packets"] == len(received) and status["bytes"] > 12 * 1000
    assert link.state == STOPPED
    assert hub.descriptions["CI.TEDT"] == "Tetar Dam Top"


def test_gcf_udp_refused():
    fake = FakeScream(refuse=True)

    async def main():
        link = GcfLink(gcf_source("gcf-udp", fake.port), DataHub())
        stop = asyncio.Event()
        task = asyncio.create_task(link.run(stop))
        ok = await until(lambda: link.state == REFUSED, 3)
        stop.set()
        await asyncio.wait_for(task, 3)
        return ok
    try:
        assert asyncio.run(main())
    finally:
        fake.close()


def btp_frame(number, block):
    frame = b"G" + bytes([number & 0xFF]) + struct.pack(">H", len(block)) + block
    return frame + struct.pack(">H", sum(frame) & 0xFFFF)


def test_gcf_tcp_btp():
    blocks = [d[:gcf.parse_datagram(d).size] for d in data_datagrams()]
    stream = b"".join(btp_frame(i, blk) for i, blk in enumerate(blocks))
    want_acks = b"".join(gcf.ack(struct.unpack_from(">I", blk, 4)[0]) for blk in blocks)

    async def main():
        acks = []
        connections = []

        async def instrument(reader, writer):
            connections.append(time.monotonic())
            got = bytearray()
            if len(connections) == 1:
                rng, pos = random.Random(3), 0
                while pos < len(stream):  # odd-sized pieces, as TCP may deliver them
                    n = rng.randint(1, 700)
                    writer.write(stream[pos:pos + n])
                    await writer.drain()
                    pos += n
            try:
                while True:
                    d = await reader.read(1024)
                    if not d:
                        break
                    got.extend(d)
            except ConnectionError:
                pass
            acks.append(bytes(got))
            writer.close()

        server = await asyncio.start_server(instrument, "127.0.0.1", 0)
        hub = DataHub()
        received = []
        hub.add_listener(lambda rec, source: received.append(rec))
        link = GcfLink(gcf_source("gcf-tcp", server.sockets[0].getsockname()[1]), hub)
        stop = asyncio.Event()
        task = asyncio.create_task(link.run(stop))
        assert await until(lambda: link.blocks == 12)
        status = link.status()
        link.kick.set()  # TCP: a new connection at once
        assert await until(lambda: len(connections) == 2 and acks)
        t0 = time.monotonic()
        stop.set()
        await asyncio.wait_for(task, 5)
        elapsed = time.monotonic() - t0
        server.close()
        await server.wait_closed()
        return hub, received, acks, status, elapsed, link
    hub, received, acks, status, elapsed, link = asyncio.run(main())
    assert acks[0] == want_acks  # one acknowledgement per BTP frame, in order
    assert hub.stream_ids() == TEDT_SIDS
    assert samples_by_sid(received) == expected_tedt_samples()
    assert status["state"] == RECEIVING and status["kind"] == "gcf-tcp"
    assert elapsed < 2 and link.state == STOPPED


# ============================================================================ review fixes

def test_gecko_stray_sync_byte_does_not_hold_back_frames():
    """A stray 0x55 with a plausible length used to stall the parser for ~10 KB (no frames, no
    acks); now the complete, valid frame behind it is delivered at once."""
    rec = mseed.encode_records("SS", "SMR03", "", "HNZ", 100, 1762500000.0, list(range(300)))[0]
    frame = gecko.make_frame(gecko.T_MSEED, gecko.HUB_ID, 7, rec.raw, src=GECKO_SERIAL)
    stray = bytes([0x55, gecko.T_MSEED, 0x00, 0x0F]) + bytes(17)    # length 3840, never completed
    p = gecko.FrameParser()
    got = p.feed(stray + frame)
    assert [(f[0], f[3]) for f in got] == [(gecko.T_MSEED, 7)] and p.bad == 1
    p = gecko.FrameParser()                                         # unknown type: no frame start
    assert len(p.feed(bytes([0x55, 0x13, 0x00, 0x26]) + bytes(17) + frame)) == 1
    p = gecko.FrameParser()                     # a real frame cut by a read boundary still waits
    assert p.feed(frame[:300]) == [] and p.feed(frame[300:]) == got


def test_gecko_serial_with_the_top_bit_set():
    serial = 0x82000143
    f = gecko.make_frame(gecko.T_TEXT, gecko.HUB_ID, 1, b"station_name=SS.00.X", src=serial)
    [(_t, src, _d, _s, _p)] = gecko.FrameParser().feed(f)
    assert src == serial and f"{src:08X}" == "82000143"
    assert gecko.serial_key(src) == gecko.serial_key("82000143") == "82000143"
    recv = gecko.GeckoReceiver(DataHub(), rename={"82000143": {"sta": "NEW"}})
    rec = mseed.encode_records("SS", "SMR03", "", "HNZ", 100, 1762500000.0, [1, 2, 3])[0]
    assert recv._renamed(src, rec).sta == "NEW"


def tedt_link(names, details=None):
    src = validate_source({"id": "g1", "kind": "gcf-udp", "host": "127.0.0.1", "port": 1567,
                           "gcf": names, "details": details or {}})
    hub = DataHub()
    return GcfLink(src, hub, None), hub


def test_gcf_link_uses_only_the_selected_streams():
    link, hub = tedt_link({"net": "CI", "sta": "TEDT", "map": {"ACCZ0": ["0J", "HNZ"]}})
    for d in datagrams():
        link.handle_block(gcf.parse_datagram(d))
    assert hub.stream_ids() == ["CI.TEDT.0J.HNZ"]
    assert link.skipped > 0 and link.blocks == 12 and link.status()["skipped"] == link.skipped
    link, hub = tedt_link({"net": "CI", "sta": "TEDT", "map": {}})   # no channel list: every stream
    for d in datagrams():
        link.handle_block(gcf.parse_datagram(d))
    assert "CI.TEDT.00.HNN" in hub.stream_ids() and len(hub.stream_ids()) == 5


def _block(sysid, start_secs, samples, stream="ACCZ0"):
    sysv = int(sysid, 36) | 0xC0000000
    diffs = [0] + [b - a for a, b in zip(samples, samples[1:])]
    body = struct.pack(">i", samples[0]) + struct.pack(">%di" % len(diffs), *diffs) + struct.pack(">i", samples[-1])
    raw = struct.pack(">III", sysv, int(stream, 36), (13500 << 17) | start_secs) + bytes([0, 100, 1, len(samples)])
    return gcf.parse_block(raw + body)


def test_gcf_link_keeps_to_one_digitiser():
    """Two units behind one Scream server, same stream names and start seconds: only the
    configured one is used; nothing is dropped as a false duplicate or mixed into a channel."""
    names = {"net": "XX", "sta": "SCR", "map": {"ACCZ0": ["00", "HNZ"]}, "system_id": "AAAA"}
    link, hub = tedt_link(names)
    for b in (_block("AAAA", 10, [10, 11, 12]), _block("BBBB", 10, [-50, -51, -52]),
              _block("AAAA", 11, [13, 14, 15]), _block("BBBB", 12, [-53, -54, -55])):
        link.handle_block(b)
    assert link.blocks == 2 and link.duplicates == 0 and link.foreign == 2
    assert samples_by_sid([r for _s, r in hub.snapshot_packets()]) == {"XX.SCR.00.HNZ": [10, 11, 12, 13, 14, 15]}
    # an older source without a system id: the probe's single one, else the first one seen
    link, _hub = tedt_link({"net": "XX", "sta": "SCR", "map": {}}, details={"system_id": "BBBB"})
    assert link.system_id == "BBBB"
    link, hub = tedt_link({"net": "XX", "sta": "SCR", "map": {}})
    link.handle_block(_block("BBBB", 10, [1, 2, 3]))
    link.handle_block(_block("AAAA", 10, [7, 8, 9]))
    assert link.system_id == "BBBB" and link.blocks == 1 and link.foreign == 1
