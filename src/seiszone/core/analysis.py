"""Analysis of a selected time range: strong-motion measures and response spectra of an
acceleration record, and the three calibration checks that compare a calibration signal fed
into a sensor (the "calibration channel") with the sensor's output.

Strong motion (acceleration a in m/s², evenly sampled):
    measures(a, rate)                 PGA, PGV, PGD, RMS, Arias intensity, CAV, the 5-95 %
                                      significant duration, the predominant period
    integrate(a, rate)                velocity and displacement (0.1 Hz high-pass at each step,
                                      so the offsets of a real record do not run away)
    response_spectrum(a, rate, T, z)  SD, PSV, PSA of a damped oscillator for each period T

Calibration. The calibration signal reaches the sensor as a current through the calibration
resistor into the feedback coil, which acts on the mass like a ground acceleration:

    equivalent acceleration = counts x (volts per count) / (resistor x coil constant)   [m/s²]

(an accelerometer whose calibration input is given directly in volts per m/s² uses resistor 1
and coil constant 1 with that factor as the volts per count). For a velocity sensor the
equivalent velocity of a sine is that acceleration / (2 pi f).
    sine_fit(y, rate)                 frequency, amplitude, phase and offset of a sine
    sine_calibration(out, cal, ...)   the sensor's sensitivity and phase at the sine's frequency
    transfer_function(cal, out, rate) gain, phase and coherence over frequency (broadband noise)
    step_response(cal, out, rate)     the output after each step of the calibration signal,
                                      stacked; step_gain / fit_velocity_step read it

Nothing here knows about channels or files: plain arrays in, numbers out.
"""

import math

import numpy as np
from scipy import optimize, signal

from . import dsp

G = 9.80665


# ------------------------------------------------------------------------------------------
# strong motion
# ------------------------------------------------------------------------------------------
def _highpass(y, rate, corner=0.1):
    return dsp.apply_spec(y, rate, dsp.make_spec(hp=corner), demean=True)


def integrate(acc, rate, corner=0.1):
    """(acceleration with the mean and drift removed, velocity, displacement)."""
    a = _highpass(np.asarray(acc, dtype=np.float64), rate, corner)
    v = _highpass(np.cumsum(a) / rate, rate, corner)
    d = _highpass(np.cumsum(v) / rate, rate, corner)
    return a, v, d


