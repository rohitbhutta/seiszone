"""History for the viewers: up to HISTORY_SECONDS (one day) of every channel, so a viewer can
show a whole day, also of times before SeisZone was started.

Two sources, both read-only for the user's data:

* Summary - a per-second summary (minimum, maximum, sum, count) of each channel, kept by the
  DataHub for every record it accepts (live data and backfill alike) for the last day. About
  2 MB per channel, so a day of every channel fits in memory, unlike the samples themselves
  (the hub keeps those only for `buffer_minutes`). The hub saves the summaries when SeisZone
  stops and loads them at the next start (save_summaries / load_summaries), so a day-long view
  survives a restart. A view showing more than about two seconds per pixel draws from these.

* RecordedData - the user's recording folders (the miniSEED files RecordingManager writes).
  They hold every sample, so they give both a full-resolution view of an older time (decoded
  file by file) and, for long windows, per-second summaries per hour of data that is FILTERED
  the way the viewer asks (a summary of raw data cannot be filtered afterwards). Reading and
  decoding run in one background thread (mseed.decode_many: about 0.1 s per hour of 200 sps);
  results are cached and `generation` counts finished jobs, so a viewer knows when to look
  again. Files are never written, moved or deleted here.
"""

import collections
import heapq
import json
import logging
import math
import os
import threading
import time

import numpy as np

from . import dsp, mseed

log = logging.getLogger("seiszone")

BIN = 1.0                       # seconds per summary bin
HISTORY_SECONDS = 24 * 3600     # how far back the viewers can go (1440 minutes)
SLOTS = 90000                   # ring size of a Summary (a little more than a day of bins)
CHUNK = 3600                    # seconds per cached summary of recorded data (UTC hours)
MAX_CHUNKS = 800                # cached hour summaries (about 70 KB each)
MAX_FILES = 24                  # decoded recording files kept for full-resolution views
LISTING_TTL = 20.0              # s a listing of a recording day folder is trusted
RECENT = 2 * CHUNK              # hours this close to now may still grow: their files are checked


# ------------------------------------------------------------------------------------------
# per-second summaries (kept by the hub)
# ------------------------------------------------------------------------------------------
def bin_reduce(start, rate, y, t_lo=-math.inf, t_hi=math.inf):
    """Per-second (bin) minimum, maximum, sum and count of samples y (sample k at start + k /
    rate) with t_lo <= time < t_hi: (bins int64, lo, hi, sum, count) or None (no sample)."""
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    if not n or rate <= 0:
        return None
    if t_lo == -math.inf and t_hi == math.inf:
        quick = _bin_firsts(start, rate, n)
        if quick is not None and np.isfinite(y).all():
            bins, first = quick
            return (bins, np.minimum.reduceat(y, first), np.maximum.reduceat(y, first), np.add.reduceat(y, first),
                    np.diff(np.append(first, n)))
    t = start + np.arange(n) / rate
    keep = (t >= t_lo) & (t < t_hi) & np.isfinite(y)
    if not keep.all():
        t, y = t[keep], y[keep]
        if not len(y):
            return None
    b = np.floor(t / BIN + 1e-9).astype(np.int64)
    first = np.concatenate(([0], np.flatnonzero(np.diff(b)) + 1))
    return (b[first], np.minimum.reduceat(y, first), np.maximum.reduceat(y, first), np.add.reduceat(y, first),
            np.diff(np.append(first, len(y))))


QUICK_BINS = 8   # records spanning at most this many bins take the quick path of bin_reduce


def _bin_of(start, rate, k):
    """The bin of sample k - the same float sum as bin_reduce's `start + np.arange(n) / rate`."""
    return math.floor((start + k / rate) / BIN + 1e-9)


def _bin_firsts(start, rate, n):
    """(bins, first sample of each) for a short record without a time array: the samples where
    the bin changes are found from the bin edges and checked with the very same arithmetic as
    the full path (so the result is identical). A record is a few seconds (200 sps: 1-4 bins),
    and making and flooring a time array for every sample was most of DataHub.add_record. None
    when the record spans more than QUICK_BINS bins or a bin would be empty (low rates)."""
    b0, b1 = _bin_of(start, rate, 0), _bin_of(start, rate, n - 1)
    if b1 - b0 > QUICK_BINS:
        return None
    first = [0]
    for b in range(b0 + 1, b1 + 1):
        k = min(n - 1, max(first[-1] + 1, math.ceil((b * BIN - start) * rate)))
        while k > first[-1] + 1 and _bin_of(start, rate, k - 1) >= b:
            k -= 1
        while k < n and _bin_of(start, rate, k) < b:
            k += 1
        if k >= n or _bin_of(start, rate, k) != b:
            return None                   # no sample in bin b: the full path handles it
        first.append(k)
    return np.arange(b0, b1 + 1, dtype=np.int64), np.asarray(first, dtype=np.intp)


