"""SeedLink client and server, in one process on 127.0.0.1 with random ports (never a real
instrument): end-to-end delivery, selectors, resume after a stop / server restart, the raw
protocol of the server, and the client against a foreign server that refuses sequence numbers."""

import asyncio
import struct
import time
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from seiszone import VERSION
from seiszone.config import State
from seiszone.core import links, mseed, seedlink_client
from seiszone.core.hub import DataHub
from seiszone.core.seedlink_client import SeedLinkClient
from seiszone.core.seedlink_server import (SeedLinkServer, Selector, StationReq, _parse_sl_time,
                                           build_info_records)

T0 = 1762500000.0
CHANS = ("HNZ", "HNN", "HNE")


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(links, "RECONNECT_DELAY", 0.2)


def make_records(t0, seconds, chans=CHANS, rate=100, net="WB", sta="KUM", loc="0J", reclen=512):
    rng = np.random.default_rng(int(t0) % 9973)
    out = []
    for cha in chans:
        y = rng.normal(0, 300, int(seconds * rate)).astype(int)
        out += mseed.encode_records(net, sta, loc, cha, rate, t0, y, reclen=reclen)
    return sorted(out, key=lambda r: (r.start, r.cha))


def filled_hub(t0=T0, seconds=30, **kw):
    hub = DataHub()
    for rec in make_records(t0, seconds, **kw):
        hub.add_record(rec, "test")
    hub.descriptions["WB.KUM"] = "KUMARI test site"
    return hub


def raws(hub):
    return [rec.raw for _seq, rec in hub.snapshot_packets()]


async def wait_until(cond, timeout=10.0):
    t_end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > t_end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


async def start_server(hub, port=0):
    srv = SeedLinkServer(hub, organization="Test Org")
    aserver = await asyncio.start_server(srv.handle, "127.0.0.1", port)
    return srv, aserver, aserver.sockets[0].getsockname()[1]


async def stop_server(aserver):
    aserver.close()
    aserver.close_clients()
    await aserver.wait_closed()


def client(port, hub, state, stations=None, selectors=None, sid="src1", **extra):
    src = {"id": sid, "name": "test", "kind": "seedlink", "host": "127.0.0.1", "port": port,
           "stations": stations if stations is not None
           else [{"net": "WB", "sta": "KUM", "selectors": selectors or []}], **extra}
    return SeedLinkClient(src, hub, state)


class Running:
    """A client task that is stopped (and must finish within 2 s) at the end of the block."""

    def __init__(self, link):
        self.link = link
        self.stop = asyncio.Event()

    async def __aenter__(self):
        self.task = asyncio.get_running_loop().create_task(self.link.run(self.stop))
        return self.link

    async def __aexit__(self, *exc):
        self.stop.set()
        t = time.monotonic()
        await asyncio.wait_for(self.task, 2)
        assert time.monotonic() - t < 2
        assert self.link.state == links.STOPPED


def new_state(tmp_path):
    return State(str(tmp_path / "state.json"))


# ---------------------------------------------------------------------- units

def test_selectors_and_station_requests():
    rec = make_records(T0, 5, chans=("HNZ",))[0]
    assert Selector("HNZ").match(rec)
    assert Selector("0JHN?").match(rec)
    assert Selector("0J").match(rec)
    assert Selector("??HNZ.D").match(rec)
    assert not Selector("00HNZ").match(rec)
    assert not Selector("HNZ.E").match(rec)
    assert not Selector("--HNZ").match(rec)
    assert Selector("!HNE").neg and Selector("!HNE").cha == "HNE"
    req = StationReq("KUM", "WB")
    req.selectors = [Selector("HN?"), Selector("!HNE")]
    assert req.match(rec)
    req.selectors.append(Selector("!HNZ"))
    assert not req.match(rec)
    assert StationReq("K?M??", "W*").match(rec)
    assert not StationReq("KUM", "XX").match(rec)
    req = StationReq("KUM", "WB")
    req.begin = rec.end + 1
    assert not req.match(rec)
    assert _parse_sl_time("2025,11,07,08,00,00") == 1762502400.0
    assert _parse_sl_time("2025,11,07") == 1762473600.0
    pkts = build_info_records("x" * 1000)
    assert [p[:8] for p in pkts] == [b"SLINFO *", b"SLINFO *", b"SLINFO  "]
    assert all(len(p) == 520 for p in pkts)


# ---------------------------------------------------------------------- client <-> server

