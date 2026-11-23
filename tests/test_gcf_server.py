"""SeisZone's own GCF server (core/gcf_server.py): the GCF encoder (checked with ObsPy's GCF
reader), the UDP protocol, the blocks cut from miniSEED records, live and backfilled data side
by side, the names, and SeisZone's own GCF client pulling from it. Loopback only, port 0."""

import asyncio
import os
import socket

import numpy as np
import pytest

from seiszone.config import State, validate_source
from seiszone.core import gcf, gcf_server, mseed
from seiszone.core.gcf_link import GcfLink
from seiszone.core.gcf_server import GcfServer
from seiszone.core.hub import DataHub

T0 = 1794326400.0            # 2026-11-10 16:00:00 UTC
SID = "KA.KAND.0J.HNZ"


def records(sid, start, n, rate=100.0, seed=0, amp=3000):
    net, sta, loc, cha = sid.split(".")
    y = np.cumsum(np.random.default_rng(seed).normal(0, amp, n)).astype(np.int32)
    return mseed.encode_records(net, sta, loc, cha, rate, start, y), y


def test_encoded_blocks_read_by_obspy(tmp_path):
    from obspy import read
    y = (np.random.default_rng(3).normal(0, 1e5, 200)).astype(int)
    for rate, n, t in ((100, 200, T0), (200, 200, T0 + 1), (1000, 250, T0 + 0.25), (1, 2, T0)):
        data = gcf.encode_block("KAND", "HN0JZ0", t, rate, y[:n])
        path = tmp_path / f"b{rate}.gcf"
        path.write_bytes(data + bytes(1024 - len(data)))   # a GCF file holds 1024-byte blocks
        st = read(str(path), format="GCF")
        assert len(st) == 1 and st[0].stats.sampling_rate == rate
        assert abs(st[0].stats.starttime.timestamp - t) < 1e-6
        assert list(st[0].data) == list(y[:n])
    small = gcf.encode_block("KAND", "HN0JZ0", T0, 100, [5, 6, 7, 8])      # 8-bit differences
    assert small[14] & 7 == 4 and gcf.parse_block(small).samples == [5, 6, 7, 8]
    with pytest.raises(ValueError):
        gcf.encode_block("KAND", "HN0JZ0", T0, 100, list(range(300)))
    assert gcf.rate_code(100.0) == (100, 1) and gcf.rate_code(2000) == (179, 8) and gcf.rate_code(0.3) is None


def test_names_are_stable_and_unique(tmp_path):
    state = State(str(tmp_path / "state.json"))
    srv = GcfServer(DataHub(), state, gcf_names={"XX.PRAT.00.HHZ": ("PRAT", "PRATZ2")})
    assert srv.ids_for("KA.KAND.0J.HNZ") == ("KAND", "HN0JZ0")
    assert srv.ids_for("KA.KAND.9J.MNZ") == ("KAND", "MN9JZ0")
    assert srv.ids_for("SS.SMR03..HHZ") == ("SMR03", "HHXXZ0")
    assert srv.ids_for("XX.PRAT.00.HHZ") == ("PRAT", "PRATZ2")          # a GCF source keeps its names
    assert srv.ids_for("AB.STA.0J.HN-Z") == ("STA", "HN0JZ0")           # odd characters dropped
    assert srv.ids_for("AB.STA.0J.HNZ") == ("STA", "HN0JZ1")            # the same id: the next tap
    srv._save_ids()
    again = GcfServer(DataHub(), State(state.path))
    assert again.ids_for("AB.STA.0J.HNZ") == ("STA", "HN0JZ1")          # kept across restarts
    for sid, (system, stream) in State(state.path).get(gcf_server.STATE_KEY).items():
        assert gcf.decode_system_id(gcf.encode_id(system, system=True))[0] == system
        assert gcf.base36(gcf.encode_id(stream)) == stream


class Client(asyncio.DatagramProtocol):
    def __init__(self):
        self.got = asyncio.Queue()

    def datagram_received(self, data, addr):
        self.got.put_nowait(data)


async def _drain(q, wait=0.3):
    out = []
    while True:
        try:
            out.append(await asyncio.wait_for(q.get(), wait))
        except TimeoutError:
            return out


