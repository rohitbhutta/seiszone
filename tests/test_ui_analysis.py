"""The selection's analysis windows (ui.analysis_dialogs) on simulated channels with known
answers: strong-motion analysis and the sinewave, step and broadband-noise calibration checks.
Offscreen; nothing touches the network."""

import math
import os
import sys
import time

import numpy as np
import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from scipy import signal

from seiszone.config import Config
from seiszone.core import mseed
from seiszone.core.hub import DataHub
from seiszone.ui import theme
from seiszone.ui.analysis_dialogs import CalibrationCheckDialog, StrongMotionDialog, guess_cal_channel
from seiszone.ui.viewer_window import ViewerWindow

if sys.platform == "win32" and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

RATE = 100.0
SECONDS = 120
OUT, CAL = "XA.CALT.00.HNZ", "XA.CALT.00.HNX"
SENS = 400000.0          # counts per m/s² of the simulated accelerometer
VPC_UV = 2.0             # microvolts per count of the calibration channel
COIL = 0.5               # so 1 V of calibration signal = 2 m/s²
_APP = []


class Engine:
    pass


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    _APP.append(app)
    theme.apply(app, "dark")
    app.setFont(QFont("Segoe UI", 9))
    return app


def make_viewer(tmp_path, cal_counts, out_counts):
    """A viewer on a hub holding the two channels, ending now; returns (window, t0, t1)."""
    eng = Engine()
    eng.config = Config(str(tmp_path / "config.json"))
    eng.responses = None
    eng.hub = DataHub(buffer_seconds=600)
    now = time.time()
    for sid, y in ((OUT, out_counts), (CAL, cal_counts)):
        net, sta, loc, cha = sid.split(".")
        for rec in mseed.encode_records(net, sta, loc, cha, RATE, now - SECONDS - 5, np.round(y).astype(int)):
            eng.hub.add_record(rec, "test")
    eng.config.set_value("calibration", {OUT: {"sensitivity": SENS, "units": "M/S**2"}})
    w = ViewerWindow(eng, eng.config, {"sids": [OUT, CAL]})
    return w, now - SECONDS - 4, now - 6


def close(qapp, w):
    w.quitting = True
    w.close()
    w.deleteLater()
    qapp.processEvents()


def set_values(d):
    d.vpc.setText(f"{VPC_UV:g}")
    d.res.setText("1")
    d.coil.setText(f"{COIL:g}")
    d.run()


def acceleration_counts(amplitude_volts, shape):
    """(calibration counts, the m/s² it puts on the mass) for a signal shape of unit size."""
    cal = amplitude_volts / (VPC_UV * 1e-6) * shape
    return cal, cal * VPC_UV * 1e-6 / COIL


def test_sinewave_calibration_window(qapp, tmp_path):
    t = np.arange(int((SECONDS + 5) * RATE)) / RATE
    cal, acc = acceleration_counts(0.5, np.sin(2 * math.pi * 1.0 * t))            # 1 m/s² at 1 Hz
    out = SENS * 1.02 * np.interp(t - 0.01, t, acc)                                # 2 % high, 10 ms late
    w, t0, t1 = make_viewer(tmp_path, cal, out)
    try:
        assert guess_cal_channel([OUT, CAL], OUT) == CAL
        m = w.build_selection_menu(t0, t1, OUT)
        texts = [a.text() for a in m.actions()]
        for name in ("Strong-motion analysis...", "Sinewave Calibration...", "Step Calibration...",
                     "Broadband Noise Calibration..."):
            assert name in texts
        d = w.open_analysis("sine", t0, t1, OUT)
        assert isinstance(d, CalibrationCheckDialog) and d.cal_box.currentText() == CAL
        assert "relative" in d.text.toPlainText()                    # no calibration values yet
        set_values(d)
        r = d.result
        assert r["freq"] == pytest.approx(1.0, abs=1e-3) and r["input"] == pytest.approx(1.0, rel=0.01)
        assert r["sensitivity"] == pytest.approx(SENS * 1.02, rel=0.005)
        assert r["phase"] == pytest.approx(-3.6, abs=0.3)             # 10 ms at 1 Hz
        text = d.text.toPlainText()
        assert "measured is +2.0" in text and "lags by" in text and "relative" not in text
        assert w.config.get("caltool")["XA.CALT"]["coil"] == COIL     # kept for the station
        d.vpc.setText("x")
        d.run()
        assert "Type numbers" in d.text.toPlainText()
        d.vpc.setText(f"{VPC_UV:g}")
        d.cal_box.setCurrentText(OUT)
        d.run()
        assert "two different channels" in d.text.toPlainText()
        d.close()
        # one channel only: said in the status bar
        w.view.set_channels([OUT])
        assert w.open_analysis("sine", t0, t1, OUT) is None and "two channels" in w.statusBar().currentMessage()
    finally:
        close(qapp, w)


