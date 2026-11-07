"""SeisZone settings and sources (config.json) and small runtime state (state.json).

config.json (written atomically; a damaged file is kept as config.json.bad and defaults are used):

{
  "version": 1,
  "sources": [                                   # instruments / servers SeisZone connects to
    {"id": "4f1c...", "name": "KUMARI", "kind": "seedlink" | "gcf-udp" | "gcf-tcp",
     "host": "10.8.0.182", "port": 18000, "enabled": true, "added": 1762500000.0,
     "stations": [{"net": "WB", "sta": "KUM", "desc": "KUMARI", "selectors": []}],   # seedlink
     "gcf": {"net": "WB", "sta": "KUM", "desc": "KUMARI", "map": {"ACCZ0": ["0J", "HNZ"]}},  # gcf-*
     "details": {...what the instrument probe found: hostname, serial, firmware, lat, lon...}}
  ],
  "gecko":   {"enabled": true, "ports": [58772], "rename": {}},   # Kelunji Gecko links connect IN
  "seedlink_server": {"enabled": true, "bind": "0.0.0.0", "port": 18010, "organization": "SeisZone"},
  "recording": {"folder": "<Documents>\\SeisZone\\Recordings", "file_minutes": 60, "layout": "channel"},
  "buffer_minutes": 60,                          # live data kept in memory per channel
  "view": {"window_seconds": 300, "filter": "none", "units": "counts", "scale": "auto",
           "channels": [], "demean": true},
  "ui": {"minimize_to_tray": true, "start_minimized": false, "start_with_windows": false,
         "geometry": "", "state": ""}
}

Selectors are SeedLink selectors ("0JHN?", "HHZ", "00BH?"); [] means every channel.
"""

import copy
import json
import logging
import os
import threading
import time
import uuid

from . import paths

log = logging.getLogger("seiszone")

SOURCE_KINDS = ("seedlink", "gcf-udp", "gcf-tcp")

DEFAULTS = {
    "version": 1,
    "sources": [],
    "gecko": {"enabled": True, "ports": [58772], "rename": {}},
    "seedlink_server": {"enabled": True, "bind": "0.0.0.0", "port": 18010, "organization": "SeisZone"},
    "recording": {"folder": "", "file_minutes": 60, "layout": "channel"},
    "buffer_minutes": 60,
    "view": {"window_seconds": 300, "filter": "none", "units": "counts", "scale": "auto",
             "channels": [], "demean": True},
    "ui": {"minimize_to_tray": True, "start_minimized": False, "start_with_windows": False,
           "geometry": "", "state": ""},
}


