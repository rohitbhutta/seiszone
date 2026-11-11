"""Signal processing for the views: filters, mean removal, min/max decimation for plotting,
amplitude spectra and simple statistics. Everything is NaN-safe (NaN marks missing data in
the views) and never fails on short arrays - the waveform view calls it many times a second.

Filters are zero-phase (scipy.signal.sosfiltfilt of Butterworth filters in second-order
sections, which stays stable even for 0.1 Hz at 200 sps). A filter is a "spec":

    {"hp": 1.0, "hp_order": 4, "lp": 10.0, "lp_order": 4}   high-pass and/or low-pass corners in
                                                            Hz (None: that part is off), orders 1-8
    {"default": True}                                       the default band-pass of the stream's
                                                            rate (default_band)

The named FILTERS are the ready-made specs the viewer offers; a viewer's own designs are specs too.
"""

import functools
import math

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy import signal

ORDER = 4
MAX_ORDER = 8

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


DEFAULT_SPEC = {"default": True}


def spec_of(name):
    """The spec of a named filter (FILTERS key), or None for "none" and unknown names."""
    f = FILTERS.get(name) or FILTERS["none"]
    kind, freq = f["type"], f["freq"]
    if kind == "highpass":
        return make_spec(hp=freq)
    if kind == "lowpass":
        return make_spec(lp=freq)
    if kind == "bandpass":
        return make_spec(hp=freq[0], lp=freq[1])
    return None


def make_spec(hp=None, lp=None, hp_order=ORDER, lp_order=ORDER):
    return {"hp": hp, "hp_order": hp_order, "lp": lp, "lp_order": lp_order}


