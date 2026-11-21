"""Incremental live traces (ui/trace_cache.py) and whole-pixel live steps (WaveformView):

* the settle length of every ready-made filter really lets an edge die away to SETTLE_LEVEL;
* filtering only the new data (spliced onto what was kept) gives what one sosfiltfilt over all
  the data gives - for every ready-made filter, the default band-pass and a slow order-8
  high-pass, with records of uneven length arriving as from an instrument;
* the reused decimation bins equal bins made from scratch, and no extreme is lost;
* a viewer computing incrementally draws the same curve as one computing everything again;
* a live view moves only by whole pixels.
"""

import json
import math
import os
import time

import numpy as np
import pytest
from scipy import signal

from seiszone.core import dsp
from seiszone.ui.trace_cache import TraceCache, aligned_envelope, bin_size

RATE = 200.0
SPECS = [dsp.spec_of(k) for k in dsp.FILTERS if k != "none"] + [
    dict(dsp.DEFAULT_SPEC), dsp.make_spec(hp=0.05, hp_order=8), dsp.make_spec(lp=2.0, lp_order=2, gain=6.0)]


def make_data(seconds, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    y = (3000 * np.sin(2 * np.pi * 0.07 * t) + 800 * np.sin(2 * np.pi * 2.3 * t) + 200 * np.sin(2 * np.pi * 17 * t)
         + rng.normal(0, 60, len(t)) + 12000)                       # an offset, as raw counts have
    k = len(y) // 2
    y[k:k + 3000] += 40000 * np.exp(-np.arange(3000) / 500) * np.sin(np.arange(3000) * 0.4)   # a quake
    return np.round(y)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: json.dumps(s, sort_keys=True))
def test_settle_length_lets_an_edge_die_away(spec):
    r = dsp.resolve(spec, RATE)
    sos = dsp.design(*r, RATE)
    k = dsp.settle_samples(spec, RATE)
    assert k is not None and k > 0
    imp = np.zeros(k + 1)
    imp[0] = 1.0
    h = signal.sosfilt(sos, imp)
    assert abs(h[k]) <= 1e3 * dsp.SETTLE_LEVEL * np.max(np.abs(h))    # (a few poles add up: margin)
    assert dsp.settle_samples(None, RATE) == 0
    assert dsp.settle_samples(dsp.make_spec(gain=6.0), RATE) == 0      # a gain alone: sample by sample


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: json.dumps(s, sort_keys=True))
def test_incremental_filter_equals_one_filter_over_all_data(spec):
    data = make_data(40 * 60)
    window, pre = 10 * 60.0, dsp.pre_roll(spec, RATE)
    cache = TraceCache()
    rng = np.random.default_rng(5)
    end = int(12 * 60 * RATE)
    first = None
    worst = 0.0
    incremental = 0
    while end < len(data):
        end = min(len(data), end + int(rng.integers(60, 700)))          # one record (0.3-3.5 s)
        i0 = max(0, end - int((window + pre) * RATE))
        first = i0 if first is None else first
        cache.begin(spec)
        fy, handle = cache.piece(1.7e9 + i0 / RATE, RATE, data[i0:end], spec)
        cache.end()
        assert handle is not None and len(fy) == end - i0
        # the reference: one zero-phase filter over everything since the cache filtered from
        # scratch (a filter settling more slowly than the window: the window itself, as before)
        if cache.stats["incremental"] == 0:
            first = i0
        ref = dsp.apply_spec(data[first:end], RATE, spec, demean=False)[i0 - first:]
        scale = np.max(np.abs(ref - np.mean(ref))) + 1.0
        visible = int(pre * RATE)
        worst = max(worst, float(np.max(np.abs(fy[visible:] - ref[visible:]))) / scale)
    incremental = cache.stats["incremental"]
    if 2 * dsp.settle_samples(spec, RATE) < (window + pre) * RATE:
        assert incremental > 100 and cache.stats["full"] == 1
    else:   # settles more slowly than the window is long: filtered in full every time (as before)
        assert incremental == 0
    assert worst < 1e-7, worst


