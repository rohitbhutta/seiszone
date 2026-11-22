"""SeisZone settings and sources (config.json) and small runtime state (state.json).

config.json (written atomically). A file that is locked for a moment (virus scanner, backup) is
read again for about a second. A damaged file is kept as config.json.<stamp>.bad and defaults
are used; a file that stays unreadable, or a damaged one that could not be kept, is never
overwritten: Config.read_only is then set and save() writes nothing (load_error says why, the
user interface warns). state.json is handled the same way (State.load_error / read_only).

{
  "version": 2,                                  # 2: SeedLink server default 18000 (was 18010)
  "sources": [                                   # instruments / servers SeisZone connects to
    {"id": "4f1c...", "name": "KUMARI", "kind": "seedlink" | "gcf-udp" | "gcf-tcp",
     "host": "10.8.0.182", "port": 18000, "enabled": true, "added": 1762500000.0,
     "stations": [{"net": "WB", "sta": "KUM", "desc": "KUMARI", "selectors": []}],   # seedlink
     "gcf": {"net": "WB", "sta": "KUM", "desc": "KUMARI", "map": {"ACCZ0": ["0J", "HNZ"]},  # gcf-*
             "system_id": "A46D"},   # optional: the digitiser used (map = the selected streams)
     "details": {...what the instrument probe found: hostname, serial, firmware, lat, lon...}}
  ],
  "gecko":   {"enabled": true, "bind": "0.0.0.0", "ports": [58772], "rename": {}},   # Kelunji Gecko links connect IN
  "seedlink_server": {"enabled": true, "bind": "0.0.0.0", "port": 18000, "organization": "SeisZone"},
  "recording": {"folder": "<Documents>\\SeisZone\\Recordings", "file_minutes": 60, "layout": "channel"},
  "buffer_minutes": 60,                          # live data kept in memory per channel
  "time": {"check": true, "servers": []},        # check the PC clock (SNTP); [] = the built-in servers
  "view": {"window_seconds": 300, "filter": "none", "units": "counts", "scale": "auto",
           "channels": [], "demean": true},       # defaults of new viewers; channels: the ticked ones
  "viewers": [{"sids": [...], "title": "", "window": 300, "filter_on": false, "filter": "default",
               "custom": null, "units": "counts", "scale": "auto", "fixed": 1000, "demean": true,
               "gain": 1, "lanes": true, "overlay": false, "background": null, "spec_size": 64,
               "traces": {"WB.KUM.0J.HNZ": {"color": "#ff0000", "scale": 2, "filter": "bp1-10",
                                            "offset": null, "locked": false, "spectrogram": true}},
               "geometry": "<base64>"}],      # the viewer windows open at the last quit
  "filters": [{"name": "Teleseisms", "spec": {"hp": 0.02, "hp_order": 4, "lp": 0.1, "lp_order": 4}}],
  "calibration": {"SS.SMR03..HNZ": {"sensitivity": 407880.0, "units": "M/S**2"}},  # counts per unit
  "tools": [{"name": "My analysis", "program": "C:\\Tools\\analysis.exe", "args": "{files}"}],
                                      # external tools that get a viewer's selection (ui/external_tools.py)
  "ui": {"theme": "dark" | "light", "minimize_to_tray": true, "start_minimized": false,
         "start_with_windows": false, "geometry": "", "state": ""}
}

Selectors are SeedLink selectors ("0JHN?", "HHZ", "00BH?"); [] means every channel.
A seedlink source with "stations": [] uses uni-station mode with its top-level "selectors" list.
"""

import copy
import json
import logging
import os
import shutil
import threading
import time
import uuid

from . import paths

log = logging.getLogger("seiszone")

SOURCE_KINDS = ("seedlink", "gcf-udp", "gcf-tcp")

CONFIG_VERSION = 2
OLD_SERVER_PORT = 18010     # the SeedLink server default of version 1 (the old service's port)