def test_step_calibration_window(qapp, tmp_path):
    t = np.arange(int((SECONDS + 5) * RATE)) / RATE
    cal, acc = acceleration_counts(0.25, np.sign(np.sin(2 * math.pi * t / 20.0 + 0.3)))   # +-0.5 m/s² every 10 s
    b, a = signal.butter(2, 20.0, fs=RATE)
    w, t0, t1 = make_viewer(tmp_path, cal, signal.lfilter(b, a, SENS * 0.97 * acc))
    try:
        d = w.open_analysis("step", t0, t1, OUT)
        set_values(d)
        text = d.text.toPlainText()
        assert d.result["steps"] >= 8 and "measured sensitivity" in text and "measured is -3.0" in text
        # the same record read as a velocity sensor's: a period and a damping are fitted
        d.type_box.setCurrentIndex(d.type_box.findData("velocity"))
        d.run()
        assert "natural period" in d.text.toPlainText() or "could not be fitted" in d.text.toPlainText()
        d.close()
    finally:
        close(qapp, w)
    # a sine has no steps
    w, t0, t1 = make_viewer(tmp_path / "b", np.sin(t) * 1000, np.sin(t) * 1000)
    try:
        d = w.open_analysis("step", t0, t1, OUT)
        assert "No steps found" in d.text.toPlainText()
        d.close()
    finally:
        close(qapp, w)


def test_broadband_noise_calibration_window(qapp, tmp_path):
    rng = np.random.default_rng(4)
    n = int((SECONDS + 5) * RATE)
    cal, acc = acceleration_counts(0.1, rng.normal(0, 1, n))
    b, a = signal.butter(2, 20.0, fs=RATE)
    w, t0, t1 = make_viewer(tmp_path, cal, signal.lfilter(b, a, SENS * acc))
    try:
        d = w.open_analysis("noise", t0, t1, OUT)
        set_values(d)
        f, h, coh = d.result
        band = (f > 1) & (f < 10)
        assert coh[band].min() > 0.95
        text = d.text.toPlainText()
        assert "usable band" in text and "counts per m/s²" in text
        gain = float(text.split("gain in that band")[1].split()[0])
        assert gain == pytest.approx(SENS, rel=0.1)                    # (the low-pass pulls the median a little)
        d.cal_box.setCurrentText(OUT)
        d.run()
        assert "two different channels" in d.text.toPlainText()
        d.close()
    finally:
        close(qapp, w)


def test_strong_motion_window(qapp, tmp_path):
    t = np.arange(int((SECONDS + 5) * RATE)) / RATE
    acc = 1.5 * np.sin(2 * math.pi * 2.0 * t) * np.exp(-((t - 60) / 12.0) ** 2)     # a 2 Hz wave packet
    w, t0, t1 = make_viewer(tmp_path, np.zeros(len(t)), SENS * acc)
    try:
        d = w.open_analysis("strong", t0, t1, OUT)
        assert isinstance(d, StrongMotionDialog)
        text = d.text.toPlainText()
        assert OUT in text and "Arias intensity" in text
        assert f"{CAL}: no sensitivity" in text                         # the other channel cannot be used
        pga = float(text.split("PGA")[1].split()[0])
        assert pga == pytest.approx(1.5, rel=0.03)
        assert list(d.data) == [OUT] and d.channel.currentText() == OUT
        x, y, xl, yl, logs = d.curve_data()                             # PSA at 5 %: its peak near 0.5 s
        assert xl == "Period (s)" and logs and x[int(np.argmax(y))] == pytest.approx(0.5, rel=0.1)
        d.graph.setCurrentIndex(d.graph.findData("vel"))
        x, y, xl, yl, logs = d.curve_data()
        assert yl == "Velocity (m/s)" and not logs
        assert np.max(np.abs(y)) == pytest.approx(1.5 / (2 * math.pi * 2.0), rel=0.05)
        assert not d.damping.isEnabled()
        d.graph.setCurrentIndex(d.graph.findData("fft"))
        x, y, _xl, _yl, _logs = d.curve_data()
        assert x[int(np.argmax(y))] == pytest.approx(2.0, abs=0.05)
        d.close()
    finally:
        close(qapp, w)