def test_end_to_end_byte_identical_and_live(tmp_path):
    async def main():
        hub1 = filled_hub()
        srv, aserver, port = await start_server(hub1)
        hub2 = DataHub()
        state = new_state(tmp_path)
        state.set_seq("src1", "WB.KUM", 0)          # resume position before packet 1: everything
        try:
            async with Running(client(port, hub2, state)) as link:
                await wait_until(lambda: len(hub2.packets) == len(hub1.packets))
                assert raws(hub2) == raws(hub1)
                assert link.state == links.RECEIVING and link.since > 0
                assert link.packets == len(hub1.packets) and link.duplicates == 0 and link.bad == 0
                assert srv.clients == 1 and srv.peers[0]["stations"] == "WB.KUM"
                for rec in make_records(T0 + 30, 10):   # live data
                    hub1.add_record(rec, "test")
                await wait_until(lambda: len(hub2.packets) == len(hub1.packets))
                assert raws(hub2) == raws(hub1)
                assert state.get_seq("src1", "WB.KUM") == hub1.seq
            await wait_until(lambda: srv.clients == 0)
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def test_without_resume_position_live_only(tmp_path):
    async def main():
        hub1 = filled_hub()
        srv, aserver, port = await start_server(hub1)
        hub2 = DataHub()
        try:
            async with Running(client(port, hub2, new_state(tmp_path))):
                await wait_until(lambda: srv.peers and srv.peers[0]["stations"])
                new = make_records(T0 + 30, 10)
                for rec in new:
                    hub1.add_record(rec, "test")
                await wait_until(lambda: len(hub2.packets) == len(new))
                await asyncio.sleep(0.2)
                assert raws(hub2) == [r.raw for r in new]
        finally:
            await stop_server(aserver)

    asyncio.run(main())


@pytest.mark.parametrize("selectors, expect", [(["HNZ"], {"HNZ"}), (["!HNE"], {"HNZ", "HNN"}),
                                               (["0JHN?", "!HNN"], {"HNZ", "HNE"})])
def test_selectors_end_to_end(tmp_path, selectors, expect):
    async def main():
        hub1 = filled_hub()
        srv, aserver, port = await start_server(hub1)
        hub2 = DataHub()
        state = new_state(tmp_path)
        state.set_seq("src1", "WB.KUM", 0)
        wanted = [r.raw for _s, r in hub1.snapshot_packets() if r.cha in expect]
        try:
            async with Running(client(port, hub2, state, selectors=selectors)):
                await wait_until(lambda: len(hub2.packets) == len(wanted))
                await asyncio.sleep(0.2)
                assert raws(hub2) == wanted
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def test_resume_after_stop_exactly_the_missing_records(tmp_path):
    async def main():
        hub1 = filled_hub()
        srv, aserver, port = await start_server(hub1)
        state = new_state(tmp_path)
        state.set_seq("src1", "WB.KUM", 0)
        try:
            hub2 = DataHub()
            async with Running(client(port, hub2, state)):
                await wait_until(lambda: len(hub2.packets) == len(hub1.packets))
            missed = make_records(T0 + 30, 20)          # arrive while the client is stopped
            for rec in missed:
                hub1.add_record(rec, "test")
            hub3 = DataHub()
            async with Running(client(port, hub3, state)) as link:
                await wait_until(lambda: len(hub3.packets) == len(missed))
                await asyncio.sleep(0.3)
                assert raws(hub3) == [r.raw for r in missed]
                assert link.packets == len(missed) and link.duplicates == 0
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def test_uni_station_mode(tmp_path):
    async def main():
        hub1 = filled_hub()
        srv, aserver, port = await start_server(hub1)
        hub2 = DataHub()
        state = new_state(tmp_path)
        state.set_seq("src1", "*", 0)
        hnz = [(s, r) for s, r in hub1.snapshot_packets() if r.cha == "HNZ"]
        link = client(port, hub2, state, stations=[])
        link.src["selectors"] = ["HNZ"]          # uni-station selectors live on the source itself
        try:
            async with Running(link):
                await wait_until(lambda: len(hub2.packets) == len(hnz))
                await asyncio.sleep(0.2)
                assert raws(hub2) == [r.raw for _s, r in hnz]
                assert srv.peers[0]["stations"] == "*"
                assert state.get_seq("src1", "*") == hnz[-1][0]
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def test_client_survives_server_restart_and_kick(tmp_path):
    async def main():
        hub1 = filled_hub()
        srv, aserver, port = await start_server(hub1)
        hub2 = DataHub()
        state = new_state(tmp_path)
        state.set_seq("src1", "WB.KUM", 0)
        try:
            async with Running(client(port, hub2, state)) as link:
                await wait_until(lambda: len(hub2.packets) == len(hub1.packets))
                await stop_server(aserver)
                await wait_until(lambda: link.state in (links.ERROR, links.WAITING, links.CONNECTING))
                for rec in make_records(T0 + 30, 10):   # while the server is down
                    hub1.add_record(rec, "test")
                srv, aserver, _ = await start_server(hub1, port)
                await wait_until(lambda: len(hub2.packets) == len(hub1.packets))
                assert raws(hub2) == raws(hub1) and link.duplicates == 0
                since = link.since
                link.kick.set()                         # Engine.reconnect(): reconnect at once
                await wait_until(lambda: link.since > since, timeout=2)
                await wait_until(lambda: srv.clients == 1)
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def test_stop_while_connecting_to_dead_port(tmp_path):
    async def main():
        srv, aserver, port = await start_server(DataHub())
        await stop_server(aserver)                      # nobody listens there any more
        async with Running(client(port, DataHub(), new_state(tmp_path))) as link:
            await wait_until(lambda: link.state == links.ERROR and link.failures >= 1)

    asyncio.run(main())


