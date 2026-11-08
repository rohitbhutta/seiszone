"""Engine and command line, end to end in one process on 127.0.0.1 with random ports (never a
real instrument, never the real ports 58772 / 18010):

  * start / stop is quick and repeatable, a busy port is reported and not fatal,
  * a seedlink source (another SeisZone SeedLink server, fed with synthetic records) reaches the
    engine's hub and is served again by the engine's own SeedLink server to a raw socket client,
  * a GCF UDP source (a local fake Scream server replaying real Fortimus datagrams),
  * remove / disable / enable / reconnect / apply_settings, recording through the engine,
  * the command line: --version, --list, --probe, --add, --engine.
"""

import asyncio
import glob
import logging
import os
import socket
import threading
import time

import numpy as np
import pytest
from test_gecko_gcf import TEDT_MAP, TEDT_SIDS, FakeScream

from seiszone import VERSION
from seiszone.config import Config, State
from seiszone.core import cli, links, mseed
from seiszone.core import probe as probe_mod
from seiszone.core.engine import Engine
from seiszone.core.hub import DataHub
from seiszone.core.seedlink_server import SeedLinkServer

CHANS = ("HNZ", "HNN", "HNE")
SIDS = [f"WB.KUM.0J.{c}" for c in CHANS]


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(links, "RECONNECT_DELAY", 0.2)


def wait_for(cond, timeout=10.0, what="condition"):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError(f"{what} not reached within {timeout} s")
        time.sleep(0.05)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_config(folder, server_port=0, gecko_ports=(0,)):
    cfg = Config(os.path.join(folder, "config.json"))
    cfg.update_section("gecko", {"enabled": True, "bind": "127.0.0.1", "ports": list(gecko_ports)})
    cfg.update_section("seedlink_server", {"enabled": True, "bind": "127.0.0.1", "port": server_port,
                                           "organization": "Engine test"})
    cfg.set("recording", "folder", os.path.join(folder, "rec"))
    return cfg


class RemoteServer:
    """Another SeisZone SeedLink server in its own loop thread; feed() keeps adding one second
    of synthetic 100 sps data per channel every 0.1 s (live data for the engine's client)."""

    def __init__(self):
        self.hub = DataHub()
        self.hub.descriptions["WB.KUM"] = "remote KUMARI"
        self.loop = asyncio.new_event_loop()
        self.t = time.time() - 120
        self.seq = 1
        self._stop = threading.Event()
        ready = threading.Event()

        async def start():
            self.sl = SeedLinkServer(self.hub, "Remote")
            self.srv = await asyncio.start_server(self.sl.handle, "127.0.0.1", 0)
            self.port = self.srv.sockets[0].getsockname()[1]
            ready.set()

        def run():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(start())
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        assert ready.wait(10)
        self.feeder = threading.Thread(target=self._feed, daemon=True)

    def add_second(self):
        rng = np.random.default_rng(self.seq)
        recs = []
        for cha in CHANS:
            y = rng.normal(0, 300, 100).astype(int)
            recs += mseed.encode_records("WB", "KUM", "0J", cha, 100, self.t, y, seq_start=self.seq)
        self.t += 1.0
        self.seq += 1

        async def add():
            for r in recs:
                self.hub.add_record(r, "remote")
        asyncio.run_coroutine_threadsafe(add(), self.loop).result(5)

    def _feed(self):
        while not self._stop.wait(0.1):
            self.add_second()

    def start_feeding(self):
        self.feeder.start()

    def close(self):
        self._stop.set()
        if self.feeder.is_alive():
            self.feeder.join(2)

        async def stop():
            self.srv.close()
            self.srv.close_clients()
            await asyncio.wait_for(self.srv.wait_closed(), 3)
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


@pytest.fixture
def remote():
    r = RemoteServer()
    yield r
    r.close()


@pytest.fixture
def make_engine(tmp_path):
    engines = []

    def make(**kw):
        e = Engine(make_config(str(tmp_path), **kw), State(str(tmp_path / "state.json")))
        e.start()
        engines.append(e)
        return e
    yield make
    for e in engines:
        e.stop()


def seedlink_source(port, sid="remote1"):
    return {"id": sid, "name": "remote", "kind": "seedlink", "host": "127.0.0.1", "port": port,
            "stations": [{"net": "WB", "sta": "KUM", "desc": "KUMARI test", "selectors": []}]}


def receiving(engine, sid):
    return engine.status()["sources"].get(sid, {}).get("state") == links.RECEIVING


