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
                "server": self.seedlink_server_status(now),
                "gcf_server": self.gcf_server_status(now),
                "started": self.started}

    def seedlink_server_status(self, now):
        s = self.config.get("seedlink_server") or {}
        on = bool(s.get("enabled", True))
        return {"enabled": on, "bind": s.get("bind", "0.0.0.0"), "port": s.get("port", 18000), "listening": on,
                "error": "", "clients": 1 if on else 0,
                "peers": [{"peer": "127.0.0.1:50555", "since": now - 60, "stations": "*", "packets": 99}] if on else [],
                "control": ["23-11-2026 01:20:00  Client 127.0.0.1:50555 connected",
                            "23-11-2026 01:20:00  Client 127.0.0.1:50555 streaming *"] if on else [],
                "buffer": self.buffer_status(s)}

    @staticmethod
    def buffer_status(s):
        days = float(s.get("buffer_days", 2))
        return {"enabled": days > 0, "days": days, "folder": r"C:\Users\me\AppData\Local\SeisZone\ServerBuffer",
                "bytes": int(2.6 * 1024 ** 3) if days > 0 else None, "oldest": 1795300000.0 if days > 0 else None,
                "paused": False, "error": ""}

    def gcf_server_status(self, now):
        from seiszone.core.gcf_server import client_list
        on = bool((self.config.get("gcf_server") or {}).get("enabled"))
        fixed = [f"{ip}:{p}" for ip, p in client_list((self.config.get("gcf_server") or {}).get("clients"))]
        peers = [{"address": "192.168.1.100:1567", "params": "B", "since": now - 120, "idle": 3.0,
                  "blocks": 4321, "fixed": False}] if on else []
        peers += [{"address": a, "params": "B", "since": now - 30, "idle": 30.0, "blocks": 77, "fixed": True}
                  for a in fixed] if on else []
        return {"enabled": on, "port": 1567, "max_rate": 1000, "listening": on, "error": "",
                "clients": len(peers), "blocks": 4321 if on else 0, "peers": peers, "fixed": fixed,
                "control": ["22-11-2026 21:16:23  Added client 192.168.1.100:1567"] if on else []}