def test_spec_change_and_other_data_start_again():
    data = make_data(15 * 60)
    spec = dsp.spec_of("bp1-10")
    cache = TraceCache()
    for end in (int(600 * RATE), int(601 * RATE)):
        cache.begin(spec)
        cache.piece(1.7e9, RATE, data[:end], spec)
        cache.end()
    assert cache.stats == {"full": 1, "incremental": 1, "env_reused_bins": 0}
    cache.begin(dsp.spec_of("hp1"))                       # another filter: computed in full
    cache.piece(1.7e9, RATE, data[:int(602 * RATE)], dsp.spec_of("hp1"))
    cache.end()
    assert cache.stats["full"] == 2
    other = data.copy()
    other[int(500 * RATE)] += 1                            # not the samples kept: in full again
    other[int(602 * RATE) - 1] += 1
    cache.begin(dsp.spec_of("hp1"))
    fy, _h = cache.piece(1.7e9, RATE, other[:int(603 * RATE)], dsp.spec_of("hp1"))
    cache.end()
    assert cache.stats["full"] == 3
    assert np.allclose(fy, dsp.apply_spec(other[:int(603 * RATE)], RATE, dsp.spec_of("hp1"), demean=False))
    with_nan = data[:int(604 * RATE)].copy()               # NaN: in full, as before, and not kept
    with_nan[-10] = np.nan
    cache.begin(dsp.spec_of("hp1"))
    fy, h = cache.piece(1.7e9, RATE, with_nan, dsp.spec_of("hp1"))
    cache.end()
    assert h is None and np.isnan(fy[-10]) and cache.runs == []


def test_aligned_envelope_keeps_every_extreme():
    y = make_data(120)
    for i0 in (0, 7, 1234567):
        b0, x, e = aligned_envelope(100.0, RATE, i0, y, 37)
        assert b0 == i0 // 37
        assert np.nanmax(e) == y.max() and np.nanmin(e) == y.min()
        assert np.all(np.diff(x) >= 0)
        assert x[0] >= 100.0 + i0 / RATE - 1e-9 and x[-1] <= 100.0 + (i0 + len(y) - 1) / RATE + 1e-9
        idx = np.round((x - 100.0) * RATE).astype(np.int64) - i0
        assert np.array_equal(y[idx], e)                    # every point is a real sample


@pytest.mark.parametrize("spec", [None, dsp.spec_of("bp1-10")], ids=["raw", "bp1-10"])
def test_reused_bins_equal_bins_made_from_scratch(spec):
    data = make_data(30 * 60)
    cache = TraceCache()
    rng = np.random.default_rng(9)
    end = int(11 * 60 * RATE)
    window, width = 600.0, 1450.0
    size = bin_size(RATE, window, width)
    while end < len(data) - 2000:
        end += int(rng.integers(60, 700))
        i_start = end - int((window + 60) * RATE)
        i_clip = end - int(window * RATE)
        cache.begin(spec)
        fy, h = cache.piece(1.7e9 + i_start / RATE, RATE, data[i_start:end], spec)
        cache.end()
        y = fy[i_clip - i_start:]
        x, e = cache.envelope(h, h[1] + (i_clip - i_start), y, size)   # (h[1]: i_start in the run)
        run = h[0]
        _b, x2, e2 = aligned_envelope(run.anchor, RATE, h[1] + (i_clip - i_start), y, size)
        assert np.array_equal(x, x2)
        assert np.allclose(e, e2, rtol=0, atol=1e-6 * (np.max(np.abs(y)) + 1))
        assert np.max(e) == pytest.approx(np.max(y)) and np.min(e) == pytest.approx(np.min(y))
    assert cache.stats["env_reused_bins"] > 1000


# ------------------------------------------------------------------------------------------
# in the viewer
# ------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from seiszone.ui import theme
    if os.path.isdir(r"C:\Windows\Fonts"):
        os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    app = QApplication.instance() or QApplication([])
    theme.apply(app, "dark")
    app.setFont(QFont("Segoe UI", 9))
    return app


def _engine(tmp_path, sids, seconds, now):
    from seiszone.config import Config
    from seiszone.core.hub import DataHub, StreamBuffer

    class Eng:
        pass
    eng = Eng()
    eng.config = Config(str(tmp_path / "config.json"))
    eng.responses = None
    eng.history = None
    eng.hub = DataHub(buffer_seconds=3600)
    data = {}
    for k, sid in enumerate(sids):
        y = make_data(seconds + 120, seed=k + 3)
        data[sid] = y
        net, sta, loc, cha = sid.split(".")
        eng.hub.buffers[sid] = StreamBuffer(RATE, 3600)
        n = int(seconds * RATE)
        eng.hub.buffers[sid].add(now - seconds, y[:n])
        eng.hub.stats[sid] = {"sid": sid, "net": net, "sta": sta, "loc": loc, "cha": cha, "rate": RATE,
                              "source": "", "packets": 1, "first": now - seconds, "last_end": now,
                              "last_arrival": now, "latency": 0.0, "gaps": 0, "gap_seconds": 0.0,
                              "last_gap": None, "backfilled": 0, "last_value": 0.0}
    return eng, data