def read_packets(port, n, timeout=10):
    """Raw SeedLink client: HELLO, STATION KUM WB, DATA, END; returns n 520-byte packets."""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        f = s.makefile("rb")
        s.sendall(b"HELLO\r\n")
        banner = f.readline() + f.readline()
        for cmd in (b"STATION KUM WB\r\n", b"DATA\r\n"):
            s.sendall(cmd)
            assert f.readline().strip() == b"OK"
        s.sendall(b"END\r\n")
        packets = [f.read(520) for _ in range(n)]
        s.sendall(b"BYE\r\n")
    return banner, packets


# ---------------------------------------------------------------------- life cycle

def test_start_stop_quick_and_repeated(tmp_path):
    cfg = make_config(str(tmp_path))
    state = State(str(tmp_path / "state.json"))
    first = Engine(cfg, state)
    for engine in (first, Engine(cfg, state), first):  # the last round restarts a stopped engine
        t0 = time.monotonic()
        engine.start()
        st = engine.status()
        assert st["server"]["listening"] and st["server"]["port"] > 0 and not st["server"]["error"]
        assert len(st["gecko"]["listening"]) == 1 and not st["gecko"]["error"]
        assert st["started"] and st["sources"] == {}
        assert any(t.name == "seiszone-engine" for t in threading.enumerate())
        engine.stop()
        engine.stop()  # idempotent
        assert time.monotonic() - t0 < 3
        assert not engine.running and engine.status()["started"] is None
        assert not engine.status()["server"]["listening"]
    Engine(cfg, state).stop()  # never started: still fine


def test_busy_port_reported_and_apply_settings(make_engine):
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    try:
        engine = make_engine(server_port=port, gecko_ports=(port, 0))
        st = engine.status()
        assert not st["server"]["listening"]
        assert st["server"]["error"] == f"port {port} is in use by another program"
        assert st["gecko"]["error"] == f"port {port} is in use by another program"
        assert len(st["gecko"]["listening"]) == 1  # the other Gecko port works
        assert engine.running

        new_port = free_port()
        engine.config.update_section("seedlink_server", {"port": new_port})
        engine.config.update_section("gecko", {"ports": [0]})
        with engine.config.lock:  # a plain number at the top of config.json (as the settings dialog does)
            engine.config.data["buffer_minutes"] = 2
            engine.config.save()
        engine.apply_settings().result(5)
        st = engine.status()
        assert st["server"]["listening"] and st["server"]["port"] == new_port and not st["server"]["error"]
        assert st["gecko"]["listening"] and not st["gecko"]["error"]
        assert engine.hub.buffer_seconds == 120
        with socket.create_connection(("127.0.0.1", new_port), timeout=5) as s:
            s.sendall(b"HELLO\r\n")
            f = s.makefile("rb")
            banner = f.readline() + f.readline()
        assert b"SeisZone v" + VERSION.encode() in banner and b"Engine test" in banner
    finally:
        busy.close()


# ---------------------------------------------------------------------- data paths

def test_seedlink_source_reaches_hub_and_is_served_again(make_engine, remote):
    remote.start_feeding()
    engine = make_engine()
    src = engine.add_source(seedlink_source(remote.port))
    assert src["id"] == "remote1" and engine.config.source("remote1")["port"] == remote.port
    wait_for(lambda: set(SIDS) <= set(engine.hub.stream_ids()), what="data from the remote server")
    wait_for(lambda: receiving(engine, "remote1"), what="state receiving")
    st = engine.status()["sources"]["remote1"]
    assert st["kind"] == "seedlink" and st["packets"] > 0 and st["name"] == "remote"
    assert engine.hub.descriptions["WB.KUM"] == "KUMARI test"   # configured text wins

    port = engine.status()["server"]["port"]
    banner, packets = read_packets(port, 6)
    assert b"SeisZone" in banner
    for p in packets:
        assert p[:2] == b"SL" and len(p) == 520
        rec, _n = mseed.parse_record(p[8:], 0, check=True)
        assert rec.sid in SIDS
    wait_for(lambda: engine.status()["server"]["clients"] == 0, what="raw client gone")