class Summary:
    """Per-second minimum / maximum / sum / count of one channel for the last `slots` seconds.
    Kept in blocks of BLOCK seconds made when data for them arrives (memory follows the data:
    a channel heard for a minute costs one block, a whole day about 2.4 MB); blocks older than
    `slots` seconds before the newest bin are dropped. Out-of-order data (backfill) is merged
    into its bins; a bin older than that is dropped."""

    BLOCK = 3600

    def __init__(self, slots=SLOTS):
        self.slots = slots
        self.rate = 0.0
        self.blocks = {}          # block number -> [lo float32, hi float32, sum float64, cnt int32]
        self.newest = -1

    def _block(self, k):
        b = self.blocks.get(k)
        if b is None:
            n = self.BLOCK
            b = self.blocks[k] = [np.full(n, np.nan, dtype=np.float32), np.full(n, np.nan, dtype=np.float32),
                                  np.zeros(n), np.zeros(n, dtype=np.int32)]
        return b

    def add(self, start, rate, samples):
        r = bin_reduce(start, rate, samples)
        if r is not None:
            self._merge(*r)

    def _merge(self, bins, lo, hi, sm, ct):
        newest = max(self.newest, int(bins[-1]))
        keep = bins > newest - self.slots
        if not keep.all():
            bins, lo, hi, sm, ct = bins[keep], lo[keep], hi[keep], sm[keep], ct[keep]
        if not len(bins):
            return
        self.newest = newest
        k0 = int(bins[0]) // self.BLOCK
        if int(bins[-1]) // self.BLOCK == k0 and (len(bins) == 1 or bool(np.all(np.diff(bins) > 0))):
            # one block, each bin once (a live record): no grouping needed
            b = self._block(k0)
            i = bins - k0 * self.BLOCK
            b[0][i] = np.fmin(b[0][i], lo)
            b[1][i] = np.fmax(b[1][i], hi)
            b[2][i] += sm
            b[3][i] += ct.astype(np.int32)
        else:
            self._merge_blocks(bins, lo, hi, sm, ct)
        oldest = (newest - self.slots) // self.BLOCK
        for k in [k for k in self.blocks if k < oldest]:
            del self.blocks[k]

    def _merge_blocks(self, bins, lo, hi, sm, ct):
        blk = bins // self.BLOCK
        for k in np.unique(blk):
            m = blk == k
            b = self._block(int(k))
            i = bins[m] - int(k) * self.BLOCK
            b[0][i] = np.fmin(b[0][i], lo[m])
            b[1][i] = np.fmax(b[1][i], hi[m])
            b[2][i] += sm[m]
            b[3][i] += ct[m].astype(np.int32)

    def query(self, t0, t1):
        """(first bin, lo, hi, sum, count) for the bins of [t0, t1); empty bins: NaN / 0."""
        b0 = int(math.floor(t0 / BIN))
        b1 = int(math.ceil(t1 / BIN))
        b0 = max(b0, b1 - self.slots)
        n = max(0, b1 - b0)
        lo = np.full(n, np.nan)
        hi = np.full(n, np.nan)
        sm = np.zeros(n)
        ct = np.zeros(n, dtype=np.int64)
        if n:
            for k in range(b0 // self.BLOCK, (b1 - 1) // self.BLOCK + 1):
                b = self.blocks.get(k)
                if b is None:
                    continue
                a, e = max(b0, k * self.BLOCK), min(b1, (k + 1) * self.BLOCK)
                src = slice(a - k * self.BLOCK, e - k * self.BLOCK)
                dst = slice(a - b0, e - b0)
                lo[dst], hi[dst], sm[dst], ct[dst] = b[0][src], b[1][src], b[2][src], b[3][src]
        return b0, lo, hi, sm, ct

    def arrays(self, since):
        """The filled bins newer than `since` (bin number) as plain arrays, for saving."""
        parts = {"idx": [], "lo": [], "hi": [], "sum": [], "cnt": []}
        for k in sorted(self.blocks):
            b = self.blocks[k]
            idx = k * self.BLOCK + np.arange(self.BLOCK, dtype=np.int64)
            ok = (b[3] > 0) & (idx > since)
            parts["idx"].append(idx[ok])
            parts["lo"].append(b[0][ok].astype(np.float64))
            parts["hi"].append(b[1][ok].astype(np.float64))
            parts["sum"].append(b[2][ok])
            parts["cnt"].append(b[3][ok].astype(np.int64))
        empty = {"idx": np.int64, "lo": np.float64, "hi": np.float64, "sum": np.float64, "cnt": np.int64}
        return {k: (np.concatenate(v) if v else np.zeros(0, dtype=empty[k])) for k, v in parts.items()}

    def load(self, a):
        bins = np.asarray(a["idx"], dtype=np.int64)
        if len(bins):
            order = np.argsort(bins)
            self._merge(bins[order], np.asarray(a["lo"], dtype=np.float64)[order],
                        np.asarray(a["hi"], dtype=np.float64)[order], np.asarray(a["sum"], dtype=np.float64)[order],
                        np.asarray(a["cnt"], dtype=np.int64)[order])


def save_summaries(path, summaries, rates, now=None):
    """Write the last day of every summary to `path` (numpy .npz, written atomically)."""
    now = time.time() if now is None else now
    since = int((now - HISTORY_SECONDS) / BIN)
    data, meta = {}, []
    for i, (sid, s) in enumerate(sorted(summaries.items())):
        a = s.arrays(since)
        if not len(a["idx"]):
            continue
        meta.append({"sid": sid, "rate": rates.get(sid, 0.0), "key": f"c{i}"})
        for k, v in a.items():
            data[f"c{i}_{k}"] = v
    data["meta"] = np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp.npz"
    np.savez_compressed(tmp, **data)
    os.replace(tmp, path)
    return len(meta)


def load_summaries(path, now=None):
    """{sid: (rate, Summary)} from a file save_summaries wrote ({} when there is none / it is
    damaged); bins older than a day are left out."""
    now = time.time() if now is None else now
    since = int((now - HISTORY_SECONDS) / BIN)
    try:
        with np.load(path) as z:
            meta = json.loads(bytes(z["meta"]).decode("utf-8"))
            out = {}
            for m in meta:
                a = {k: z[f"{m['key']}_{k}"] for k in ("idx", "lo", "hi", "sum", "cnt")}
                ok = a["idx"] > since
                s = Summary()
                s.load({k: v[ok] for k, v in a.items()})
                out[m["sid"]] = (float(m.get("rate") or 0.0), s)
            return out
    except FileNotFoundError:
        return {}
    except Exception as e:   # a damaged file costs the overview of the last day, nothing more
        log.warning("viewer history %s not read: %s", path, e)
        return {}


# ------------------------------------------------------------------------------------------
# recorded data (the user's recording folders, read only)
# ------------------------------------------------------------------------------------------
def spec_key(spec):
    return json.dumps(dsp.normalize_spec(spec), sort_keys=True) if spec else ""


class RecordedData:
    """Reads the recording folders for the viewers, in a background thread.

    chunk(sid, hour, spec) -> (result, pending): the per-second summary of the recorded data of
        that UTC hour, filtered with spec; result {"lo", "hi", "sum", "cnt"} (arrays of CHUNK)
        or None (nothing recorded); pending True while it is still being read.
    segments(sid, t0, t1) -> (segments, pending): full-resolution [(start, rate, float array)].
    """

    def __init__(self, folders_fn):
        self.folders_fn = folders_fn
        self.lock = threading.Lock()
        self.generation = 0                         # finished jobs (viewers look again when it moves)
        self._chunks = collections.OrderedDict()    # (sid, hour, spec key) -> (signature, result)
        self._files = collections.OrderedDict()     # path -> (signature, [(start, rate, int array)])
        self._listing = {}                          # (folder, station, day) -> (read at, [(t, path)])
        self._jobs = []                             # heap of (priority, n, key, job)
        self._queued = set()
        self._n = 0
        self._wake = threading.Event()
        self._thread = None
        self._stop = False

    # --- public ----------------------------------------------------------------------
    def chunk(self, sid, hour, spec=None):
        key = (sid, int(hour), spec_key(spec))
        sig = self._signature(sid, hour - dsp.pre_roll(spec, 1.0) - 1, hour + CHUNK, recent=hour > time.time() - RECENT)
        with self.lock:
            hit = self._chunks.get(key)
            if hit is not None and (sig is None or hit[0] == sig):
                self._chunks.move_to_end(key)
                return hit[1], False
        if sig == ():                                # no files at all for that hour
            return None, False
        self._queue(key, ("chunk", sid, int(hour), spec), priority=-hour)
        return (hit[1] if hit else None), True

    def segments(self, sid, t0, t1):
        files = self._files_for(sid, t0, t1)
        out, pending = [], False
        for _t, path in files:
            sig = _stat(path)
            with self.lock:
                hit = self._files.get(path)
                if hit is not None and hit[0] == sig:
                    self._files.move_to_end(path)
                    segs = hit[1]
                else:
                    segs = None
            if segs is None:
                pending = True
                self._queue(("file", path), ("file", path), priority=-t1)
                continue
            for start, rate, y, s in segs:
                piece = _clip(start, rate, y, t0, t1) if s == sid else None
                if piece is not None:
                    out.append(piece)
        out.sort(key=lambda s: s[0])
        return _join(_dedupe(out)), pending        # pieces of neighbouring files joined

    def stop(self):
        self._stop = True
        self._wake.set()

    # --- files -----------------------------------------------------------------------
    def _folders(self):
        try:
            return [f for f in dict.fromkeys(self.folders_fn() or []) if f and os.path.isdir(f)]
        except Exception:
            log.exception("recording folders for the viewers")
            return []

    def _files_for(self, sid, t0, t1):
        """Recording files that may hold `sid` in [t0, t1) (a cached listing of the day folders)."""
        from .export import MAX_FILE_SPAN, _station
        from .recorder import FILE_RE, stamp_time
        import datetime as dt
        station = _station(sid)
        out = []
        day = dt.datetime.fromtimestamp(t0 - MAX_FILE_SPAN, dt.timezone.utc).date()
        last = dt.datetime.fromtimestamp(t1, dt.timezone.utc).date()
        now = time.monotonic()
        while day <= last:
            for folder in self._folders():
                key = (folder, station, day.isoformat())
                hit = self._listing.get(key)
                if hit is None or now - hit[0] > LISTING_TTL:
                    entries = []
                    try:
                        for e in os.scandir(os.path.join(folder, station, day.isoformat())):
                            m = FILE_RE.match(e.name)
                            if m:
                                entries.append((stamp_time(m.group("stamp")), m.group("code"), e.path))
                    except OSError:
                        pass
                    hit = self._listing[key] = (now, entries)
                for t, code, path in hit[1]:
                    if code in (sid, station) and t0 - MAX_FILE_SPAN <= t < t1:
                        out.append((t, path))
            day += dt.timedelta(days=1)
        return sorted(set(out))

    def _signature(self, sid, t0, t1, recent):
        """() when no file holds that span; the files with size and time when they may still
        grow (recent); else None (a cached result is good)."""
        files = self._files_for(sid, t0, t1)
        if not files:
            return ()
        if not recent:
            return None
        return tuple((p, _stat(p)) for _t, p in files)

    # --- the background thread ---------------------------------------------------------
    def _queue(self, key, job, priority):
        with self.lock:
            if key in self._queued:
                return
            self._queued.add(key)
            self._n += 1
            heapq.heappush(self._jobs, (priority, self._n, key, job))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="seiszone-history", daemon=True)
                self._thread.start()
        self._wake.set()

    def _run(self):
        while not self._stop:
            with self.lock:
                item = heapq.heappop(self._jobs) if self._jobs else None
            if item is None:
                self._wake.wait(5.0)
                self._wake.clear()
                continue
            _p, _n, key, job = item
            try:
                if job[0] == "chunk":
                    self._do_chunk(key, *job[1:])
                else:
                    self._do_file(job[1])
            except Exception:
                log.exception("reading recorded data for the viewer (%s)", job[:3])
            finally:
                with self.lock:
                    self._queued.discard(key)
                    self.generation += 1

    def _decoded(self, path):
        sig = _stat(path)
        with self.lock:
            hit = self._files.get(path)
            if hit is not None and hit[0] == sig:
                return hit[1]
        try:
            recs = [r for r in mseed.read_file(path) if r.rate > 0 and r.nsamp > 0]
        except OSError as e:
            log.warning("recording %s not read: %s", path, e)
            recs = []
        ys = mseed.decode_many(recs)
        by_sid = collections.defaultdict(list)
        for r, y in zip(recs, ys):
            if y is not None:
                by_sid[r.sid].append((r.start, r.rate, y))
        segs = []
        for sid, pieces in by_sid.items():
            segs.extend((start, rate, y, sid) for start, rate, y in _join(pieces))
        with self.lock:
            self._files[path] = (sig, segs)
            self._files.move_to_end(path)
            while len(self._files) > MAX_FILES:
                self._files.popitem(last=False)
        return segs

    def _do_file(self, path):
        self._decoded(path)

    def _do_chunk(self, key, sid, hour, spec):
        pre = dsp.pre_roll(spec, 1.0) if spec else 0.0
        t_lo, t_hi = hour - pre - 1, hour + CHUNK
        sig = self._signature(sid, t_lo, t_hi, recent=True)
        pieces = []
        for _t, path in self._files_for(sid, t_lo, t_hi):
            for start, rate, y, s in self._decoded(path):
                if s == sid:
                    piece = _clip(start, rate, y, t_lo, t_hi)
                    if piece is not None:
                        pieces.append(piece)
        pieces.sort(key=lambda p: p[0])
        result = None
        if pieces:
            lo = np.full(CHUNK, np.nan)
            hi = np.full(CHUNK, np.nan)
            sm = np.zeros(CHUNK)
            ct = np.zeros(CHUNK, dtype=np.int64)
            for start, rate, y in _join(_dedupe(pieces)):
                y = np.asarray(y, dtype=np.float64)
                if spec:
                    y = dsp.apply_spec(y, rate, spec, demean=False)
                r = bin_reduce(start, rate, y, hour, hour + CHUNK)
                if r is None:
                    continue
                bins, blo, bhi, bsm, bct = r
                i = bins - int(hour / BIN)
                lo[i] = np.fmin(lo[i], blo)
                hi[i] = np.fmax(hi[i], bhi)
                sm[i] += bsm
                ct[i] += bct
            if ct.any():
                result = {"lo": lo.astype(np.float32), "hi": hi.astype(np.float32), "sum": sm, "cnt": ct}
        with self.lock:
            self._chunks[key] = (sig if hour > time.time() - RECENT else None, result)
            self._chunks.move_to_end(key)
            while len(self._chunks) > MAX_CHUNKS:
                self._chunks.popitem(last=False)