# ---------------------------------------------------------------------- raw server protocol

async def cmd(r, w, text, lines=1):
    w.write(text.encode() + b"\r\n")
    await w.drain()
    out = [(await asyncio.wait_for(r.readline(), 5)).decode().strip() for _ in range(lines)]
    return out[0] if lines == 1 else out


async def read_packets(r, n):
    out = []
    for _ in range(n):
        pkt = await asyncio.wait_for(r.readexactly(520), 5)
        out.append((int(pkt[2:8], 16), pkt[8:]))
    return out


async def read_info(r):
    xml = b""
    while True:
        pkt = await asyncio.wait_for(r.readexactly(520), 5)
        assert pkt[:6] == b"SLINFO"
        rec = pkt[8:]
        n, off = struct.unpack(">H", rec[30:32])[0], struct.unpack(">H", rec[44:46])[0]
        xml += rec[off:off + n]
        if pkt[6:8] != b" *":
            return ET.fromstring(xml.decode())


async def read_to_end(r):
    data = b""
    while True:
        chunk = await asyncio.wait_for(r.read(65536), 5)
        if not chunk:
            return data
        data += chunk


def test_server_raw_protocol():
    async def main():
        hub = filled_hub()
        big = make_records(T0, 30, chans=("HHZ",), sta="BIG", reclen=4096)
        for rec in big:
            hub.add_record(rec, "test")
        srv, aserver, port = await start_server(hub)
        held = hub.snapshot_packets()
        try:
            # HELLO banner, errors
            r, w = await asyncio.open_connection("127.0.0.1", port)
            banner = await cmd(r, w, "HELLO", lines=2)
            assert banner == [f"SeedLink v3.1 (SeisZone v{VERSION}) :: SLPROTO:3.1 CAP EXTREPLY "
                              "NSWILDCARD BATCH WS:13", "Test Org"]
            assert await cmd(r, w, "STATION") == "ERROR"
            assert await cmd(r, w, "FOO") == "ERROR"
            assert await cmd(r, w, "CAPABILITIES EXTREPLY") == "OK"
            assert (await cmd(r, w, "FOO")).startswith("ERROR UNSUPPORTED")
            # INFO
            w.write(b"INFO ID\r\n")
            root = await read_info(r)
            assert root.get("software") == f"SeisZone v{VERSION}" and root.get("organization") == "Test Org"
            w.write(b"INFO STREAMS\r\n")
            root = await read_info(r)
            st = {s.get("name"): s for s in root.iter("station")}
            assert st["KUM"].get("network") == "WB" and st["KUM"].get("description") == "KUMARI test site"
            assert {(s.get("location"), s.get("seedname")) for s in st["KUM"].iter("stream")} == \
                {("0J", c) for c in CHANS}
            assert st["KUM"].get("end_seq") == f"{hub.seq:06X}"
            w.write(b"CAT\r\n")
            await w.drain()
            cat = (await asyncio.wait_for(r.readuntil(b"END"), 5)).decode()
            assert "WB KUM   KUMARI test site" in cat and "WB BIG" in cat
            # BYE closes
            w.write(b"BYE\r\n")
            assert await read_to_end(r) == b""
            w.close()

            # DATA n starts AT packet n
            hnz = [(s, rec) for s, rec in held if rec.sta == "KUM" and rec.cha == "HNZ"]
            n = hnz[3][0]
            expect = [(s, rec.raw) for s, rec in hnz if s >= n]
            r, w = await asyncio.open_connection("127.0.0.1", port)
            await cmd(r, w, "HELLO", lines=2)
            assert await cmd(r, w, "STATION KUM WB") == "OK"
            assert await cmd(r, w, "SELECT HNZ") == "OK"
            assert await cmd(r, w, f"DATA {n:06X}") == "OK"
            w.write(b"END\r\n")
            got = await read_packets(r, len(expect))
            assert got[0][0] == n and got == expect
            # INFO CONNECTIONS while streaming (from a second connection)
            r2, w2 = await asyncio.open_connection("127.0.0.1", port)
            await cmd(r2, w2, "HELLO", lines=2)
            w2.write(b"INFO CONNECTIONS\r\n")
            root = await read_info(r2)
            conns = list(root.iter("connection"))
            assert len(conns) == 2 and srv.clients == 2
            assert any(c.get("stations") == "WB.KUM" and int(c.get("txcount")) == len(expect) for c in conns)
            w2.close()
            # live packets follow on the streaming connection
            new = make_records(T0 + 30, 5, chans=("HNZ", "HNE"))
            for rec in new:
                hub.add_record(rec, "test")
            got = await read_packets(r, sum(1 for x in new if x.cha == "HNZ"))
            assert [p for _s, p in got] == [x.raw for x in new if x.cha == "HNZ"]
            w.close()

            # unknown sequence number: live only
            r, w = await asyncio.open_connection("127.0.0.1", port)
            await cmd(r, w, "HELLO", lines=2)
            assert await cmd(r, w, "STATION KUM WB") == "OK"
            assert await cmd(r, w, "DATA 7FFF00") == "OK"
            w.write(b"END\r\n")
            await w.drain()
            await wait_until(lambda: any(p["stations"] == "WB.KUM" and p["packets"] == 0 for p in srv.peers))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(r.readexactly(520), 0.3)
            w.close()

            # FETCH: everything held for the station, then END and close
            kum = [rec.raw for _s, rec in hub.snapshot_packets() if rec.sta == "KUM"]
            r, w = await asyncio.open_connection("127.0.0.1", port)
            await cmd(r, w, "HELLO", lines=2)
            assert await cmd(r, w, "STATION KUM WB") == "OK"
            assert await cmd(r, w, "FETCH") == "OK"
            w.write(b"END\r\n")
            data = await read_to_end(r)
            w.close()
            assert data.endswith(b"END") and (len(data) - 3) % 520 == 0
            assert [data[i + 8:i + 520] for i in range(0, len(data) - 3, 520)] == kum

            # uni-station FETCH with a selector, no OK: data at once
            r, w = await asyncio.open_connection("127.0.0.1", port)
            await cmd(r, w, "HELLO", lines=2)
            assert await cmd(r, w, "SELECT 0JHNN") == "OK"
            w.write(b"FETCH\r\n")
            data = await read_to_end(r)
            w.close()
            assert [data[i + 8:i + 520] for i in range(0, len(data) - 3, 520)] == \
                [rec.raw for _s, rec in hub.snapshot_packets() if rec.cha == "HNN"]

            # TIME: from the oldest held packet that ends after the begin time
            begin = T0 + 15
            want = [rec.raw for _s, rec in hub.snapshot_packets() if rec.sta == "KUM" and rec.end > begin]
            r, w = await asyncio.open_connection("127.0.0.1", port)
            await cmd(r, w, "HELLO", lines=2)
            assert await cmd(r, w, "STATION KUM WB") == "OK"
            stamp = time.strftime("%Y,%m,%d,%H,%M,%S", time.gmtime(begin))
            assert await cmd(r, w, f"TIME {stamp}") == "OK"
            w.write(b"END\r\n")
            got = await read_packets(r, len(want))
            assert [p for _s, p in got] == want
            w.close()

            # records of 4096 bytes are re-encoded to 512 bytes, samples unchanged
            r, w = await asyncio.open_connection("127.0.0.1", port)
            await cmd(r, w, "HELLO", lines=2)
            assert await cmd(r, w, "STATION BIG WB") == "OK"
            assert await cmd(r, w, "FETCH") == "OK"
            w.write(b"END\r\n")
            data = await read_to_end(r)
            w.close()
            recs = [mseed.parse_record(data[i + 8:i + 520], 0, check=True)[0]
                    for i in range(0, len(data) - 3, 520)]
            assert all(x.reclen == 512 for x in recs) and len(recs) > len(big)
            assert np.array_equal(np.concatenate([x.samples for x in recs]),
                                  np.concatenate([x.samples for x in big]))
            await wait_until(lambda: srv.clients == 0)
        finally:
            await stop_server(aserver)

    asyncio.run(main())