def test_recording_through_engine(make_engine, remote, tmp_path):
    remote.start_feeding()
    engine = make_engine()
    folder = str(tmp_path / "rec")
    rid = engine.recorder.start("engine test", folder)
    engine.add_source(seedlink_source(remote.port))
    wait_for(lambda: len(glob.glob(os.path.join(folder, "WB.KUM", "*", "*.mseed"))) == 3, what="recording files")
    wait_for(lambda: all(os.path.getsize(p) >= 2048 for p in glob.glob(os.path.join(folder, "WB.KUM", "*", "*.mseed"))),
             what="several records per file")
    engine.recorder.stop(rid)
    files = sorted(glob.glob(os.path.join(folder, "WB.KUM", "*", "*.mseed")))
    assert sorted(os.path.basename(p).split("__")[0] for p in files) == sorted(SIDS)
    for p in files:
        recs = mseed.read_file(p, check=True)
        assert recs and {r.sid for r in recs} == {os.path.basename(p).split("__")[0]}
    assert os.path.exists(os.path.join(folder, "recording_log.txt"))


def test_gcf_udp_source_and_remove(make_engine):
    fake = FakeScream()
    try:
        engine = make_engine()
        engine.add_source({"id": "fmus", "name": "KUMARI", "kind": "gcf-udp", "host": "127.0.0.1",
                           "port": fake.port, "gcf": {"net": "CI", "sta": "TEDT", "desc": "Tetar Dam Top",
                                                      "map": TEDT_MAP}})
        wait_for(lambda: engine.hub.stream_ids() == TEDT_SIDS, what="GCF data")
        wait_for(lambda: receiving(engine, "fmus"), what="state receiving")
        assert engine.status()["sources"]["fmus"]["kind"] == "gcf-udp"
        assert engine.hub.descriptions["CI.TEDT"] == "Tetar Dam Top"
        assert engine.remove_source("fmus")
        wait_for(lambda: "fmus" not in engine.status()["sources"], what="link removed")
        wait_for(lambda: fake.cmds.count(b"GCFSTOP") >= 2, what="GCFSTOP sent")
        assert engine.config.source("fmus") is None
        assert not engine.remove_source("fmus")
    finally:
        fake.close()


def test_enable_disable_reconnect_and_remove_seedlink(make_engine, remote):
    remote.start_feeding()
    engine = make_engine()
    engine.add_source(seedlink_source(remote.port))
    wait_for(lambda: receiving(engine, "remote1"), what="receiving")
    wait_for(lambda: engine.state.get_seq("remote1", "WB.KUM") is not None, what="resume position saved")

    before = {p["peer"] for p in remote.sl.peers}
    assert engine.reconnect("remote1")
    wait_for(lambda: any(p["peer"] not in before for p in remote.sl.peers), what="new connection after reconnect")
    wait_for(lambda: remote.sl.clients == 1, what="old connection closed")
    assert not engine.reconnect("unknown")

    assert engine.set_enabled("remote1", False)["enabled"] is False
    assert engine.config.source("remote1")["enabled"] is False
    wait_for(lambda: "remote1" not in engine.status()["sources"], what="link stopped")
    wait_for(lambda: remote.sl.clients == 0, what="disconnected from the remote server")
    assert not engine.reconnect("remote1")

    packets = engine.hub.stats_snapshot()[SIDS[0]]["packets"]
    engine.set_enabled("remote1", True)
    wait_for(lambda: receiving(engine, "remote1") and engine.hub.stats_snapshot()[SIDS[0]]["packets"] > packets,
             what="receiving again")
    assert remote.sl.clients == 1
    assert engine.set_enabled("unknown", True) is None

    engine.update_source(dict(engine.config.source("remote1"), name="renamed"))
    wait_for(lambda: engine.status()["sources"].get("remote1", {}).get("name") == "renamed", what="updated link")

    engine.remove_source("remote1")
    wait_for(lambda: remote.sl.clients == 0 and "remote1" not in engine.status()["sources"], what="removed")
    wait_for(lambda: engine.state.get_seq("remote1", "WB.KUM") is None, what="resume position dropped")


def test_call_soon_runs_in_engine_thread(make_engine):
    engine = make_engine()
    seen = threading.Event()
    names = []

    def where():
        names.append(threading.current_thread().name)
        seen.set()
    engine.call_soon(where)
    assert seen.wait(5) and names == ["seiszone-engine"]
    assert "sources 0/0 receiving" in engine.status_line()


# ---------------------------------------------------------------------- command line

@pytest.fixture
def quiet_log():
    """cli.main() sets up the program log: restore the logger for the other tests."""
    lg = logging.getLogger("seiszone")
    saved = (list(lg.handlers), lg.level, lg.propagate)
    yield
    for h in lg.handlers:
        if h not in saved[0]:
            h.close()
    lg.handlers[:] = saved[0]
    lg.setLevel(saved[1])
    lg.propagate = saved[2]