def _stat(path):
    try:
        st = os.stat(path)
        return st.st_size, st.st_mtime_ns
    except OSError:
        return None


def _clip(start, rate, y, t0, t1):
    i0 = max(0, int(math.ceil((t0 - start) * rate - 1e-6)))
    i1 = min(len(y), int(math.ceil((t1 - start) * rate - 1e-6)))
    if i1 <= i0:
        return None
    return start + i0 / rate, rate, y[i0:i1]


def _join(pieces):
    """Records of one channel (start, rate, samples) sorted -> contiguous segments."""
    out = []
    for start, rate, y in sorted(pieces, key=lambda p: p[0]):
        if out:
            s0, r0, parts, n = out[-1]
            end = s0 + n / r0
            if r0 == rate and abs(start - end) <= 0.5 / rate:
                parts.append(y)
                out[-1] = (s0, r0, parts, n + len(y))
                continue
            if r0 == rate and start + len(y) / rate <= end + 0.5 / rate:
                continue                              # wholly inside what is held (a duplicate)
        out.append((start, rate, [y], len(y)))
    return [(s, r, np.concatenate(parts) if len(parts) > 1 else parts[0]) for s, r, parts, _n in out]


def _dedupe(segs):
    """Sorted segments with any overlap with an earlier one cut away."""
    out = []
    end = -math.inf
    for start, rate, y in segs:
        if start + len(y) / rate <= end + 0.5 / rate:
            continue
        if start < end - 0.5 / rate:
            k = int(round((end - start) * rate))
            start, y = start + k / rate, y[k:]
        out.append((start, rate, y))
        end = max(end, start + len(y) / rate)
    return out
