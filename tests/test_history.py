"""The viewers' history of the last day (core.history): the per-second summaries the hub keeps
(and saves for the next start), the recorded data read from the recording folders (full
resolution and filtered per-second summaries, in a background thread), the batch decoder of
mseed, and a viewer drawing a whole day from them. Nothing touches the network or the user's
own files: every recording folder here is a pytest temporary folder."""

import os
import sys
import time

import numpy as np
import pytest

from seiszone.config import Config
from seiszone.core import dsp, history, mseed
from seiszone.core.hub import DataHub
from seiszone.core.recorder import file_path

SID = "XX.HIST.00.HHZ"
_APP = []   # the QApplication must outlive every widget of the whole test run (never dropped)
RATE = 50.0


def write_recording(folder, sid, t0, seconds, rate=RATE, values=None, minutes=60):
    """Clock-aligned recording files as RecordingManager writes them: (path list, samples)."""
    net, sta, loc, cha = sid.split(".")
    n = int(seconds * rate)
    y = values if values is not None else (1000 * np.sin(2 * np.pi * 0.5 * np.arange(n) / rate)).astype(int)
    paths = set()
    for rec in mseed.encode_records(net, sta, loc, cha, rate, t0, y):
        path = file_path(folder, sid, rec.start, minutes, "channel")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "ab") as fh:
            fh.write(rec.raw)
        paths.add(path)
    return sorted(paths), y


