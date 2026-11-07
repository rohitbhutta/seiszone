"""FDSN StationXML per station: the instrument responses SeisZone uses to show physical units.

    <folder>/<NET>.<STA>.xml     one file per station (default paths.stationxml_dir())

The files come from the instrument itself (Guralp web interface, read by the probe) or are
added by the user. Only what the live view needs is read, with ElementTree (no ObsPy in the
program): station position and site, and for every channel epoch (startDate / endDate) the
sample rate and the overall InstrumentSensitivity (Value, InputUnits Name), so
counts / sensitivity = ground motion in InputUnits.

StationXML 1.0, 1.1 and 1.2 share the namespace http://www.fdsn.org/xml/station/1; the reader
matches element names without caring about the namespace, so a file written without it (seen
from some older tools) is read too.
"""

import datetime as dt
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET

from .. import paths

log = logging.getLogger("seiszone")

CODE_RE = re.compile(r"^[A-Za-z0-9]{1,8}$")
_FRACTION_RE = re.compile(r"(\.\d{1,6})\d*")


def _local(tag):
    """Element name without its namespace ('{http://www.fdsn.org/xml/station/1}Channel' -> 'Channel')."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, *names):
    """Text of the nested child el/names[0]/names[1]/... or '' when any level is missing."""
    for n in names:
        if el is None:
            return ""
        el = _child(el, n)
    return (el.text or "").strip() if el is not None else ""


def _float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_time(s):
    """StationXML dateTime -> epoch seconds, or None. Accepts 'Z', offsets, no zone (= UTC),
    more than 6 fraction digits and a date without a time."""
    s = (s or "").strip()
    if not s:
        return None
    s = _FRACTION_RE.sub(r"\1", s.replace("Z", "+00:00").replace("z", "+00:00"))
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def _loc(code):
    """Location code as in SEED ids: '' for empty / blank / '--'."""
    code = (code or "").strip()
    return "" if code == "--" else code


def parse(data):
    """Read StationXML bytes / text. Returns {(net, sta): station dict} (see _station), or None
    if `data` is not FDSN StationXML."""
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, TypeError, ValueError):
        return None
    if _local(root.tag) != "FDSNStationXML":
        return None
    out = {}
    for n in _children(root, "Network"):
        for st in _children(n, "Station"):
            key = (n.get("code", "").strip(), st.get("code", "").strip())
            out[key] = _station(key, st, out.get(key))
    return out


def _station(key, st, prev=None):
    """One station: summary fields plus every channel epoch. A station listed twice (several
    station epochs) is merged into one entry."""
    s = prev or {"net": key[0], "sta": key[1], "site": "", "lat": None, "lon": None, "elev": None,
                 "model": "", "serial": "", "epochs": []}
    s["site"] = _text(st, "Site", "Name") or s["site"]
    for k, name in (("lat", "Latitude"), ("lon", "Longitude"), ("elev", "Elevation")):
        v = _float(_text(st, name))
        if v is not None:
            s[k] = v
    s["model"] = _text(st, "Equipment", "Model") or s["model"]
    s["serial"] = _text(st, "Equipment", "SerialNumber") or s["serial"]
    for c in _children(st, "Channel"):
        resp = _child(c, "Response")
        sens = _child(resp, "InstrumentSensitivity") if resp is not None else None
        value = _float(_text(sens, "Value")) if sens is not None else None
        s["epochs"].append({
            "loc": _loc(c.get("locationCode")), "cha": c.get("code", "").strip(),
            "rate": _float(_text(c, "SampleRate")) or 0.0,
            "start": parse_time(c.get("startDate")), "end": parse_time(c.get("endDate")),
            "sensitivity": value, "units": _text(sens, "InputUnits", "Name") if sens is not None else "",
            "has_response": resp is not None,
        })
    return s


def _summary(s):
    return {"net": s["net"], "sta": s["sta"], "site": s["site"], "lat": s["lat"], "lon": s["lon"],
            "elev": s["elev"], "model": s["model"], "serial": s["serial"],
            "channels": [(e["loc"], e["cha"], e["rate"], e["start"], e["end"]) for e in s["epochs"]],
            "responses": sum(1 for e in s["epochs"] if e["has_response"])}


def summarize(data, net=None, sta=None):
    """Summary dict of the first station in `data` (that station only when net / sta are given):
    net, sta, site, lat, lon, elev, model, serial, channels [(loc, cha, rate, start, end)],
    responses (number of channel epochs with a response). None if not StationXML / not there."""
    stations = parse(data)
    for (n, s), st in (stations or {}).items():
        if (net and n != net) or (sta and s != sta):
            continue
        return _summary(st)
    return None


def _pick_epoch(epochs, t):
    """The epoch covering time t; when none does, the newest one (latest start)."""
    for e in epochs:
        if (e["start"] is None or e["start"] <= t) and (e["end"] is None or t < e["end"]):
            return e
    return max(epochs, key=lambda e: e["start"] if e["start"] is not None else float("-inf"))


def _atomic_write(path, data, tries=20):
    """Write bytes so a crash or power cut never leaves a half-written file."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # a virus scanner or the user's editor has it open for a moment
            if i == tries - 1:
                raise
            time.sleep(0.05)