# ---------------------------------------------------------------------- foreign server

class ForeignServer:
    """A SeedLink server that does not accept sequence numbers, has only WB.KUM, writes its
    stream in small pieces with an INFO packet, a damaged record and junk bytes in between."""

    def __init__(self, records):
        self.records = records
        self.commands = []

    def stream(self):
        out = [b"SL%06X" % (100 + i) + rec.raw for i, rec in enumerate(self.records)]
        bad = bytearray(out[1])
        bad[8 + 100:8 + 140] = b"\xff" * 40                # damaged Steim frames
        out[1] = bytes(bad)
        out.insert(1, build_info_records("<seedlink/>")[0])
        out.insert(3, b"#junk#\r\n")
        return b"".join(out)

    async def handle(self, reader, writer):
        station = None
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                c = line.decode().strip()
                self.commands.append(c)
                verb = c.split()[0] if c else ""
                if verb == "HELLO":
                    writer.write(b"SeedLink v3.0 (Other server) :: SLPROTO:3.0\r\nOther org\r\n")
                elif verb == "STATION":
                    station = c.split()[1]
                    writer.write(b"OK\r\n" if station == "KUM" else b"ERROR\r\n")
                elif verb == "SELECT":
                    writer.write(b"OK\r\n")
                elif verb == "DATA":
                    if c != "DATA":
                        writer.write(b"ERROR\r\n")
                    elif station is not None:
                        writer.write(b"OK\r\n")
                    else:
                        await self.send(writer)
                elif verb == "END":
                    await self.send(writer)
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async def send(self, writer):
        data = self.stream()
        for i in range(0, len(data), 37):                   # partial reads on the client side
            writer.write(data[i:i + 37])
            await writer.drain()