def _corner(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def _order(value):
    try:
        return min(MAX_ORDER, max(1, int(value)))
    except (TypeError, ValueError):
        return ORDER


def normalize_spec(spec):
    """A clean copy of a spec from config (any junk becomes "off"), or None when it filters
    nothing. A high-pass corner at or above the low-pass one is not a filter: None."""
    if not isinstance(spec, dict):
        return None
    if spec.get("default"):
        return dict(DEFAULT_SPEC)
    hp, lp = _corner(spec.get("hp")), _corner(spec.get("lp"))
    if hp is None and lp is None:
        return None
    if hp is not None and lp is not None and hp >= lp:
        return None
    return make_spec(hp, lp, _order(spec.get("hp_order")), _order(spec.get("lp_order")))


def default_band(rate):
    """(high-pass, low-pass) corners of the default band-pass: 10-30 s period for 1 or 2 sps
    data, else 0.1 and 0.9 of the Nyquist frequency (5-45 Hz at 100 sps) - it takes the long
    periods out of a broadband record so it lies flat."""
    if rate <= 2.0:
        return 1.0 / 30.0, 1.0 / 10.0
    nyq = rate / 2.0
    return 0.1 * nyq, 0.9 * nyq


def resolve(spec, rate):
    """(hp, hp_order, lp, lp_order) of a spec at a sample rate, or None (no filter)."""
    spec = normalize_spec(spec)
    if spec is None:
        return None
    if spec.get("default"):
        hp, lp = default_band(rate)
        return hp, ORDER, lp, ORDER
    return spec["hp"], spec["hp_order"], spec["lp"], spec["lp_order"]


def describe(spec):
    """Short text of a spec: "Band-pass 1-10 Hz", "High-pass 0.1 Hz (order 2)", "Default band-pass"."""
    spec = normalize_spec(spec)
    if spec is None:
        return "No filter"
    if spec.get("default"):
        return "Default band-pass"

    def hz(v):
        return f"{v:g} Hz" if v >= 1 else f"{v:.3g} Hz"

    hp, lp = spec["hp"], spec["lp"]
    if hp is not None and lp is not None:
        text = f"Band-pass {hp:g}-{hz(lp)}"
    elif hp is not None:
        text = f"High-pass {hz(hp)}"
    else:
        text = f"Low-pass {hz(lp)}"
    orders = {o for o, v in ((spec["hp_order"], hp), (spec["lp_order"], lp)) if v is not None}
    if orders != {ORDER}:
        text += " (order " + "/".join(str(o) for o in sorted(orders)) + ")"
    return text


@functools.lru_cache(maxsize=256)
def design(hp, hp_order, lp, lp_order, rate):
    """Second-order sections of the high-pass and/or low-pass at a sample rate, or None when
    nothing is left to filter. Corners at/above 0.95 x Nyquist are dropped (a low-pass there
    passes everything; a high-pass there would leave nothing, so the filter is skipped)."""
    if rate <= 0:
        return None
    limit = 0.95 * rate / 2
    if hp is not None and hp >= limit:
        return None
    if lp is not None and lp >= limit:
        lp = None
    parts = []
    if hp is not None:
        parts.append(signal.butter(hp_order, hp, btype="highpass", fs=rate, output="sos"))
    if lp is not None:
        parts.append(signal.butter(lp_order, lp, btype="lowpass", fs=rate, output="sos"))
    return np.vstack(parts) if parts else None


def _design(name, rate):
    """Sections of a named filter at a sample rate (None: nothing to filter)."""
    r = resolve(spec_of(name), float(rate))
    return design(*r, float(rate)) if r else None


def _padlen(sos):
    """The padding sosfiltfilt uses by default (an array must be longer than this)."""
    return 3 * (2 * len(sos) + 1 - min((sos[:, 2] == 0).sum(), (sos[:, 5] == 0).sum()))


def apply(y, rate, name="none", demean=True):
    """Filtered copy of y (float64) with a named filter (FILTERS); see apply_spec."""
    return apply_spec(y, rate, spec_of(name), demean)


def apply_spec(y, rate, spec, demean=True):
    """Filtered copy of y (float64). NaN samples stay NaN; they are bridged by the mean while
    filtering. Arrays too short for the filter are only demeaned. Without demean a low-pass
    alone keeps the offset (a high-pass takes it out anyway)."""
    y = np.array(y, dtype=np.float64)
    r = resolve(spec, float(rate)) if spec else None
    sos = design(*r, float(rate)) if r else None
    bad = ~np.isfinite(y)
    if (sos is None and not demean) or bad.all():
        return y
    mean = float(np.mean(y[~bad]))
    y[bad] = mean
    y -= mean
    if sos is not None and len(y) > _padlen(sos):
        y = signal.sosfiltfilt(sos, y)
    if not demean and r is not None and r[0] is None:   # a low-pass alone keeps the offset
        y += mean
    y[bad] = np.nan
    return y


def pre_roll(spec, rate=0.0):
    """Seconds of data read before the visible start so a filter's edge transient stays off
    screen: three periods of the lowest corner frequency, at most 60 s."""
    if isinstance(spec, str):
        spec = spec_of(spec)
    r = resolve(spec, rate if rate > 0 else 100.0) if spec else None
    if not r:
        return 0.0
    low = r[0] if r[0] is not None else r[2]
    return min(60.0, 3.0 / low) if low and low > 0 else 0.0


def response(spec, rate, freqs):
    """Gain in dB of the filter as applied (forwards and backwards: the square of the
    Butterworth response, -6 dB at a corner) at these frequencies in Hz. 0 dB everywhere when
    the spec filters nothing at this rate."""
    freqs = np.asarray(freqs, dtype=np.float64)
    r = resolve(spec, float(rate)) if spec else None
    sos = design(*r, float(rate)) if r else None
    if sos is None:
        return np.zeros_like(freqs)
    _w, h = signal.sosfreqz(sos, worN=freqs, fs=rate)
    return 40.0 * np.log10(np.maximum(np.abs(h), 1e-12))


def spectrogram(y, rate, bins, max_columns):
    """Power spectrogram for display: (power[columns, bins + 1] from 0 Hz to Nyquist, seconds
    from one column to the next, seconds from the first sample to the centre of the first
    column), or None when y is shorter than one segment. Segments of 2*bins samples (Hann
    window), stepped so there are at most max_columns columns (at least half a segment). NaN
    counts as the mean (a gap is quiet, not loud)."""
    y = np.asarray(y, dtype=np.float64)
    nper = 2 * int(bins)
    n = len(y)
    if n < nper or nper < 4 or rate <= 0:
        return None
    good = np.isfinite(y)
    if not good.any():
        return None
    y = np.where(good, y, float(np.mean(y[good])))
    step = max(nper // 2, math.ceil((n - nper + 1) / max(1, int(max_columns))))
    frames = sliding_window_view(y, nper)[::step]
    frames = (frames - frames.mean(axis=1, keepdims=True)) * np.hanning(nper)
    power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    return power, step / rate, (nper / 2) / rate


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