def _samples(blocks, stream):
    """{start: samples} of the data blocks of one stream, joined where they continue."""
    pieces = sorted((b.start, b.samples) for b in blocks if not b.is_status and b.stream_id == stream)
    out = []
    for start, s in pieces:
        if out and abs(out[-1][0] + len(out[-1][1]) / 100.0 - start) < 1e-6:
            out[-1][1].extend(s)
        else:
            out.append([start, list(s)])
    return out


def test_protocol_blocks_status_and_backfill(tmp_path):
    async def main():
        hub = DataHub()
        hub.descriptions["KA.KAND"] = "KANGSABATI DOWN"
        srv = GcfServer(hub, State(str(tmp_path / "s.json")), seedlink_port=18000)
        port = await srv.open("127.0.0.1", 0)
        loop = asyncio.get_running_loop()
        tr, cl = await loop.create_datagram_endpoint(Client, remote_addr=("127.0.0.1", port))
        tr.sendto(b"GCFPING")
        assert await asyncio.wait_for(cl.got.get(), 2) == b"GCFACKN"
        assert not srv.clients
        tr.sendto(b"GCFSEND:B")
        await asyncio.sleep(0.1)
        assert len(srv.clients) == 1 and srv.peers()[0]["params"] == "B"
        assert "Added client 127.0.0.1" in srv.control[-1]
        # live data from 16:10:00.37 (not on a whole second), then a backfill of 16:00:00.2 - 16:01
        live, y_live = records(SID, T0 + 600.37, 6000, seed=1)
        old, y_old = records(SID, T0 + 0.2, 6000, seed=2)
        for k in range(max(len(live), len(old))):      # interleaved, like the two SeedLink connections
            if k < len(live):
                hub.add_record(live[k], "t")
            if k < len(old):
                hub.add_record(old[k], "t")
        hi, hi_y = records("KA.KAND.0J.HHZ", T0 + 600, 100, rate=2000.0, seed=4)
        srv.max_rate = 1000.0
        for r in hi:
            hub.add_record(r, "t")
        srv.tick(now=srv.clients[next(iter(srv.clients))]["last"] + 1)   # status blocks; nothing idle yet
        data = await _drain(cl.got)
        blocks = [gcf.parse_datagram(d) for d in data]
        assert all(b is not None for b in blocks)
        status = [b.status_text for b in blocks if b.is_status]
        assert len(status) == 1 and status[0].startswith("SeismoVerse ") and "KA.KAND KANGSABATI DOWN" in status[0]
        assert "seedlink=18000" in status[0] and "KAND HN0JZ0 = KA.KAND.0J.HNZ" in status[0]
        assert any("KA.KAND.0J.HHZ not sent: 2000 sps is above the limit" in c for c in srv.control)
        data_blocks = [b for b in blocks if not b.is_status]
        assert {b.system_id for b in data_blocks} == {"KAND"} and {b.stream_id for b in data_blocks} == {"HN0JZ0"}
        assert all(abs(b.start - round(b.start)) < 1e-9 for b in data_blocks)     # whole seconds
        assert max(len(b.samples) for b in data_blocks) == 200                    # 2 s at 100 sps
        got = _samples(data_blocks, "HN0JZ0")
        assert len(got) == 2                         # the backfill and the live piece, apart
        (s_old, v_old), (s_live, v_live) = got
        assert s_old == T0 + 1 and v_old == list(y_old[80:80 + len(v_old)])       # from 16:00:01.00
        assert s_live == T0 + 601 and v_live == list(y_live[63:63 + len(v_live)])  # from 16:10:01.00
        assert len(v_live) == len(y_live) - 63 - (len(y_live) - 63) % 200          # the rest waits
        # the rest of a piece goes out when it is not continued for PIECE_IDLE s
        srv.tick(now=srv.clients[next(iter(srv.clients))]["last"] + gcf_server.PIECE_IDLE + 1)
        rest = [gcf.parse_datagram(d) for d in await _drain(cl.got)]
        assert sum(len(b.samples) for b in rest if not b.is_status) == (6000 - 63) % 200 + (6000 - 80) % 200
        tr.sendto(b"GCFSTOP")
        assert await asyncio.wait_for(cl.got.get(), 2) == b"GCFACKN"
        await asyncio.sleep(0.05)
        assert not srv.clients and "Removed client" in srv.control[-1]
        tr.close()
        srv.close()

    asyncio.run(main())


