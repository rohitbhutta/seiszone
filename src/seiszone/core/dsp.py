"""Signal processing for the views: filters, mean removal, min/max decimation for plotting,
amplitude spectra and simple statistics. Everything is NaN-safe (NaN marks missing data in
the views) and never fails on short arrays - the waveform view calls it many times a second.

Filters are zero-phase (scipy.signal.sosfiltfilt of a 4th-order Butterworth in second-order
sections, which stays stable even for 0.1 Hz at 200 sps).
"""

import functools
import math

import numpy as np
from scipy import signal

ORDER = 4

FILTERS = {
    "none": {"label": "No filter", "type": None, "freq": None},
    "hp0.1": {"label": "High-pass 0.1 Hz", "type": "highpass", "freq": 0.1},
    "hp1": {"label": "High-pass 1 Hz", "type": "highpass", "freq": 1.0},
    "lp5": {"label": "Low-pass 5 Hz", "type": "lowpass", "freq": 5.0},
    "lp20": {"label": "Low-pass 20 Hz", "type": "lowpass", "freq": 20.0},
    "bp0.5-5": {"label": "Band-pass 0.5-5 Hz", "type": "bandpass", "freq": (0.5, 5.0)},
    "bp1-10": {"label": "Band-pass 1-10 Hz", "type": "bandpass", "freq": (1.0, 10.0)},
    "bp1-20": {"label": "Band-pass 1-20 Hz", "type": "bandpass", "freq": (1.0, 20.0)},
}


@functools.lru_cache(maxsize=64)
def _design(name, rate):
    """Second-order sections for a filter at a sample rate, or None when nothing is left to
    filter (e.g. a 20 Hz low-pass on 20 sps data). Corners at/above 0.95 x Nyquist are dropped."""
    spec = FILTERS[name]
    kind, freq = spec["type"], spec["freq"]
    if kind is None or rate <= 0:
        return None
    limit = 0.95 * rate / 2
    if kind == "bandpass":
        lo, hi = freq
        if lo >= limit:
            return None
        if hi >= limit:
            kind, freq = "highpass", lo
    elif freq >= limit:
        return None
    return signal.butter(ORDER, freq, btype=kind, fs=rate, output="sos")


def _padlen(sos):
    """The padding sosfiltfilt uses by default (an array must be longer than this)."""
    return 3 * (2 * len(sos) + 1 - min((sos[:, 2] == 0).sum(), (sos[:, 5] == 0).sum()))


def apply(y, rate, name="none", demean=True):
    """Filtered copy of y (float64). NaN samples stay NaN; they are bridged by the mean while
    filtering. Arrays too short for the filter are only demeaned."""
    y = np.array(y, dtype=np.float64)
    kind = FILTERS.get(name, FILTERS["none"])["type"]
    sos = _design(name, float(rate)) if kind else None
    bad = ~np.isfinite(y)
    if (sos is None and not demean) or bad.all():
        return y
    mean = float(np.mean(y[~bad]))
    y[bad] = mean
    y -= mean
    if sos is not None and len(y) > _padlen(sos):
        y = signal.sosfiltfilt(sos, y)
    if not demean and kind == "lowpass":   # a low-pass alone keeps the offset
        y += mean
    y[bad] = np.nan
    return y


def demean(y):
    """y minus its mean (NaN ignored)."""
    return apply(y, 0, "none", demean=True)


def envelope(t0, rate, y, max_points):
    """Min/max decimation for plotting: (x epoch seconds, y), both float64.

    Up to max_points samples are returned unchanged. Otherwise the samples are cut into
    max_points // 2 bins and each bin gives its exact minimum and maximum, placed at their own
    sample times in time order - so no peak is ever lost on screen. A bin of NaN gives NaN."""
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    if n <= max_points:
        return t0 + np.arange(n, dtype=np.float64) / rate, y.copy()
    bins = max(1, max_points // 2)
    size = math.ceil(n / bins)
    bins = math.ceil(n / size)
    grid = np.full(bins * size, np.nan)
    grid[:n] = y
    grid = grid.reshape(bins, size)
    nan = np.isnan(grid)
    imin = np.argmin(np.where(nan, np.inf, grid), axis=1)
    imax = np.argmax(np.where(nan, -np.inf, grid), axis=1)
    rows = np.arange(bins)
    vmin, vmax = grid[rows, imin], grid[rows, imax]
    first = np.minimum(imin, imax)
    second = np.maximum(imin, imax)
    min_first = imin <= imax
    xs = np.empty(2 * bins)
    ys = np.empty(2 * bins)
    xs[0::2] = t0 + (rows * size + first) / rate
    xs[1::2] = t0 + (rows * size + second) / rate
    ys[0::2] = np.where(min_first, vmin, vmax)
    ys[1::2] = np.where(min_first, vmax, vmin)
    return xs, ys


def spectrum(y, rate):
    """Amplitude spectral density (units / sqrt(Hz)) by Welch's method, mean removed:
    (frequencies without 0 Hz, amplitudes). Segments of a power of two, about 8 per array
    (overlapping by half), at least 64 samples; empty arrays for fewer than 16 good samples."""
    y = np.asarray(y, dtype=np.float64)
    y = y[np.isfinite(y)]
    if len(y) < 16 or rate <= 0:
        return np.zeros(0), np.zeros(0)
    nperseg = min(len(y), max(64, 2 ** int(math.log2(len(y) / 4))))
    f, pxx = signal.welch(y, fs=rate, nperseg=nperseg, detrend="constant", scaling="density")
    return f[1:], np.sqrt(pxx[1:])


def stats(y):
    """{"n", "min", "max", "mean", "rms"} of the finite samples; rms is taken around the mean."""
    y = np.asarray(y, dtype=np.float64)
    y = y[np.isfinite(y)]
    if not len(y):
        return {"n": 0, "min": math.nan, "max": math.nan, "mean": math.nan, "rms": math.nan}
    mean = float(np.mean(y))
    return {"n": int(len(y)), "min": float(np.min(y)), "max": float(np.max(y)), "mean": mean,
            "rms": float(np.sqrt(np.mean((y - mean) ** 2)))}
