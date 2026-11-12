"""DataHub - the centre of SeisZone. Every source (SeedLink client, Gecko receiver, GCF link)
hands its miniSEED records to DataHub.add_record(); from there they go to

  * the packet ring        -> SeisZone's own SeedLink server (other programs read from it)
  * listeners              -> the recorder (continuous recordings), anything else registered
  * a per-channel buffer   -> the live waveform views (the last `buffer_minutes` per channel)
  * per-channel statistics -> the channel table (rate, latest sample, latency, gaps, packets)

A record that starts more than FUTURE_LIMIT s ahead of the PC clock and far past the channel's
newest data (GPS glitch, firmware time jump) still goes to the packet ring and the listeners
(so it is recorded), but not into the live buffer and the statistics: one bad time stamp
cannot wipe a channel's live data or pin its latest time in the future.

The packet ring keeps only the raw bytes of each record (the decoded samples are dropped once
they are in the live buffer). Sequence numbers wrap at 24 bits; the engine starts them where
the previous run ended (plus a safety gap), so a number a SeedLink client saved is never
reused for another packet after a restart.

Threading: add_record(), packet waiting and listeners run in the engine's asyncio thread only
(other threads use Engine.call_soon). The read methods (stats, window, ...) are safe from any
thread, e.g. the user interface.
"""

import asyncio
import collections
import logging
import threading
import time

import numpy as np

from . import history

log = logging.getLogger("seiszone")

GAP_TOLERANCE = 0.5     # in samples: a jump bigger than this between records is a gap / overlap
FUTURE_LIMIT = 600      # s ahead of the PC clock before a record's time may be an outlier
FUTURE_LOG_EVERY = 60   # s between warnings about such records


class _Segment:
    """Contiguous samples: arr[off:n] with time of arr[i] = t0 + i / rate. Filled values are never
    changed in place, so views handed out stay valid while new samples are appended."""

    __slots__ = ("t0", "rate", "arr", "n", "off")

    def __init__(self, t0, rate, samples):
        self.t0 = t0
        self.rate = rate
        cap = max(1024, len(samples) * 4)
        self.arr = np.empty(cap, dtype=np.float64)
        self.arr[:len(samples)] = samples
        self.n = len(samples)
        self.off = 0

    @property
    def start(self):
        return self.t0 + self.off / self.rate

    @property
    def end(self):  # time after the last sample
        return self.t0 + self.n / self.rate

    def append(self, samples):
        k = len(samples)
        if self.n + k > len(self.arr):
            live = self.n - self.off
            cap = max(1024, (live + k) * 2)
            new = np.empty(cap, dtype=np.float64)
            new[:live] = self.arr[self.off:self.n]
            self.t0 += self.off / self.rate
            self.arr, self.n, self.off = new, live, 0
        self.arr[self.n:self.n + k] = samples
        self.n += k

    def trim_before(self, t):
        i = int((t - self.t0) * self.rate)
        if i > self.off:
            self.off = min(i, self.n)

    def view(self, t0=None, t1=None):
        i0 = self.off if t0 is None else max(self.off, int(np.floor((t0 - self.t0) * self.rate)))
        i1 = self.n if t1 is None else min(self.n, int(np.ceil((t1 - self.t0) * self.rate)) + 1)
        if i1 <= i0:
            return None
        return self.t0 + i0 / self.rate, self.arr[i0:i1]


class StreamBuffer:
    """The last `seconds` of one channel as contiguous segments (gaps between them)."""

    def __init__(self, rate, seconds):
        self.rate = rate
        self.seconds = seconds
        self.segments = []

    def add(self, start, samples):
        rate = self.rate
        tol = GAP_TOLERANCE / rate
        samples = np.asarray(samples, dtype=np.float64)
        if not len(samples):
            return
        segs = self.segments
        if segs:
            last = segs[-1]
            if abs(start - last.end) <= tol:            # the normal case: continues the newest
                last.append(samples)
                self._trim()
                return
            if start < last.end - tol:                   # overlap / older data (backfill)
                self._insert(start, samples)
                self._trim()
                return
        segs.append(_Segment(start, rate, samples))
        self._trim()

    def _insert(self, start, samples):
        """Out-of-order data: keep only the part not held yet, then merge touching segments."""
        dt_ = 1.0 / self.rate
        tol = GAP_TOLERANCE * dt_
        end = start + len(samples) * dt_
        pieces = [(start, samples)]
        for seg in self.segments:
            out = []
            for s, y in pieces:
                e = s + len(y) * dt_
                if e <= seg.start + tol or s >= seg.end - tol:
                    out.append((s, y))
                    continue
                if s < seg.start - tol:                  # part before this segment
                    k = int(round((seg.start - s) / dt_))
                    out.append((s, y[:k]))
                if e > seg.end + tol:                    # part after it
                    k = int(round((seg.end - s) / dt_))
                    out.append((s + k * dt_, y[k:]))
            pieces = [(s, y) for s, y in out if len(y)]
        if not pieces:
            return
        for s, y in pieces:
            self.segments.append(_Segment(s, self.rate, y))
        self.segments.sort(key=lambda g: g.start)
        merged = [self.segments[0]]
        for seg in self.segments[1:]:
            prev = merged[-1]
            if abs(seg.start - prev.end) <= tol:
                prev.append(seg.arr[seg.off:seg.n])
            else:
                merged.append(seg)
        self.segments = merged
        del end

    def _trim(self):
        if not self.segments:
            return
        # a segment far in the future (bad time stamp) must never evict the real data
        cutoff = min(self.segments[-1].end, time.time() + FUTURE_LIMIT) - self.seconds
        while self.segments and self.segments[0].end <= cutoff:
            self.segments.pop(0)
        if self.segments:
            self.segments[0].trim_before(cutoff)

    def window(self, t0=None, t1=None):
        out = []
        for seg in self.segments:
            if t1 is not None and seg.start > t1:
                break
            if t0 is not None and seg.end < t0:
                continue
            v = seg.view(t0, t1)
            if v is not None:
                out.append((v[0], self.rate, v[1]))
        return out

    @property
    def first(self):
        return self.segments[0].start if self.segments else None

    @property
    def last(self):
        return self.segments[-1].end if self.segments else None


