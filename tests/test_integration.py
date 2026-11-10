"""The whole application in one process, offscreen, the way a user runs it:

  * a local SeisZone SeedLink server ("the instrument") holds three minutes of synthetic 200 sps
    data and keeps adding half a second per channel every 0.5 s in real time,
  * the real Engine (random ports on 127.0.0.1) gets it as a source and backfills the history
    through the SeedLink resume sequence, while the real MainWindow shows it,
  * the captured Kelunji Gecko link is replayed over TCP into the engine's Gecko port,
  * a recording is started through engine.recorder, the last 30 s are exported with core.export
    (part from the recording files, part from the live buffer) and compared sample by sample,
  * everything shuts down cleanly; build/screens/integration.png shows the window.

Nothing here contacts a real instrument or listens on 58772 / 18010.
"""

import asyncio
import glob
import logging
import os
import socket
import sys
import threading
import time

import numpy as np
import pytest

# the offscreen platform has no font list of its own: without this the screenshot shows boxes
if sys.platform == "win32" and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication

from seiszone import logsetup, paths
from seiszone.config import Config, State
from seiszone.core import export, links, mseed
from seiszone.core.engine import Engine
from seiszone.core.hub import DataHub
from seiszone.core.seedlink_server import SeedLinkServer
from seiszone.ui import theme

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS = os.path.join(ROOT, "build", "screens")
CAPTURE = os.path.join(ROOT, "tests", "data", "gecko_native_capture.bin")
RATE = 200
CHUNK = 0.5                     # s of data the fake instrument adds per channel and step
HISTORY = 180                   # s it already holds when SeisZone connects
CHANS = ("HNZ", "HNN", "HNE")
SIDS = [f"WB.KUM.0J.{c}" for c in CHANS]
GECKO_SIDS = ["SS.SMR03.00.HNE", "SS.SMR03.00.HNN", "SS.SMR03.00.HNZ"]
SOURCE_ID = "kumari"
log = logging.getLogger("seiszone")


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(links, "RECONNECT_DELAY", 0.2)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pump(app, seconds, until=None):
    """Run the Qt event loop for `seconds` (or until `until()` is true); returns until()'s value."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return until() if until is not None else None


def synthetic(cha, n0, n):
    """Samples n0 .. n0+n of one channel: background noise, a microseism, and a small local
    event every 60 s - continuous across calls, so the live chunks join without a step."""
    k = np.arange(n0, n0 + n, dtype=np.float64)
    t = k / RATE
    phase = CHANS.index(cha)
    y = 240.0 * (np.mod(k * 0.6180339887 + phase * 0.37, 1.0) - 0.5) * np.sin(k * 1.7 + phase)  # noise-like
    y += 600.0 * np.sin(2 * np.pi * 0.23 * t + phase) + 150.0 * np.sin(2 * np.pi * 3.1 * t + 2 * phase)
    since = np.mod(t, 60.0) - 20.0          # event onset 20 s into every minute
    env = np.where(since >= 0, np.exp(-since / 4.0) * (1 - np.exp(-since * 6.0)), 0.0)
    y += 9000.0 * env * np.sin(2 * np.pi * (6.0 - phase) * t + phase)
    return np.round(y).astype(np.int64)


class Instrument:
    """A SeisZone SeedLink server in its own loop thread, fed by an asyncio task in real time."""

    def __init__(self):
        self.hub = DataHub()
        self.hub.descriptions["WB.KUM"] = "KUMARI test site"
        now = time.time()
        self.n = self.first = int((now - HISTORY) * RATE)   # absolute sample index = epoch * RATE
        self.sent = {cha: [] for cha in CHANS}      # every sample handed out, per channel
        self.mseq = 1
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def start():
            self.add(int(HISTORY * RATE))           # the history the instrument holds
            self.server = SeedLinkServer(self.hub, "Fake instrument")
            self.srv = await asyncio.start_server(self.server.handle, "127.0.0.1", 0)
            self.port = self.srv.sockets[0].getsockname()[1]
            self.feeder = asyncio.ensure_future(self.feed())
            ready.set()

        def run():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(start())
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, name="fake-instrument", daemon=True)
        self.thread.start()
        assert ready.wait(30), "fake instrument did not start"

    def add(self, n):
        start = self.n / RATE
        for cha in CHANS:
            y = synthetic(cha, self.n, n)
            self.sent[cha].append(y)
            for rec in mseed.encode_records("WB", "KUM", "0J", cha, RATE, start, y, seq_start=self.mseq):
                self.hub.add_record(rec, "instrument")
                self.mseq += 1
        self.n += n

    def samples(self, cha, n0, n):
        """What the instrument sent of samples n0 .. n0+n of one channel."""
        y = np.concatenate(list(self.sent[cha]))
        return y[n0 - self.first:n0 - self.first + n]

    async def feed(self):
        """Every 0.5 s the half second that has just ended (like a real digitiser)."""
        step = int(CHUNK * RATE)
        while True:
            due = (self.n + step) / RATE
            await asyncio.sleep(max(0.0, due - time.time()))
            self.add(step)

    def close(self):
        async def stop():
            self.feeder.cancel()
            self.srv.close()
            self.srv.close_clients()
            await asyncio.wait_for(self.srv.wait_closed(), 5)
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


class Errors(logging.Handler):
    """Collects every ERROR the program logs while the test runs."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