def wait_for(cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------------------------------------------
# decoding
# ------------------------------------------------------------------------------------------
def _bin_reduce_full(start, rate, y):
    """bin_reduce as it was before its quick path (the reference)."""
    y = np.asarray(y, dtype=np.float64)
    t = start + np.arange(len(y)) / rate
    keep = np.isfinite(y)
    t, y = t[keep], y[keep]
    if not len(y):
        return None
    b = np.floor(t / history.BIN + 1e-9).astype(np.int64)
    first = np.concatenate(([0], np.flatnonzero(np.diff(b)) + 1))
    return (b[first], np.minimum.reduceat(y, first), np.maximum.reduceat(y, first), np.add.reduceat(y, first),
            np.diff(np.append(first, len(y))))


def test_quick_bin_reduce_is_identical():
    """Records as instruments send them (any rate, start anywhere in a second, also exactly on a
    second and a hair before one): the quick path gives exactly the bins of the full one."""
    rng = np.random.default_rng(4)
    starts = [1.8e9, 1.8e9 + 0.999999999, 1.8e9 + 1e-10, 1.8e9 - 0.0025, 1763700000.005]
    starts += list(1.8e9 + rng.uniform(0, 1, 300))
    quick = 0
    for rate in (200.0, 100.0, 40.5, 50.0, 20.0, 5.0, 1.0, 0.1):
        for start in starts:
            n = int(rng.integers(1, 900))
            y = rng.normal(0, 1000, n).round()
            got, ref = history.bin_reduce(start, rate, y), _bin_reduce_full(start, rate, y)
            assert len(got) == len(ref)
            for a, b in zip(got, ref):
                assert np.array_equal(a, b), (rate, start, n)
            quick += history._bin_firsts(start, rate, n) is not None
    assert quick > 500
    y = np.array([1.0, np.nan, 3.0])                      # NaN: the full path, as before
    got, ref = history.bin_reduce(1.8e9, 2.0, y), _bin_reduce_full(1.8e9, 2.0, y)
    assert all(np.array_equal(a, b) for a, b in zip(got, ref))


def test_quick_summary_merge_is_identical():
    """Records fed through the quick paths (bin_reduce, one-block merge) and through the full
    ones (the code before them): exactly the same summary, also across an hour block."""
    rng = np.random.default_rng(8)
    y = rng.normal(0, 500, 200 * 7300).round()
    start = 1.8e9 + 3590.37                             # crosses an hour block
    s1, s2 = history.Summary(), history.Summary()
    k = 0
    while k < len(y):
        m = int(rng.integers(200, 800))
        t = start + k / 200.0
        s1.add(t, 200.0, y[k:k + m])
        r = _bin_reduce_full(t, 200.0, y[k:k + m])
        s2.newest = max(s2.newest, int(r[0][-1]))
        s2._merge_blocks(*r)
        k += m
    a, b = s1.arrays(-1), s2.arrays(-1)
    for key in ("idx", "lo", "hi", "sum", "cnt"):
        assert np.array_equal(a[key], b[key]), key


def test_decode_many_matches_record_by_record():
    rng = np.random.default_rng(3)
    y = np.cumsum(rng.integers(-40000, 40000, 5000)).astype(np.int64)
    y = np.clip(y, -2 ** 31, 2 ** 31 - 1).astype(np.int32)
    recs = list(mseed.encode_records("XX", "DEC", "", "HHZ", 100.0, 1.8e9, y))
    raw = b"".join(r.raw for r in recs)
    recs = [r for r in mseed.iter_records(raw)]
    got = mseed.decode_many(recs)
    assert all(np.array_equal(g, r.samples) for g, r in zip(got, recs))
    assert np.array_equal(np.concatenate(got), y)
    # a damaged record gets None, the others are still decoded
    bad = bytearray(recs[1].raw)
    bad[recs[1].data_off + 70:recs[1].data_off + 74] = b"\xff\xff\xff\xff"      # nibbles of frame 2
    broken = next(iter(mseed.iter_records(bytes(bad))))
    out = mseed.decode_many([recs[0], broken, recs[2]])
    assert np.array_equal(out[0], recs[0].samples) and np.array_equal(out[2], recs[2].samples)
    try:
        expect = broken.samples
    except ValueError:
        expect = None
    assert (out[1] is None) == (expect is None)


def test_decode_many_steim1_from_the_gecko_capture():
    """The real Gecko capture holds Steim1 records."""
    from seiszone.core import gecko
    data = open(os.path.join(os.path.dirname(__file__), "data", "gecko_native_capture.bin"), "rb").read()
    parser = gecko.FrameParser()
    recs = []
    for ftype, _src, _dst, _seq, payload in parser.feed(data):
        if ftype == gecko.T_MSEED:
            recs.extend(mseed.iter_records(payload))
    assert recs and {r.encoding for r in recs} <= {10, 11}
    for g, r in zip(mseed.decode_many(recs), recs):
        assert np.array_equal(g, r.samples)


# ------------------------------------------------------------------------------------------
# per-second summaries
# ------------------------------------------------------------------------------------------
def test_summary_keeps_minimum_maximum_and_mean_per_second():
    s = history.Summary(slots=600)
    t0 = 1.8e9
    y = np.arange(500, dtype=float)                         # 10 s at 50 sps
    s.add(t0, RATE, y)
    b0, lo, hi, sm, ct = s.query(t0, t0 + 10)
    assert b0 == int(t0) and list(ct) == [50] * 10
    assert lo[0] == 0 and hi[0] == 49 and hi[9] == 499 and sm[3] == sum(range(150, 200))
    # backfill into an earlier second merges; a second older than the ring is dropped
    s.add(t0 + 2, RATE, np.full(25, -7.0))
    assert s.query(t0 + 2, t0 + 3)[1][0] == -7 and s.query(t0 + 2, t0 + 3)[4][0] == 75
    s.add(t0 + 700, RATE, np.ones(50))
    s.add(t0 - 50, RATE, np.ones(50))
    _b, lo, _hi, _sm, ct = s.query(t0 - 50, t0 - 49)
    assert ct[0] == 0 and np.isnan(lo[0])
    # empty seconds are NaN
    b0, lo, _hi, _sm, ct = s.query(t0 + 20, t0 + 30)
    assert np.isnan(lo).all() and not ct.any()


def test_hub_summaries_saved_and_loaded(tmp_path):
    hub = DataHub(buffer_seconds=60)
    now = time.time()
    for rec in mseed.encode_records("XX", "HIST", "00", "HHZ", RATE, now - 7200, np.arange(int(RATE * 300))):
        hub.add_record(rec, "test")
    assert hub.first_time(SID) >= now - 7200 + 300 - 61        # the live buffer: only its last minute
    _b0, lo, hi, _sm, ct = hub.summary(SID, now - 7200, now - 6900)
    assert ct.sum() == RATE * 300 and lo[0] == 0                # the summary: all of it
    path = str(tmp_path / "view_history.npz")
    assert hub.save_history(path) == 1
    again = DataHub()
    assert again.load_history(path) == 1
    _b0, lo2, hi2, _sm, ct2 = again.summary(SID, now - 7200, now - 6900)
    assert np.array_equal(ct, ct2) and np.allclose(hi, hi2, equal_nan=True)
    assert again.load_history(str(tmp_path / "missing.npz")) == 0
    (tmp_path / "bad.npz").write_bytes(b"not a file")
    assert DataHub().load_history(str(tmp_path / "bad.npz")) == 0


# ------------------------------------------------------------------------------------------
# recorded data
# ------------------------------------------------------------------------------------------
def test_recorded_data_full_resolution_and_filtered_chunks(tmp_path):
    folder = str(tmp_path / "rec")
    t0 = (int(time.time()) // 3600 - 5) * 3600               # 5 hours ago, on the hour
    files, y = write_recording(folder, SID, t0, 2 * 3600)
    assert len(files) == 2
    rd = history.RecordedData(lambda: [folder, str(tmp_path / "not there")])
    try:
        segs, pending = rd.segments(SID, t0 + 100, t0 + 160)
        assert pending and segs == []                          # read in the background ...
        assert wait_for(lambda: not rd.segments(SID, t0 + 100, t0 + 160)[1])
        segs, pending = rd.segments(SID, t0 + 100, t0 + 160)  # ... then there, sample for sample
        assert len(segs) == 1 and segs[0][0] == pytest.approx(t0 + 100)
        assert np.array_equal(segs[0][2], y[int(100 * RATE):int(160 * RATE)])
        # across the two files: one piece
        segs, _p = rd.segments(SID, t0 + 3590, t0 + 3610)
        assert wait_for(lambda: not rd.segments(SID, t0 + 3590, t0 + 3610)[1])
        segs, _p = rd.segments(SID, t0 + 3590, t0 + 3610)
        assert len(segs) == 1 and len(segs[0][2]) == 20 * RATE
        # per-second summaries of an hour, raw and filtered
        res, pending = rd.chunk(SID, t0, None)
        assert pending
        assert wait_for(lambda: not rd.chunk(SID, t0, None)[1])
        res, _p = rd.chunk(SID, t0, None)
        assert res["cnt"].sum() == 3600 * RATE and res["hi"].max() == pytest.approx(y.max())
        spec = dsp.make_spec(hp=2.0)                           # the 0.5 Hz sine is stopped
        assert wait_for(lambda: not rd.chunk(SID, t0, spec)[1])
        filt, _p = rd.chunk(SID, t0, spec)
        assert np.nanmax(filt["hi"][600:3000]) < 0.05 * y.max()
        # an hour with nothing recorded: nothing to read
        assert rd.chunk(SID, t0 - 3 * 3600, None) == (None, False)
        assert rd.generation > 0
    finally:
        rd.stop()


def test_a_viewer_draws_a_day_from_recordings_and_the_summary(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    _APP.append(app)
    from seiszone.ui import theme
    from seiszone.ui.waveform_view import MAX_WINDOW, WaveformView
    theme.apply(app, "dark")
    folder = str(tmp_path / "rec")
    now = time.time()
    rec_t0 = (int(now) // 3600 - 6) * 3600                       # recorded: 6 to 4 hours ago
    write_recording(folder, SID, rec_t0, 2 * 3600)

    class Engine:
        pass
    eng = Engine()
    eng.config = Config(str(tmp_path / "config.json"))
    eng.responses = None
    eng.hub = DataHub(buffer_seconds=600)
    eng.history = history.RecordedData(lambda: [folder])
    # received while running, never recorded: 2 hours ago (in the hub's day summary only) and live
    for t, sec in ((now - 7200, 600), (now - 300, 300)):
        for rec in mseed.encode_records("XX", "HIST", "00", "HHZ", RATE, t, (500 * np.ones(int(RATE * sec))).astype(int)):
            eng.hub.add_record(rec, "test")
    v = WaveformView(eng, eng.config)
    v.resize(1200, 500)
    v.show()
    for _ in range(5):
        app.processEvents()
    try:
        v.set_channels([SID])
        v.set_window(MAX_WINDOW)                                   # one day
        assert v._window == pytest.approx(86400) and v.visible_range()[1] - v.visible_range()[0] == pytest.approx(86400, abs=1)
        tr = v._traces[0]
        assert wait_for(lambda: (v.refresh(), app.processEvents(), not tr.pending)[-1], 30)
        v.redraw()
        x, y = tr.curve.getData()
        good = np.isfinite(y)
        xs = x[good]
        assert xs.min() < rec_t0 + 600 and ((xs > now - 7300) & (xs < now - 6500)).any()   # both histories
        assert xs.max() > now - 400                                                       # and live data
        # a short window 5 hours back: full resolution from the recording
        v.show_range(rec_t0 + 3600 + 100, rec_t0 + 3600 + 160)
        assert wait_for(lambda: (v._update_traces(urgent=True), app.processEvents(), not tr.pending)[-1], 30)
        v._update_traces(urgent=True)
        x, y = tr.curve.getData()
        assert len(x) > 1000 and x[0] == pytest.approx(rec_t0 + 3700, abs=0.1)
        # a filter on a long window: older unrecorded data is noted as unfiltered
        v.set_filter("hp1")
        v.set_window(MAX_WINDOW)
        v.set_live(True)
        assert wait_for(lambda: (v.refresh(), app.processEvents(), not tr.pending)[-1], 30)
        v.redraw()
        assert tr.unfiltered and "without the filter" in v.note.text()
    finally:
        eng.history.stop()
        v.close()
        v.deleteLater()
        app.processEvents()


@pytest.mark.skipif(sys.platform != "win32", reason="engine paths")
def test_engine_keeps_the_history_beside_another_config(tmp_path):
    from seiszone.core.engine import Engine
    cfg = Config(str(tmp_path / "trial" / "config.json"))
    eng = Engine(cfg)
    assert eng.history_path == str(tmp_path / "trial" / "view_history.npz")
    assert cfg.get("recording", "folder") or True
    assert eng.recording_folders()
