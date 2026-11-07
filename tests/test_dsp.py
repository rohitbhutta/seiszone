"""dsp: filters pass / stop test sines, envelope keeps every extreme, spectrum finds the peak."""

import numpy as np
import pytest

from seiszone.core import dsp

RATE = 200.0


def sine(freq, seconds=60, rate=RATE, amp=1000.0):
    t = np.arange(int(seconds * rate)) / rate
    return amp * np.sin(2 * np.pi * freq * t)


def rms_mid(y):
    """RMS of the middle half (away from the edges)."""
    n = len(y)
    return float(np.sqrt(np.mean(y[n // 4:3 * n // 4] ** 2)))


@pytest.mark.parametrize("name,freq,passes", [
    ("lp5", 1.0, True), ("lp5", 30.0, False),
    ("lp20", 10.0, True), ("lp20", 60.0, False),
    ("hp1", 10.0, True), ("hp1", 0.05, False),
    ("hp0.1", 2.0, True), ("hp0.1", 0.005, False),
    ("bp0.5-5", 2.0, True), ("bp0.5-5", 0.05, False), ("bp0.5-5", 40.0, False),
    ("bp1-10", 4.0, True), ("bp1-10", 50.0, False),
    ("bp1-20", 8.0, True), ("bp1-20", 0.1, False), ("bp1-20", 70.0, False),
])
def test_filters_pass_and_stop(name, freq, passes):
    seconds = 600 if freq < 0.05 else 120
    y = sine(freq, seconds)
    out = dsp.apply(y, RATE, name)
    ratio = rms_mid(out) / rms_mid(y)
    if passes:
        assert 0.9 < ratio < 1.1, ratio
    else:
        assert ratio < 0.1, ratio


def test_filter_names_and_none():
    assert list(dsp.FILTERS)[0] == "none"
    assert {"hp0.1", "hp1", "lp5", "lp20", "bp0.5-5", "bp1-10", "bp1-20"} <= set(dsp.FILTERS)
    assert all(v["label"] for v in dsp.FILTERS.values())
    y = sine(1.0, 10) + 5000
    assert np.array_equal(dsp.apply(y, RATE, "none", demean=False), y)
    assert abs(np.mean(dsp.apply(y, RATE, "none"))) < 1e-6
    # a low-pass without demean keeps the offset, high-pass removes it anyway
    assert abs(np.mean(dsp.apply(y, RATE, "lp5", demean=False)) - 5000) < 5
    assert abs(np.mean(dsp.apply(y, RATE, "hp1", demean=False))) < 50


def test_short_arrays_and_nan():
    for n in (0, 1, 5, 20):
        y = np.arange(n, dtype=float) + 100
        out = dsp.apply(y, RATE, "bp1-10")
        assert len(out) == n
        if n:
            assert abs(np.mean(out)) < 1e-9   # too short to filter: demeaned only
    y = sine(2.0, 30) + 300
    y[1000:1100] = np.nan
    out = dsp.apply(y, RATE, "bp0.5-5")
    assert np.isnan(out[1000:1100]).all()
    assert np.isfinite(out[:1000]).all() and np.isfinite(out[1100:]).all()
    assert np.isnan(dsp.apply(np.full(50, np.nan), RATE, "lp5")).all()
    # a filter above the Nyquist frequency of slow data leaves the signal alone
    slow = sine(0.2, 600, rate=1.0)
    assert np.allclose(dsp.apply(slow, 1.0, "lp20"), slow - slow.mean())


def test_envelope_keeps_extremes():
    rng = np.random.default_rng(3)
    y = rng.normal(0, 10, 100_000)
    y[12345] = 999.0
    y[67890] = -777.0
    t0 = 1_700_000_000.0
    x, e = dsp.envelope(t0, RATE, y, 2000)
    assert x.dtype == np.float64 and e.dtype == np.float64
    assert len(x) == len(e) <= 2000
    assert e.max() == 999.0 and e.min() == -777.0
    assert x[np.argmax(e)] == pytest.approx(t0 + 12345 / RATE)
    assert x[np.argmin(e)] == pytest.approx(t0 + 67890 / RATE)
    assert np.all(np.diff(x) >= 0)
    # every bin's min and max are exact
    size = int(np.ceil(len(y) / (len(x) // 2)))
    for k in range(len(x) // 2):
        part = y[k * size:(k + 1) * size]
        assert sorted(e[2 * k:2 * k + 2]) == [part.min(), part.max()]


def test_envelope_pass_through_and_nan():
    y = np.arange(100, dtype=np.int32)
    x, e = dsp.envelope(10.0, 100.0, y, 500)
    assert np.array_equal(e, y.astype(float)) and x[0] == 10.0 and x[-1] == pytest.approx(10.99)
    y = np.ones(10_000)
    y[:5000] = np.nan
    x, e = dsp.envelope(0.0, 100.0, y, 100)
    assert np.isnan(e[:40]).all() and (e[-40:] == 1.0).all()


def test_spectrum_peak():
    y = sine(7.5, 120) + np.random.default_rng(1).normal(0, 50, 24000)
    f, a = dsp.spectrum(y, RATE)
    assert len(f) == len(a) and f[0] > 0
    assert abs(f[np.argmax(a)] - 7.5) < 0.5
    f, a = dsp.spectrum(np.ones(5), RATE)
    assert len(f) == 0


def test_stats():
    s = dsp.stats([1.0, 3.0, np.nan, 5.0])
    assert s["n"] == 3 and s["min"] == 1.0 and s["max"] == 5.0 and s["mean"] == 3.0
    assert s["rms"] == pytest.approx(np.sqrt(8 / 3))
    assert dsp.stats([])["n"] == 0