def test_client_against_foreign_server(tmp_path, monkeypatch):
    monkeypatch.setattr(seedlink_client, "KEEPALIVE", 0.3)
    monkeypatch.setattr(seedlink_client, "POLL", 0.1)

    async def main():
        records = make_records(T0, 20)
        fake = ForeignServer(records)
        aserver = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
        port = aserver.sockets[0].getsockname()[1]
        good = [r.raw for i, r in enumerate(records) if i != 1]   # record 1 arrives damaged
        try:
            hub = DataHub()
            state = new_state(tmp_path)
            state.set_seq("src1", "WB.KUM", 10)
            stations = [{"net": "WB", "sta": "KUM", "selectors": ["HN?"]},
                        {"net": "XX", "sta": "NOPE", "selectors": []}]
            async with Running(client(port, hub, state, stations=stations)) as link:
                await wait_until(lambda: len(hub.packets) == len(good))
                assert raws(hub) == good
                assert fake.commands[:5] == ["HELLO", "STATION KUM WB", "SELECT HN?", "DATA 00000B", "DATA"]
                assert "STATION NOPE XX" in fake.commands and "END" in fake.commands
                assert link.detail == "station XX.NOPE not on this server"
                assert link.state == links.RECEIVING and link.bad == 1 and link.garbage > 0
                assert state.get_seq("src1", "WB.KUM") == 100 + len(records) - 1
                await wait_until(lambda: "INFO ID" in fake.commands, timeout=3)   # keep-alive

            # uni-station mode: the refused "DATA <seq>" is repeated without it
            fake.commands.clear()
            hub = DataHub()
            state.set_seq("src2", "*", 5)
            async with Running(client(port, hub, state, stations=[], sid="src2")):
                await wait_until(lambda: len(hub.packets) == len(good))
                cmds = fake.commands[fake.commands.index("HELLO"):]   # (a late BYE of the last one)
                assert cmds[:3] == ["HELLO", "DATA 000006", "DATA"]

            # none of the stations on the server: error, retried later
            link = client(port, DataHub(), state, stations=[{"net": "XX", "sta": "NOPE"}], sid="src3")
            async with Running(link):
                await wait_until(lambda: link.state == links.ERROR)
                assert link.detail == "station XX.NOPE not on this server"
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def test_not_a_seedlink_server(tmp_path):
    async def main():
        async def http(reader, writer):
            await reader.readline()
            writer.write(b"HTTP/1.0 400 Bad Request\r\n\r\n")
            await writer.drain()
            writer.close()

        aserver = await asyncio.start_server(http, "127.0.0.1", 0)
        port = aserver.sockets[0].getsockname()[1]
        try:
            link = client(port, DataHub(), new_state(tmp_path))
            async with Running(link):
                await wait_until(lambda: link.state == links.ERROR)
                assert "not a SeedLink server" in link.detail
        finally:
            await stop_server(aserver)

    asyncio.run(main())