def test_client_timeout_and_limit(tmp_path, monkeypatch):
    srv = GcfServer(DataHub())
    sent = []
    srv._send = lambda data, addr: sent.append((data, addr))
    srv.transport = object()
    srv.datagram_received(b"GCFSEND:B", ("10.0.0.2", 1567))
    assert ("10.0.0.2", 1567) in srv.clients
    srv.tick(now=srv.clients[("10.0.0.2", 1567)]["last"] + gcf_server.CLIENT_TIMEOUT + 1)
    assert not srv.clients and "no request" in srv.control[-1]
    monkeypatch.setattr(gcf_server, "MAX_CLIENTS", 1)
    srv.datagram_received(b"GCFSEND:B", ("10.0.0.2", 1567))
    srv.datagram_received(b"GCFSEND:B", ("10.0.0.3", 1567))
    assert list(srv.clients) == [("10.0.0.2", 1567)] and sent[-1] == (b"GCFNOSV", ("10.0.0.3", 1567))
    srv.transport = None


def test_seiszone_pulls_from_seiszone(tmp_path):
    """A second SeisZone (its GCF client, as for any digitiser) pulls from this one's server:
    the samples arrive unchanged under the names it maps them to."""
    async def main():
        hub = DataHub()
        srv = GcfServer(hub, State(str(tmp_path / "s.json")))
        port = await srv.open("127.0.0.1", 0)
        hub2 = DataHub()
        src = validate_source({"id": "pc1", "name": "First PC", "kind": "gcf-udp", "host": "127.0.0.1",
                               "port": port, "gcf": {"net": "KA", "sta": "KAND", "system_id": "KAND",
                                                     "map": {"HN0JZ0": ["0J", "HNZ"]}}})
        link = GcfLink(src, hub2)
        stop = asyncio.Event()
        task = asyncio.create_task(link.run(stop))
        for _ in range(50):
            await asyncio.sleep(0.05)
            if srv.clients:
                break
        assert srv.clients
        recs, y = records(SID, T0 + 0.37, 3000, seed=5)
        for r in recs:
            hub.add_record(r, "t")
        for _ in range(40):
            await asyncio.sleep(0.05)
            if hub2.stats.get(SID, {}).get("packets"):
                st = hub2.stats[SID]
                if st["last_end"] >= T0 + 30 - 2:
                    break
        stop.set()
        await asyncio.wait_for(task, 5)
        srv.close()
        segs = hub2.window(SID, T0, T0 + 40)
        return segs, y

    segs, y = asyncio.run(main())
    assert len(segs) == 1
    start, rate, got = segs[0]
    assert start == T0 + 1 and rate == 100.0
    assert np.array_equal(got, y[63:63 + len(got)]) and len(got) >= 2800


# ------------------------------------------------------------------------------------------
# computers added by hand (Data server window > GCF over UDP > Send to a computer)
# ------------------------------------------------------------------------------------------
def test_parse_client_address_and_port():
    assert gcf_server.parse_client("192.168.1.20") == ("192.168.1.20", 1567)
    assert gcf_server.parse_client(" 192.168.1.20 ", "1568") == ("192.168.1.20", 1568)
    assert gcf_server.parse_client("192.168.1.20:1600") == ("192.168.1.20", 1600)
    assert gcf_server.parse_client("192.168.1.20:1600", "1600") == ("192.168.1.20", 1600)
    for text, port, says in (("", "", "Type the other computer's IP address"),
                             ("my-pc", "", "is not an IP address"),
                             ("192.168.1.300", "", "is not an IP address"),
                             ("192.168.1.20", "abc", "from 1 to 65535"),
                             ("192.168.1.20", "70000", "from 1 to 65535"),
                             ("192.168.1.20:1600", "1567", "keep only one")):
        with pytest.raises(ValueError, match=says):
            gcf_server.parse_client(text, port)
    assert gcf_server.client_list(["10.0.0.2", "10.0.0.2:1567", "bad", "10.0.0.3:1600"]) == [
        ("10.0.0.2", 1567), ("10.0.0.3", 1600)]


