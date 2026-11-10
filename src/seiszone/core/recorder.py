"""Continuous recordings: every record a recording wants is appended, byte for byte, to a
miniSEED file of a fixed, clock-aligned period (15, 30 or 60 minutes, UTC):

    <folder>/<NET.STA>/<YYYY-MM-DD>/<NET.STA.LOC.CHA>__<YYYYMMDDTHHMMSSZ>.mseed   layout "channel"
    <folder>/<NET.STA>/<YYYY-MM-DD>/<NET.STA>__<YYYYMMDDTHHMMSSZ>.mseed          layout "station"

The period is chosen by each record's start time, so records that arrive late (SeedLink
backfill after a VPN outage) go into the older file they belong to. Nothing is decoded or
re-encoded (lossless) and nothing is ever deleted: a recording only starts and stops when the
user says so, survives restarts of SeisZone (State["recordings"]) and only warns on low disk.

A file that already existed when this run first appends to it (SeisZone restarted after a
crash or power cut) is read once: records it already holds are not appended again, so the
SeedLink resume (whose position is saved every few seconds) never duplicates data. Records that
cannot be written (disk full, drive gone) wait in memory up to PENDING_MAX_BYTES and are
retried; any still unwritten when SeisZone closes are reported in the log with their time span,
and the channel's end is set back so the next start logs the hole as a GAP.

Each recording folder has recording_log.txt (UTF-8, local time and UTC): start, every new file,
gaps per channel, streams going silent and coming back, write errors, stop.

Threading: on_record() runs in the engine thread (hub listener); start/stop/sessions/flush are
called from the user interface. One lock protects everything; a small background thread closes
idle files, watches for silent streams and saves the state.
"""

import collections
import datetime as dt
import logging
import os
import re
import shutil
import threading
import time
import uuid

from . import mseed

log = logging.getLogger("seiszone")

FILE_MINUTES = (15, 30, 60)
LAYOUTS = ("channel", "station")
LOG_NAME = "recording_log.txt"
FILE_RE = re.compile(r"^(?P<code>.+?)__(?P<stamp>\d{8}T\d{6})Z\.mseed$")
GAP_SAMPLES = 1.5                  # a jump of more than this many samples is logged as a gap
IDLE_CLOSE = 60.0                  # s: an append handle unused this long is closed
MAX_OPEN = 64                      # open append handles at most (least recently used closed first)
SILENT_AFTER = 300.0               # s without data from a station -> "stream silent" in the log
ERROR_LOG_EVERY = 600.0            # s: a failing recording logs its write error at most this often
PENDING_MAX_BYTES = 64 * 1024 ** 2  # records kept in memory per recording while writing fails
PERSIST_EVERY = 10.0               # s: byte counters / channel ends saved at most this often
DISK_CHECK_EVERY = 60.0
DISK_WARN_LOG_EVERY = 3600.0
DISK_WARN_BYTES = 5 * 1024 ** 3
DISK_WARN_FRACTION = 0.05
TICK = 5.0
UTC = dt.timezone.utc


def period_start(t, minutes):
    """Start (epoch s) of the clock-aligned file period containing t."""
    step = minutes * 60
    return int(t // step) * step


def file_path(folder, sid, t, minutes, layout):
    """Path of the recording file that holds a record of channel `sid` starting at t."""
    p0 = period_start(t, minutes)
    d = dt.datetime.fromtimestamp(p0, UTC)
    station = ".".join(sid.split(".")[:2])
    code = sid if layout == "channel" else station
    return os.path.join(folder, station, d.strftime("%Y-%m-%d"), f"{code}__{d:%Y%m%dT%H%M%S}Z.mseed")


def stamp_time(stamp):
    """Epoch of a file name stamp 'YYYYMMDDTHHMMSS' (the Z is not part of the group)."""
    return dt.datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=UTC).timestamp()


def _both_times(t):
    return f"{mseed.fmt_time(t, local=True)} local / {mseed.fmt_time(t)}"


def _size(n):
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


