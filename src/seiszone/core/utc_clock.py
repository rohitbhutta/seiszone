"""True UTC "now", for comparing with the data's own time stamps.

The instruments stamp their data with GNSS time, and the recording files are named and cut
from those stamps (recorder.period_start(rec.start)), so recordings are right whatever the PC
clock says. Latency, "now minus the end of the newest sample", also needs the true time now,
and the PC clock can be off: on 2026-11-21 it was 19.05 s fast and had never been synced, so
every channel showed ~21 s of latency where the real delay was ~2.5 s.

UtcClock asks internet time servers (SNTP, RFC 4330, UDP port 123) when the engine starts and
every 15 minutes, and keeps the offset between their UTC and the PC clock. now() is the PC clock
plus that offset; before an answer (or with no internet) it is the PC clock, as before. A jump
of the PC clock (Windows "Sync now", a manual change, sleep) is noticed within a second and the
servers are asked again at once. Only the 48-byte SNTP request is sent: no user data.

SEISZONE_TIME_CHECK=off (set by the tests) or config "time": {"check": false} turns it off.
"""

import logging
import os
import socket
import struct
import threading
import time

log = logging.getLogger("seiszone")

SERVERS = ("time.google.com", "time.windows.com", "pool.ntp.org")
PORT = 123
TIMEOUT = 2.0           # s to wait for one answer
SAMPLES = 4             # requests per check; the one with the shortest round trip counts
MAX_DELAY = 2.0         # s; answers slower than this are not trusted
EVERY = 900.0           # s between checks
RETRY = 120.0           # s after a failed check
JUMP = 2.0              # s; a PC clock step this big starts a new check
TOLERANCE = 1.0         # s; a PC clock within this is shown as right
LOG_CHANGE = 0.5        # s; a new offset this different from the last one is logged
ENV = "SEISZONE_TIME_CHECK"
NTP_EPOCH = 2208988800  # 1900-01-01 to 1970-01-01, s


