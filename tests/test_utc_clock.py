"""utc_clock: the SNTP arithmetic, a local fake time server (never the internet), the clock's
states, and latency measured against UTC instead of a wrong PC clock."""

import socket
import struct
import threading
import time

import pytest

from seiszone.core import mseed, utc_clock
from seiszone.core.hub import DataHub
from seiszone.core.utc_clock import NTP_EPOCH, UtcClock, describe, parse_reply, query, request

SID = "WB.KUM.0J.HNZ"


def stamp(t):
    return struct.pack("!II", int(t // 1) + NTP_EPOCH, int((t % 1.0) * 4294967296.0))


def reply(sent, t2, t3, leap=0, mode=4, stratum=2):
    head = bytes([(leap << 6) | (4 << 3) | mode, stratum, 6, 0xEC]) + bytes(8) + b"GPS\0"
    return head + bytes(8) + sent[40:48] + stamp(t2) + stamp(t3)


class FakeServer:
    """SNTP server on 127.0.0.1 (a free port) whose clock is the PC clock + shift."""

    def __init__(self, shift, hold=0.0, **kw):
        self.shift, self.hold, self.kw, self.asked = shift, hold, kw, 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.quit = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.quit.is_set():
            try:
                data, peer = self.sock.recvfrom(512)
            except OSError:
                continue
            self.asked += 1
            time.sleep(self.hold)          # a slow network (before the server's own time stamps)
            t2 = time.time() + self.shift
            self.sock.sendto(reply(data, t2, time.time() + self.shift, **self.kw), peer)

    def close(self):
        self.quit.set()
        self.thread.join(2)
        self.sock.close()


@pytest.fixture
def server():
    made = []

    def make(shift, **kw):
        made.append(FakeServer(shift, **kw))
        return made[-1]
    yield make
    for s in made:
        s.close()


@pytest.fixture
def shared_clock():
    """The process-wide clock, put back as it was after the test."""
    c = utc_clock.clock
    saved = (c.offset, c.known, c.state)
    yield c
    c.offset, c.known, c.state = saved


def test_offset_and_round_trip_arithmetic():
    sent = request(1000.0)
    # server 19 s behind the PC; 0.1 s on the way out, 0.05 s inside, 0.1 s back
    off, delay = parse_reply(reply(sent, 1000.1 - 19, 1000.15 - 19), sent, 1000.25)
    assert off == pytest.approx(-19.0, abs=1e-6) and delay == pytest.approx(0.2, abs=1e-6)


@pytest.mark.parametrize("kw, text", [({"mode": 3}, "mode"), ({"stratum": 0}, "kiss"),
                                      ({"leap": 3}, "not synchronised"), ({"stratum": 16}, "not synchronised")])
def test_unusable_replies_are_refused(kw, text):
    sent = request(1000.0)
    with pytest.raises(ValueError, match=text):
        parse_reply(reply(sent, 1000, 1000, **kw), sent, 1000.1)


def test_a_reply_to_another_request_or_a_short_one_is_refused():
    sent, other = request(1000.0), request(1001.0)
    with pytest.raises(ValueError, match="another request"):
        parse_reply(reply(other, 1000, 1000), sent, 1000.1)
    with pytest.raises(ValueError, match="short"):
        parse_reply(b"\x24" * 20, sent, 1000.1)


def test_query_a_local_server_that_is_19_s_behind(server):
    s = server(-19.05)
    off, delay = query("127.0.0.1", s.port)
    assert off == pytest.approx(-19.05, abs=0.02) and 0 <= delay < 0.5
    assert s.asked == utc_clock.SAMPLES


def test_slow_answers_are_not_trusted(server, monkeypatch):
    monkeypatch.setattr(utc_clock, "MAX_DELAY", 0.05)
    s = server(-19.0, hold=0.1)
    with pytest.raises(ValueError, match="round trip"):
        query("127.0.0.1", s.port, samples=2)


def test_clock_now_follows_the_server(server):
    s = server(-19.0)
    c = UtcClock(servers=["127.0.0.1"], port=s.port)
    assert c.now() == pytest.approx(time.time(), abs=0.01) and c.status()["state"] == "unchecked"
    assert c.check()
    st = c.status()
    assert st["state"] == "ok" and st["server"] == "127.0.0.1" and st["offset"] == pytest.approx(-19.0, abs=0.02)
    assert c.now() == pytest.approx(time.time() - 19.0, abs=0.02)
    assert describe(st["offset"]) == "PC clock 19.0 s fast"
    assert describe(3.4) == "PC clock 3.4 s slow" and describe(-0.2).startswith("PC clock right")


def test_no_answer_keeps_the_pc_clock_until_a_server_answers(server):
    good = server(+2.5)
    dead = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # a port that never answers
    dead.bind(("127.0.0.1", 0))
    try:
        c = UtcClock(servers=["127.0.0.1"], port=dead.getsockname()[1], timeout=0.1)
        assert not c.check()
        assert c.status()["state"] == "failed" and c.status()["offset"] is None
        assert c.now() == pytest.approx(time.time(), abs=0.01)      # the PC clock, as before
        c.port = good.port
        assert c.check() and c.status()["offset"] == pytest.approx(2.5, abs=0.02)
    finally:
        dead.close()


def test_off_by_environment_never_starts_a_thread(monkeypatch):
    monkeypatch.setenv(utc_clock.ENV, "off")
    c = UtcClock(servers=["127.0.0.1"], port=9)
    c.start()
    assert c._thread is None and c.status()["state"] == "off"


def test_a_clock_step_starts_a_new_check(server, monkeypatch):
    monkeypatch.delenv(utc_clock.ENV, raising=False)
    s = server(-19.0)
    c = UtcClock(servers=["127.0.0.1"], port=s.port, every=3600)
    c.start()
    try:
        deadline = time.monotonic() + 5
        while c.status()["state"] != "ok" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert c.status()["state"] == "ok" and not c.stepped()
        asked = s.asked
        s.shift = 0.0                 # Windows "Sync now": the PC clock jumps 19 s back ...
        c._step += 19.0               # ... seen as a step of PC clock - monotonic
        assert c.stepped()
        deadline = time.monotonic() + 5
        while s.asked == asked and time.monotonic() < deadline:
            time.sleep(0.05)
        deadline = time.monotonic() + 5
        while abs(c.status()["offset"]) > 0.1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert c.status()["offset"] == pytest.approx(0.0, abs=0.05)
    finally:
        c.stop()


def test_latency_is_measured_against_utc_not_the_pc_clock(shared_clock):
    """The PC is 19 s fast; data arriving 2.5 s after it was sampled shows 2.5 s, not 21.5 s."""
    shared_clock.offset, shared_clock.known, shared_clock.state = -19.0, True, "ok"
    utc = time.time() - 19.0
    hub = DataHub()
    for r in mseed.encode_records("WB", "KUM", "0J", "HNZ", 100, int(utc) - 30, [1] * 2000):
        hub.add_record(r, "test")
    st = hub.stats_snapshot()[SID]
    assert st["last_end"] == int(utc) - 10
    assert 9.8 < st["latency"] < 11.2                          # by the PC clock it would be ~29-30 s
    assert 9.8 < utc_clock.now() - st["last_end"] < 11.2       # what the Latency column shows
    assert abs(st["last_arrival"] - time.time()) < 1          # arrival times stay on the PC clock


def test_status_bar_text():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from seiszone.ui import theme
    from seiszone.ui.main_window import clock_status
    text, color, tip = clock_status({"state": "ok", "offset": -19.0, "server": "time.example", "delay": 0.07,
                                     "checked": time.time()})
    assert text == "PC clock 19.0 s fast - corrected" and color == theme.COLORS["warn"]
    assert "time.example" in tip and "Sync now" in tip
    assert clock_status({"state": "ok", "offset": 0.3, "checked": time.time()})[0] == "Clock: UTC ok"
    assert clock_status({"state": "failed", "error": "x"})[0] == "PC clock not checked"
    assert clock_status({"state": "off"})[0] == "PC clock (time check off)"
    assert clock_status({})[0].startswith("Checking")
