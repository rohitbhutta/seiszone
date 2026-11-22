"""PacketStore: the SeedLink server's packets kept on disk for a number of days (like the
"Server Buffer" of the user's earlier viewer), so that

  * a SeedLink client of this PC (another SeisZone, SeisComP, ObsPy ...) that was away for hours
    gets what it missed - also after SeisZone itself restarted (the in-memory packet ring of the
    DataHub only holds the last ~200,000 packets and starts empty);
  * the viewers show data older than their live buffer although nothing is being recorded.

On disk (folder: config seedlink_server.buffer_folder, default %LOCALAPPDATA%\\SeisZone\\ServerBuffer):

    packets/<YYYYMMDD>_<HH>.slp        every packet in arrival order, per UTC hour of arrival:
                                       seq (4 bytes, big-endian) + length (2 bytes) + raw record
    <NET.STA>/<YYYY-MM-DD>/<NET.STA>_<YYYYMMDD>_<HHMM>_<HHMM>.mseed
                                       the same records per station and data hour (the layout and
                                       names of a recording with one file per station), read by
                                       the viewers and Export like a recording folder

Files older than `days` are removed (this is a rolling buffer, not a recording: the user's
recordings are never touched). SeedLink 3 sequence numbers are 24 bit and compared within half
of that range, so the packets kept also stay below MAX_SPAN numbers (about 2.5 days at the
user's ~33 packets per second); older packet files go first.

Threading: append / flush / read_from / seq_at_time run in the engine's asyncio thread; purge
may run in a worker thread (it only removes whole files under the lock); status() from any thread.
"""

import datetime as dt
import logging
import os
import re
import shutil
import struct
import threading
import time

from . import mseed
from .recorder import FILE_RE as STATION_FILE_RE
from .recorder import file_path, name_time

log = logging.getLogger("seiszone")

MASK = 0xFFFFFF
MAX_SPAN = 0x700000          # sequence numbers kept (below the half range 0x800000 SeedLink compares in)
ENTRY = struct.Struct(">IH")  # seq, record length
PACKET_DIR = "packets"
PACKET_RE = re.compile(r"^(\d{8})_(\d{2})\.slp$")
FILE_MINUTES = 60
MIN_FREE = 2 * 1024 ** 3     # stop writing when the disk has less free space than this
FUTURE = 86400               # station files are not written for records dated this far ahead
OPEN_FILES = 64              # station files kept open
INDEX_EVERY = 256            # one (seq, offset) mark per this many packets in a file
UTC = dt.timezone.utc


def hour_name(t):
    return dt.datetime.fromtimestamp(t, UTC).strftime("%Y%m%d_%H") + ".slp"


def hour_of(name):
    m = PACKET_RE.match(name)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H").replace(tzinfo=UTC).timestamp()


class _PacketFile:
    """One hour of packets: name, hour start, first seq, and (seq, offset) marks once scanned."""

    __slots__ = ("path", "hour", "first", "marks", "scanned_to", "last")

    def __init__(self, path, hour, first):
        self.path, self.hour, self.first = path, hour, first
        self.marks = [(first, 0)] if first is not None else []
        self.scanned_to = 0          # bytes covered by the marks
        self.last = first