def atomic_write_json(path, obj, tries=20):
    """Write JSON so a crash or power cut never leaves a half-written file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # another program (virus scanner, backup) has it open for a moment
            if i == tries - 1:
                raise
            time.sleep(0.05)


def _merge(defaults, data):
    out = copy.deepcopy(defaults)
    for k, v in (data or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def new_source_id():
    return uuid.uuid4().hex[:12]


def source_label(src):
    """'KUMARI (WB.KUM) 10.8.0.182:18000' style label for lists and logs."""
    codes = [f"{s['net']}.{s['sta']}" for s in src.get("stations", [])]
    if src.get("gcf"):
        codes.append(f"{src['gcf'].get('net')}.{src['gcf'].get('sta')}")
    name = src.get("name") or ", ".join(codes) or src.get("host", "?")
    extra = f" ({', '.join(codes)})" if codes and name not in codes else ""
    return f"{name}{extra} {src.get('host')}:{src.get('port')}"


def validate_source(src):
    """Clean copy of a source dict, or ValueError with a message for the user."""
    s = copy.deepcopy(src)
    if s.get("kind") not in SOURCE_KINDS:
        raise ValueError(f"unknown source kind {s.get('kind')!r}")
    host = str(s.get("host", "")).strip()
    if not host:
        raise ValueError("the source has no IP address / host name")
    try:
        port = int(s.get("port"))
    except (TypeError, ValueError):
        raise ValueError("the port must be a number") from None
    if not 1 <= port <= 65535:
        raise ValueError("the port must be 1-65535")
    s["host"], s["port"] = host, port
    s.setdefault("id", new_source_id())
    s.setdefault("enabled", True)
    s.setdefault("added", time.time())
    s.setdefault("name", "")
    s.setdefault("details", {})
    if s["kind"] == "seedlink":
        s.setdefault("stations", [])
        for st in s["stations"]:
            st.setdefault("desc", "")
            st.setdefault("selectors", [])
    else:
        g = s.setdefault("gcf", {})
        g.setdefault("net", "XX")
        g.setdefault("sta", "GCF")
        g.setdefault("desc", "")
        g.setdefault("map", {})
    return s


class Config:
    """Thread-safe access to config.json. Changes are saved at once."""

    def __init__(self, path=None):
        self.path = path or paths.config_file()
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as e:
            log.error("config file %s unreadable (%s) - kept as .bad, using defaults", self.path, e)
            try:
                os.replace(self.path, self.path + ".bad")
            except OSError:
                pass
            data = {}
        cfg = _merge(DEFAULTS, data)
        if not cfg["recording"].get("folder"):
            cfg["recording"]["folder"] = paths.recordings_dir()
        cfg["sources"] = [s for s in cfg.get("sources", []) if isinstance(s, dict)]
        return cfg

    def save(self):
        with self.lock:
            atomic_write_json(self.path, self.data)

    # --- generic -----------------------------------------------------------------
    def get(self, section, key=None, default=None):
        with self.lock:
            v = self.data.get(section, default)
            if key is None:
                return copy.deepcopy(v)
            return copy.deepcopy(v.get(key, default) if isinstance(v, dict) else default)

    def set(self, section, key, value, save=True):
        with self.lock:
            self.data.setdefault(section, {})[key] = copy.deepcopy(value)
            if save:
                self.save()

    def update_section(self, section, values, save=True):
        with self.lock:
            self.data.setdefault(section, {}).update(copy.deepcopy(values))
            if save:
                self.save()

    # --- sources -----------------------------------------------------------------
    def sources(self):
        with self.lock:
            return copy.deepcopy(self.data["sources"])

    def source(self, sid):
        with self.lock:
            for s in self.data["sources"]:
                if s.get("id") == sid:
                    return copy.deepcopy(s)
        return None

    def add_source(self, src):
        s = validate_source(src)
        with self.lock:
            self.data["sources"] = [x for x in self.data["sources"] if x.get("id") != s["id"]] + [s]
            self.save()
        return copy.deepcopy(s)

    def update_source(self, src):
        s = validate_source(src)
        with self.lock:
            for i, x in enumerate(self.data["sources"]):
                if x.get("id") == s["id"]:
                    self.data["sources"][i] = s
                    break
            else:
                self.data["sources"].append(s)
            self.save()
        return copy.deepcopy(s)

    def remove_source(self, sid):
        with self.lock:
            before = len(self.data["sources"])
            self.data["sources"] = [x for x in self.data["sources"] if x.get("id") != sid]
            if len(self.data["sources"]) != before:
                self.save()
                return True
        return False

    def find_source(self, host, port):
        with self.lock:
            for s in self.data["sources"]:
                if str(s.get("host")).lower() == str(host).lower() and int(s.get("port", 0)) == int(port):
                    return copy.deepcopy(s)
        return None


class State:
    """Small runtime state (state.json): SeedLink resume sequence numbers, recordings.
    Saved at most every `every` seconds unless flush() is called."""

    def __init__(self, path=None, every=5.0):
        self.path = path or paths.state_file()
        self.every = every
        self.lock = threading.RLock()
        self.dirty = False
        self.saved = 0.0
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        with self.lock:
            return copy.deepcopy(self.data.get(key, default))

    def set(self, key, value):
        with self.lock:
            self.data[key] = copy.deepcopy(value)
            self.dirty = True
        self.maybe_flush()

    def get_seq(self, source_id, station):
        with self.lock:
            return self.data.get("seedlink_seq", {}).get(f"{source_id}|{station}")

    def set_seq(self, source_id, station, seq):
        with self.lock:
            self.data.setdefault("seedlink_seq", {})[f"{source_id}|{station}"] = seq
            self.dirty = True
        self.maybe_flush()

    def drop_source(self, source_id):
        with self.lock:
            seqs = self.data.get("seedlink_seq", {})
            for k in [k for k in seqs if k.startswith(source_id + "|")]:
                del seqs[k]
            self.dirty = True
        self.flush()

    def maybe_flush(self):
        if self.dirty and time.time() - self.saved >= self.every:
            self.flush()

    def flush(self):
        with self.lock:
            if not self.dirty:
                return
            try:
                atomic_write_json(self.path, self.data)
                self.dirty = False
                self.saved = time.time()
            except OSError:
                log.exception("state file write failed")