def test_cli_version_and_main_dispatch(capsys, quiet_log):
    from seiszone.__main__ import main, wants_cli
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"SeisZone {VERSION}"
    assert wants_cli(["--probe=1.2.3.4"]) and not wants_cli(["--minimized"])


def test_cli_probe_add_list(remote, tmp_path, capsys, monkeypatch, quiet_log):
    monkeypatch.setattr(probe_mod, "guralp_web", lambda *a, **k: None)  # no web page on 127.0.0.1
    for _ in range(5):
        remote.add_second()
    target = f"127.0.0.1:{remote.port}"
    assert cli.main(["--probe", target]) == 0
    out = capsys.readouterr().out
    assert f"Connection: SeedLink on {target}" in out
    assert "Station WB.KUM 'remote KUMARI': 3 channel(s)" in out  # rates come from the web page or StationXML

    cfg_path = str(tmp_path / "cli" / "config.json")
    assert cli.main(["--add", target, "--config", cfg_path]) == 0
    assert "Added: remote KUMARI (WB.KUM) " + target in capsys.readouterr().out
    assert cli.main(["--add", target, "--config", cfg_path]) == 0   # same address: replaced
    assert "Replaced:" in capsys.readouterr().out
    srcs = Config(cfg_path).sources()
    assert len(srcs) == 1 and srcs[0]["kind"] == "seedlink" and srcs[0]["port"] == remote.port
    assert cli.main(["--list", "--config", cfg_path]) == 0
    assert srcs[0]["id"] in capsys.readouterr().out

    assert cli.main(["--probe", "not a host!"]) == 1
    assert "Error:" in capsys.readouterr().out


def test_cli_engine_runs_and_prints_status(remote, tmp_path, capsys, quiet_log):
    remote.start_feeding()
    cfg = make_config(str(tmp_path / "cli"))
    cfg.add_source(seedlink_source(remote.port))
    t0 = time.monotonic()
    assert cli.main(["--engine", "--seconds", "2.5", "--interval", "1", "--config", cfg.path]) == 0
    assert time.monotonic() - t0 < 8
    out = capsys.readouterr().out
    assert "engine running for 2.5 s" in out
    assert "source remote (WB.KUM) 127.0.0.1:" in out
    assert "WB.KUM.0J.HNZ" in out and "100 sps" in out and "latency" in out
    assert os.path.exists(os.path.join(str(tmp_path / "cli"), "state.json"))


def test_cli_probe_and_receive_is_read_only(remote, tmp_path, capsys, monkeypatch, quiet_log):
    """--engine --probe: receive from the probed address with temporary settings; the user's
    config is not changed and no listener port is opened."""
    monkeypatch.setattr(probe_mod, "guralp_web", lambda *a, **k: None)
    remote.add_second()  # the probe lists what the server holds
    remote.start_feeding()
    cfg_path = str(tmp_path / "user" / "config.json")
    make_config(str(tmp_path / "user"), server_port=free_port(), gecko_ports=(free_port(),))
    before = open(cfg_path, encoding="utf-8").read()
    target = f"127.0.0.1:{remote.port}"
    assert cli.main(["--engine", "--probe", target, "--seconds", "2", "--interval", "1",
                     "--config", cfg_path]) == 0
    out = capsys.readouterr().out
    assert f"Connection: SeedLink on {target}" in out
    assert "WB.KUM.0J.HNZ" in out
    assert "Gecko receiver" not in out and "SeedLink server:" not in out
    assert open(cfg_path, encoding="utf-8").read() == before


def test_cli_output_file(remote, tmp_path, capsys, quiet_log):
    """--output: the windowed SeisZone.exe has no console, so everything goes into a file
    (status, the program log lines of the engine and the exit code)."""
    remote.start_feeding()
    cfg = make_config(str(tmp_path / "cli"))
    cfg.add_source(seedlink_source(remote.port))
    out = str(tmp_path / "engine.txt")
    assert cli.main(["--engine", "--seconds", "2", "--interval", "1", "--config", cfg.path, "--output", out]) == 0
    assert capsys.readouterr().out == ""
    text = open(out, encoding="utf-8").read()
    assert "engine running for 2 s" in text and "WB.KUM.0J.HNZ" in text
    assert "engine stopped" in text and text.rstrip().endswith("exit code 0")
    # no log handler is left writing into the closed file
    assert not any(getattr(h, "stream", None) is not None and h.stream.closed
                   for h in logging.getLogger("seiszone").handlers)
