"""SeedLink client and server, in one process on 127.0.0.1 with random ports (never a real
instrument): end-to-end delivery, selectors, resume after a stop / server restart, the raw
protocol of the server, and the client against a foreign server that refuses sequence numbers."""

import asyncio
import socket
import struct
import threading
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
    # the test records are from 2025: a first connection's "last day" (TIME) would skip them;
    # test_first_connection_asks_for_the_last_day checks that request with records of today
    monkeypatch.setattr(seedlink_client, "BACKFILL_SECONDS", 0)


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


# ---------------------------------------------------------------------- review fixes

def test_server_commands_ending_in_bare_cr():
    """libslink, slinktool, slarchive and ObsPy end every command with a bare CR."""
    async def main():
        hub = filled_hub()
        srv, aserver, port = await start_server(hub)
        hnz = [(s, rec.raw) for s, rec in hub.snapshot_packets() if rec.cha == "HNZ"]
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            for text, lines in (("HELLO", 2), ("STATION KUM WB", 1), ("SELECT HNZ", 1),
                                (f"DATA {hnz[0][0]:06X}", 1)):
                w.write(text.encode() + b"\r")
                await w.drain()
                got = [await asyncio.wait_for(r.readline(), 2) for _ in range(lines)]
                assert got[-1].strip() in (b"OK", b"Test Org")
            w.write(b"END\r")
            assert await read_packets(r, len(hnz)) == hnz
            w.write(b"INFO ID\r")                      # keep-alive while streaming
            root = await read_info(r)
            assert root.get("software") == f"SeisZone v{VERSION}"
            w.write(b"BYE\r")
            assert await read_to_end(r) == b""
            w.close()
            await wait_until(lambda: srv.clients == 0)
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def test_obspy_client_gets_data():
    """ObsPy's own SeedLink client (libslink framing: bare CR) against the server."""
    from obspy import UTCDateTime
    from obspy.clients.seedlink.client.seedlinkconnection import SeedLinkConnection
    from obspy.clients.seedlink.slpacket import SLPacket

    hub = filled_hub()
    loop = asyncio.new_event_loop()
    ready = []

    async def start():
        ready.append(await start_server(hub))

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    end = time.monotonic() + 5
    while not ready and time.monotonic() < end:
        time.sleep(0.02)
    _srv, aserver, port = ready[0]
    got = []
    conn = SeedLinkConnection(timeout=5)
    conn.netto, conn.netdly = 5, 1
    conn.set_sl_address(f"127.0.0.1:{port}")
    conn.add_stream("WB", "KUM", "HNZ", -1, None)
    conn.begin_time = UTCDateTime(T0)

    def collect():
        while len(got) < 3:
            pkt = conn.collect()
            if pkt is None or pkt == SLPacket.SLTERMINATE:
                return
            if pkt != SLPacket.SLNOPACKET and pkt.get_type() not in (SLPacket.TYPE_SLINF,
                                                                     SLPacket.TYPE_SLINFT):
                got.append(pkt)

    worker = threading.Thread(target=collect, daemon=True)
    worker.start()
    worker.join(15)
    try:
        assert len(got) == 3, "ObsPy got no data from the SeisZone SeedLink server"
        trace = got[0].get_trace()
        assert (trace.stats.network, trace.stats.station, trace.stats.channel) == ("WB", "KUM", "HNZ")
    finally:
        conn.terminate()
        conn.close()

        async def stop():
            await stop_server(aserver)
        asyncio.run_coroutine_threadsafe(stop(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        th.join(5)


def test_resume_across_a_restart_of_the_server_app(tmp_path):
    """The upstream SeisZone restarted: its packet numbers continue after the old ones (the
    engine restores hub.seq), so the downstream's saved number is not held and it gets every
    packet of the new run (also those received before it reconnected), not live data only."""
    async def main():
        state = new_state(tmp_path)
        state.set_seq("src1", "WB.KUM", 36)       # saved during the upstream's previous run
        hub1 = DataHub()
        hub1.seq = 100036 + 1                     # what the engine restores: saved + gap
        for rec in make_records(T0, 4):
            hub1.add_record(rec, "test")
        srv, aserver, port = await start_server(hub1)
        hub2 = DataHub()
        try:
            async with Running(client(port, hub2, state)):
                await wait_until(lambda: len(hub2.packets) == len(hub1.packets))
                assert raws(hub2) == raws(hub1)
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def test_client_sends_the_time_and_gets_what_the_ring_still_holds(tmp_path):
    async def main():
        hub1 = DataHub(ring_size=50)
        recs = make_records(T0, 60)               # 75 packets: the ring keeps the newest 50
        for rec in recs:
            hub1.add_record(rec, "test")
        held = [r.raw for _s, r in hub1.snapshot_packets()]
        state = new_state(tmp_path)
        last = recs[10]                          # received long ago (pushed out of the ring)
        state.set_seq("src1", "WB.KUM", 11, last.end)
        srv, aserver, port = await start_server(hub1)
        hub2 = DataHub()
        try:
            link = client(port, hub2, state)
            stamp = time.strftime("%Y,%m,%d,%H,%M,%S", time.gmtime(int(last.end)))
            assert link._data_commands("WB.KUM") == [f"DATA {12:06X} {stamp}", f"DATA {12:06X}", "DATA"]
            async with Running(link):
                await wait_until(lambda: len(hub2.packets) == len(held))
                assert raws(hub2) == held
                assert state.get_seq_time("src1", "WB.KUM") == hub1.snapshot_packets()[-1][1].end
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def steim2_wrapped():
    """A Steim2 record as a C encoder writes it: samples [2^31-1, -2^31, 2^31-1], int32-wrapped
    differences [0, +1, -1]."""
    rec = mseed.encode_records("WB", "KUM", "0J", "HNZ", 100, T0, [0, 1, 0])[0]
    raw = bytearray(rec.raw)
    struct.pack_into(">i", raw, 64 + 4, 2 ** 31 - 1)    # X0
    struct.pack_into(">i", raw, 64 + 8, 2 ** 31 - 1)    # Xn
    return bytes(raw)


def test_steim_differences_wrap_like_int32():
    res = mseed.parse_record(steim2_wrapped(), 0, check=True)
    assert isinstance(res, tuple)
    assert res[0].samples.tolist() == [2 ** 31 - 1, -2 ** 31, 2 ** 31 - 1]


def test_xn_mismatch_keeps_the_samples(tmp_path):
    y = list(range(200))
    rec = mseed.encode_records("WB", "KUM", "0J", "HNZ", 100, T0, y)[0]
    raw = bytearray(rec.raw)
    struct.pack_into(">i", raw, 64 + 8, y[-1] + 1)       # wrong Xn, every sample right
    before = mseed.xn_mismatches
    res = mseed.parse_record(bytes(raw), 0, check=True)
    assert isinstance(res, tuple) and res[0].samples.tolist() == y
    assert mseed.xn_mismatches == before + 1
    with pytest.raises(ValueError):
        mseed.steim_decode(bytes(raw[64:]), 200, 2, strict_xn=True)
    hub = DataHub()
    link = client(1, hub, new_state(tmp_path))
    link._packet(5, bytes(raw))
    assert len(hub.packets) == 1 and link.bad == 0


class BadPacketServer:
    """Sends packets 1 (good), 2 (a Steim2 record whose differences wrap) and 3 (good), from
    the sequence number the client asks for."""

    def __init__(self, records):
        self.records = records

    async def handle(self, reader, writer):
        start = 1
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                c = line.decode().strip()
                if c == "HELLO":
                    writer.write(b"SeedLink v3.1 (test) :: SLPROTO:3.1\r\ntest\r\n")
                elif c.startswith(("STATION", "SELECT")):
                    writer.write(b"OK\r\n")
                elif c.startswith("DATA"):
                    parts = c.split()
                    start = int(parts[1], 16) if len(parts) > 1 else 1
                    writer.write(b"OK\r\n")
                elif c == "END":
                    for i, raw in enumerate(self.records, 1):
                        if i >= start:
                            writer.write(b"SL%06X" % i + raw)
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()


def test_client_passes_a_record_with_wrapping_differences(tmp_path):
    async def main():
        good = make_records(T0, 4, chans=("HNZ",))
        fake = BadPacketServer([good[0].raw, steim2_wrapped(), good[1].raw])
        aserver = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
        port = aserver.sockets[0].getsockname()[1]
        hub = DataHub()
        try:
            async with Running(client(port, hub, new_state(tmp_path))) as link:
                await wait_until(lambda: len(hub.packets) == 3)
                assert link.failures == 0 and link.bad == 0
                assert hub.packets[-1][1].raw == good[1].raw
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def test_wire_never_stops_a_client():
    """Records of another length are re-encoded: a non-integer rate above 32.767 sps fits int16
    now, and samples beyond int32 are skipped instead of disconnecting every client."""
    assert mseed.seed_rate(40.5) == (405, -10)
    assert mseed.rate_from(*mseed.seed_rate(0.4)) == pytest.approx(0.4)
    hub = DataHub()
    big = mseed.encode_records("WB", "BIG", "", "HHZ", 40.5, T0, list(range(3000)), reclen=4096)
    hub.add_record(big[0], "test")
    srv = SeedLinkServer(hub)
    seq, rec = hub.snapshot_packets()[0]
    pkts = srv._wire(seq, rec)
    assert pkts and all(len(p) == 520 for p in pkts)
    back = np.concatenate([mseed.parse_record(p[8:], 0, check=True)[0].samples for p in pkts])
    assert back.tolist() == rec.samples.tolist()
    huge = mseed.encode_records("WB", "BIG", "", "HHZ", 40.5, T0 + 100, [1, 2, 3], reclen=1024)[0]
    raw = bytearray(huge.raw)
    raw[52] = 5                                          # blockette 1000 encoding: float64
    struct.pack_into(">3d", raw, 64, 5e9, 1.0, 2.0)
    res = mseed.parse_record(bytes(raw), 0, check=True)[0]
    assert srv._wire(99, res) == []                       # skipped, no exception


def test_idle_client_closed_and_keepalive(monkeypatch):
    from seiszone.core import seedlink_server as sls
    monkeypatch.setattr(sls, "IDLE_TIMEOUT", 0.3)
    seen = []
    orig = sls._keepalive

    def spy(writer):
        orig(writer)
        sock = writer.get_extra_info("socket")
        seen.append(sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE))
        if hasattr(socket, "TCP_KEEPIDLE"):   # a vanished peer is noticed after about a minute
            seen.append(sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE))
    monkeypatch.setattr(sls, "_keepalive", spy)

    async def main():
        srv, aserver, port = await start_server(DataHub())
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            await wait_until(lambda: srv.clients == 1)
            assert await asyncio.wait_for(r.read(100), 3) == b""   # silent: closed by the server
            await wait_until(lambda: srv.clients == 0 and not srv.peers)
            w.close()
            assert seen and seen[0] != 0
            if hasattr(socket, "TCP_KEEPIDLE"):
                assert seen[1] == 60
        finally:
            await stop_server(aserver)

    asyncio.run(main())