class ResponseStore:
    """StationXML files of all stations, parsed once and cached (re-read when a file changes).
    Thread-safe: the engine saves, the user interface asks for sensitivities."""

    def __init__(self, folder=None):
        self.folder = folder or paths.stationxml_dir()
        self.lock = threading.RLock()
        self._cache = {}   # (net, sta) -> ((mtime_ns, size), station dict or None)

    def path(self, net, sta):
        if not (CODE_RE.match(net or "") and CODE_RE.match(sta or "")):
            raise ValueError(f"'{net}.{sta}' is not a network and station code")
        return os.path.join(self.folder, f"{net}.{sta}.xml")

    def has(self, net, sta):
        try:
            return os.path.isfile(self.path(net, sta))
        except ValueError:
            return False

    def save(self, net, sta, data):
        """Store StationXML for NET.STA (replacing an older file of that station). ValueError if
        `data` is not StationXML or does not describe that station. Returns the file path."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        stations = parse(data)
        if stations is None:
            raise ValueError("the file is not FDSN StationXML")
        if (net, sta) not in stations:
            listed = ", ".join(f"{n}.{s}" for n, s in stations) or "no station"
            raise ValueError(f"the StationXML describes {listed}, not {net}.{sta}")
        path = self.path(net, sta)
        os.makedirs(self.folder, exist_ok=True)
        _atomic_write(path, data)
        with self.lock:
            self._cache.pop((net, sta), None)
        log.info("StationXML of %s.%s saved (%d KB, %d channel epochs)", net, sta, len(data) // 1024,
                 len(stations[(net, sta)]["epochs"]))
        return path

    def _station(self, net, sta):
        """Parsed station from the cache, re-read when the file's time or size changed."""
        try:
            path = self.path(net, sta)
            st = os.stat(path)
        except (ValueError, OSError):
            return None
        key = (st.st_mtime_ns, st.st_size)
        with self.lock:
            hit = self._cache.get((net, sta))
            if hit and hit[0] == key:
                return hit[1]
        try:
            with open(path, "rb") as fh:
                stations = parse(fh.read())
        except OSError as e:
            log.warning("StationXML %s unreadable: %s", path, e)
            return None
        station = (stations or {}).get((net, sta))
        if station is None:
            log.warning("StationXML %s does not describe %s.%s", path, net, sta)
        with self.lock:
            self._cache[(net, sta)] = (key, station)
        return station

    def load(self, net, sta):
        """Summary dict (see summarize) of the stored file, or None."""
        s = self._station(net, sta)
        return _summary(s) if s else None

    def channels(self, net, sta):
        """[(loc, cha, rate, start, end)] of every channel epoch in the stored file."""
        s = self._station(net, sta)
        return [(e["loc"], e["cha"], e["rate"], e["start"], e["end"]) for e in s["epochs"]] if s else []

    def sensitivity(self, sid, t=None):
        """(InstrumentSensitivity value, input units) for 'NET.STA.LOC.CHA' at time t (epoch s,
        default now): the channel epoch covering t, else the newest epoch. None when there is no
        file, no such channel or no usable sensitivity."""
        parts = sid.split(".")
        if len(parts) != 4:
            return None
        net, sta, loc, cha = parts
        s = self._station(net, sta)
        if not s:
            return None
        epochs = [e for e in s["epochs"] if e["loc"] == _loc(loc) and e["cha"] == cha]
        if not epochs:
            return None
        e = _pick_epoch(epochs, time.time() if t is None else t)
        if not e["sensitivity"]:
            return None
        return e["sensitivity"], e["units"]