class DataHub:
    def __init__(self, ring_size=200000, buffer_seconds=3600, dedupe=200000):
        self.lock = threading.RLock()
        self.packets = collections.deque(maxlen=ring_size)   # (seq, Record), oldest first
        self.seq = 0                                         # sequence of the newest packet
        self.buffer_seconds = buffer_seconds
        self.buffers = {}                                    # sid -> StreamBuffer
        self.summaries = {}                                  # sid -> history.Summary (the last day)
        self.stats = {}                                      # sid -> dict (see _new_stats)
        self.descriptions = {}                               # "NET.STA" -> site description
        self.listeners = []                                  # fn(rec, source_id)
        self._recent = collections.OrderedDict()
        self._dedupe = dedupe
        self._waiters = set()                                # asyncio.Event per SeedLink client
        self.started = time.time()
        self.duplicates = 0
        self.decode_errors = 0
        self.future_rejected = 0    # records with a time far ahead (kept out of the live data)
        self._future_logged = 0.0

    # ------------------------------------------------------------------ writing (engine thread)
    def add_record(self, rec, source_id=""):
        """Accept one record. Returns False for a duplicate (already received from any source)."""
        if rec.rate <= 0 or rec.nsamp <= 0:
            return False  # no time series data (log / info records)
        key = (rec.sid, round(rec.start, 3), rec.nsamp)
        if key in self._recent:
            self.duplicates += 1
            return False
        try:
            samples = rec.samples
        except Exception:
            self.decode_errors += 1
            return False
        self._recent[key] = True
        if len(self._recent) > self._dedupe:
            self._recent.popitem(last=False)
        now = time.time()
        with self.lock:
            self.seq = (self.seq + 1) & 0xFFFFFF
            self.packets.append((self.seq, rec))
            sid = rec.sid
            st = self.stats.get(sid)
            if (st is not None and rec.start > now + FUTURE_LIMIT
                    and rec.start > st["last_end"] + self.buffer_seconds):
                self._future(st, rec, now)   # recorded and served, but kept out of the live data
            else:
                if st is None or abs(st["rate"] - rec.rate) > 1e-6:
                    st = self.stats[sid] = self._new_stats(rec, source_id)
                    self.buffers[sid] = StreamBuffer(rec.rate, self.buffer_seconds)
                    old = self.summaries.get(sid)
                    if old is None or abs(getattr(old, "rate", rec.rate) - rec.rate) > 1e-6:
                        self.summaries[sid] = history.Summary()
                    self.summaries[sid].rate = rec.rate
                else:
                    tol = GAP_TOLERANCE / rec.rate
                    if rec.start > st["last_end"] + tol:
                        st["gaps"] += 1
                        st["gap_seconds"] += rec.start - st["last_end"]
                        st["last_gap"] = (st["last_end"], rec.start)
                    elif rec.end < st["last_end"] - tol:
                        st["backfilled"] += 1
                st["packets"] += 1
                st["source"] = source_id or st["source"]
                st["first"] = min(st["first"], rec.start)
                st["last_end"] = max(st["last_end"], rec.end)
                st["last_arrival"] = now
                st["latency"] = now - st["last_end"]
                st["last_value"] = float(samples[-1])
                self.buffers[sid].add(rec.start, samples)
                self.summaries[sid].add(rec.start, rec.rate, samples)
            rec._samples = None   # the ring keeps raw bytes only; the live buffer holds a copy
        for ev in list(self._waiters):
            ev.set()
        for fn in list(self.listeners):
            try:
                fn(rec, source_id)
            except Exception:
                log.exception("data listener %r failed", fn)
        return True

    def _future(self, st, rec, now):
        st["future_rejected"] += 1
        self.future_rejected += 1
        if now - self._future_logged >= FUTURE_LOG_EVERY:
            self._future_logged = now
            log.warning("%s: record time %s is %.0f s ahead of the PC clock - recorded, but not "
                        "shown live (bad time stamp?)", rec.sid,
                        time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(rec.start)), rec.start - now)

    @staticmethod
    def _new_stats(rec, source_id):
        return {"sid": rec.sid, "net": rec.net, "sta": rec.sta, "loc": rec.loc, "cha": rec.cha,
                "rate": rec.rate, "source": source_id, "packets": 0, "first": rec.start,
                "last_end": rec.end, "last_arrival": 0.0, "latency": 0.0, "gaps": 0,
                "gap_seconds": 0.0, "last_gap": None, "backfilled": 0, "last_value": 0.0,
                "future_rejected": 0}

    def add_listener(self, fn):
        if fn not in self.listeners:
            self.listeners.append(fn)

    def remove_listener(self, fn):
        if fn in self.listeners:
            self.listeners.remove(fn)

    # ------------------------------------------------------------------ SeedLink server side
    def new_waiter(self):
        """asyncio.Event set whenever a packet arrives (engine thread only)."""
        ev = asyncio.Event()
        self._waiters.add(ev)
        return ev

    def drop_waiter(self, ev):
        self._waiters.discard(ev)

    def first_seq(self):
        with self.lock:
            return self.packets[0][0] if self.packets else None

    def packets_from(self, next_seq, limit=2000):
        """Packets with sequence >= next_seq (24-bit wrapping), oldest first, at most `limit`.
        If next_seq is older than the ring holds, starts at the oldest packet held.
        Returns (list[(seq, Record)], next_seq_after_them)."""
        with self.lock:
            if not self.packets:
                return [], next_seq
            first = self.packets[0][0]
            offset = (next_seq - first) & 0xFFFFFF
            if offset >= 0x800000:          # older than the ring (client fell behind / old resume)
                offset = 0
            if offset >= len(self.packets):  # not arrived yet
                return [], next_seq
            out = [self.packets[i] for i in range(offset, min(len(self.packets), offset + limit))]
        return out, (out[-1][0] + 1) & 0xFFFFFF

    def next_seq(self):
        """Sequence number the next packet will get."""
        with self.lock:
            return (self.seq + 1) & 0xFFFFFF

    def has_seq(self, seq):
        with self.lock:
            if not self.packets:
                return False
            return ((seq - self.packets[0][0]) & 0xFFFFFF) < len(self.packets)

    def snapshot_packets(self):
        with self.lock:
            return list(self.packets)

    # ------------------------------------------------------------------ reading (any thread)
    def stream_ids(self):
        with self.lock:
            return sorted(self.stats)

    def stats_snapshot(self):
        with self.lock:
            return {sid: dict(st) for sid, st in self.stats.items()}

    def window(self, sid, t0=None, t1=None):
        """[(start_epoch, rate, numpy float64 array)] of the buffered samples in [t0, t1].
        The arrays are read-only views: copy before changing them."""
        with self.lock:
            buf = self.buffers.get(sid)
            return buf.window(t0, t1) if buf else []

    def summary(self, sid, t0, t1):
        """Per-second (first bin, lo, hi, sum, count) of the last day for [t0, t1), or None."""
        with self.lock:
            s = self.summaries.get(sid)
            return s.query(t0, t1) if s is not None else None

    def first_time(self, sid):
        """Start of the oldest sample the live buffer holds for sid (None: nothing)."""
        with self.lock:
            buf = self.buffers.get(sid)
            return buf.first if buf else None

    def save_history(self, path):
        """Keep the per-second summaries of the last day for the next start."""
        with self.lock:
            sums = dict(self.summaries)
            rates = {sid: getattr(s, "rate", 0.0) for sid, s in sums.items()}
            return history.save_summaries(path, sums, rates)

    def load_history(self, path):
        """The summaries saved by the last run (merged with anything already received)."""
        loaded = history.load_summaries(path)
        with self.lock:
            for sid, (rate, s) in loaded.items():
                cur = self.summaries.get(sid)
                if cur is None:
                    s.rate = rate
                    self.summaries[sid] = s
                elif abs(getattr(cur, "rate", rate) - rate) < 1e-6:
                    cur.load(s.arrays(-1))
        return len(loaded)

    def latest_time(self):
        with self.lock:
            ends = [st["last_end"] for st in self.stats.values()]
        return max(ends) if ends else None

    def description(self, station):
        return self.descriptions.get(station, "")

    def forget_stream(self, sid):
        with self.lock:
            self.stats.pop(sid, None)
            self.buffers.pop(sid, None)
            self.summaries.pop(sid, None)

    def set_buffer_seconds(self, seconds):
        with self.lock:
            self.buffer_seconds = seconds
            for b in self.buffers.values():
                b.seconds = seconds