def test_live_first_then_the_missed_data_in_the_background(tmp_path, monkeypatch):
    """SeisZone was closed for two hours: the connection asks for live data at once (plain
    DATA) and a second connection fetches the gap (TIME <resume time>) - so the newest sample
    is live within moments, not hours old until the whole gap has come through."""
    monkeypatch.setattr(seedlink_client, "BACKFILL_SECONDS", 24 * 3600)
    monkeypatch.setattr(seedlink_client, "BACKFILL_IDLE", 1.5)
    now = time.time()
    old = make_records(now - 3 * 3600, 20)                  # received before SeisZone was closed
    missed = make_records(now - 3600, 60)                   # while it was closed
    state = new_state(tmp_path)
    state.set_seq("src1", "WB.KUM", 5, now - 2 * 3600)      # the resume position: two hours ago

    async def main():
        hub1 = DataHub()
        for rec in old + missed:
            hub1.add_record(rec, "test")
        srv, aserver, port = await start_server(hub1)
        hub2 = DataHub()
        try:
            link = client(port, hub2, state)
            assert link._data_commands("WB.KUM") == ["DATA"]                 # live first
            assert state.get(seedlink_client.GAPS)["src1|WB.KUM"] == pytest.approx(now - 7200, abs=1)
            async with Running(link):
                await wait_until(lambda: srv.peers and srv.peers[0]["stations"])
                live = make_records(now - 5, 5)
                for rec in live:
                    hub1.add_record(rec, "test")
                await wait_until(lambda: any(r.start >= now - 6 for _s, r in hub2.packets))
                await wait_until(lambda: len(hub2.packets) >= len(missed) + len(live), timeout=20)
                await wait_until(lambda: not (state.get(seedlink_client.GAPS) or {}), timeout=20)
                starts = sorted(r.start for _s, r in hub2.packets)
                assert starts[0] >= now - 3600 - 1                           # nothing from before the gap
                assert link.backfilled == len(missed) and link.detail == ""
                assert hub2.stats_snapshot()["WB.KUM.0J.HNZ"]["last_end"] >= now - 6
        finally:
            await stop_server(aserver)

    asyncio.run(main())
    # a new station (no resume position): its last day is fetched the same way
    monkeypatch.setattr(seedlink_client, "BACKFILL_SECONDS", 3600)
    fresh = new_state(tmp_path / "fresh")
    link = client(1, DataHub(), fresh)
    assert link._data_commands("WB.KUM") == ["DATA"]
    assert fresh.get(seedlink_client.GAPS)["src1|WB.KUM"] == pytest.approx(time.time() - 3600, abs=2)
    # a short gap (a reconnect): the plain resume by sequence number, no second connection
    quick = new_state(tmp_path / "quick")
    quick.set_seq("src1", "WB.KUM", 5, time.time() - 20)
    link = client(1, DataHub(), quick)
    assert link._data_commands("WB.KUM")[0].startswith("DATA 000006") and not quick.get(seedlink_client.GAPS)


def test_an_interrupted_fetch_continues_from_where_it_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(seedlink_client, "BACKFILL_SECONDS", 24 * 3600)
    now = time.time()
    state = new_state(tmp_path)
    state.set_seq("src1", "WB.KUM", 5, now - 30)             # the live position is recent ...
    state.set(seedlink_client.GAPS, {"src1|WB.KUM": now - 5000})   # ... but a fetch was not finished
    link = client(1, DataHub(), state)
    assert link._data_commands("WB.KUM") == ["DATA"]
    assert state.get(seedlink_client.GAPS)["src1|WB.KUM"] == pytest.approx(now - 5000)