DEFAULTS = {
    "version": CONFIG_VERSION,
    "sources": [],
    "gecko": {"enabled": True, "bind": "0.0.0.0", "ports": [58772], "rename": {}},
    "seedlink_server": {"enabled": True, "bind": "0.0.0.0", "port": 18000, "organization": "SeisZone"},
    "gcf_server": {"enabled": False, "bind": "0.0.0.0", "port": 1567, "max_rate": 1000},
    "recording": {"folder": "", "file_minutes": 60, "layout": "channel"},
    "buffer_minutes": 60,
    "view": {"window_seconds": 300, "filter": "none", "units": "counts", "scale": "auto",
             "channels": [], "demean": True},
    "viewers": [],
    "filters": [],
    "calibration": {},
    "tools": [],
    "ui": {"theme": "dark", "minimize_to_tray": True, "start_minimized": False,
           "start_with_windows": False, "reopen_viewers": False, "geometry": "", "state": ""},
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


READ_TRIES = 20          # reads of a locked file (0.05 s apart), like atomic_write_json


def read_json(path, tries=READ_TRIES):
    """The JSON object in `path`. FileNotFoundError when missing; OSError when it stays
    unreadable (locked) after the retries; ValueError when it is damaged or not an object."""
    for i in range(tries):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            break
        except FileNotFoundError:
            raise
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(0.05)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def keep_bad(path):
    """Keep an unreadable file under a new, unique name (path.<stamp>.bad); returns that name,
    or None when the copy failed (the original must then not be overwritten)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bad, n = f"{path}.{stamp}.bad", 1
    while os.path.exists(bad):
        n += 1
        bad = f"{path}.{stamp}-{n}.bad"
    try:
        shutil.copy2(path, bad)
        return bad
    except OSError:
        return None


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
        s.setdefault("selectors", [])
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
        self.read_only = False     # the file could not be read and is kept as it is: no save()
        self.load_error = ""
        self._warned = False
        self.data = self._load()
        self._migrate()

    def _migrate(self):
        """Version 1 files: the SeedLink server moves from the old default 18010 (also the old Gecko
        SeedLink service's port) to the standard SeedLink port 18000. Saved at once."""
        if self.data.get("version", 1) >= CONFIG_VERSION:
            return
        server = self.data["seedlink_server"]
        if server.get("port") == OLD_SERVER_PORT:
            server["port"] = DEFAULTS["seedlink_server"]["port"]
            log.info("SeedLink server port changed from %d to %d (new default)", OLD_SERVER_PORT, server["port"])
        self.data["version"] = CONFIG_VERSION
        if os.path.exists(self.path):
            self.save()

    def _load(self):
        try:
            data = read_json(self.path)
        except FileNotFoundError:
            data = {}
        except ValueError as e:     # damaged (bad JSON, not an object, not UTF-8)
            bad = keep_bad(self.path)
            if bad:
                log.error("config file %s is damaged (%s) - kept as %s, using defaults", self.path, e, bad)
            else:
                self.read_only = True
                log.error("config file %s is damaged (%s) and could not be copied - using defaults, "
                          "settings are not saved", self.path, e)
            self.load_error = f"{self.path} is damaged ({e})" + (f"; kept as {bad}" if bad else "")
            data = {}
        except OSError as e:        # locked by another program even after the retries
            self.read_only = True
            self.load_error = f"{self.path} could not be read ({e})"
            log.error("config file %s could not be read (%s) - using defaults, settings are not "
                      "saved until SeisZone is started again", self.path, e)
            data = {}
        cfg = _merge(DEFAULTS, data)
        if not cfg["recording"].get("folder"):
            cfg["recording"]["folder"] = paths.recordings_dir()
        cfg["sources"] = [s for s in cfg.get("sources", []) if isinstance(s, dict)]
        return cfg

    def save(self):
        with self.lock:
            if self.read_only:   # never replace a config.json that could not be read
                if not self._warned:
                    self._warned = True
                    log.warning("settings not saved: %s", self.load_error)
                return
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

    def set_value(self, key, value, save=True):
        """A top-level entry that is not a section of settings ("viewers", "filters",
        "calibration")."""
        with self.lock:
            self.data[key] = copy.deepcopy(value)
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
    """Small runtime state (state.json): SeedLink resume positions (sequence number and end
    time of the last record), the server's packet number, recordings.
    Saved at most every `every` seconds unless flush() is called. A state.json that cannot be
    read is copied to .bad before it is ever replaced; while even that copy fails nothing is
    written (read_only), so a short lock never wipes recordings or resume positions."""

    def __init__(self, path=None, every=5.0):
        self.path = path or paths.state_file()
        self.every = every
        self.lock = threading.RLock()
        self.dirty = False
        self.saved = 0.0
        self.load_error = ""
        self.read_only = False
        try:
            self.data = read_json(self.path)
        except FileNotFoundError:
            self.data = {}
        except (OSError, ValueError) as e:
            self.data = {}
            bad = keep_bad(self.path)
            self.read_only = bad is None
            self.load_error = (f"{self.path} could not be read ({e}); "
                               + (f"kept as {bad}" if bad else "it is left unchanged"))
            log.error("state file: %s - recordings in progress and SeedLink resume positions "
                      "were not restored", self.load_error)

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

    def get_seq_time(self, source_id, station):
        """End time (epoch s) of the last record received at that resume position, or None."""
        with self.lock:
            return self.data.get("seedlink_time", {}).get(f"{source_id}|{station}")

    def set_seq(self, source_id, station, seq, end=None):
        with self.lock:
            key = f"{source_id}|{station}"
            self.data.setdefault("seedlink_seq", {})[key] = seq
            if end is not None:
                self.data.setdefault("seedlink_time", {})[key] = end
            self.dirty = True
        self.maybe_flush()

    def drop_source(self, source_id):
        with self.lock:
            for name in ("seedlink_seq", "seedlink_time"):
                d = self.data.get(name, {})
                for k in [k for k in d if k.startswith(source_id + "|")]:
                    del d[k]
            self.dirty = True
        self.flush()

    def maybe_flush(self):
        if self.dirty and time.time() - self.saved >= self.every:
            self.flush()

    def flush(self):
        with self.lock:
            if not self.dirty:
                return
            if self.read_only:   # the unreadable original is not kept yet: try the copy again
                if os.path.exists(self.path) and keep_bad(self.path) is None:
                    return
                self.read_only = False
            try:
                atomic_write_json(self.path, self.data)
                self.dirty = False
                self.saved = time.time()
            except OSError:
                log.exception("state file write failed")