def _feed(eng, data, seconds, t0, upto):
    for sid, y in data.items():
        st = eng.hub.stats[sid]
        a = int(round((st["last_end"] - t0) * RATE))
        b = int(round((upto - t0) * RATE))
        if b > a:
            eng.hub.buffers[sid].add(st["last_end"], y[a:b])
            st["last_end"] = t0 + b / RATE


@pytest.mark.parametrize("filt", [None, "bp1-10", "hp0.1"])
def test_viewer_draws_the_same_curve_incrementally(qapp, tmp_path, filt):
    """A view moved step by step (as live) with the cache draws, at every step, the curve made
    from scratch: one zero-phase filter over all the data since the cache began, clipped to the
    window, mean removed, decimated on the run's grid - the same points (x exactly, y to 1e-6)."""
    from seiszone.ui.waveform_view import WaveformView
    sids = ["WB.KUM.0J.HNZ", "WB.KUM.0J.HNN"]
    now = time.time()
    seconds = 1800.0
    eng, data = _engine(tmp_path, sids, seconds, now)
    t_data0 = now - seconds
    v = WaveformView(eng, eng.config, state={"filter_on": bool(filt), "filter": filt or "default"})
    v.resize(1500, 600)
    v.show()
    qapp.processEvents()
    v.set_channels(sids)
    qapp.processEvents()
    window = 600.0
    rng = np.random.default_rng(2)
    end = now
    reused = 0
    for step in range(25):
        end += float(rng.uniform(0.3, 4.0))
        _feed(eng, data, seconds, t_data0, end)
        v.show_range(end - window, end)          # what a live view does at each pixel step
        t0, t1 = v.visible_range()
        spec = v.trace_spec(sids[0])
        size = bin_size(RATE, t1 - t0, v._plot_width())
        for tr in v._traces:
            x, y = tr.curve.getData()
            run = tr.cache.runs[0]
            ci = int(math.ceil((t0 - run.anchor) * RATE - 1e-6))     # first sample shown (run index)
            origin = run.origin if spec else ci        # where the cache last filtered from scratch
            [(start, _r, raw)] = eng.hub.window(tr.sid, run.anchor + (origin - 0.5) / RATE, t1)
            k = run.index(start)
            assert k is not None and k <= origin
            raw = raw[origin - k:]
            ref = dsp.apply_spec(raw, RATE, spec, demean=False) if spec else np.asarray(raw, dtype=float)
            vis = ref[ci - origin:]
            _b, xr, yr = aligned_envelope(run.anchor, RATE, ci, vis - np.mean(vis), size)
            assert np.array_equal(x, xr)
            assert np.allclose(y, yr, rtol=0, atol=1e-6 * (np.max(np.abs(yr)) + 1.0))
            reused += tr.cache.stats["incremental"] + tr.cache.stats["env_reused_bins"]
    assert reused > 0
    v.close()
    v.deleteLater()


def test_live_view_moves_by_whole_pixels(qapp, tmp_path, monkeypatch):
    from seiszone.ui import waveform_view as wv

    class Clock:
        def __init__(self, t):
            self.t = t
            self.perf_counter, self.monotonic = time.perf_counter, time.monotonic
            self.strftime, self.gmtime = time.strftime, time.gmtime

        def time(self):
            return self.t

    now = time.time()
    eng, _data = _engine(tmp_path, ["WB.KUM.0J.HNZ"], 3700.0, now)
    v = wv.WaveformView(eng, eng.config)
    v.resize(1500, 400)
    v.show()
    qapp.processEvents()
    v.set_channels(["WB.KUM.0J.HNZ"])
    qapp.processEvents()
    v.set_window(3600)
    clock = Clock(now)
    monkeypatch.setattr(wv, "time", clock)
    v._set_range(*v._live_range())                         # on the live edge of the fake clock
    moves = []
    v.rangeChanged.connect(lambda a, b: moves.append((a, b)))
    px = (v.visible_range()[1] - v.visible_range()[0]) / v._plot_width()
    assert px > 1.0                                        # an hour: seconds per pixel
    start = v.visible_range()
    steps = int(px / 0.25)
    if steps * 0.25 >= px:
        steps -= 1
    for _ in range(steps):                                 # 250 ms refreshes, less than a pixel
        clock.t += 0.25
        v.refresh()
    assert moves == [] and v.visible_range() == start
    clock.t += 0.25
    v.refresh()
    assert len(moves) == 1
    assert v.visible_range()[1] - start[1] >= px - 1e-6
    # a short window still moves at every refresh
    v.set_window(60)
    moves.clear()
    for _ in range(4):
        clock.t += 0.25
        v.refresh()
    assert len(moves) == 4
    v.close()
    v.deleteLater()
    assert math.isfinite(px)