class _Session:
    """One recording. Persistent fields are saved in State["recordings"]; the rest is runtime."""

    SAVED = ("id", "name", "folder", "sids", "file_minutes", "layout", "started", "stopped", "state",
             "files", "bytes", "last_write", "last_file", "last_end")

    def __init__(self, **kw):
        self.id = kw["id"]
        self.name = kw.get("name") or kw["id"]
        self.folder = kw["folder"]
        self.sids = list(kw["sids"]) if kw.get("sids") is not None else None
        self.file_minutes = int(kw.get("file_minutes", 60))
        self.layout = kw.get("layout", "channel")
        self.started = float(kw.get("started") or time.time())
        self.stopped = kw.get("stopped")
        self.state = kw.get("state", "recording")
        self.files = int(kw.get("files", 0))
        self.bytes = int(kw.get("bytes", 0))
        self.last_write = float(kw.get("last_write", 0.0))
        self.last_file = kw.get("last_file", "")
        self.last_end = dict(kw.get("last_end") or {})    # sid -> end of the newest record written
        self.sid_set = set(self.sids) if self.sids is not None else None
        self.last_error = ""
        self.error_logged = 0.0
        self.pending = collections.deque()                # (path, raw) not written yet (write error)
        self.pending_bytes = 0
        self.dropped = 0                                  # records lost because pending was full
        self.arrival = {}                                 # station -> wall time of the newest record
        self.silent = set()                               # stations reported silent
        self.paths = {}                                   # (sid, period) -> file path

    @property
    def active(self):
        return self.state == "recording"

    def wants(self, sid):
        return self.sid_set is None or sid in self.sid_set

    def path_for(self, rec):
        key = (rec.sid, period_start(rec.start, self.file_minutes))
        path = self.paths.get(key)
        if path is None:
            if len(self.paths) > 2000:
                self.paths.clear()
            path = self.paths[key] = file_path(self.folder, rec.sid, rec.start, self.file_minutes, self.layout)
        return path

    def saved(self):
        return {k: getattr(self, k) for k in self.SAVED}

    def public(self):
        d = self.saved()
        del d["last_end"]
        d["sids"] = list(self.sids) if self.sids is not None else None
        d["last_error"] = self.last_error
        d["pending"] = len(self.pending)
        return d


