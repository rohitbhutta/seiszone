"""FakeEngine: the Engine contract (plan.md 5.14) with synthetic data, for user-interface tests
and offscreen screenshots. A real DataHub is filled with three Fortimus-like channels at
200 sps and one Gecko-like channel at 100 sps; status() looks like a running engine."""

import time

import numpy as np

from seiszone.config import Config, State, validate_source
from seiszone.core import mseed
from seiszone.core.hub import DataHub


class FakeRecorder:
    def __init__(self):
        self._sessions = []

    def start(self, name, folder, sids=None, file_minutes=60, layout="channel"):
        sid = f"rec{len(self._sessions) + 1}"
        self._sessions.append({"id": sid, "name": name, "folder": folder, "sids": sids,
                               "file_minutes": file_minutes, "layout": layout, "started": time.time(),
                               "stopped": None, "state": "recording", "files": 0, "bytes": 0,
                               "last_write": 0.0, "last_error": ""})
        return sid

    def stop(self, rid):
        for s in self._sessions:
            if s["id"] == rid:
                s["state"], s["stopped"] = "stopped", time.time()

    def sessions(self):
        return [dict(s) for s in self._sessions]

    def disk_status(self, folder):
        return 100 * 1024 ** 3, None


class FakeEngine:
    def __init__(self, config=None, seconds=600, now=None):
        self.config = config or Config()
        self.state = State()
        self.hub = DataHub(buffer_seconds=3600)
        self.recorder = FakeRecorder()
        self.responses = None
        self.calls = []
        self.started = time.time()
        now = now or time.time()
        rng = np.random.default_rng(7)
        t0 = now - seconds
        chans = [("WB", "KUM", "0J", "HNZ", 200), ("WB", "KUM", "0J", "HNN", 200),
                 ("WB", "KUM", "0J", "HNE", 200), ("SS", "SMR03", "", "HHZ", 100)]
        for net, sta, loc, cha, rate in chans:
            n = int(seconds * rate)
            t = np.arange(n) / rate
            y = (2000 * np.sin(2 * np.pi * 1.3 * t) + 500 * np.sin(2 * np.pi * 7 * t)
                 + rng.normal(0, 150, n)).astype(int)
            k = n // 2  # a quake-like burst in the middle
            y[k:k + 2000] += (40000 * np.exp(-np.arange(2000) / 400) * np.sin(np.arange(2000) * 0.6)).astype(int)
            for rec in mseed.encode_records(net, sta, loc, cha, rate, t0, y):
                self.hub.add_record(rec, "fake1" if net == "WB" else "gecko:0000A1B2")
        self.hub.descriptions.update({"WB.KUM": "KUMARI", "SS.SMR03": "SMR03 - Kelunji Gecko"})
        if not self.config.sources():
            self.config.add_source({"id": "fake1", "name": "KUMARI", "kind": "seedlink", "host": "10.8.0.182",
                                    "port": 18000, "stations": [{"net": "WB", "sta": "KUM", "desc": "KUMARI",
                                                                 "selectors": []}]})

    # --- Engine contract ---------------------------------------------------------------
    def start(self):
        self.calls.append(("start",))

    def stop(self, timeout=5):
        self.calls.append(("stop",))

    def add_source(self, src):
        self.calls.append(("add_source", src))
        return self.config.add_source(validate_source(src))

    def remove_source(self, sid):
        self.calls.append(("remove_source", sid))
        self.config.remove_source(sid)

    def update_source(self, src):
        self.calls.append(("update_source", src))
        return self.config.update_source(src)

    def set_enabled(self, sid, enabled):
        self.calls.append(("set_enabled", sid, enabled))
        s = self.config.source(sid)
        if s:
            s["enabled"] = enabled
            self.config.update_source(s)

    def reconnect(self, sid):
        self.calls.append(("reconnect", sid))

    def apply_settings(self):
        self.calls.append(("apply_settings",))

    def call_soon(self, fn, *args):
        fn(*args)

    def status(self):
        now = time.time()
        srcs = {}
        for s in self.config.sources():
            srcs[s["id"]] = {"id": s["id"], "kind": s["kind"], "address": f"{s['host']}:{s['port']}",
                             "state": "receiving" if s.get("enabled", True) else "stopped", "detail": "",
                             "packets": 1234, "duplicates": 0, "bytes": 631808, "last_packet": now - 1,
                             "since": now - 3600}
        return {"sources": srcs,
                "gecko": {"ports": [58772], "listening": [58772], "error": "",
                          "links": [{"peer": "10.10.100.254:50123", "serial": "0000A1B2", "station": "SS.SMR03",
                                     "sitename": "SMR03", "records": 4567, "bytes": 2338304, "since": now - 7200}]},
                "server": {"port": 18010, "listening": True, "error": "", "clients": 1,
                           "peers": [{"peer": "127.0.0.1:50555", "since": now - 60, "stations": "*", "packets": 99}]},
                "started": self.started}
