"""export_window: exact sample counts at non-aligned window edges, raw copies of inner records,
combined / separate files, gap report, duplicates and overlaps removed, live-buffer fallback,
ObsPy cross-check and speed (1 h of 200 sps)."""

import os
import time

import numpy as np
import pytest

from seiszone.config import State
from seiszone.core import export, mseed
from seiszone.core.hub import DataHub
from seiszone.core.recorder import RecordingManager

HOUR = 1761998400            # 2025-11-01 12:00:00 UTC
Z, N, E = "WB.KUM.0J.HNZ", "WB.KUM.0J.HNN", "WB.KUM.0J.HNE"
T_START = HOUR - 120.0       # recorded data: 11:58 -> 13:02 UTC


def signal(seconds, rate, seed):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    return (3000 * np.sin(2 * np.pi * 1.7 * t) + rng.normal(0, 200, len(t))).astype(np.int64)


def encode(sid, rate, start, y):
    net, sta, loc, cha = sid.split(".")
    return mseed.encode_records(net, sta, loc, cha, rate, start, y)


def record_into(folder, recs, minutes=15):
    """Write records through a real RecordingManager (the files the export reads)."""
    hub = DataHub(buffer_seconds=60)
    mgr = RecordingManager(hub, State(os.path.join(folder, "state.json")), folder, background=False)
    mgr.start("t", folder, file_minutes=minutes)
    for r in recs:
        hub.add_record(r, "test")
    mgr.close()


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    base = tmp_path_factory.mktemp("export")
    folder = str(base / "rec")
    seconds = 3600 + 240
    ys = {Z: signal(seconds, 200.0, 1), N: signal(seconds, 20.0, 2), E: signal(seconds, 20.0, 3)}
    recs = {Z: encode(Z, 200.0, T_START, ys[Z]), N: encode(N, 20.0, T_START, ys[N]),
            E: encode(E, 20.0, T_START, ys[E])}
    gap = recs[E][50:55]                         # these HNE records were never received
    feed = recs[Z] + recs[N] + [r for r in recs[E] if r not in gap]
    record_into(folder, feed)
    return {"base": base, "folder": folder, "ys": ys, "recs": recs, "gap": gap}


def read_samples(path, sid=None):
    """{sid: [(start, samples)]} via SeisZone's own reader."""
    out = {}
    for r in mseed.read_file(path, check=True):
        if sid is None or r.sid == sid:
            out.setdefault(r.sid, []).append((r.start, r.samples))
    return out


def joined(parts):
    return np.concatenate([p[1] for p in parts]).astype(np.int64)


def test_exact_edges_and_raw_copies(data, tmp_path):
    from obspy import read
    t0 = HOUR + 600.0123                         # not on a sample
    t1 = HOUR + 1500.0071
    paths, report = export.export_window([Z], t0, t1, str(tmp_path), folders=[data["folder"]])
    mseeds = [p for p in paths if p.endswith(".mseed")]
    assert len(mseeds) == 1 and paths[-1].endswith(".txt") and os.path.basename(paths[-1]).startswith("export_info__")
    k0 = int(np.ceil((t0 - T_START) * 200))      # first sample at / after t0
    k1 = int(np.ceil((t1 - T_START) * 200))      # samples before t1
    parts = read_samples(mseeds[0])[Z]
    assert parts[0][0] == pytest.approx(T_START + k0 / 200, abs=1e-4)
    assert np.array_equal(joined(parts), data["ys"][Z][k0:k1])
    assert f"{k1 - k0} samples of {k1 - k0} expected, 0 gap(s)" in report
    # records completely inside the window are copied byte for byte
    with open(mseeds[0], "rb") as fh:
        out = fh.read()
    inner = [r for r in data["recs"][Z] if r.start >= t0 and r.last_sample_time < t1]
    assert inner and all(r.raw in out for r in inner)
    assert len(out) % 512 == 0
    st = read(mseeds[0])
    st.merge()
    assert len(st) == 1 and st[0].stats.npts == k1 - k0 and st[0].stats.sampling_rate == 200
    assert np.array_equal(st[0].data, data["ys"][Z][k0:k1])
    assert abs(st[0].stats.starttime.timestamp - (T_START + k0 / 200)) < 1e-4
    assert "Window : 2025-11-01 12:10:00.012 UTC" in report and "Local  :" in report