class PacketStore:
    def __init__(self, folder, days=2.0):
        self.folder = folder
        self.days = float(days)
        self.lock = threading.RLock()
        self.files = []              # _PacketFile, oldest first
        self._out = None             # (hour, file object, _PacketFile) being written
        self._stations = {}          # path -> file object (station files open for appending)
        self._paused = False         # the disk is (almost) full
        self._bytes = None           # cached size of the folder (status)
        self._bytes_at = 0.0
        self.written = 0
        self.closed = False
        os.makedirs(os.path.join(folder, PACKET_DIR), exist_ok=True)
        self._scan()

    # ------------------------------------------------------------------ opening
    def _scan(self):
        pdir = os.path.join(self.folder, PACKET_DIR)
        found = []
        for name in os.listdir(pdir):
            hour = hour_of(name)
            if hour is None:
                continue
            path = os.path.join(pdir, name)
            first = self._first_seq(path)
            if first is None:
                continue
            found.append(_PacketFile(path, hour, first))
        found.sort(key=lambda f: f.hour)
        self.files = found
        if found:   # the newest file: its last packet (the numbers go on from there)
            self._index(found[-1], to_end=True)

    @staticmethod
    def _first_seq(path):
        try:
            with open(path, "rb") as fh:
                head = fh.read(ENTRY.size)
        except OSError:
            return None
        if len(head) < ENTRY.size:
            return None
        return ENTRY.unpack(head)[0] & MASK

    def _index(self, pf, to_end=False, until_seq=None):
        """Extend the (seq, offset) marks of a file by reading its entry headers."""
        try:
            size = os.path.getsize(pf.path)
        except OSError:
            return
        if pf.scanned_to >= size:
            return
        if self._out is not None and self._out[2] is pf:
            self._out[1].flush()
        n = 0
        with open(pf.path, "rb") as fh:
            off = pf.scanned_to
            fh.seek(off)
            while off + ENTRY.size <= size:
                head = fh.read(ENTRY.size)
                if len(head) < ENTRY.size:
                    break
                seq, ln = ENTRY.unpack(head)
                if off + ENTRY.size + ln > size:
                    break                       # a packet cut off by a crash: ignored
                if n % INDEX_EVERY == 0 and (not pf.marks or pf.marks[-1][1] != off):
                    pf.marks.append((seq & MASK, off))
                pf.last = seq & MASK
                n += 1
                off += ENTRY.size + ln
                fh.seek(off)
                if not to_end and until_seq is not None and ((seq - until_seq) & MASK) < 0x800000:
                    break
            pf.scanned_to = off

    # ------------------------------------------------------------------ the numbers held
    def first_seq(self):
        with self.lock:
            return self.files[0].first if self.files else None

    def last_seq(self):
        with self.lock:
            return self.files[-1].last if self.files else None

    def holds(self, seq):
        """True when seq lies between the oldest and newest packet on disk (it may be a number
        that was never given out: reading continues at the next packet)."""
        with self.lock:
            if not self.files:
                return False
            first, last = self.files[0].first, self.files[-1].last
            return ((seq - first) & MASK) <= ((last - first) & MASK)

    def seq_at_time(self, t):
        """First sequence number of the packets that arrived at or after t (minus one hour: a
        PC clock behind the data's time), None when nothing on disk is that old or newer."""
        with self.lock:
            hour = (t // 3600) * 3600 - 3600
            for pf in self.files:
                if pf.hour >= hour:
                    return pf.first
            return None

    # ------------------------------------------------------------------ writing
    def append(self, seq, rec, now=None):
        if self.closed or self._paused:
            return
        now = time.time() if now is None else now
        raw = rec.raw
        if not raw or len(raw) > 0xFFFF:
            return
        try:
            with self.lock:
                hour = (now // 3600) * 3600
                if self._out is None or self._out[0] != hour:
                    self._roll(hour, seq)
                fh, pf = self._out[1], self._out[2]
                fh.write(ENTRY.pack(seq & MASK, len(raw)) + raw)
                pf.last = seq & MASK
                if rec.start < now + FUTURE:
                    path = file_path(self.folder, rec.sid, rec.start, FILE_MINUTES, "station")
                    sf = self._stations.get(path)
                    if sf is None:
                        sf = self._open_station(path)
                    sf.write(raw)
            self.written += 1
        except (OSError, ValueError) as e:
            log.warning("SeedLink server buffer: writing failed (%s) - kept in memory only", e)
            self._paused = True

    def _roll(self, hour, seq):
        if self._out is not None:
            self._out[1].close()
        path = os.path.join(self.folder, PACKET_DIR, hour_name(hour))
        existing = next((f for f in self.files if f.path == path), None)
        fh = open(path, "ab")
        if existing is None:
            existing = _PacketFile(path, hour, seq & MASK)
            self.files.append(existing)
        else:
            self._index(existing, to_end=True)
        self._out = (hour, fh, existing)

    def _open_station(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        while len(self._stations) >= OPEN_FILES:
            old, fh = next(iter(self._stations.items()))
            fh.close()
            del self._stations[old]
        fh = self._stations[path] = open(path, "ab")
        return fh

    def flush(self):
        with self.lock:
            if self._out is not None:
                self._out[1].flush()
            for fh in self._stations.values():
                fh.flush()

    def close(self):
        with self.lock:
            self.closed = True
            if self._out is not None:
                self._out[1].close()
                self._out = None
            for fh in self._stations.values():
                fh.close()
            self._stations.clear()

    # ------------------------------------------------------------------ reading
    def read_from(self, seq, limit, stop_before=None):
        """Up to `limit` packets [(seq, Record)] from the first one numbered seq or later, and the
        number after them. Packets at or after `stop_before` (the oldest one the memory ring
        holds) are left to the ring. ([], None) when the disk holds nothing from seq on."""
        out = []
        with self.lock:
            if self._out is not None:
                self._out[1].flush()
            if not self.files:
                return [], None
            base = self.files[0].first
            rel = (seq - base) & MASK
            if rel >= 0x800000:
                seq, rel = base, 0
            k = 0
            for i, pf in enumerate(self.files):
                if ((pf.first - base) & MASK) <= rel:
                    k = i
            for pf in self.files[k:]:
                if len(out) >= limit:
                    break
                self._index(pf, until_seq=seq)
                off = 0
                for mseq, moff in pf.marks:
                    if ((mseq - base) & MASK) <= rel:
                        off = moff
                try:
                    with open(pf.path, "rb") as fh:
                        fh.seek(off)
                        while len(out) < limit:
                            head = fh.read(ENTRY.size)
                            if len(head) < ENTRY.size:
                                break
                            s, ln = ENTRY.unpack(head)
                            raw = fh.read(ln)
                            if len(raw) < ln:
                                break
                            if ((s - base) & MASK) < rel:
                                continue
                            if stop_before is not None and ((s - base) & MASK) >= ((stop_before - base) & MASK):
                                return out, (out[-1][0] + 1) & MASK if out else None
                            parsed = mseed.parse_record(raw, 0, check=False)
                            if isinstance(parsed, tuple):
                                out.append((s, parsed[0]))
                except OSError as e:
                    log.warning("SeedLink server buffer: reading %s failed (%s)", pf.path, e)
                    continue
        if not out:
            return [], None
        return out, (out[-1][0] + 1) & MASK

    # ------------------------------------------------------------------ housekeeping
    def purge(self, now=None):
        """Remove what is older than `days` (and packet files beyond MAX_SPAN numbers); pause
        writing while the disk is almost full. Returns the number of files removed."""
        now = time.time() if now is None else now
        cutoff = now - self.days * 86400
        removed = 0
        with self.lock:
            keep = []
            last = self.files[-1].last if self.files else None
            for i, pf in enumerate(self.files):
                writing = self._out is not None and self._out[2] is pf
                old = pf.hour + 3600 <= cutoff
                too_many = last is not None and ((last - pf.first) & MASK) > MAX_SPAN
                if not writing and (old or too_many) and i < len(self.files) - 1:
                    try:
                        os.remove(pf.path)
                        removed += 1
                        continue
                    except OSError as e:
                        log.warning("SeedLink server buffer: cannot remove %s (%s)", pf.path, e)
                keep.append(pf)
            self.files = keep
            oldest_packet_hour = self.files[0].hour if self.files else now
        removed += self._purge_stations(min(cutoff, oldest_packet_hour))
        try:
            free = shutil.disk_usage(self.folder).free
        except OSError:
            free = None
        if free is not None:
            if free < MIN_FREE and not self._paused:
                log.warning("SeedLink server buffer: less than %d GB free on the disk - not written "
                            "until there is space again", MIN_FREE // 1024 ** 3)
                self._paused = True
            elif free >= MIN_FREE * 1.5 and self._paused:
                log.info("SeedLink server buffer: disk space is back - writing again")
                self._paused = False
        self._bytes = None
        return removed

    def _purge_stations(self, cutoff):
        removed = 0
        cutoff_day = dt.datetime.fromtimestamp(cutoff, UTC).date()
        try:
            stations = [e for e in os.scandir(self.folder) if e.is_dir() and e.name != PACKET_DIR]
        except OSError:
            return 0
        for st in stations:
            try:
                days = [e for e in os.scandir(st.path) if e.is_dir()]
            except OSError:
                continue
            for day in days:
                try:
                    d = dt.date.fromisoformat(day.name)
                except ValueError:
                    continue
                if d > cutoff_day:
                    continue
                for f in list(os.scandir(day.path)):
                    m = STATION_FILE_RE.match(f.name)
                    if not m or name_time(m) + FILE_MINUTES * 60 > cutoff:
                        continue
                    with self.lock:
                        fh = self._stations.pop(f.path, None)
                        if fh is not None:
                            fh.close()
                    try:
                        os.remove(f.path)
                        removed += 1
                    except OSError:
                        pass
                try:
                    os.rmdir(day.path)          # only when it is empty
                except OSError:
                    pass
            try:
                os.rmdir(st.path)
            except OSError:
                pass
        return removed

    def status(self):
        now = time.time()
        if self._bytes is None or now - self._bytes_at > 60:
            total = 0
            for root, _dirs, names in os.walk(self.folder):
                for n in names:
                    try:
                        total += os.path.getsize(os.path.join(root, n))
                    except OSError:
                        pass
            self._bytes, self._bytes_at = total, now
        with self.lock:
            oldest = self.files[0].hour if self.files else None
            return {"folder": self.folder, "days": self.days, "bytes": self._bytes, "oldest": oldest,
                    "first_seq": self.files[0].first if self.files else None,
                    "last_seq": self.files[-1].last if self.files else None,
                    "paused": self._paused, "written": self.written}
