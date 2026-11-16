"""core.analysis against cases with known answers: strong-motion measures and response spectra,
and the three calibration checks on simulated sensors."""

import math

import numpy as np
import pytest
from scipy import signal

from seiszone.core import analysis as an

RATE = 200.0


def test_measures_of_a_known_record():
    t = np.arange(int(40 * RATE)) / RATE
    env = np.where((t > 10) & (t < 20), 1.0, 0.0)             # a 10 s burst of a 2 Hz sine, 1.5 m/s² peak
    a = 1.5 * env * np.cos(2 * math.pi * 2.0 * t) + 0.3       # on an offset a real record has
    m = an.measures(a, RATE)
    assert m["pga"] == pytest.approx(1.5, rel=0.03) and m["pga_g"] == pytest.approx(1.5 / an.G, rel=0.03)
    assert m["pgv"] == pytest.approx(1.5 / (2 * math.pi * 2.0), rel=0.05)          # v = a / (2 pi f)
    assert 0.8 < m["pgd"] / (1.5 / (2 * math.pi * 2.0) ** 2) < 2.3   # (1 - cos): up to twice a / w²
    assert m["arias"] == pytest.approx(math.pi / (2 * an.G) * 1.5 ** 2 / 2 * 10, rel=0.03)
    assert m["significant_duration"] == pytest.approx(9.0, abs=0.3)                # 5 % to 95 % of 10 s
    assert m["t5"] == pytest.approx(10.5, abs=0.2) and m["predominant_period"] == pytest.approx(0.5, rel=0.02)
    assert m["cav"] == pytest.approx(1.5 * 2 / math.pi * 10, rel=0.03)
    assert an.measures(a[:4], RATE) is None


def test_response_spectrum_resonance_and_limits():
    """A long sine: an oscillator tuned to it reaches a0 / (2 z); a much stiffer one follows
    the ground (PSA = a0); a much softer one sees the ground displacement (SD = a0 / w²)."""
    f0, a0, z = 2.0, 1.0, 0.05
    t = np.arange(int(120 * RATE)) / RATE
    a = a0 * np.cos(2 * math.pi * f0 * t) * np.clip(t / 5.0, 0, 1)    # faded in: no jolt at the start
    periods = np.array([0.02, 1 / f0, 10.0])
    p, sd, psv, psa = an.response_spectrum(a, RATE, periods, z)
    assert psa[1] == pytest.approx(a0 / (2 * z), rel=0.03)
    assert psa[0] == pytest.approx(a0, rel=0.05)
    assert 0.9 < sd[2] / (a0 / (2 * math.pi * f0) ** 2) < 2.2   # the ground's own swing, (1 - cos)
    assert np.allclose(psv, sd * 2 * math.pi / p) and np.allclose(psa, psv * 2 * math.pi / p)
    more = an.response_spectrum(a, RATE, periods, 0.10)[3]
    assert more[1] == pytest.approx(a0 / 0.2, rel=0.03)                             # more damping: half
    assert len(an.default_periods(RATE)) == 80 and an.default_periods(RATE)[-1] == pytest.approx(10.0)