def test_combined_and_separate(data, tmp_path):
    from obspy import read
    t0, t1 = HOUR + 100, HOUR + 400
    sep, _ = export.export_window([Z, N], t0, t1, str(tmp_path / "sep"), mode="separate", folders=[data["folder"]])
    names = sorted(os.path.basename(p) for p in sep)
    assert names == ["WB.KUM.0J.HNN__20251101T120140Z_5min.mseed", "WB.KUM.0J.HNZ__20251101T120140Z_5min.mseed",
                     "export_info__20251101T120140Z.txt"]
    comb, report = export.export_window([Z, N, E], t0, t1, str(tmp_path / "comb"), mode="combined",
                                        folders=[data["folder"]])
    assert [os.path.basename(p) for p in comb] == ["WB.KUM__20251101T120140Z_5min_3ch.mseed",
                                                   "export_info__20251101T120140Z.txt"]
    st = read(comb[0])
    st.merge()
    assert sorted(tr.id for tr in st) == sorted([Z, N, E])
    for tr in st.select(id="WB.KUM.0J.HN[ZN]"):    # HNE has its missing records in this window
        assert tr.stats.npts == int((t1 - t0) * tr.stats.sampling_rate)
    assert "one file with all channels" in report
    # an export never overwrites an earlier one
    again, _ = export.export_window([Z, N, E], t0, t1, str(tmp_path / "comb"), mode="combined",
                                    folders=[data["folder"]])
    assert os.path.basename(again[0]) == "WB.KUM__20251101T120140Z_5min_3ch_2.mseed"
    assert os.path.exists(comb[0])


def test_gap_report(data, tmp_path):
    gap = data["gap"]
    g0, g1 = gap[0].start, gap[-1].end
    missing = sum(r.nsamp for r in gap)
    t0, t1 = HOUR, HOUR + 1800
    paths, report = export.export_window([E], t0, t1, str(tmp_path), folders=[data["folder"]])
    expected = 1800 * 20
    assert f"{expected - missing} samples of {expected} expected, 1 gap(s)" in report
    assert f"({g1 - g0:.3f} s, {missing} samples missing)" in report
    assert mseed.fmt_time(g0, ms=True) in report
    parts = read_samples(paths[0])[E]
    assert sum(len(p[1]) for p in parts) == expected - missing


def test_no_data(data, tmp_path):
    paths, report = export.export_window(["XX.NONE..HHZ"], HOUR, HOUR + 60, str(tmp_path), folders=[data["folder"]])
    assert len(paths) == 1 and paths[0].endswith(".txt")
    assert "XX.NONE..HHZ: NO DATA" in report
    with pytest.raises(ValueError):
        export.export_window([Z], HOUR, HOUR, str(tmp_path))


def test_duplicates_and_overlaps_removed(data, tmp_path):
    """The same data recorded twice: once identical, once with other record boundaries."""
    y = data["ys"][N]
    start = T_START + 37 / 20                      # same samples, records cut elsewhere
    other = str(tmp_path / "other")
    record_into(other, encode(N, 20.0, start, y[37:37 + 20 * 900]), minutes=30)
    t0, t1 = HOUR + 0.01, HOUR + 600.01
    paths, report = export.export_window([N], t0, t1, str(tmp_path / "out"),
                                         folders=[data["folder"], other, data["folder"]])
    k0, k1 = int(np.ceil((t0 - T_START) * 20)), int(np.ceil((t1 - T_START) * 20))
    assert np.array_equal(joined(read_samples(paths[0])[N]), y[k0:k1])
    assert f"{k1 - k0} samples of {k1 - k0} expected, 0 gap(s)" in report
    assert "duplicate records dropped" in report


