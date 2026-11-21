"""Incremental traces for the live waveform view (WaveformView._compute).

A live view used to filter and decimate the WHOLE window each time it moved by a pixel: in a
60-minute window of 200 sps that is 720 000 samples through sosfiltfilt and the min/max
decimation for every channel every ~2.5 s, although only a few seconds of data are new. A
TraceCache keeps, per contiguous piece of a channel (a "run": one segment of the hub's live
buffer), what was computed last time and redoes only the part the new data can change:

* Filtered samples. A zero-phase filter (forwards and backwards) lets an edge of the data change
  the output only within dsp.settle_samples() samples of it (S: the slowest pole has decayed to
  SETTLE_LEVEL, 1e-10). When new samples arrive after the cached ones (ending at E), the output
  before E - S is final and kept; [E - S, new end) is refiltered from E - 2 S (the first S
  samples of that piece only let its own start edge die away and are thrown away). The kept and
  the new samples therefore agree with filtering everything at once to about SETTLE_LEVEL of the
  signal - far below what a pixel or the 24-bit data resolve; tests/test_trace_cache.py checks
  it against one sosfiltfilt over all the data.

* Min/max decimation. The bins are laid on a fixed grid of the run (multiples of `size`
  samples from the run's anchor), not on the window start, so a bin keeps its samples while
  the window moves: bins whose samples did not change are reused, only the first (cut by the
  window start) and the newest ones are made again. Each bin still gives its exact minimum and
  maximum at their own sample times, so no peak is lost (dsp.envelope's rule).

Anything unusual falls back to the full computation, exactly as before: another filter or sample
rate, a piece that starts before the cached samples or ends before them (a paused view moved
back), samples that are not the ones cached (checked on the last ones), NaN in the data, a
filter that settles more slowly than the piece is long.

Memory: a filtered channel keeps one float64 copy of its window (60 min x 200 sps: 5.8 MB).
"""

import json
import math

import numpy as np

from ..core import dsp

GRID_TOL = 1e-3     # samples: a piece whose start is this close to the run's grid is on that grid
CHECK = 4           # raw samples compared at the end of the cached part (the data must be the same)


