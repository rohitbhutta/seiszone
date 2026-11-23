"""Tests for core.probe (adding an instrument by IP:port) and the GCF decoding it relies on.

Uses real data captured from the Fortimus FMUS-A46D (CI.TEDT): GCF datagrams pulled over UDP
(GCFSEND:B) and its web pages. Network tests run against local fake servers on 127.0.0.1 with
random ports only - never a real instrument."""

import asyncio
import contextlib
import http.server
import json
import os
import socket
import struct
import threading
import time

import pytest
from conftest import DATA

from seiszone.config import validate_source
from seiszone.core import gcf, mseed
from seiszone.core import probe as ip
from seiszone.core.gcf_link import block_records
from seiszone.core.hub import DataHub
from seiszone.core.seedlink_server import SeedLinkServer

TEDT_MAP = {"ACCZ0": ["0J", "HNZ"], "ACCN0": ["0J", "HNN"], "ACCE0": ["0J", "HNE"],
            "AXLZ0": ["9J", "BNZ"], "AXLN0": ["9J", "BNN"], "AXLE0": ["9J", "BNE"]}


def datagrams():
    with open(os.path.join(DATA, "fortimus_udp.bin"), "rb") as fh:
        raw = fh.read()
    out, i = [], 0
    while i < len(raw):
        n = int.from_bytes(raw[i:i + 4], "big")
        out.append(raw[i + 4:i + 4 + n])
        i += 4 + n
    return out