def replay_gecko(port, data):
    """Act as the USR-W610 of the SMR03: connect, send the captured link bytes, keep reading the
    hub's answers. Returns (socket, reader thread); the caller closes the socket."""
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    back = bytearray()

    def read():
        try:
            while True:
                d = s.recv(65536)
                if not d:
                    return
                back.extend(d)
        except OSError:
            return
    reader = threading.Thread(target=read, name="gecko-replay", daemon=True)
    reader.start()
    for i in range(0, len(data), 4096):
        s.sendall(data[i:i + 4096])
        time.sleep(0.002)
    return s, reader, back


def test_application_end_to_end(tmp_path):
    app = QApplication.instance() or QApplication([])
    theme.apply(app, "dark")
    if not log.handlers:
        logsetup.setup(folder=paths.log_dir())
    errors = Errors()
    log.addHandler(errors)

    cfg = Config(str(tmp_path / "config.json"))
    gecko_port, server_port = free_port(), free_port()
    cfg.update_section("gecko", {"enabled": True, "bind": "127.0.0.1", "ports": [gecko_port]})
    cfg.update_section("seedlink_server", {"enabled": True, "bind": "127.0.0.1", "port": server_port,
                                           "organization": "Integration test"})
    rec_folder = str(tmp_path / "Recordings")
    cfg.set("recording", "folder", rec_folder)
    cfg.set("view", "window_seconds", 120)
    state = State(str(tmp_path / "state.json"))
    # the source was known before: resume from the start of what the instrument holds, so the
    # history is backfilled exactly as after a VPN outage
    state.set_seq(SOURCE_ID, "WB.KUM", 0)

    inst = Instrument()
    engine = Engine(cfg, state)
    window = None
    try:
        engine.start()
        st = engine.status()
        assert st["server"]["listening"] and st["server"]["port"] == server_port
        assert st["gecko"]["listening"] == [gecko_port] and not st["gecko"]["error"]

        src = engine.add_source({"id": SOURCE_ID, "name": "KUMARI", "kind": "seedlink", "host": "127.0.0.1",
                                 "port": inst.port,
                                 "stations": [{"net": "WB", "sta": "KUM", "desc": "KUMARI", "selectors": []}]})
        assert src["id"] == SOURCE_ID

        from seiszone.ui.main_window import MainWindow
        window = MainWindow(engine, cfg)
        window.show()

        # ---- live data: channels in the table, traces in the view
        def channels_listed():
            return {r["sid"] for r in window.channels.model.rows} >= set(SIDS)
        assert pump(app, 20, channels_listed), "channels did not appear in the Channels table"
        pump(app, 8)
        assert set(window.view.channels()) == set(SIDS)
        assert engine.status()["sources"][SOURCE_ID]["state"] == links.RECEIVING
        stats = engine.hub.stats_snapshot()
        for sid in SIDS:
            assert stats[sid]["rate"] == RATE
            assert stats[sid]["first"] <= time.time() - HISTORY + 5, "history not backfilled"
            assert time.time() - stats[sid]["last_end"] < 5, "live data late"
            assert stats[sid]["gaps"] == 0
        for tr in window.view._traces:
            x, y = tr.curve.getData()
            assert x is not None and len(x) > 100 and np.isfinite(y).any(), f"no trace data for {tr.sid}"
        assert "1/1 sources receiving" in window.st["sources"].text()

        # ---- the Kelunji Gecko link connects in and replays its captured session
        with open(CAPTURE, "rb") as fh:
            sock, reader, back = replay_gecko(gecko_port, fh.read())
        try:
            assert pump(app, 20, lambda: set(GECKO_SIDS) <= set(engine.hub.stream_ids())), "no Gecko data"
            assert pump(app, 5, lambda: "Gecko links: 1" in window.st["gecko"].text())
            glinks = engine.status()["gecko"]["links"]
            assert len(glinks) == 1 and glinks[0]["serial"] == "02000143" and glinks[0]["station"] == "SS.SMR03"
            assert pump(app, 5, lambda: {r["sid"] for r in window.channels.model.rows} >= set(GECKO_SIDS))
            assert len(back) >= 21                     # the receiver greeted and acknowledged
        finally:
            sock.close()
            reader.join(5)
        assert pump(app, 10, lambda: not engine.status()["gecko"]["links"]), "Gecko link not closed"

        # ---- recording through the engine's recorder
        rid = engine.recorder.start("integration", rec_folder)
        files = lambda: sorted(glob.glob(os.path.join(rec_folder, "WB.KUM", "*", "*.mseed")))
        assert pump(app, 10, lambda: len(files()) == 3), "recording files did not appear"
        pump(app, 3)
        assert all(os.path.getsize(p) >= 1024 for p in files())
        assert "Recording 1" in window.st["recording"].text()
        assert window.act_record.text() == "Recording (1)"

        os.makedirs(SCREENS, exist_ok=True)
        shot = os.path.join(SCREENS, "integration.png")
        assert window.grab().save(shot)

        # ---- export the last 30 s: older part from the live buffer, newest from the files
        t1 = min(engine.hub.stats_snapshot()[sid]["last_end"] for sid in SIDS)
        t1 = np.floor(t1 * RATE) / RATE
        t0 = t1 - 30
        out = str(tmp_path / "Exports")
        written, report = export.export_window(SIDS, t0, t1, out, mode="separate", folders=[rec_folder],
                                               hub=engine.hub, log=lambda _m: None)
        assert len(written) == 4 and written[-1].endswith(".txt")
        for sid in SIDS:
            assert f"{sid}: 200 sps, 6000 samples of 6000 expected, 0 gap(s)" in report
        assert "recordings:" in report and "live buffer:" in report
        for path in written[:-1]:
            recs = list(mseed.read_file(path, check=True))
            sid = recs[0].sid
            assert {r.sid for r in recs} == {sid} and all(r.reclen == 512 for r in recs)
            got = np.concatenate([r.samples for r in recs])
            assert abs(recs[0].start - t0) < 0.5 / RATE
            assert len(got) == 6000
            n0 = int(round(recs[0].start * RATE))
            assert np.array_equal(got, inst.samples(sid.split(".")[-1], n0, 6000))  # lossless end to end

        assert engine.recorder.stop(rid)
        assert "RECORDING STOPPED" in open(os.path.join(rec_folder, "recording_log.txt"), encoding="utf-8").read()
    finally:
        if window is not None:
            window.prepare_quit()
            window.close()
            window.deleteLater()
        t0 = time.monotonic()
        engine.stop()
        stop_time = time.monotonic() - t0
        inst.close()
        log.removeHandler(errors)
        pump(app, 0.2)

    assert stop_time < 6, f"engine stop took {stop_time:.1f} s"
    assert not engine.running and not any(t.name == "seiszone-engine" for t in threading.enumerate())
    assert errors.lines == [], "errors were logged:\n" + "\n".join(errors.lines)
    # the instrument's channels, then the Gecko's as they joined (new channels are shown by themselves;
    # within each group in the order they arrived)
    saved = Config(cfg.path).get("view", "channels")
    assert cfg.source(SOURCE_ID)["enabled"] and len(saved) == len(SIDS) + len(GECKO_SIDS)
    assert set(saved[:len(SIDS)]) == set(SIDS) and set(saved[len(SIDS):]) == set(GECKO_SIDS)