def measures(acc, rate):
    """Strong-motion measures of an acceleration record in m/s² (see the module text)."""
    a, v, d = integrate(acc, rate)
    n = len(a)
    if n < 8:
        return None
    dt = 1.0 / rate
    energy = np.cumsum(a * a) * dt
    total = float(energy[-1])
    t5 = float(np.searchsorted(energy, 0.05 * total)) * dt if total > 0 else 0.0
    t95 = float(np.searchsorted(energy, 0.95 * total)) * dt if total > 0 else 0.0
    spec = np.abs(np.fft.rfft(a * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, dt)
    k = int(np.argmax(spec[1:])) + 1 if len(spec) > 1 else 0
    return {"n": n, "duration": n * dt,
            "pga": float(np.max(np.abs(a))), "pga_g": float(np.max(np.abs(a))) / G,
            "pgv": float(np.max(np.abs(v))), "pgd": float(np.max(np.abs(d))),
            "rms": float(np.sqrt(np.mean(a * a))),
            "arias": math.pi / (2 * G) * total, "cav": float(np.sum(np.abs(a)) * dt),
            "t5": t5, "t95": t95, "significant_duration": max(0.0, t95 - t5),
            "predominant_period": (1.0 / float(freqs[k])) if k and freqs[k] > 0 else math.nan}


def default_periods(rate, n=80):
    """Oscillator periods from 4 samples (the shortest that is still resolved) to 10 s."""
    return np.logspace(math.log10(max(0.02, 4.0 / rate)), 1.0, n)


def response_spectrum(acc, rate, periods=None, damping=0.05):
    """(periods, SD in m, PSV in m/s, PSA in m/s²): the largest relative displacement of an
    oscillator of each period and its pseudo velocity / acceleration. Solved in the frequency
    domain; the record is padded so each oscillator can ring down."""
    a = np.asarray(acc, dtype=np.float64)
    a = a - a.mean()
    periods = default_periods(rate) if periods is None else np.asarray(periods, dtype=np.float64)
    n = len(a)
    pad = int(rate * min(60.0, 3.0 * float(periods.max()) / max(damping, 0.01)))
    size = 1 << int(math.ceil(math.log2(max(16, n + pad))))
    spec = np.fft.rfft(a, size)
    w = 2 * math.pi * np.fft.rfftfreq(size, 1.0 / rate)
    sd = np.empty(len(periods))
    for i, period in enumerate(periods):
        wn = 2 * math.pi / period
        u = np.fft.irfft(-spec / (wn * wn - w * w + 2j * damping * wn * w), size)
        sd[i] = float(np.max(np.abs(u)))
    wn = 2 * math.pi / periods
    return periods, sd, sd * wn, sd * wn * wn


# ------------------------------------------------------------------------------------------
# calibration
# ------------------------------------------------------------------------------------------
def equivalent_acceleration(counts, volts_per_count, resistor=1.0, coil=1.0):
    """m/s² the calibration signal acts on the mass with (see the module text)."""
    return np.asarray(counts, dtype=np.float64) * volts_per_count / (resistor * coil)


def sine_fit(y, rate):
    """{"freq", "amp", "phase" (rad, of amp*sin(2 pi f t + phase)), "offset", "residual" (RMS of
    what the sine does not explain, as a fraction of amp)} or None."""
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    if n < 16:
        return None
    t = np.arange(n) / rate
    spec = np.abs(np.fft.rfft((y - y.mean()) * np.hanning(n)))
    k = int(np.argmax(spec[1:])) + 1
    f0 = k * rate / n

    def solve(f):
        m = np.column_stack([np.sin(2 * math.pi * f * t), np.cos(2 * math.pi * f * t), np.ones(n)])
        coef, *_ = np.linalg.lstsq(m, y, rcond=None)
        return coef, float(np.sum((y - m @ coef) ** 2))

    step = rate / n
    res = optimize.minimize_scalar(lambda f: solve(f)[1], bounds=(max(f0 - step, step / 4), f0 + step),
                                   method="bounded", options={"xatol": step * 1e-4})
    f = float(res.x)
    (s, c, off), err = solve(f)
    amp = math.hypot(s, c)
    if amp <= 0:
        return None
    return {"freq": f, "amp": amp, "phase": math.atan2(c, s), "offset": float(off),
            "residual": math.sqrt(err / n) / amp}


def sine_calibration(out, cal, rate, volts_per_count, resistor=1.0, coil=1.0, velocity=False):
    """A sine fed in as calibration signal: {"freq", "input" (m/s² or m/s amplitude),
    "output" (counts amplitude), "sensitivity" (counts per m/s² or per m/s), "phase" (degrees the
    output leads the input), "residual_out", "residual_cal"} or None."""
    fo, fc = sine_fit(out, rate), sine_fit(cal, rate)
    if fo is None or fc is None:
        return None
    if abs(fo["freq"] - fc["freq"]) > 0.02 * fc["freq"] + 2.0 * rate / len(cal):
        return {"error": f"the two channels do not carry the same sine ({fc['freq']:.4g} Hz in the calibration "
                         f"channel, {fo['freq']:.4g} Hz in the output)"}
    freq = fc["freq"]
    # the output's sine at the calibration channel's frequency (one common frequency for the phase)
    t = np.arange(len(out)) / rate
    m = np.column_stack([np.sin(2 * math.pi * freq * t), np.cos(2 * math.pi * freq * t), np.ones(len(t))])
    (s, c, _o), *_ = np.linalg.lstsq(m, np.asarray(out, dtype=np.float64), rcond=None)
    (s2, c2, _o2), *_ = np.linalg.lstsq(m[:len(cal)], np.asarray(cal, dtype=np.float64)[:len(t)], rcond=None)
    out_amp, cal_amp = math.hypot(s, c), math.hypot(s2, c2)
    inp = float(equivalent_acceleration(cal_amp, volts_per_count, resistor, coil))
    if velocity:
        inp /= 2 * math.pi * freq
    phase = math.degrees(math.atan2(c, s) - math.atan2(c2, s2))
    phase = (phase + 180.0) % 360.0 - 180.0
    return {"freq": freq, "input": inp, "output": out_amp, "sensitivity": out_amp / inp if inp else math.nan,
            "phase": phase, "residual_out": fo["residual"], "residual_cal": fc["residual"]}


def transfer_function(cal, out, rate):
    """(frequencies, complex output / input in counts per count, coherence 0-1) between the
    calibration signal and the sensor output (Welch cross spectra; 0 Hz left out)."""
    x = np.asarray(cal, dtype=np.float64)
    y = np.asarray(out, dtype=np.float64)
    n = min(len(x), len(y))
    if n < 64:
        return np.zeros(0), np.zeros(0, dtype=complex), np.zeros(0)
    x, y = x[:n], y[:n]
    nper = min(n, max(64, 2 ** int(math.log2(n / 8))))
    f, pxx = signal.welch(x, fs=rate, nperseg=nper, detrend="constant")
    _f, pyy = signal.welch(y, fs=rate, nperseg=nper, detrend="constant")
    _f, pxy = signal.csd(x, y, fs=rate, nperseg=nper, detrend="constant")
    ok = pxx[1:] > 0
    h = np.where(ok, pxy[1:] / np.where(ok, pxx[1:], 1.0), 0)
    coh = np.where(ok & (pyy[1:] > 0), np.abs(pxy[1:]) ** 2 / np.where(ok, pxx[1:] * pyy[1:], 1.0), 0.0)
    return f[1:], h, np.clip(coh.real, 0.0, 1.0)


def find_steps(cal, rate, min_gap=None):
    """[(sample index, step size in counts)] of the jumps of a square-wave calibration signal:
    where it moves by more than 30 % of its range within a tenth of a second."""
    x = np.asarray(cal, dtype=np.float64)
    n = len(x)
    w = max(1, int(rate * 0.05))
    if n < 8 * w:
        return []
    kernel = np.ones(w) / w
    smooth = np.convolve(x, kernel, mode="same")
    diff = smooth[2 * w:] - smooth[:-2 * w]              # change across 2w samples, centred at i + w
    span = float(np.percentile(x, 98) - np.percentile(x, 2))
    if span <= 0:
        return []
    big = np.abs(diff) > 0.3 * span
    steps, i = [], 0
    gap = int(rate * (min_gap if min_gap else 0.5))
    while i < len(big):
        if big[i]:
            j = i
            while j < len(big) and big[j]:
                j += 1
            k = i + int(np.argmax(np.abs(diff[i:j])))
            steps.append((k + w, float(diff[k])))
            i = j + gap
        else:
            i += 1
    return steps


def step_response(cal, out, rate, seconds=None):
    """The sensor output after each step of the calibration signal, turned to one sign and
    averaged: {"t", "response" (counts, starting near 0), "steps" (number), "step" (mean step
    size of the calibration signal in counts), "length" (s)} or None when no step is found."""
    y = np.asarray(out, dtype=np.float64)
    steps = find_steps(cal, rate)
    if not steps:
        return None
    idx = [k for k, _s in steps]
    gaps = np.diff(idx) if len(idx) > 1 else np.array([len(y) - idx[0]])
    length = int(min(float(np.min(gaps)) * 0.9, (seconds or 1e9) * rate, len(y)))
    pre = max(1, int(rate * 0.1))
    stack = []
    for k, size in steps:
        if k - pre < 0 or k + length > len(y) or length < 8:
            continue
        seg = y[k:k + length] - float(np.mean(y[k - pre:k - pre // 2 + 1]))
        stack.append(seg * (1.0 if size > 0 else -1.0))
    if not stack:
        return None
    return {"t": np.arange(length) / rate, "response": np.mean(stack, axis=0), "steps": len(stack),
            "step": float(np.mean([abs(s) for _k, s in steps])), "length": length / rate}


def step_gain(resp):
    """Output counts per calibration count once the output has settled (the last fifth of the
    stacked response) - the measure for a sensor that answers a step with a step
    (an accelerometer)."""
    r = resp["response"]
    tail = r[int(len(r) * 0.8):]
    return float(np.mean(tail)) / resp["step"] if resp["step"] else math.nan


def fit_velocity_step(resp):
    """A velocity sensor answers an acceleration step with a damped swing:
    A * exp(-h w0 t) * sin(w0 sqrt(1 - h²) t) / sqrt(1 - h²). Returns {"period" (s, the natural
    period 2 pi / w0), "damping" (h, fraction of critical), "amplitude", "misfit"} or None."""
    t, r = resp["t"], resp["response"]
    if len(t) < 16:
        return None
    peak = int(np.argmax(np.abs(r)))
    sign = 1.0 if r[peak] >= 0 else -1.0
    t_peak = max(float(t[peak]), float(t[1]))
    w0 = 1.0 / t_peak                        # for h near 0.7 the peak comes at about 1 / w0

    def model(p):
        a, w, h, t0 = p                      # t0: the step may sit a few samples off where it was found
        h = min(max(h, 0.05), 0.999)
        root = math.sqrt(1.0 - h * h)
        tt = np.maximum(t - t0, 0.0)
        return a * np.exp(-h * w * tt) * np.sin(w * root * tt) / root

    start = [sign * float(np.max(np.abs(r))) * 2.0, w0, 0.7, 0.0]
    shift = min(0.25, 0.5 * t_peak)
    try:
        fit = optimize.least_squares(lambda p: model(p) - r, start,
                                     bounds=([-np.inf, w0 / 50, 0.05, -shift], [np.inf, w0 * 50, 0.999, shift]))
    except (ValueError, RuntimeError):
        return None
    a, w, h, _t0 = fit.x
    scale = float(np.max(np.abs(r))) or 1.0
    return {"period": 2 * math.pi / w, "damping": float(h), "amplitude": float(a),
            "misfit": float(np.sqrt(np.mean(fit.fun ** 2))) / scale, "model": model(fit.x)}