def test_hand_added_client_gets_data_without_asking(tmp_path):
    """A computer added by hand never sends a request: it still gets the status and every data
    block, is never removed for being idle, keeps its data after a GCFSTOP, and goes only when
    it is removed by hand. Loopback only, random ports."""
    async def main():
        hub = DataHub()
        hub.descriptions["KA.KAND"] = "KANGSABATI DOWN"
        srv = GcfServer(hub, State(str(tmp_path / "s.json")), seedlink_port=18000)
        port = await srv.open("127.0.0.1", 0)
        loop = asyncio.get_running_loop()
        tr, cl = await loop.create_datagram_endpoint(Client, local_addr=("127.0.0.1", 0))   # only listens
        me = tr.get_extra_info("sockname")[:2]
        srv.set_fixed([me])
        assert srv.peers()[0]["fixed"] and srv.peers()[0]["address"] == f"{me[0]}:{me[1]}"
        assert "by hand" in srv.control[-1]
        recs, y = records(SID, T0, 1000, seed=5)
        for r in recs:
            hub.add_record(r, "t")
        srv.tick(now=srv.clients[me]["last"] + 1)
        blocks = [gcf.parse_datagram(d) for d in await _drain(cl.got)]
        assert any(b.is_status and "KA.KAND KANGSABATI DOWN" in b.status_text for b in blocks)
        got = _samples([b for b in blocks if not b.is_status], "HN0JZ0")
        assert got and got[0][0] == T0 and got[0][1] == list(y[:len(got[0][1])]) and len(got[0][1]) >= 800
        # never removed for being idle
        srv.tick(now=srv.clients[me]["last"] + gcf_server.CLIENT_TIMEOUT * 10)
        assert me in srv.clients
        # its GCFSTOP is answered, but it keeps getting data (only the user removes it)
        srv.datagram_received(b"GCFSTOP", me)
        assert me in srv.clients and "added by hand" in srv.control[-1]
        await _drain(cl.got)
        # the same list again changes nothing; an empty list removes it
        srv.set_fixed([me])
        assert len(srv.clients) == 1
        srv.set_fixed([])
        assert not srv.clients and "removed by hand" in srv.control[-1]
        more, _y = records(SID, T0 + 10, 400, seed=6)
        for r in more:
            hub.add_record(r, "t")
        assert not [d for d in await _drain(cl.got) if d != b"GCFACKN"]   # nothing more is sent
        # a computer that asked by itself and is then added by hand stays, now kept
        tr2, cl2 = await loop.create_datagram_endpoint(Client, remote_addr=("127.0.0.1", port))
        tr2.sendto(b"GCFSEND:B")
        await asyncio.sleep(0.1)
        other = next(iter(srv.clients))
        assert not srv.clients[other].get("fixed")
        srv.set_fixed([other])
        assert srv.clients[other]["fixed"]
        tr.close()
        tr2.close()
        srv.close()

    asyncio.run(main())


def test_engine_applies_hand_added_clients(tmp_path):
    """config["gcf_server"]["clients"] reaches the running GCF server at once (apply_settings)
    and after a restart of the server; the status lists them. Loopback, random port."""
    from seiszone.config import Config
    from seiszone.core.engine import Engine
    cfg = Config(str(tmp_path / "config.json"))
    cfg.update_section("gcf_server", {"enabled": True, "bind": "127.0.0.1", "port": 0, "clients": []})
    cfg.update_section("seedlink_server", {"enabled": False})
    cfg.update_section("gecko", {"enabled": False})
    eng = Engine(cfg, State(str(tmp_path / "state.json")))
    eng.start()
    try:
        for _ in range(100):
            if eng.status()["gcf_server"]["listening"]:
                break
            __import__("time").sleep(0.05)
        assert eng.status()["gcf_server"]["listening"]
        cfg.update_section("gcf_server", {"clients": ["127.0.0.1:59999", "bad address"]})
        eng.apply_settings().result(10)
        st = eng.status()["gcf_server"]
        assert st["fixed"] == ["127.0.0.1:59999"]
        assert [(p["address"], p["fixed"]) for p in st["peers"]] == [("127.0.0.1:59999", True)]
        cfg.update_section("gcf_server", {"clients": []})
        eng.apply_settings().result(10)
        assert eng.status()["gcf_server"]["peers"] == []
    finally:
        eng.stop()