def test_sine_fit_and_sine_calibration():
    t = np.arange(int(60 * RATE)) / RATE
    rng = np.random.default_rng(1)
    f = 1.2345
    y = 5000 * np.sin(2 * math.pi * f * t + 0.6) + 1200 + rng.normal(0, 20, len(t))
    fit = an.sine_fit(y, RATE)
    assert fit["freq"] == pytest.approx(f, abs=1e-4) and fit["amp"] == pytest.approx(5000, rel=0.002)
    assert fit["phase"] == pytest.approx(0.6, abs=0.01) and fit["offset"] == pytest.approx(1200, abs=5)
    assert fit["residual"] < 0.01
    # an accelerometer: 1 V of calibration signal = 2 m/s²; sensor 400000 counts per m/s², 20° lag
    vpc = 2.0e-6                                   # volts per count of the calibration channel
    cal = 250000 * np.sin(2 * math.pi * f * t)     # 0.5 V
    acc = an.equivalent_acceleration(cal, vpc, resistor=1.0, coil=0.5)        # 1 m/s² amplitude
    assert np.max(acc) == pytest.approx(1.0, rel=1e-3)
    out = 400000 * 1.0 * np.sin(2 * math.pi * f * t - math.radians(20)) + rng.normal(0, 50, len(t))
    r = an.sine_calibration(out, cal, RATE, vpc, 1.0, 0.5)
    assert r["freq"] == pytest.approx(f, abs=1e-4) and r["sensitivity"] == pytest.approx(400000, rel=0.002)
    assert r["phase"] == pytest.approx(-20.0, abs=0.1) and r["input"] == pytest.approx(1.0, rel=1e-3)
    # the same input into a velocity sensor: its sensitivity is per m/s
    v = an.sine_calibration(out, cal, RATE, vpc, 1.0, 0.5, velocity=True)
    assert v["sensitivity"] == pytest.approx(400000 * 2 * math.pi * f, rel=0.002)
    # two different sines: said so
    other = np.sin(2 * math.pi * 3.0 * t)
    assert "do not carry the same sine" in an.sine_calibration(other, cal, RATE, vpc)["error"]
    assert an.sine_fit(y[:8], RATE) is None


def test_transfer_function_of_a_known_filter():
    """Broadband noise through a known sensor (gain 3, low-pass 20 Hz): the gain, the phase and a
    coherence near 1 come back; an unrelated output has no coherence."""
    rng = np.random.default_rng(2)
    x = rng.normal(0, 1000, int(300 * RATE))
    b, a = signal.butter(2, 20.0, fs=RATE)
    y = 3.0 * signal.lfilter(b, a, x)
    f, h, coh = an.transfer_function(x, y, RATE)
    _w, expect = signal.freqz(b, a, worN=f, fs=RATE)
    band = (f > 1) & (f < 40)
    assert np.allclose(np.abs(h[band]), 3.0 * np.abs(expect[band]), rtol=0.05)
    assert np.allclose(np.angle(h[band]), np.angle(expect[band]), atol=0.05)
    assert coh[band].min() > 0.98
    _f, _h, none = an.transfer_function(x, rng.normal(0, 1000, len(x)), RATE)
    assert none[band].mean() < 0.2
    assert len(an.transfer_function(x[:10], y[:10], RATE)[0]) == 0


def test_step_calibration_of_an_accelerometer_and_a_velocity_sensor():
    n = int(120 * RATE)
    t = np.arange(n) / RATE
    cal = 100000 * np.sign(np.sin(2 * math.pi * t / 20.0 + 0.3))          # a square wave, a step every 10 s
    steps = an.find_steps(cal, RATE)
    assert len(steps) == 12 and all(abs(abs(s) - 200000) < 2000 for _k, s in steps)
    # accelerometer: the output is the input x 2.5, slightly smoothed
    b, a = signal.butter(2, 30.0, fs=RATE)
    out = signal.lfilter(b, a, 2.5 * cal) + 4000
    resp = an.step_response(cal, out, RATE)
    assert resp["steps"] >= 10 and resp["step"] == pytest.approx(200000, rel=0.01)
    assert an.step_gain(resp) == pytest.approx(2.5, rel=0.01)
    # velocity sensor: natural period 2 s, damping 0.7 - its answer to an acceleration step
    w0, h = 2 * math.pi / 2.0, 0.7
    sys = signal.lti([1.0, 0.0], [1.0, 2 * h * w0, w0 * w0])               # s / (s² + 2 h w0 s + w0²)
    _t, vel, _x = signal.lsim(sys, cal, t)
    resp = an.step_response(cal, vel, RATE)
    fit = an.fit_velocity_step(resp)
    assert fit["period"] == pytest.approx(2.0, rel=0.03) and fit["damping"] == pytest.approx(0.7, abs=0.03)
    assert fit["misfit"] < 0.05
    # no steps in a sine
    assert an.step_response(np.sin(t), out, RATE) is None and an.find_steps(np.zeros(100), RATE) == []