class RecordingManager:
    """Recordings of hub data into miniSEED period files (plan.md 5.11)."""

    def __init__(self, hub, state, folder_default, background=True):
        self.hub = hub
        self.state = state
        self.folder_default = folder_default
        self.lock = threading.RLock()
        self._sessions = []
        self._handles = {}            # path -> [file object, last use (wall time)]
        self._persisted = 0.0
        self._dirty = False
        self._disk_checked = 0.0
        self._disk_warned = {}        # folder -> wall time of the last warning in its log
        self._unreadable = []         # state entries that are not understood: saved back unchanged
        self._seen_paths = set()      # files this run has appended to (or created)
        self._existing = {}           # path -> record keys already in a file that existed before
        for d in state.get("recordings", []) or []:
            try:
                s = _Session(**d)
            except (KeyError, TypeError, ValueError):
                log.error("recording entry in the state file is unreadable, kept as it is: %r", d)
                self._unreadable.append(d)
                continue
            self._sessions.append(s)
            if s.active:
                now = time.time()
                for station in self._stations_of(s):
                    s.arrival[station] = now
                self._rec_log(s, "SeisZone restarted - recording continues")
                log.info("recording %r continues -> %s", s.name, s.folder)
        hub.add_listener(self.on_record)
        self._stop = threading.Event()
        self._thread = None
        if background:
            self._thread = threading.Thread(target=self._loop, name="seiszone-recorder", daemon=True)
            self._thread.start()

    # ------------------------------------------------------------------ user interface side
    def start(self, name, folder=None, sids=None, file_minutes=60, layout="channel"):
        """Start a recording; returns its id. sids=None records every channel, including channels
        that appear later. ValueError for bad arguments or a folder that cannot be created."""
        file_minutes = int(file_minutes)
        if file_minutes not in FILE_MINUTES:
            raise ValueError("the file length must be 15, 30 or 60 minutes")
        if layout not in LAYOUTS:
            raise ValueError(f"unknown file layout {layout!r}")
        if sids is not None:
            sids = sorted({str(s) for s in sids})
            if not sids:
                raise ValueError("no channels selected")
            bad = [s for s in sids if s.count(".") != 3]
            if bad:
                raise ValueError(f"not a channel id (NET.STA.LOC.CHA): {', '.join(bad)}")
        folder = os.path.abspath(folder or self.folder_default)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            raise ValueError(f"the recording folder cannot be created: {folder} ({e})") from None
        now = time.time()
        rid = dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:4]
        s = _Session(id=rid, name=(name or "").strip() or rid, folder=folder, sids=sids,
                     file_minutes=file_minutes, layout=layout, started=now)
        with self.lock:
            for station in self._stations_of(s):
                s.arrival[station] = now
            self._sessions.append(s)
            chans = "all channels (also channels that appear later)" if sids is None else ", ".join(sids)
            self._rec_log(s, f"RECORDING STARTED  id={rid}")
            self._rec_log(s, f"  channels   : {chans}")
            self._rec_log(s, f"  file length: {file_minutes} min (aligned to the clock, UTC)")
            self._rec_log(s, "  layout     : " + ("one file per channel" if layout == "channel"
                                                   else "one file per station (all its channels)"))
            self._rec_log(s, f"  folder     : {folder}")
            self._persist(force=True)
        log.info("recording %r started: %s, %d min files -> %s", s.name, chans, file_minutes, folder)
        return rid

    def stop(self, rid):
        """Stop a recording (its files stay). Returns False if it was not recording."""
        with self.lock:
            s = self._find(rid)
            if s is None or not s.active:
                return False
            self._retry_pending(s)
            s.state, s.stopped = "stopped", time.time()
            self._close_handles()
            lost = f", {s.dropped} records could not be written" if s.dropped else ""
            if s.pending:
                lost += f", {len(s.pending)} records still unwritten ({s.last_error})"
            self._rec_log(s, f"RECORDING STOPPED  files={s.files}  data={_size(s.bytes)}  "
                             f"({_both_times(s.started)} -> {_both_times(s.stopped)}){lost}")
            self._persist(force=True)
        log.info("recording %r stopped (%d files, %s)", s.name, s.files, _size(s.bytes))
        return True

    def sessions(self):
        with self.lock:
            return [s.public() for s in self._sessions]

    def flush(self):
        """Save the recordings state now (the data itself is written on arrival)."""
        with self.lock:
            for s in self._sessions:
                if s.active and s.pending:
                    self._retry_pending(s)
            self._persist(force=True)

    def close(self):
        """Close all files and save the state. Active recordings stay active: they continue the
        next time SeisZone starts. Records that still cannot be written are reported (program
        log and recording_log.txt) and the channel ends set back, so the next start logs the hole."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.hub.remove_listener(self.on_record)
        with self.lock:
            for s in self._sessions:
                if s.active and s.pending:
                    self._retry_pending(s)
                if s.active and s.pending:
                    self._report_lost(s, list(s.pending), "SeisZone closing")
                    s.pending.clear()
                    s.pending_bytes = 0
            self._close_handles()
            self._persist(force=True)

    def disk_status(self, folder=None):
        """(free bytes, warning text or None) for the drive of `folder`."""
        folder = os.path.abspath(folder or self.folder_default)
        probe = folder
        while not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        try:
            u = shutil.disk_usage(probe)
        except OSError as e:
            return 0, f"The free space of {folder} cannot be read ({e})."
        if u.free < DISK_WARN_BYTES or u.free < u.total * DISK_WARN_FRACTION:
            pct = 100.0 * u.free / u.total if u.total else 0.0
            return u.free, (f"Low disk space for {folder}: {u.free / 1024 ** 3:.1f} GB free of "
                            f"{u.total / 1024 ** 3:.0f} GB ({pct:.1f} %). SeisZone never deletes "
                            f"recordings - free up space or choose another folder.")
        return u.free, None

    # ------------------------------------------------------------------ engine side
    def on_record(self, rec, source_id=""):
        """Hub listener: append the record to every active recording that wants its channel."""
        if rec.rate <= 0 or rec.nsamp <= 0:
            return
        now = time.time()
        with self.lock:
            written = set()  # two recordings into the same folder never append a record twice
            for s in self._sessions:
                if s.active and s.wants(rec.sid):
                    self._take(s, rec, now, written)
            if self._dirty and now - self._persisted >= PERSIST_EVERY:
                self._persist()

    def _take(self, s, rec, now, written):
        sid, station = rec.sid, rec.station
        if station in s.silent:
            s.silent.discard(station)
            gone = now - s.arrival.get(station, now)
            self._rec_log(s, f"{station}: data back after {gone / 60:.1f} min without data")
        s.arrival[station] = now
        path = s.path_for(rec)
        if self._already_in(path, rec):   # resent after a crash: in the file already
            if rec.end > s.last_end.get(sid, rec.end - 1):
                s.last_end[sid] = rec.end
            return
        prev = s.last_end.get(sid)
        if prev is None:
            self._rec_log(s, f"{sid}: first data, {rec.rate:g} sps, from {mseed.fmt_time(rec.start, ms=True)}")
        elif rec.start > prev + GAP_SAMPLES / rec.rate:
            gap = rec.start - prev
            self._rec_log(s, f"GAP {sid}: {gap:.3f} s ({round(gap * rec.rate)} samples) from "
                             f"{mseed.fmt_time(prev, ms=True)} to {mseed.fmt_time(rec.start, ms=True)}")
        if prev is None or rec.end > prev:
            s.last_end[sid] = rec.end
        if path in written:
            return
        written.add(path)
        self._write(s, [(path, rec.raw)], now)

    def _write(self, s, items, now):
        """Append items [(path, raw)] after any still pending ones (oldest first). On a write
        error the session keeps running: the unwritten records wait in memory (bounded) and are
        retried with the next record. O(1) per record however many are waiting."""
        for item in items:
            s.pending.append(item)
            s.pending_bytes += len(item[1])
        try:
            while s.pending:
                path, raw = s.pending[0]
                self._append(s, path, raw, now)
                s.pending.popleft()
                s.pending_bytes -= len(raw)
        except (OSError, ValueError) as e:
            s.last_error = f"{type(e).__name__}: {e}"
            lost = []
            while s.pending_bytes > PENDING_MAX_BYTES and len(s.pending) > 1:
                item = s.pending.popleft()
                s.pending_bytes -= len(item[1])
                s.dropped += 1
                lost.append(item)
            if lost:
                self._report_lost(s, lost, "no memory left for unwritten records", quiet=True)
            if now - s.error_logged >= ERROR_LOG_EVERY:
                s.error_logged = now
                log.error("recording %r: writing failed (%s) - will retry with the next data; "
                          "%d records waiting", s.name, s.last_error, len(s.pending))
                self._rec_log(s, f"WRITE FAILED: {s.last_error} - retrying with the next data")
            return
        if s.last_error:
            log.info("recording %r: writing works again", s.name)
            self._rec_log(s, "writing works again" + (f" ({s.dropped} records were lost while it failed)"
                                                      if s.dropped else ""))
        s.pending_bytes, s.last_error = 0, ""

    def _report_lost(self, s, items, why, quiet=False):
        """Records that will never be written: say which time span is missing per channel and
        set the channel's end back, so the hole is logged as a GAP by the next record (also
        after a restart, as last_end is saved). quiet: only the first loss of a write error
        is logged (records are dropped one by one while the memory stays full)."""
        spans = {}
        for _path, raw in items:
            res = mseed.parse_record(raw, 0, check=False)
            if not isinstance(res, tuple):
                continue
            r = res[0]
            a, b = spans.get(r.sid, (r.start, r.end))
            spans[r.sid] = (min(a, r.start), max(b, r.end))
        for sid, (a, _b) in spans.items():
            if sid in s.last_end:
                s.last_end[sid] = min(s.last_end[sid], a)
        self._dirty = True
        if quiet and s.dropped > len(items):
            return
        text = "; ".join(f"{sid} {mseed.fmt_time(a, ms=True)} to {mseed.fmt_time(b, ms=True)}"
                         for sid, (a, b) in sorted(spans.items()))
        msg = f"{why}: {len(items)} records could not be written and are lost ({s.last_error}): {text}"
        log.error("recording %r: %s", s.name, msg)
        self._rec_log(s, "LOST " + msg)

    def _retry_pending(self, s):
        if s.pending:
            self._write(s, [], time.time())

    def _already_in(self, path, rec):
        """True when `rec` is already in a file that existed before this run appended to it."""
        if path not in self._seen_paths:
            self._seen_paths.add(path)
            if len(self._seen_paths) > 20000:
                self._seen_paths = {path}
            keys = set()
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
                keys = {(r.sid, round(r.start, 3), r.nsamp) for r in mseed.iter_records(data)}
            except OSError:
                pass   # a new file (or unreadable: appending then fails and is reported)
            if keys:
                self._existing[path] = keys
        keys = self._existing.get(path)
        if keys is None:
            return False
        key = (rec.sid, round(rec.start, 3), rec.nsamp)
        if key in keys:
            return True
        keys.add(key)
        return False

    def _append(self, s, path, raw, now):
        h = self._handles.get(path)
        if h is None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            is_new = not os.path.exists(path)
            if len(self._handles) >= MAX_OPEN:   # make room first: the new handle is never closed
                oldest = min(self._handles, key=lambda p: self._handles[p][1])
                self._close_handle(oldest)
            # unbuffered: every write goes to the operating system at once, so a crash of
            # SeisZone loses nothing that was received
            fh = open(path, "ab", buffering=0)
            h = self._handles[path] = [fh, now]
            if is_new:
                s.files += 1
                s.last_file = path
                self._rec_log(s, f"new file {os.path.relpath(path, s.folder)}")
                self._dirty = True
                self._persisted = 0.0   # a new file is saved in the state at once
        fh = h[0]
        size = os.fstat(fh.fileno()).st_size
        try:
            n = fh.write(raw)
            if n != len(raw):
                raise OSError(f"only {n} of {len(raw)} bytes written (disk full?)")
        except OSError:
            self._close_handle(path, truncate_to=size)   # never leave half a record in the file
            raise
        h[1] = now
        s.bytes += len(raw)
        s.last_write = now
        self._dirty = True

    # ------------------------------------------------------------------ housekeeping
    def _loop(self):
        while not self._stop.wait(TICK):
            try:
                self.tick()
            except Exception:
                log.exception("recorder housekeeping failed")

    def tick(self, now=None):
        """Close idle files, report silent streams, check the disk, save the state."""
        now = time.time() if now is None else now
        with self.lock:
            for path in [p for p, h in self._handles.items() if now - h[1] >= IDLE_CLOSE]:
                self._close_handle(path)
            active = [s for s in self._sessions if s.active]
            for s in active:
                for station, t in s.arrival.items():
                    if station not in s.silent and now - t > SILENT_AFTER:
                        s.silent.add(station)
                        self._rec_log(s, f"{station}: stream silent for > {SILENT_AFTER / 60:.0f} min "
                                         f"(last data received {_both_times(t)})")
                if s.pending:
                    self._retry_pending(s)
            if now - self._disk_checked >= DISK_CHECK_EVERY:
                self._disk_checked = now
                for folder in {s.folder for s in active}:
                    _free, warn = self.disk_status(folder)
                    if warn and now - self._disk_warned.get(folder, 0.0) >= DISK_WARN_LOG_EVERY:
                        self._disk_warned[folder] = now
                        log.warning(warn)
                        for s in active:
                            if s.folder == folder:
                                self._rec_log(s, "WARNING " + warn)
            if self._dirty:
                self._persist()

    # ------------------------------------------------------------------ helpers
    def _find(self, rid):
        for s in self._sessions:
            if s.id == rid:
                return s
        return None

    @staticmethod
    def _stations_of(s):
        return sorted({".".join(sid.split(".")[:2]) for sid in s.sids}) if s.sids is not None else []

    def _close_handle(self, path, truncate_to=None):
        h = self._handles.pop(path, None)
        self._existing.pop(path, None)   # the resend after a restart is over by then
        if h is None:
            return
        try:
            if truncate_to is not None:
                h[0].truncate(truncate_to)
        except OSError:
            log.warning("could not remove a partly written record from %s", path)
        try:
            h[0].close()
        except OSError:
            pass

    def _close_handles(self):
        for path in list(self._handles):
            self._close_handle(path)

    def _persist(self, force=False):
        self.state.set("recordings", self._unreadable + [s.saved() for s in self._sessions])
        self._dirty = False
        self._persisted = time.time()
        if force:
            self.state.flush()

    def _rec_log(self, s, text, t=None):
        line = f"{_both_times(t or time.time())}  [{s.name}] {text}\n"
        try:
            with open(os.path.join(s.folder, LOG_NAME), "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass  # the folder is unusable: the write error itself is reported via last_error