def aligned_envelope(anchor, rate, i0, y, size):
    """Min/max decimation on a fixed grid: sample k of y has index i0 + k (time anchor + index /
    rate); bin b holds indices [b * size, (b + 1) * size). Returns (first bin, x, y) with two
    points per bin (its minimum and maximum, in time order, at their own sample times); a bin
    cut by the ends of y uses only the samples y has. A bin of NaN only gives NaN."""
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    if n == 0:
        return i0 // size, np.zeros(0), np.zeros(0)
    lead = i0 % size
    b0 = i0 // size
    bins = -(-(lead + n) // size)
    grid = np.full(bins * size, np.nan)
    grid[lead:lead + n] = y
    grid = grid.reshape(bins, size)
    nan = np.isnan(grid)
    if nan.any():
        imin = np.argmin(np.where(nan, np.inf, grid), axis=1)
        imax = np.argmax(np.where(nan, -np.inf, grid), axis=1)
    else:
        imin = np.argmin(grid, axis=1)
        imax = np.argmax(grid, axis=1)
    rows = np.arange(bins)
    vmin, vmax = grid[rows, imin], grid[rows, imax]
    min_first = imin <= imax
    base = (b0 + rows) * size
    xs = np.empty(2 * bins)
    ys = np.empty(2 * bins)
    xs[0::2] = anchor + (base + np.minimum(imin, imax)) / rate
    xs[1::2] = anchor + (base + np.maximum(imin, imax)) / rate
    ys[0::2] = np.where(min_first, vmin, vmax)
    ys[1::2] = np.where(min_first, vmax, vmin)
    return b0, xs, ys


class _Run:
    """What was computed for one contiguous piece of a channel. Sample i lies at
    anchor + i / rate; `arr[k]` is the filtered sample of index base + k (no array: unfiltered)."""

    __slots__ = ("rate", "anchor", "base", "arr", "end", "tail", "env", "origin")

    def __init__(self, rate, anchor):
        self.rate = rate
        self.anchor = anchor
        self.base = 0
        self.arr = None
        self.end = 0            # index after the newest sample seen
        self.tail = None        # the last CHECK raw samples seen (the data must still be them)
        self.env = None         # (size, first bin, xs, ys) of the complete bins computed last time
        self.origin = 0         # index where the last full filtering began (its start edge)

    def index(self, t):
        k = (t - self.anchor) * self.rate
        i = round(k)
        return int(i) if abs(k - i) <= GRID_TOL else None

    def keep(self, i_start, upto, values, at):
        """Filtered samples: keep [i_start, upto) of the cache, then `values` from index `at`."""
        need = at + len(values) - i_start
        if self.arr is None or at + len(values) - self.base > len(self.arr) or i_start - self.base > len(self.arr) // 2:
            new = np.empty(max(1024, 2 * need))
            if self.arr is not None and upto > i_start:
                new[:upto - i_start] = self.arr[i_start - self.base:upto - self.base]
            self.arr, self.base = new, i_start
        self.arr[at - self.base:at - self.base + len(values)] = values

    def view(self, i0, i1):
        return self.arr[i0 - self.base:i1 - self.base]


class TraceCache:
    """The runs of one plotted channel (see the module text). One `begin()` per computation,
    then `piece()` for each hub segment in time order and `envelope()` for its clipped part,
    then `end()` (runs not used this time are dropped)."""

    def __init__(self):
        self.key = None
        self.runs = []
        self._used = []
        self.stats = {"full": 0, "incremental": 0, "env_reused_bins": 0}   # for tests / profiling

    def clear(self):
        self.key = None
        self.runs = []
        self._used = []

    def begin(self, spec):
        key = json.dumps(dsp.normalize_spec(spec), sort_keys=True) if spec else ""
        if key != self.key:
            self.clear()
            self.key = key
        self._used = []

    def end(self):
        self.runs = self._used
        self._used = []

    def _find(self, start, rate):
        """The run this piece continues: same rate, on its sample grid, starting before its end."""
        for run in self.runs:
            if run.rate != rate or any(r is run for r in self._used):
                continue
            i = run.index(start)
            if i is not None and i < run.end:
                return run, i
        return None, None

    def piece(self, start, rate, y, spec):
        """Filtered samples of one hub segment (start, rate, raw samples y): (samples, handle).
        The samples are what dsp.apply_spec(y, rate, spec, demean=False) gives (y itself when
        spec is None) - computed only where new data can change them. `handle` is for
        envelope()."""
        y = np.asarray(y, dtype=np.float64)
        n = len(y)
        run, i_start = self._find(start, rate)
        new_run = run is None
        if new_run:
            run, i_start = _Run(rate, start), 0
        i_end = i_start + n
        same = (not new_run and i_end >= run.end and run.tail is not None
                and run.end - CHECK >= i_start
                and np.array_equal(y[run.end - CHECK - i_start:run.end - i_start], run.tail))
        if spec is None:
            fy = y
            stable = run.end if same else i_start
            if not same:
                run.env = None
        else:
            settle = dsp.settle_samples(spec, rate)
            fresh = None
            if (same and settle is not None and run.arr is not None and i_start >= run.base
                    and run.end - 2 * settle >= i_start):
                if i_end == run.end:                       # nothing new: all of it is final
                    stable = i_end
                else:
                    tail_from = run.end - settle
                    w0 = tail_from - settle
                    raw = y[w0 - i_start:]
                    if np.isfinite(raw).all():
                        part = dsp.apply_spec(raw, rate, spec, demean=False)
                        run.keep(i_start, tail_from, part[settle:], tail_from)
                        self._forget_bins_from(run, tail_from)
                        stable = tail_from
                        self.stats["incremental"] += 1
                    else:
                        fresh = True
            else:
                fresh = True
            if fresh:
                fy = dsp.apply_spec(y, rate, spec, demean=False)
                self.stats["full"] += 1
                if settle is None or not np.isfinite(y).all():
                    return fy, None                        # not kept: computed in full every time
                run.arr, run.base, run.origin = None, i_start, i_start
                run.keep(i_start, i_start, fy, i_start)
                run.env = None
                stable = i_start
            fy = run.view(i_start, i_end)
        run.end = i_end
        run.tail = y[-CHECK:].copy() if n >= CHECK else None
        self._used.append(run)
        return fy, (run, i_start, stable)

    @staticmethod
    def _forget_bins_from(run, index):
        """Drop the cached bins that hold a sample from `index` on (those samples were refiltered)."""
        if run.env is None:
            return
        size, e0, exs, eys = run.env
        keep = index // size - e0
        run.env = (size, e0, exs[:2 * keep], eys[:2 * keep]) if keep > 0 else None

    def envelope(self, handle, i_clip, y, size):
        """Min/max decimation of the clipped samples y (index i_clip of the run onwards, to the
        end of the piece) in bins of `size` samples: (x, y). Bins that hold the same samples
        as last time are reused."""
        run, _i_start, stable = handle
        n = len(y)
        i_end = i_clip + n
        b_first = i_clip // size
        b_done = i_end // size                      # bins before this one are complete
        env = run.env if run.env is not None and run.env[0] == size else None
        ra = rb = None
        if env is not None:
            _size, e0, exs, eys = env
            e1 = e0 + len(exs) // 2
            ra = max(b_first + 1, e0)
            rb = min(e1, stable // size)
            if rb <= ra:
                ra = rb = None
        if ra is None:
            b0, xs, ys = aligned_envelope(run.anchor, run.rate, i_clip, y, size)
        else:
            _b, xa, ya = aligned_envelope(run.anchor, run.rate, i_clip, y[:ra * size - i_clip], size)
            _b, xc, yc = aligned_envelope(run.anchor, run.rate, rb * size, y[rb * size - i_clip:], size)
            k0, k1 = 2 * (ra - e0), 2 * (rb - e0)
            xs = np.concatenate([xa, exs[k0:k1], xc])
            ys = np.concatenate([ya, eys[k0:k1], yc])
            b0 = b_first
            self.stats["env_reused_bins"] += rb - ra
        # keep the complete bins (not the first one when the window start cuts it)
        c0 = b_first + (1 if i_clip % size else 0)
        if b_done > c0:
            k0, k1 = 2 * (c0 - b0), 2 * (b_done - b0)
            run.env = (size, c0, xs[k0:k1].copy(), ys[k0:k1].copy())
        else:
            run.env = None
        return xs, ys


def bin_size(rate, span, width):
    """Samples per decimation bin: one bin (two points) per pixel."""
    return max(1, int(math.ceil(rate * span / max(1.0, width) - 1e-9)))