def page(name):
    with open(os.path.join(DATA, name), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def free_port():
    """A TCP port nothing listens on (bound once, then released)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_block(stream="TESTZ2", sysid="ABCD", rate_code=100, comp=4, samples=None, day=13500, secs=3600):
    samples = samples or [i % 7 - 3 for i in range(400)]
    diffs = [0] + [b - a for a, b in zip(samples, samples[1:])]
    cc = comp & 7
    nrec = len(samples) // cc
    body = struct.pack(">i", samples[0]) + struct.pack({1: ">%di", 2: ">%dh", 4: ">%db"}[cc] % len(diffs), *diffs) \
        + struct.pack(">i", samples[-1])
    sysv = int(sysid, 36) | 0xC0000000
    return struct.pack(">III", sysv, int(stream, 36), (day << 17) | secs) + bytes([0, rate_code, comp, nrec]) + body


class Closer:
    """TCP listener that accepts and closes at once: a port with no SeedLink / GCF behind it that
    answers quickly (a refused connection takes about 2 s on Windows)."""

    def __init__(self, port=0):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                conn, _a = self.sock.accept()
            except OSError:
                return
            conn.close()

    def close(self):
        self.sock.close()


class NotFound(http.server.BaseHTTPRequestHandler):
    """A web server that is not a Guralp unit: 404 for everything."""

    def do_GET(self):
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def fast(monkeypatch):
    """Short GCF samples and web retries; the default SeedLink port and the web page answer
    quickly but are not an instrument."""
    closer = Closer()
    web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), NotFound)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    monkeypatch.setattr(ip, "GCF_SAMPLE_SECONDS", 2)
    monkeypatch.setattr(ip, "WEB_RETRY_PAUSE", 0.1)
    monkeypatch.setattr(ip, "SEEDLINK_PORT", closer.port)
    monkeypatch.setattr(ip, "HTTP_PORT", web.server_address[1])
    monkeypatch.no_web_port = web.server_address[1]
    yield monkeypatch
    closer.close()
    web.shutdown()
    web.server_close()


# --------------------------------------------------------------------------
# GCF decoding (real Fortimus blocks)
# --------------------------------------------------------------------------

def test_gcf_real_blocks():
    blocks = [gcf.parse_datagram(d) for d in datagrams() if len(d) > 100]
    ok = [b for b in blocks if b]
    assert len(ok) == len(blocks) == 12
    b = ok[0]
    assert (b.system_id, b.sys_type, b.stream_id, b.rate, len(b.samples)) == ("A46D", 2, "ACCN0", 200.0, 1000)
    assert time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(b.start)) == "2026-11-04 20:32:18"
    assert b.samples[:3] == [-60301, -60296, -60285] and b.samples[-1] == -60314  # as ObsPy reads them
    assert any(x.stream_id == "AXLZ0" and x.rate == 10 for x in ok)
    bad = bytearray(datagrams()[1])
    bad[500] ^= 0x55
    assert gcf.parse_datagram(bytes(bad)) is None, "a corrupted block must be rejected (RIC mismatch)"
    assert gcf.parse_datagram(b"GCFACKN") is None


def test_gcf_rates_and_btp():
    b = gcf.parse_block(make_block(rate_code=171, comp=0x14, samples=list(range(1000))))
    assert b and b.rate == 400 and abs(b.start % 1 - 0.125) < 1e-6
    b = gcf.parse_block(make_block(rate_code=194, comp=0x3C, samples=list(range(1000))))
    assert b and b.rate == 5000 and abs(b.start % 1 - 0.95) < 1e-6
    b = gcf.parse_block(make_block(rate_code=157, comp=1, samples=[5, 6, 7, 9]))
    assert b and b.rate == 0.1 and b.samples == [5, 6, 7, 9]
    blk = make_block()
    frame = b"G" + bytes([7]) + struct.pack(">H", len(blk)) + blk
    frame += struct.pack(">H", sum(frame) & 0xFFFF)
    p = gcf.StreamParser()
    got = []
    for i in range(0, len(frame) * 2, 37):  # two frames, fed in odd-sized pieces
        got += p.feed((frame + frame)[i:i + 37])
    assert len(got) == 2 and got[0][0].stream_id == "TESTZ2"
    assert got[0][1] == bytes([1]) + bytes([blk[7], 0, blk[6], blk[5], blk[4]])
    broken = bytearray(frame)
    broken[-1] ^= 1
    assert all(a is None for _b, a in gcf.StreamParser().feed(bytes(broken))), "bad checksum must not be acked"
    got = gcf.StreamParser().feed(b"\x00junk" + blk + blk[:10])
    assert len(got) == 1 and got[0][1] is None
    assert gcf.default_seed_name("ACCZ0", 200) == ("00", "HNZ")
    assert gcf.default_seed_name("1234Z2", 100) == ("02", "HHZ")
    assert gcf.default_seed_name("1234N4", 20)[1] == "BHN"


# --------------------------------------------------------------------------
# parsers
# --------------------------------------------------------------------------

def test_web_parsers():
    st = ip.parse_guralp_status(page("fortimus_status.html"))
    assert (st["hostname"], st["label"], st["system_type"], st["serial"], st["firmware"]) \
        == ("FMUS-A46D", "Tetar Dam Top", "Fortimus", "0A46D", "2.1-29184")
    assert (st.get("net"), st.get("sta"), st.get("site")) == ("CI", "TEDT", "Tetar Dam Top CH1050")
    assert st["lat"] == "24.92003" and st["lon"] == "85.2879"
    ch = ip.parse_guralp_streams(page("fortimus_datastream.html"))
    assert [(c["gcf"], c["loc"], c["cha"], c["rate"]) for c in ch] == [
        ("ACCZ0", "0J", "HNZ", 200), ("ACCN0", "0J", "HNN", 200), ("ACCE0", "0J", "HNE", 200),
        ("AXLZ0", "9J", "BNZ", 10), ("AXLN0", "9J", "BNN", 10), ("AXLE0", "9J", "BNE", 10)]
    assert ch[0]["sensitivity"] == "2.44E-6"
    assert "sd/Sensor0.xml" in ip.find_stationxml_links(page("fortimus_storage.html"))
    assert ip.parse_guralp_status("<html><title>router</title></html>") is None
    assert ip.banner_host("SeedLink v3.0 (FMUS-A46D Tetar Dam Top) :: SLPROTO:3.0") == "FMUS-A46D Tetar Dam Top"


def test_parse_target_and_selectors():
    assert ip.parse_target(" 10.8.0.203 ") == ("10.8.0.203", None)
    assert ip.parse_target("10.8.0.203:1567") == ("10.8.0.203", 1567)
    assert ip.parse_target("http://10.8.0.203/") == ("10.8.0.203", None)
    for bad in ("10.8.0.203:0", "10.8.0.203:99999", "a b", "", "10.8.0.203:x"):
        with pytest.raises(ValueError):
            ip.parse_target(bad)
    av = [{"loc": "0J", "cha": c} for c in ("HNZ", "HNN", "HNE")] + [{"loc": "9J", "cha": c} for c in ("BNZ", "BNN", "BNE")] \
        + [{"loc": "00", "cha": "SOH"}]
    assert ip.selectors_for(av[:6], av) == "0JHN?,9JBN?"  # whole groups -> wildcards, SOH left out
    assert ip.selectors_for(av[:2], av) == "0JHNZ,0JHNN"  # part of a group -> single channels
    assert ip.selectors_for(av, av) == ""                 # everything -> no selector
    # a one-character location (Minimus Lite, older Minimus: "1"): "1?" matches its padding
    one = [{"loc": "1", "cha": c} for c in ("BHZ", "BHN", "BHE")] + [{"loc": "00", "cha": "SOH"}]
    assert ip.selectors_for(one[:3], one) == "1?BH?"
    assert ip.selectors_for(one[:1], one) == "1?BHZ"


def test_bad_text_raises_but_dead_address_does_not(fast):
    with pytest.raises(ValueError):
        ip.probe("not a host")
    port = free_port()
    nothing = ip.probe(f"127.0.0.1:{port}", log=lambda m: None)
    assert not nothing.found and nothing.notes and f"127.0.0.1:{port}" in nothing.notes[0]
    assert nothing.summary_lines() == ["Nothing usable was found at 127.0.0.1."]
    with pytest.raises(ValueError):
        nothing.to_source()


# --------------------------------------------------------------------------
# local fake instrument / server
# --------------------------------------------------------------------------

class FakeGuralp:
    """Local Fortimus look-alike: web pages and a Scream UDP server sending the real blocks."""

    def __init__(self, fail_first_page=0):
        for _ in range(20):  # UDP port plus a quick-closing TCP port of the same number
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.bind(("127.0.0.1", 0))
            self.udp_port = self.udp.getsockname()[1]
            try:
                self.tcp = Closer(self.udp_port)
                break
            except OSError:
                self.udp.close()
        self.cmds = []
        self.agents = []
        self.running = True
        self.dgrams = [d for d in datagrams() if len(d) > 100]
        self.thread = threading.Thread(target=self._udp, daemon=True)
        self.thread.start()
        sx = ('<?xml version="1.0"?><FDSNStationXML xmlns="http://www.fdsn.org/xml/station/1" schemaVersion="1.1">'
              '<Network code="CI"><Station code="TEDT"><Latitude>24.92</Latitude><Longitude>85.28</Longitude>'
              '<Elevation>129</Elevation><Site><Name>Tetar Dam Top CH1050</Name></Site>'
              '<Channel code="HNZ" locationCode="0J"><SampleRate>200</SampleRate></Channel></Station></Network>'
              '</FDSNStationXML>').encode()
        pages = {"/": page("fortimus_status.html"), "/tab6.html": page("fortimus_datastream.html"),
                 "/tab9.html": page("fortimus_storage.html")}
        self.paths = paths = []
        agents = self.agents
        fails = [fail_first_page]

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                agents.append(self.headers.get("User-Agent"))
                if self.path == "/" and fails[0] > 0:  # a request lost over the VPN
                    fails[0] -= 1
                    self.send_response(503)
                    self.end_headers()
                    return
                body = sx if self.path == "/sd/Sensor0.xml" else pages.get(self.path, "").encode()
                self.send_response(200 if body else 404)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.http_port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def _udp(self):
        client = None
        last = 0
        self.udp.settimeout(0.2)
        while self.running:
            try:
                d, a = self.udp.recvfrom(100)
                self.cmds.append(d)
                if d in (b"GCFPING", b"GCFSTOP"):
                    self.udp.sendto(b"GCFACKN", a)
                if d == b"GCFSEND:B":
                    client = a
                if d == b"GCFSTOP":
                    client = None
            except (socket.timeout, ConnectionResetError):  # Windows: ICMP 'port unreachable' after a client left
                pass
            except OSError:
                break  # closed
            if client and time.time() - last > 0.3:
                last = time.time()
                for dg in self.dgrams:
                    try:
                        self.udp.sendto(dg, client)
                    except OSError:
                        break

    def close(self):
        self.running = False
        self.thread.join(2)
        self.udp.close()
        self.tcp.close()
        self.http.shutdown()
        self.http.server_close()


@pytest.fixture
def fake():
    f = FakeGuralp()
    yield f
    f.close()


def tedt_hub():
    """DataHub holding the real Fortimus blocks as miniSEED, as SeisZone's GCF link makes them."""
    hub = DataHub()
    hub.descriptions["CI.TEDT"] = "Tetar Dam Top CH1050"
    names = {"net": "CI", "sta": "TEDT", "map": TEDT_MAP}
    seqs = {}
    for d in datagrams():
        b = gcf.parse_datagram(d)
        if b and not b.is_status:
            for rec in block_records(names, b, seqs):
                hub.add_record(rec, "test")
    return hub


@contextlib.contextmanager
def seedlink_server(hub):
    """SeisZone's own SeedLink server on 127.0.0.1:<random port>, in a background loop."""
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    box = {}

    async def start():
        srv = await asyncio.start_server(SeedLinkServer(hub, "test").handle, "127.0.0.1", 0)
        box["srv"], box["port"] = srv, srv.sockets[0].getsockname()[1]
        ready.set()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(10)
    try:
        yield box["port"]
    finally:
        async def stop():
            box["srv"].close()
            await box["srv"].wait_closed()
        asyncio.run_coroutine_threadsafe(stop(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)
        loop.close()


# --------------------------------------------------------------------------
# probe against the fakes
# --------------------------------------------------------------------------

def test_probe_gcf_offline(fake, fast):
    fast.setattr(ip, "HTTP_PORT", fake.http_port)
    asked = []
    quick = ip.probe(f"127.0.0.1:{fake.udp_port}", log=lambda m: None,
                     need_xml=lambda net, sta: asked.append((net, sta)) and False)
    # StationXML already in SeisZone -> not downloaded again (a minute over a slow VPN)
    assert quick.found and quick.stationxml is None and asked == [("CI", "TEDT")]
    assert "/sd/Sensor0.xml" not in fake.paths
    assert "StationXML: already in SeismoVerse (not downloaded again)" in quick.summary_lines()

    lines = []
    inst = ip.probe(f"127.0.0.1:{fake.udp_port}", log=lines.append)
    st = inst.stations[0]
    assert inst.method == "gcf-udp" and inst.found and inst.port == fake.udp_port
    assert not inst.details.get("seedlink_port"), "a GCF-only unit stays on the GCF route"
    assert (st["net"], st["sta"], st["desc"]) == ("CI", "TEDT", "Tetar Dam Top CH1050")
    assert (inst.details["hostname"], inst.details["system_type"], inst.details["serial"]) \
        == ("FMUS-A46D", "Fortimus", "0A46D")
    assert {k: list(v) for k, v in inst.gcf_map.items()} == TEDT_MAP
    assert inst.stationxml is not None and b"TEDT" in inst.stationxml
    assert [c for c in fake.cmds if c != b"GCFSEND:B"][:1] == [b"GCFPING"]
    assert b"GCFSEND:B" in fake.cmds and fake.cmds[-1] == b"GCFSTOP"
    assert fake.agents and all(a.startswith("SeismoVerse/") and a.endswith("instrument probe") for a in fake.agents)
    assert any("GCF instrument found" in m for m in lines)

    src = inst.to_source()
    assert src["kind"] == "gcf-udp" and (src["host"], src["port"]) == ("127.0.0.1", fake.udp_port)
    assert src["name"] == "Tetar Dam Top CH1050"
    assert src["gcf"] == {"net": "CI", "sta": "TEDT", "desc": "Tetar Dam Top CH1050", "map": TEDT_MAP,
                          "system_id": "A46D"}   # the one digitiser this source uses
    assert src["details"]["serial"] == "0A46D" and "stationxml" not in src["details"]
    clean = validate_source(src)
    assert clean["gcf"]["map"] == TEDT_MAP
    json.dumps(src)  # everything stored in config.json must be JSON

    part = inst.to_source(["CI.TEDT.0J.HNZ", ("CI", "TEDT", "9J", "BNZ")])
    assert part["gcf"]["map"] == {"ACCZ0": ["0J", "HNZ"], "AXLZ0": ["9J", "BNZ"]}
    with pytest.raises(ValueError):
        inst.to_source(["XX.NONE.00.HHZ"])

    text = inst.summary_lines()
    assert text[0] == "Instrument: Fortimus FMUS-A46D 'Tetar Dam Top'"
    assert text[1] == f"Connection: GCF over UDP (Scream protocol) on 127.0.0.1:{fake.udp_port}"
    assert "Station CI.TEDT 'Tetar Dam Top CH1050': 6 channel(s) (3 x 200 sps, 3 x 10 sps)" in text
    assert "Serial number: 0A46D" in text and "Firmware: 2.1-29184" in text
    assert any(t.startswith("Position: latitude 24.92003, longitude 85.2879") for t in text)
    assert any(t.startswith("StationXML: found on the instrument (sd/Sensor0.xml") for t in text)


def test_probe_web_retried_and_gcf_without_web(fast):
    flaky = FakeGuralp(fail_first_page=2)  # the first two page requests are lost
    try:
        fast.setattr(ip, "HTTP_PORT", flaky.http_port)
        inst = ip.probe(f"127.0.0.1:{flaky.udp_port}", log=lambda m: None)
        assert inst.found and inst.stations[0]["sta"] == "TEDT" and flaky.paths.count("/") == 3
        fast.setattr(ip, "HTTP_PORT", fast.no_web_port)  # no Guralp page -> standard names from the GCF ids
        plain = ip.probe(f"127.0.0.1:{flaky.udp_port}", log=lambda m: None)
    finally:
        flaky.close()
    st = plain.stations[0]
    assert plain.found and plain.details.get("standard_names")
    assert (st["net"], st["sta"], st["desc"]) == ("XX", "A46D", "Guralp A46D")
    assert plain.gcf_map["ACCZ0"] == ("00", "HNZ") and plain.notes
    src = validate_source(plain.to_source())
    assert src["gcf"]["map"]["ACCZ0"] == ["00", "HNZ"] and src["gcf"]["net"] == "XX"
    assert "StationXML: not found (values are shown in counts)" in plain.summary_lines()
    assert plain.summary_lines()[0] == "Instrument: Guralp GCF system A46D"


def test_probe_seedlink_offline(fast):
    """Probe against SeisZone's own SeedLink server holding converted Fortimus data."""
    hub = tedt_hub()
    want = sorted(f"{s['loc']}.{s['cha']}" for s in hub.stats_snapshot().values())
    with seedlink_server(hub) as port:
        inst = ip.probe(f"127.0.0.1:{port}", log=lambda m: None)
    st = inst.stations[0]
    chans = sorted(f"{c['loc']}.{c['cha']}" for c in st["channels"])
    assert inst.method == "seedlink" and inst.port == port
    assert (st["net"], st["sta"], st["desc"]) == ("CI", "TEDT", "Tetar Dam Top CH1050")
    assert chans == want and "0J.HNZ" in chans and len(chans) >= 5
    assert not inst.details.get("no_streams")

    src = inst.to_source()
    assert src["kind"] == "seedlink" and src["name"] == "Tetar Dam Top CH1050"
    assert src["stations"] == [{"net": "CI", "sta": "TEDT", "desc": "Tetar Dam Top CH1050", "selectors": []}]
    validate_source(src)
    part = inst.to_source({"CI.TEDT.0J.HNZ", "CI.TEDT.0J.HNN", "CI.TEDT.0J.HNE", "CI.TEDT.9J.BNZ"})
    assert part["stations"][0]["selectors"] == ["0JHN?", "9JBNZ"]
    assert validate_source(part)["stations"][0]["selectors"] == ["0JHN?", "9JBNZ"]

    text = inst.summary_lines()
    assert text[1] == f"Connection: SeedLink (miniSEED) on 127.0.0.1:{port}"
    assert any(t.startswith(f"Station CI.TEDT 'Tetar Dam Top CH1050': {len(chans)} channel(s)") for t in text)
    assert text[-1] == "StationXML: not found (values are shown in counts)"


@pytest.mark.parametrize("reply, want", [
    (b"SeedLink v3.1 (2024.001) :: SLPROTO:3.1 CAP\r\nGEOFON DC\r\n", ("SeedLink v3.1 (2024.001) :: SLPROTO:3.1 CAP", "GEOFON DC")),
    (b"SeedLink v3.0 (ringserver/2020.075)\nIRIS DMC\n", ("SeedLink v3.0 (ringserver/2020.075)", "IRIS DMC")),
    (b"SeedLink v3.1 (digitiser) :: SLPROTO:3.1\rStation\r", ("SeedLink v3.1 (digitiser) :: SLPROTO:3.1", "Station")),
    (b"SeedLink v2.5 (old unit)\r\n", ("SeedLink v2.5 (old unit)", "")),          # only one line
    (b"HTTP/1.1 400 Bad Request\r\nServer: x\r\n", None),                        # not SeedLink
])
def test_any_seedlink_server_banner(fast, monkeypatch, reply, want):
    """HELLO answers of different SeedLink servers on a port typed by the user: line ends CR LF,
    LF or CR, one line only; another service on the port is not taken for SeedLink."""
    monkeypatch.setattr(ip, "BANNER_SECOND_LINE_WAIT", 0.3)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        with conn:
            conn.recv(100)
            conn.sendall(reply)
            time.sleep(1.0)
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        assert ip.seedlink_hello("127.0.0.1", port, timeout=3) == want
    finally:
        t.join(3)
        srv.close()


def test_probe_prefers_direct_seedlink(fake, fast):
    """Typed IP:1567 on a unit that also has SeedLink -> SeisZone uses the instrument's SeedLink."""
    fast.setattr(ip, "HTTP_PORT", fake.http_port)
    with seedlink_server(tedt_hub()) as port:
        fast.setattr(ip, "SEEDLINK_PORT", port)
        direct = ip.probe(f"127.0.0.1:{fake.udp_port}", log=lambda m: None)
        direct_paths = list(fake.paths)
        via = ip.probe(f"127.0.0.1:{fake.udp_port}", log=lambda m: None, prefer_direct=False)
    st = direct.stations[0]
    assert direct.method == "seedlink" and direct.port == port and (st["net"], st["sta"]) == ("CI", "TEDT")
    assert direct.details.get("typed") == f"127.0.0.1:{fake.udp_port}"
    assert "SeismoVerse connects to the instrument's own SeedLink server" in direct.notes[0]
    assert via.method == "gcf-udp" and via.details.get("seedlink_port") == port
    # the web page and StationXML are read once, not again for the SeedLink port
    assert direct_paths.count("/") == 1 and direct_paths.count("/sd/Sensor0.xml") == 1
    assert direct.stationxml is not None
    # rates come from the web page's channel table
    assert {c["cha"]: c["rate"] for c in st["channels"]}["HNZ"] == 200
    assert st["channels"][0]["gcf"]

    src = validate_source(direct.to_source())
    assert (src["kind"], src["host"], src["port"]) == ("seedlink", "127.0.0.1", port)
    assert src["details"]["typed"] == f"127.0.0.1:{fake.udp_port}" and src["details"]["hostname"]
    text = direct.summary_lines()
    assert text[1] == f"Connection: SeedLink (miniSEED) on 127.0.0.1:{port} (typed 127.0.0.1:{fake.udp_port})"
    assert any(t.startswith("StationXML: found on the instrument") for t in text)


def test_to_source_station_without_channel_list():
    """A SeedLink server that listed stations but not streams: all channels are requested."""
    inst = ip.Instrument("192.0.2.10")
    inst.method, inst.port = "seedlink", 18000
    inst.details = {"no_streams": True, "software": "SeedLink v3.1", "organization": "x", "raw": b"\x00"}
    inst.stations = [{"net": "WB", "sta": "KUM", "desc": "KUMARI", "channels": []},
                     {"net": "WB", "sta": "KDT", "desc": "", "channels": [
                         {"loc": "0J", "cha": "HNZ", "rate": 200, "gcf": "", "sensitivity": "", "units": ""}]}]
    src = validate_source(inst.to_source())
    assert [s["sta"] for s in src["stations"]] == ["KUM", "KDT"] and src["name"] == "192.0.2.10"
    assert "raw" not in src["details"]
    only = inst.to_source(["WB.KDT.0J.HNZ"])
    assert [s["sta"] for s in only["stations"]] == ["KDT"] and only["name"] == "192.0.2.10"
    assert "Station WB.KUM 'KUMARI': channel list not available (all channels will be requested)" \
        in inst.summary_lines()


def test_mseed_from_probe_blocks_matches_gcf():
    """Records made from the probed blocks carry exactly the GCF samples (sanity of the chain)."""
    hub = tedt_hub()
    by = {}
    for _seq, rec in hub.snapshot_packets():
        by.setdefault(rec.sid, []).extend(int(x) for x in rec.samples)
    want = {}
    for d in datagrams():
        b = gcf.parse_datagram(d)
        if b and not b.is_status:
            loc, cha = TEDT_MAP[b.stream_id]
            want.setdefault(f"CI.TEDT.{loc}.{cha}", []).extend(b.samples)
    assert by == want
    assert all(isinstance(mseed.parse_record(rec.raw, 0), tuple) for _s, rec in hub.snapshot_packets())


# --------------------------------------------------------------------------
# review fixes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("piece", [536, 1000, 1460])
def test_gcf_tcp_bare_blocks_split_by_reads(piece):
    """Bare blocks (no BTP) arrive in ordinary TCP segments: a block cut by a read boundary waits
    for its rest, and the leftover bytes are never taken for status blocks."""
    stream = b"".join(d[:gcf.parse_datagram(d).size] for d in datagrams() if len(d) > 100)
    p = gcf.StreamParser()
    got = []
    for i in range(0, len(stream), piece):
        got += p.feed(stream[i:i + piece])
    assert len([b for b, _a in got if not b.is_status]) == 12
    assert [b for b, _a in got if b.is_status] == []


MAX_PAD = gcf.MAX_BLOCK + 64


def test_gcf_status_blocks_in_a_stream_are_checked():
    text = b"GPS lock OK, temp 31.5 C".ljust(28, b"\x00")
    head = struct.pack(">III", int("ABCD", 36) | 0xC0000000, int("ABCD00", 36), 13500 << 17)
    status = head + bytes([0, 0, 0, len(text) // 4]) + text
    got = gcf.StreamParser().feed(status + make_block())
    assert [b.status_text for b, _a in got if b.is_status] == ["GPS lock OK, temp 31.5 C"]
    junk = head + bytes([0, 0, 0, 7]) + bytes(range(1, 29))       # a status header, binary text
    got = gcf.StreamParser().feed(junk + make_block() + bytes(MAX_PAD))   # (more bytes follow)
    assert [b.is_status for b, _a in got] == [False]


def test_probe_uses_one_digitiser_of_a_scream_server():
    """Two units behind one Scream server with the same stream names: one system id is used."""
    a = [gcf.parse_block(make_block(stream="ACCZ0", sysid="AAAA", secs=s)) for s in (10, 20, 30)]
    b = [gcf.parse_block(make_block(stream="ACCZ0", sysid="BBBB", secs=s)) for s in (10, 20)]
    assert ip.pick_system(a + b) == ("AAAA", ["BBBB"])
    assert ip.pick_system(a + b, {"hostname": "FMUS-BBBB"}) == ("BBBB", ["AAAA"])
    inst = ip.Instrument("127.0.0.1")
    inst.method, inst.port = "gcf-udp", 1567
    inst = ip._finish_gcf(inst, {"blocks": b + a, "status": []}, None, lambda m: None)
    assert inst.details["system_id"] == "AAAA" and any("BBBB" in n for n in inst.notes)
    assert validate_source(inst.to_source())["gcf"]["system_id"] == "AAAA"


def test_probe_does_not_wait_for_the_web_page_of_a_plain_seedlink_server(fast):
    """A SeedLink server that is not a Guralp unit, whose port 80 drops packets: the result is
    there once the first try of the web page has failed, not after all tries."""
    def dropped(host, path, port=None, timeout=20, limit=0):
        time.sleep(1.0)
        raise socket.timeout("timed out")
    fast.setattr(ip, "http_get", dropped)
    fast.setattr(ip, "WEB_TRIES", 4)
    assert not ip.looks_guralp(("SeedLink v3.1 (SeisZone v1.0.0) :: SLPROTO:3.1", "SeisZone"))
    assert ip.looks_guralp(("SeedLink v3.0 (FMUS-5469 KUMARI) :: SLPROTO:3.0", "Guralp"))
    with seedlink_server(tedt_hub()) as port:
        t = time.monotonic()
        inst = ip.probe(f"127.0.0.1:{port}", log=lambda m: None)
        took = time.monotonic() - t
    assert inst.found and inst.method == "seedlink"
    assert took < 2.5, f"the probe waited {took:.1f} s for a web page that is not there"


def test_the_typed_port_decides_the_protocol(fake, fast):
    """The user's rule: IP alone and IP:18000 -> SeedLink; IP:1567 -> GCF, never switched; IP
    alone falls back to GCF only when no SeedLink server answers (and says so)."""
    fast.setattr(ip, "HTTP_PORT", fake.http_port)
    fast.setattr(ip, "GCF_PORT", fake.udp_port)           # the fake GCF unit plays port 1567
    with seedlink_server(tedt_hub()) as port:
        fast.setattr(ip, "SEEDLINK_PORT", port)            # and its SeedLink server port 18000
        alone = ip.probe("127.0.0.1", log=lambda m: None)
        typed_sl = ip.probe(f"127.0.0.1:{port}", log=lambda m: None)
        typed_gcf = ip.probe(f"127.0.0.1:{fake.udp_port}", log=lambda m: None)
    assert alone.method == "seedlink" and alone.port == port
    assert typed_sl.method == "seedlink"
    assert typed_gcf.method == "gcf-udp" and typed_gcf.port == fake.udp_port   # not switched
    # no SeedLink server: IP alone -> GCF with a note; IP:18000 -> nothing (no GCF on 18000)
    closed = Closer()
    try:
        fast.setattr(ip, "SEEDLINK_PORT", closed.port)
        lines = []
        fallback = ip.probe("127.0.0.1", log=lines.append)
        none = ip.probe(f"127.0.0.1:{closed.port}", log=lambda m: None)
    finally:
        closed.close()
    assert fallback.method == "gcf-udp" and "No SeedLink server answered" in fallback.notes[0]
    assert sum("Trying SeedLink" in x for x in lines) == ip.SEEDLINK_TRIES
    assert not none.found and "no data port did" in none.notes[-1]          # (the fake has a web page)
    assert none.method is None


# --------------------------------------------------------------------------
# another SeisZone typed as IP:1567 (its GCF server, core/gcf_server.py)
# --------------------------------------------------------------------------

@contextlib.contextmanager
def seiszone_pc(with_seedlink):
    """Another SeisZone: its GCF server (and SeedLink server) on 127.0.0.1, data flowing."""
    import numpy as np
    from seiszone.core.gcf_server import GcfServer
    from seiszone.core import mseed
    hub = DataHub()
    hub.descriptions["KA.KAND"] = "KANGSABATI DOWN"
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    box = {}

    async def feed():
        t = time.time() // 1 - 30
        while True:
            for cha in ("HNZ", "HNN"):
                y = np.arange(100, dtype=np.int32) * (3 if cha == "HNZ" else 5)
                for r in mseed.encode_records("KA", "KAND", "0J", cha, 100.0, t, y):
                    hub.add_record(r, "t")
            t += 1.0
            await asyncio.sleep(0.05)

    async def start():
        sl_port = None
        if with_seedlink:
            box["sl"] = await asyncio.start_server(SeedLinkServer(hub, "test").handle, "127.0.0.1", 0)
            sl_port = box["sl"].sockets[0].getsockname()[1]
        box["gcf"] = GcfServer(hub, seedlink_port=sl_port)
        box["port"] = await box["gcf"].open("127.0.0.1", 0)
        box["feed"] = asyncio.ensure_future(feed())
        box["sl_port"] = sl_port
        ready.set()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(10)
    try:
        yield box
    finally:
        async def stop():
            box["feed"].cancel()
            box["gcf"].close()
            if "sl" in box:
                box["sl"].close()
                await box["sl"].wait_closed()
        asyncio.run_coroutine_threadsafe(stop(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)
        loop.close()


def test_probe_another_seiszone_uses_its_seedlink_server(fast):
    with seiszone_pc(with_seedlink=True) as pc:
        inst = ip.probe(f"127.0.0.1:{pc['port']}", log=lambda m: None)
    assert inst.method == "seedlink" and inst.port == pc["sl_port"]
    assert [(s["net"], s["sta"], s["desc"]) for s in inst.stations] == [("KA", "KAND", "KANGSABATI DOWN")]
    assert inst.details["typed"] == f"127.0.0.1:{pc['port']}"
    assert "this is SeismoVerse on another computer" in inst.notes[0]


def test_probe_another_seiszone_without_seedlink_reads_gcf_with_its_names(fast):
    with seiszone_pc(with_seedlink=False) as pc:
        inst = ip.probe(f"127.0.0.1:{pc['port']}", log=lambda m: None)
    assert inst.method == "gcf-udp" and inst.details["system_id"] == "KAND"
    st = inst.stations[0]
    assert (st["net"], st["sta"], st["desc"]) == ("KA", "KAND", "KANGSABATI DOWN")
    assert sorted((c["loc"], c["cha"], c["gcf"]) for c in st["channels"]) == [
        ("0J", "HNN", "HN0JN0"), ("0J", "HNZ", "HN0JZ0")]
    src = validate_source(inst.to_source())
    assert src["gcf"]["map"] == {"HN0JN0": ["0J", "HNN"], "HN0JZ0": ["0J", "HNZ"]}
    status = ip.seiszone_status(["SeisZone 1.8.0 KA.KAND KANGSABATI DOWN seedlink=18000\r\nKAND HN0JZ0 = KA.KAND.0J.HNZ"])
    assert status == {"seedlink": 18000, "sites": {"KA.KAND": "KANGSABATI DOWN"},
                      "names": {"KAND": {"HN0JZ0": "KA.KAND.0J.HNZ"}}}
    assert ip.seiszone_status(["Fortimus status: GPS locked"]) is None



def test_status_blocks_of_seismoverse_and_of_the_old_seiszone():
    for name in ("SeismoVerse 2.0.0", "SeisZone 1.9.0"):
        status = ip.seiszone_status([f"{name} KA.KAND KANGSABATI DOWN seedlink=18000"])
        assert status and status["seedlink"] == 18000 and status["sites"] == {"KA.KAND": "KANGSABATI DOWN"}


def test_info_xml_split_right_after_a_space_is_joined_intact():
    """A SeedLink server's INFO answer is cut into 456-byte pieces. When a piece ends on a space
    (between two XML attributes), the space must stay, or the station list comes back empty."""
    from seiszone.core.seedlink_server import build_info_records
    head = '<?xml version="1.0"?><seedlink software="X" organization="Y">'
    pad = 456 - len(head) - len('<station name="KUM" network="WB"') - 1
    xml = (head + "<!--" + "x" * (pad - 7) + "-->" + '<station name="KUM" network="WB" description="Kumari">'
           '<stream location="0J" seedname="HNZ" type="D"/></station></seedlink>')
    payload = xml.encode()
    assert payload[455:456] == b" "                         # the first piece ends on a space
    records = build_info_records(xml)
    assert len(records) == 2
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        with conn:
            f = conn.makefile("rb")
            f.readline()
            conn.sendall(b"SeedLink v3.1 (test) :: SLPROTO:3.1\r\nTest\r\n")
            f.readline()
            conn.sendall(b"".join(records))
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        got = ip.seedlink_info("127.0.0.1", port, "STREAMS", timeout=5)
    finally:
        t.join(5)
        srv.close()
    assert got == xml
    assert ip.parse_seedlink_streams(got) == [{"net": "WB", "sta": "KUM", "desc": "Kumari",
                                               "streams": [("0J", "HNZ")]}]