def _ntp_stamp(t):
    sec = int(t // 1) + NTP_EPOCH
    frac = int((t % 1.0) * 4294967296.0) & 0xFFFFFFFF
    return struct.pack("!II", sec & 0xFFFFFFFF, frac)


def _from_stamp(raw):
    sec, frac = struct.unpack("!II", raw)
    return sec - NTP_EPOCH + frac / 4294967296.0


def request(t1):
    """The 48-byte SNTP client request (version 4, mode 3) sent at PC time t1."""
    return bytes([(4 << 3) | 3]) + bytes(39) + _ntp_stamp(t1)


def parse_reply(data, sent, t4):
    """(offset, delay) in seconds from a server reply received at PC time t4, where `sent` is
    the request (its transmit time is t1). offset = server UTC - PC clock. Raises ValueError
    for anything that is not a usable answer to this request."""
    if len(data) < 48:
        raise ValueError("short reply")
    leap, mode, stratum = data[0] >> 6, data[0] & 7, data[1]
    if mode != 4:
        raise ValueError(f"not a server reply (mode {mode})")
    if stratum == 0:
        raise ValueError("server refused (kiss-o'-death " + data[12:16].decode("ascii", "replace") + ")")
    if leap == 3 or stratum > 15:
        raise ValueError("server clock not synchronised")
    if data[24:32] != sent[40:48]:
        raise ValueError("reply to another request")
    t1 = _from_stamp(sent[40:48])
    t2, t3 = _from_stamp(data[32:40]), _from_stamp(data[40:48])
    if data[40:48] == bytes(8):
        raise ValueError("no transmit time")
    offset = ((t2 - t1) + (t3 - t4)) / 2.0
    delay = (t4 - t1) - (t3 - t2)
    return offset, delay


def query(host, port=PORT, samples=SAMPLES, timeout=TIMEOUT, wall=time.time):
    """Ask one server `samples` times; (offset, delay) of the answer with the shortest round
    trip. Raises OSError / ValueError when no answer is usable."""
    infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
    if not infos:
        raise OSError(f"{host}: no address")
    addr = infos[0][4]
    best, error = None, None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        for _ in range(samples):
            try:
                t1 = wall()
                sent = request(t1)
                s.sendto(sent, addr)
                while True:
                    data, peer = s.recvfrom(512)
                    t4 = wall()
                    if peer[0] == addr[0]:
                        break
                offset, delay = parse_reply(data, sent, t4)
            except (OSError, ValueError) as e:
                error = e
                continue
            if delay < 0 or delay > MAX_DELAY:
                error = ValueError(f"round trip {delay:.2f} s")
                continue
            if best is None or delay < best[1]:
                best = (offset, delay)
    if best is None:
        raise error or OSError(f"{host}: no answer")
    return best


class UtcClock:
    """Process-wide: now() is UTC from the time servers' offset, or the PC clock."""

    def __init__(self, servers=SERVERS, port=PORT, every=EVERY, retry=RETRY, timeout=TIMEOUT):
        self.servers, self.port, self.every, self.retry = tuple(servers), port, every, retry
        self.timeout = timeout
        self.offset = 0.0          # s to add to the PC clock
        self.known = False         # an offset from a server is in use
        self.state = "unchecked"   # unchecked | ok | failed | off
        self.server = ""
        self.delay = None
        self.checked = None        # PC time of the last good check
        self.error = ""
        self._lock = threading.Lock()
        self._thread = None
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._step = time.time() - time.monotonic()   # PC clock - monotonic, to notice steps

    # ------------------------------------------------------------------ reading
    def now(self):
        """UTC now as epoch seconds (the PC clock until a server has answered)."""
        return time.time() + self.offset

    def status(self):
        with self._lock:
            return {"state": self.state, "offset": self.offset if self.known else None,
                    "server": self.server, "delay": self.delay, "checked": self.checked,
                    "error": self.error}

    # ------------------------------------------------------------------ checking
    def check(self):
        """Ask the servers in turn (blocking); True when one answered."""
        errors = []
        for host in self.servers:
            try:
                offset, delay = query(host, self.port, timeout=self.timeout)
            except (OSError, ValueError) as e:
                errors.append(f"{host}: {e}")
                continue
            with self._lock:
                old = self.offset if self.known else None
                self.offset, self.known, self.state = offset, True, "ok"
                self.server, self.delay, self.checked, self.error = host, delay, time.time(), ""
                self._step = time.time() - time.monotonic()
            if old is None or abs(offset - old) >= LOG_CHANGE:
                log.info("PC clock checked against %s: %s (round trip %.0f ms); latency uses UTC from "
                         "the time server", host, describe(offset), delay * 1000)
            return True
        with self._lock:
            first = self.state != "failed"
            self.state, self.error = "failed", "; ".join(errors)
        if first:
            log.warning("PC clock could not be checked (%s); latency uses the PC clock", self.error)
        return False

    def start(self, servers=None):
        """Check in the background now and every `every` s (once per process; idempotent)."""
        if os.environ.get(ENV, "").lower() in ("off", "0", "no", "false"):
            with self._lock:
                self.state = "off"
            return
        if servers:
            self.servers = tuple(servers)
        if self._thread is not None and self._thread.is_alive():
            return
        self._quit.clear()
        self._thread = threading.Thread(target=self._run, name="seiszone-utc-clock", daemon=True)
        self._thread.start()

    def turn_off(self):
        """No checks; now() is the PC clock again."""
        self.stop()
        with self._lock:
            self.offset, self.known, self.state = 0.0, False, "off"

    def stop(self):
        self._quit.set()
        self._wake.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(self.timeout * SAMPLES + 1)

    def stepped(self):
        """True when the PC clock was stepped (by more than JUMP s) since the last check."""
        return abs((time.time() - time.monotonic()) - self._step) > JUMP

    def _run(self):
        due = 0.0
        while not self._quit.is_set():
            if time.monotonic() >= due or self.stepped():
                ok = self.check()
                due = time.monotonic() + (self.every if ok else self.retry)
                if not ok:   # do not ask again at every second because of the same step
                    self._step = time.time() - time.monotonic()
            self._wake.wait(1.0)
            self._wake.clear()


def describe(offset):
    """'PC clock 19.0 s fast' / '... slow' / 'PC clock right (0.03 s)'."""
    if abs(offset) < TOLERANCE:
        return f"PC clock right (within {abs(offset):.2f} s)"
    return f"PC clock {abs(offset):.1f} s {'fast' if offset < 0 else 'slow'}"


clock = UtcClock()


def now():
    """UTC now (epoch s): the PC clock corrected by the last time-server check."""
    return clock.now()