def test_live_buffer_fills_what_is_missing(data, tmp_path):
    hub = DataHub(buffer_seconds=7200)
    for r in data["recs"][E]:                    # the live buffer has all HNE records
        hub.add_record(r, "test")
    t0, t1 = HOUR + 0.02, HOUR + 1800.02
    paths, report = export.export_window([E], t0, t1, str(tmp_path / "a"), folders=[data["folder"]], hub=hub)
    k0, k1 = int(np.ceil((t0 - T_START) * 20)), int(np.ceil((t1 - T_START) * 20))
    assert np.array_equal(joined(read_samples(paths[0])[E]), data["ys"][E][k0:k1])
    missing = sum(r.nsamp for r in data["gap"])
    assert f"live buffer: {missing} samples" in report and "0 gap(s)" in report
    # live buffer only (no recording folders)
    paths, report = export.export_window([E], t0, t1, str(tmp_path / "b"), hub=hub)
    assert np.array_equal(joined(read_samples(paths[0])[E]), data["ys"][E][k0:k1])
    assert f"{k1 - k0} samples of {k1 - k0} expected, 0 gap(s)" in report


def test_live_float_data(tmp_path):
    """Non-integer live data is written as float64 records (lossless)."""
    from obspy import read
    y = np.sin(np.arange(400) / 7.0) * 1.2345
    raw, _ = export._float_records("XX", "FLT", "", "BHZ", 50, 1, HOUR, y, 1)
    hub = DataHub()
    for i in range(0, len(raw), 512):
        hub.add_record(mseed.parse_record(raw, i)[0], "test")
    paths, report = export.export_window(["XX.FLT..BHZ"], HOUR, HOUR + 8, str(tmp_path), hub=hub)
    st = read(paths[0])
    st.merge()
    assert st[0].data.dtype == np.float64 and np.array_equal(st[0].data, y)
    assert "400 samples of 400 expected" in report


def test_speed_one_hour_200sps(data, tmp_path):
    t = time.time()
    paths, report = export.export_window([Z], HOUR, HOUR + 3600, str(tmp_path), folders=[data["folder"]])
    took = time.time() - t
    assert took < 10, took
    assert "720000 samples of 720000 expected, 0 gap(s)" in report
    assert os.path.basename(paths[0]) == "WB.KUM.0J.HNZ__20251101T120000Z_60min.mseed"


# ---------------------------------------------------------------------- review fixes

def test_low_rate_channel_record_from_an_older_file(tmp_path):
    """A 0.1 sps record holds ~2 h of samples; it lies in the file of the hour it starts in."""
    folder = str(tmp_path / "rec")
    sid = "XX.TEST.00.VMZ"
    y = np.zeros(2000, dtype=np.int64) + 5                # quiet: many samples per record
    start = HOUR - 3 * 3600 + 59 * 60                     # 08:59 UTC (HOUR is 12:00)
    recs = encode(sid, 0.1, start, y)
    assert recs[0].end - recs[0].start > 2 * 3600
    record_into(folder, recs[:1], minutes=60)
    t0, t1 = HOUR - 3600 + 30 * 60, HOUR - 3600 + 50 * 60  # 10:30 -> 10:50
    written, report = export.export_window([sid], t0, t1, str(tmp_path / "out"), folders=[folder])
    assert "NO DATA" not in report and "0 gap(s)" in report, report
    got = read_samples(written[0], sid)
    assert len(joined(got[sid])) == 120


def test_recorded_channels_of_an_offline_station(data):
    sids = export.recorded_sids([data["folder"]], HOUR - 60, HOUR + 60)
    assert sids == sorted([Z, N, E])
    assert export.recorded_sids([data["folder"]], HOUR + 86400 * 10, HOUR + 86400 * 10 + 60) == []
