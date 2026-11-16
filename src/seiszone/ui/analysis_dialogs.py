"""What a viewer offers for a selected time range (Shift+drag) besides zoom and save:

* PsdDialog - the power spectral density of every channel of the selection, in acceleration
  units ((m/s²)²/Hz in dB) or in velocity units ((m/s)²/Hz in dB). The channel's sensitivity
  (calibration or StationXML) turns counts into its own unit; the other unit follows from the
  frequency (acceleration = velocity x 2*pi*f). A channel without sensitivity is shown in
  counts²/Hz and named in the note.
* peak_report - peak, peak-to-peak and RMS of every channel (mean removed), in counts and in
  physical units; for acceleration channels also the peak in g and the peak velocity (the
  acceleration integrated after a 0.1 Hz high-pass).
* orientation_report - the angle between the horizontal sensors of two stations: the rotation of
  the second station's N / E pair that fits the first station's N best, with the correlation
  reached (both need N and E channels of the same sample rate in the viewer).

All of them read the longest gap-free piece of each channel in the selection, unfiltered.
"""

import math
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from ..core import analysis, dsp
from . import theme
from .spectrum_view import longest_segment

G = 9.80665
ACCEL = {"M/S**2", "M/S2", "M/SEC**2", "M/S/S"}
VELOCITY = {"M/S", "M/SEC"}


def channel_data(view, sid, t0, t1):
    """(rate, samples float, sensitivity or None, unit name upper or "") of a channel's longest
    gap-free piece in [t0, t1] - from the live buffer, else from the recordings read so far."""
    segs = view.engine.hub.window(sid, t0, t1)
    history = getattr(view.engine, "history", None)
    if not segs and history is not None:
        segs, _pending = history.segments(sid, t0, t1)
    seg = longest_segment(segs)
    if seg is None or len(seg[2]) < 2:
        return None
    sens = view._sensitivity(sid)
    return seg[1], np.asarray(seg[2], dtype=np.float64), (float(sens[0]) if sens else None), \
        (str(sens[1]).upper() if sens else "")


def span_text(t0, t1):
    return (f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t0))} - "
            f"{time.strftime('%H:%M:%S', time.gmtime(t1))} UTC ({t1 - t0:.2f} s)")


def psd_curves(view, sids, t0, t1, want):
    """[(sid, freqs, dB, unit text)] and notes; want: "acceleration" or "velocity"."""
    curves, notes = [], []
    for sid in sids:
        d = channel_data(view, sid, t0, t1)
        if d is None:
            notes.append(f"{sid}: no data in the selection")
            continue
        rate, y, sens, unit = d
        f, pxx = dsp.psd(y, rate)
        if not len(f):
            notes.append(f"{sid}: too few samples")
            continue
        if sens is None or unit not in ACCEL | VELOCITY:
            notes.append(f"{sid}: no sensitivity for acceleration or velocity - shown in counts²/Hz")
            curves.append((sid, f, 10 * np.log10(np.maximum(pxx, 1e-300)), "counts²/Hz"))
            continue
        pxx = pxx / sens ** 2
        w2 = (2 * math.pi * f) ** 2
        if want == "acceleration" and unit in VELOCITY:
            pxx = pxx * w2
        elif want == "velocity" and unit in ACCEL:
            pxx = pxx / w2
        text = "(m/s²)²/Hz" if want == "acceleration" else "(m/s)²/Hz"
        curves.append((sid, f, 10 * np.log10(np.maximum(pxx, 1e-300)), text))
    return curves, notes


class PsdDialog(QDialog):
    def __init__(self, view, sids, t0, t1, want="acceleration", parent=None):
        super().__init__(parent)
        self.want = want
        self.setWindowTitle(f"PSD in {want} units")
        self.resize(900, 600)
        c = theme.COLORS
        self.curves, self.notes = psd_curves(view, sids, t0, t1, want)
        head = QLabel(f"{len(self.curves)} channel(s) - {span_text(t0, t1)}")
        head.setStyleSheet("font-weight: 600;")
        self.plot = pg.PlotWidget(background=c["plot_bg"])
        self.plot.setLogMode(x=True, y=False)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setMenuEnabled(False)
        units = sorted({u for _s, _f, _d, u in self.curves}) or ["counts²/Hz"]
        self.plot.setLabel("left", f"Power spectral density (dB rel. 1 {' or '.join(units)})", color=c["muted"])
        self.plot.setLabel("bottom", "Frequency", units="Hz", color=c["muted"])
        for name in ("left", "bottom"):
            ax = self.plot.getAxis(name)
            ax.setPen(pg.mkPen(c["border"]))
            ax.setTextPen(pg.mkPen(c["muted"]))
            ax.enableAutoSIPrefix(False)
        self.plot.addLegend(offset=(-10, 10), labelTextColor=c["text"])
        for i, (sid, f, db, _u) in enumerate(self.curves):
            self.plot.plot(f, db, pen=pg.mkPen(view.trace_color(i, sid), width=1.2), name=sid)
        note = QLabel("\n".join(self.notes))
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {c['muted']};")
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(self.plot, 1)
        lay.addWidget(note)
        lay.addWidget(box)


def peak_report(view, sids, t0, t1):
    lines = [f"Peak values - {span_text(t0, t1)}", ""]
    for sid in sids:
        d = channel_data(view, sid, t0, t1)
        if d is None:
            lines.append(f"{sid}: no data in the selection")
            continue
        rate, y, sens, unit = d
        y = y - y.mean()
        peak, pp, rms = float(np.max(np.abs(y))), float(y.max() - y.min()), float(np.sqrt(np.mean(y * y)))
        lines.append(f"{sid}  ({len(y)} samples at {rate:g} sps)")
        lines.append(f"    counts:   peak {peak:.0f}   peak-to-peak {pp:.0f}   RMS {rms:.1f}")
        if sens:
            name = {"M/S**2": "m/s²", "M/S2": "m/s²", "M/S": "m/s"}.get(unit, unit.lower() or "units")
            lines.append(f"    {name}:   peak {peak / sens:.4e}   peak-to-peak {pp / sens:.4e}   RMS {rms / sens:.4e}")
            if unit in ACCEL:
                acc = dsp.apply_spec(y / sens, rate, dsp.make_spec(hp=0.1), demean=True)
                vel = np.cumsum(acc) / rate
                vel = dsp.apply_spec(vel, rate, dsp.make_spec(hp=0.1), demean=True)
                lines.append(f"    peak acceleration {peak / sens / G:.4e} g   "
                             f"peak velocity {float(np.max(np.abs(vel))) * 100:.4e} cm/s (0.1 Hz high-pass)")
        else:
            lines.append("    (no sensitivity: physical values need StationXML or a Calibration)")
    return "\n".join(lines)


def _horizontals(sids):
    """{station: {"N": sid, "E": sid}} of the stations with both horizontal channels."""
    out = {}
    for sid in sids:
        sta = ".".join(sid.split(".")[:2])
        comp = {"N": "N", "1": "N", "E": "E", "2": "E"}.get(sid[-1:].upper())
        if comp:
            out.setdefault(sta, {}).setdefault(comp, sid)
    return {s: c for s, c in out.items() if len(c) == 2}


def relative_orientation(ref_n, n2, e2):
    """(angle in degrees, correlation): the rotation of the second pair, clockwise from its N
    towards its E, whose combination n2*cos + e2*sin fits ref_n best."""
    a = float(np.dot(ref_n, n2))
    b = float(np.dot(ref_n, e2))
    angle = math.atan2(b, a)
    fit = n2 * math.cos(angle) + e2 * math.sin(angle)
    den = math.sqrt(float(np.dot(ref_n, ref_n)) * float(np.dot(fit, fit)))
    return math.degrees(angle) % 360.0, (float(np.dot(ref_n, fit)) / den if den else 0.0)


def orientation_report(view, sids, t0, t1, ref_sid=None):
    pairs = _horizontals(sids)
    lines = [f"Relative orientation - {span_text(t0, t1)}", ""]
    if len(pairs) < 2:
        return "\n".join(lines + ["Two stations with N and E channels are needed in this viewer "
                                  f"(found: {', '.join(pairs) or 'none'})."])
    ref = ".".join(ref_sid.split(".")[:2]) if ref_sid else None
    ref = ref if ref in pairs else sorted(pairs)[0]
    rn = channel_data(view, pairs[ref]["N"], t0, t1)
    if rn is None:
        return "\n".join(lines + [f"No data of {pairs[ref]['N']} in the selection."])
    lines.append(f"Reference: {ref} (its N channel {pairs[ref]['N']})")
    for sta in sorted(pairs):
        if sta == ref:
            continue
        n2 = channel_data(view, pairs[sta]["N"], t0, t1)
        e2 = channel_data(view, pairs[sta]["E"], t0, t1)
        if n2 is None or e2 is None or n2[0] != rn[0] or e2[0] != rn[0]:
            lines.append(f"{sta}: no data, or another sample rate than the reference")
            continue
        m = min(len(rn[1]), len(n2[1]), len(e2[1]))
        a, b, c = (x[1][:m] - x[1][:m].mean() for x in (rn, n2, e2))
        angle, corr = relative_orientation(a, b, c)
        lines.append(f"{sta}: its sensors fit the reference when turned {angle:.1f}° (N towards E); "
                     f"correlation {corr:.3f}" + ("" if corr >= 0.7 else "  - weak: the result is not reliable"))
    return "\n".join(lines)


class ReportDialog(QDialog):
    """A text result that can be selected and copied."""

    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 420)
        self.text = QPlainTextEdit(text)
        self.text.setReadOnly(True)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.text, 1)
        lay.addWidget(box)


# ------------------------------------------------------------------------------------------
# strong-motion analysis and the calibration checks (core.analysis)
# ------------------------------------------------------------------------------------------
def acceleration_of(view, sid, t0, t1):
    """(rate, acceleration in m/s², note) of a channel in the selection, or (None, None, why).
    A velocity channel is differentiated; a channel without sensitivity cannot be used."""
    d = channel_data(view, sid, t0, t1)
    if d is None:
        return None, None, "no data in the selection"
    rate, y, sens, unit = d
    if sens is None or unit not in ACCEL | VELOCITY:
        return None, None, ("no sensitivity for acceleration or velocity: right-click the channel > "
                            "Calibration... (or add its StationXML)")
    y = (y - y.mean()) / sens
    if unit in VELOCITY:
        return rate, np.gradient(y) * rate, "velocity channel, differentiated"
    return rate, y, ""


def strong_motion_text(sid, m, note=""):
    return "\n".join([
        f"{sid}" + (f"  ({note})" if note else ""),
        f"  record            {m['n']} samples, {m['duration']:.2f} s",
        f"  PGA               {m['pga']:.5g} m/s²   =  {m['pga_g']:.5g} g",
        f"  PGV               {m['pgv'] * 100:.5g} cm/s",
        f"  PGD               {m['pgd'] * 100:.5g} cm",
        f"  RMS acceleration  {m['rms']:.5g} m/s²",
        f"  Arias intensity   {m['arias']:.5g} m/s",
        f"  CAV               {m['cav']:.5g} m/s",
        f"  duration 5-95 %   {m['significant_duration']:.2f} s  (from {m['t5']:.2f} s to {m['t95']:.2f} s)",
        f"  predominant period {m['predominant_period']:.4g} s",
        "  (velocity and displacement: integrated after a 0.1 Hz high-pass)"])


class StrongMotionDialog(QDialog):
    """Strong-motion analysis of the selection: the measures of every acceleration channel and,
    for the channel chosen, its response spectra (PSA, PSV, SD at 2, 5 or 10 % damping), its
    acceleration, velocity and displacement, or its Fourier amplitude spectrum."""

    GRAPHS = [("psa", "Response spectrum: pseudo acceleration (PSA)"), ("psv", "Response spectrum: pseudo velocity"),
              ("sd", "Response spectrum: displacement"), ("acc", "Acceleration"), ("vel", "Velocity"),
              ("dis", "Displacement"), ("fft", "Fourier amplitude spectrum")]

    def __init__(self, view, sids, t0, t1, ref_sid=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Strong-motion analysis")
        self.resize(1000, 680)
        c = theme.COLORS
        self.view = view
        self.data, lines = {}, [f"Strong-motion analysis - {span_text(t0, t1)}", ""]
        for sid in sids:
            rate, acc, note = acceleration_of(view, sid, t0, t1)
            if acc is None:
                lines += [f"{sid}: {note}", ""]
                continue
            m = analysis.measures(acc, rate)
            if m is None:
                lines += [f"{sid}: too few samples", ""]
                continue
            self.data[sid] = (rate, acc)
            lines += [strong_motion_text(sid, m, note), ""]
        self.text = QPlainTextEdit("\n".join(lines))
        self.text.setReadOnly(True)
        self.channel = QComboBox()
        self.channel.addItems(list(self.data))
        if ref_sid in self.data:
            self.channel.setCurrentText(ref_sid)
        self.graph = QComboBox()
        for key, label in self.GRAPHS:
            self.graph.addItem(label, key)
        self.damping = QComboBox()
        for z in (0.02, 0.05, 0.10):
            self.damping.addItem(f"{z * 100:g} % damping", z)
        self.damping.setCurrentIndex(1)
        self.plot = _plot()
        self.curve = self.plot.plot([], [], pen=pg.mkPen(c["accent"], width=1.5))
        for w in (self.channel, self.graph, self.damping):
            w.currentIndexChanged.connect(self.draw)
        top = QHBoxLayout()
        top.addWidget(self.channel)
        top.addWidget(self.graph, 1)
        top.addWidget(self.damping)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.text, 2)
        lay.addLayout(top)
        lay.addWidget(self.plot, 3)
        lay.addWidget(box)
        self.draw()

    def curve_data(self):
        """(x, y, x label, y label, log axes) of the graph chosen, or None."""
        sid = self.channel.currentText()
        if sid not in self.data:
            return None
        rate, acc = self.data[sid]
        kind = self.graph.currentData()
        if kind in ("psa", "psv", "sd"):
            p, sd, psv, psa = analysis.response_spectrum(acc, rate, None, self.damping.currentData())
            y, unit = {"psa": (psa, "PSA (m/s²)"), "psv": (psv, "PSV (m/s)"), "sd": (sd, "SD (m)")}[kind]
            return p, y, "Period (s)", unit, True
        if kind == "fft":
            a = acc - acc.mean()
            f = np.fft.rfftfreq(len(a), 1.0 / rate)[1:]
            return f, np.abs(np.fft.rfft(a))[1:] / rate, "Frequency (Hz)", "Fourier amplitude (m/s)", True
        a, v, d = analysis.integrate(acc, rate)
        y, unit = {"acc": (a, "Acceleration (m/s²)"), "vel": (v, "Velocity (m/s)"), "dis": (d, "Displacement (m)")}[kind]
        return np.arange(len(y)) / rate, y, "Time (s)", unit, False

    def draw(self, *_args):
        self.damping.setEnabled(self.graph.currentData() in ("psa", "psv", "sd"))
        r = self.curve_data()
        if r is None:
            self.curve.setData([], [])
            return
        x, y, xl, yl, logs = r
        c = theme.COLORS
        self.plot.setLogMode(x=logs, y=logs)
        good = (x > 0) & (y > 0) if logs else np.ones(len(x), dtype=bool)
        self.curve.setData(x[good], y[good])
        self.plot.setLabel("bottom", xl, color=c["muted"])
        self.plot.setLabel("left", yl, color=c["muted"])
        self.plot.enableAutoRange()


def _plot():
    c = theme.COLORS
    plot = pg.PlotWidget(background=c["plot_bg"])
    plot.setMenuEnabled(False)
    plot.showGrid(x=True, y=True, alpha=0.3)
    for name in ("left", "bottom"):
        ax = plot.getAxis(name)
        ax.setPen(pg.mkPen(c["border"]))
        ax.setTextPen(pg.mkPen(c["muted"]))
        ax.enableAutoSIPrefix(False)
    return plot


def guess_cal_channel(sids, out_sid=None):
    """The channel that looks like a calibration signal return: a code ending in X or starting
    with C in the output's station, else another channel of that station."""
    sta = ".".join(out_sid.split(".")[:2]) if out_sid else None
    same = [s for s in sids if s != out_sid and (sta is None or ".".join(s.split(".")[:2]) == sta)]
    for s in same:
        cha = s.rsplit(".", 1)[-1].upper()
        if cha.endswith("X") or cha.startswith(("C", "BC", "HC")) or "CAL" in cha:
            return s
    return (same or [s for s in sids if s != out_sid] or [None])[0]


class CalibrationCheckDialog(QDialog):
    """Sinewave, step or broadband-noise calibration of the selection: the sensor's output
    channel compared with the calibration signal's channel (core.analysis). The calibration
    channel's volts per count, the calibration resistor and the coil constant turn the signal
    into the acceleration it puts on the mass; without them (all 1) the results are relative
    (output counts per calibration count). They are kept per station (config "caltool")."""

    TITLES = {"sine": "Sinewave calibration", "step": "Step calibration", "noise": "Broadband noise calibration"}

    def __init__(self, view, sids, t0, t1, kind, ref_sid=None, parent=None):
        super().__init__(parent)
        self.view, self.kind, self.t0, self.t1 = view, kind, t0, t1
        self.result = None
        self.setWindowTitle(self.TITLES[kind])
        self.resize(940, 700)
        self.out_box = QComboBox()
        self.out_box.addItems(sids)
        if ref_sid in sids:
            self.out_box.setCurrentText(ref_sid)
        self.cal_box = QComboBox()
        self.cal_box.addItems(sids)
        guess = guess_cal_channel(sids, self.out_box.currentText())
        if guess:
            self.cal_box.setCurrentText(guess)
        saved = (view.config.get("caltool") or {}).get(self._station(), {})
        self.type_box = QComboBox()
        self.type_box.addItem("Acceleration sensor (flat to acceleration)", "acceleration")
        self.type_box.addItem("Velocity sensor (seismometer)", "velocity")
        self.type_box.setCurrentIndex(max(0, self.type_box.findData(saved.get("type", "acceleration"))))
        self.vpc = QLineEdit(f"{saved.get('uv_per_count', 1.0):g}")
        self.res = QLineEdit(f"{saved.get('resistor', 1.0):g}")
        self.coil = QLineEdit(f"{saved.get('coil', 1.0):g}")
        self.vpc.setToolTip("Microvolts per count of the calibration channel (the digitiser's calibration sheet)")
        self.res.setToolTip("Calibration resistor in ohms (51000 for many seismometers, 1 for accelerometers)")
        self.coil.setToolTip("Coil constant in A/(m/s²) from the sensor's calibration sheet (1 for accelerometers)")
        form = QFormLayout()
        form.addRow("Sensor output channel", self.out_box)
        form.addRow("Calibration signal channel", self.cal_box)
        form.addRow("Sensor type", self.type_box)
        form.addRow("Calibration channel, µV per count", self.vpc)
        form.addRow("Calibration resistor, ohm", self.res)
        form.addRow("Coil constant, A/(m/s²)", self.coil)
        run = QPushButton("Calculate")
        run.setDefault(True)
        run.clicked.connect(self.run)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.plot = _plot()
        self.plot.addLegend(offset=(-10, 10), labelTextColor=theme.COLORS["text"])
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{self.TITLES[kind]} - {span_text(t0, t1)}"))
        lay.addLayout(form)
        lay.addWidget(run)
        lay.addWidget(self.text, 2)
        lay.addWidget(self.plot, 3)
        lay.addWidget(box)
        self.run()

    def _station(self):
        return ".".join(self.out_box.currentText().split(".")[:2])

    def params(self):
        """(volts per count, resistor, coil, velocity sensor?) - None for a field that is no number."""
        try:
            uv, res, coil = (float(w.text().replace(",", ".")) for w in (self.vpc, self.res, self.coil))
        except ValueError:
            return None
        if uv <= 0 or res <= 0 or coil <= 0:
            return None
        return uv * 1e-6, res, coil, self.type_box.currentData() == "velocity"

    def _save(self, vpc, res, coil, velocity):
        tool = dict(self.view.config.get("caltool") or {})
        tool[self._station()] = {"uv_per_count": vpc * 1e6, "resistor": res, "coil": coil,
                                 "type": "velocity" if velocity else "acceleration"}
        self.view.config.set_value("caltool", tool)

    def _pair(self):
        out_sid, cal_sid = self.out_box.currentText(), self.cal_box.currentText()
        if not out_sid or not cal_sid or out_sid == cal_sid:
            return None, "Choose two different channels: the sensor's output and the calibration signal."
        o = channel_data(self.view, out_sid, self.t0, self.t1)
        k = channel_data(self.view, cal_sid, self.t0, self.t1)
        if o is None or k is None:
            return None, "No data of one of the two channels in the selection."
        if o[0] != k[0]:
            return None, f"The two channels have different sample rates ({o[0]:g} and {k[0]:g} sps)."
        n = min(len(o[1]), len(k[1]))
        return (o[0], o[1][:n], k[1][:n], o[2], o[3]), ""

    def _nominal(self, lines, measured, nominal, nominal_unit):
        if nominal:
            lines.append(f"nominal sensitivity  {nominal:.6g} counts per {nominal_unit.lower()}  -  measured is "
                         f"{(measured / nominal - 1) * 100:+.2f} %")

    def run(self):
        self.plot.clear()
        self.plot.setLogMode(x=False, y=False)
        self.result = None
        p = self.params()
        pair, why = self._pair()
        if p is None:
            self.text.setPlainText("Type numbers above 0 for the three calibration values (1 when unknown).")
            return
        if pair is None:
            self.text.setPlainText(why)
            return
        vpc, res, coil, velocity = p
        self._save(vpc, res, coil, velocity)
        rate, out, cal, nominal, nominal_unit = pair
        unit = "m/s" if velocity else "m/s²"
        relative = abs(vpc - 1e-6) < 1e-15 and res == 1.0 and coil == 1.0
        lines = [f"{self.TITLES[self.kind]} - {span_text(self.t0, self.t1)}",
                 f"output {self.out_box.currentText()}, calibration signal {self.cal_box.currentText()}, "
                 f"{len(out)} samples at {rate:g} sps", ""]
        c = theme.COLORS
        if self.kind == "sine":
            r = analysis.sine_calibration(out, cal, rate, vpc, res, coil, velocity)
            self.result = r
            if r is None or "error" in r:
                lines.append((r or {}).get("error", "Too few samples for a sine."))
            else:
                lines += [f"frequency            {r['freq']:.5g} Hz  (period {1 / r['freq']:.5g} s)",
                          f"equivalent input     {r['input']:.5g} {unit} (amplitude)",
                          f"sensor output        {r['output']:.6g} counts (amplitude)",
                          f"measured sensitivity {r['sensitivity']:.6g} counts per {unit}",
                          f"phase                output {'leads' if r['phase'] >= 0 else 'lags'} by "
                          f"{abs(r['phase']):.2f}°",
                          f"misfit               output {r['residual_out'] * 100:.2f} %, calibration signal "
                          f"{r['residual_cal'] * 100:.2f} % (what a pure sine does not explain)"]
                if not relative:
                    self._nominal(lines, r["sensitivity"], nominal, nominal_unit)
                t = np.arange(len(out)) / rate
                show = slice(0, min(len(t), int(rate * 4 / r["freq"]) + 1))
                self.plot.plot(t[show], (out[show] - out.mean()) / (np.max(np.abs(out - out.mean())) or 1),
                               pen=pg.mkPen(c["accent"], width=1.5), name="output (normalised)")
                self.plot.plot(t[show], (cal[show] - cal.mean()) / (np.max(np.abs(cal - cal.mean())) or 1),
                               pen=pg.mkPen(c["accent2"], width=1.5), name="calibration signal (normalised)")
                self.plot.setLabel("bottom", "Time (s)", color=c["muted"])
        elif self.kind == "step":
            resp = analysis.step_response(cal, out, rate)
            self.result = resp
            if resp is None:
                lines.append("No steps found in the calibration signal channel (a square wave is needed).")
            else:
                acc_step = float(analysis.equivalent_acceleration(resp["step"], vpc, res, coil))
                lines += [f"steps stacked        {resp['steps']}  ({resp['length']:.2f} s after each step)",
                          f"step size            {resp['step']:.6g} counts = {acc_step:.5g} m/s²"]
                self.plot.plot(resp["t"], resp["response"], pen=pg.mkPen(c["accent"], width=1.5),
                               name="output, stacked")
                if velocity:
                    fit = analysis.fit_velocity_step(resp)
                    self.result = dict(resp, fit=fit)
                    if fit is None:
                        lines.append("The response could not be fitted.")
                    else:
                        lines += [f"natural period       {fit['period']:.5g} s",
                                  f"damping              {fit['damping']:.4g} of critical",
                                  f"misfit               {fit['misfit'] * 100:.2f} % of the peak"]
                        self.plot.plot(resp["t"], fit["model"], pen=pg.mkPen(c["accent2"], width=1.5),
                                       name="fitted model")
                else:
                    g = analysis.step_gain(resp)
                    lines.append(f"settled output       {g * resp['step']:.6g} counts = {g:.6g} per calibration count")
                    if not relative and acc_step:
                        sens = g * resp["step"] / acc_step
                        lines.append(f"measured sensitivity {sens:.6g} counts per m/s²")
                        self._nominal(lines, sens, nominal, nominal_unit)
                self.plot.setLabel("bottom", "Time after the step (s)", color=c["muted"])
        else:
            f, h, coh = analysis.transfer_function(cal, out, rate)
            self.result = (f, h, coh)
            if not len(f):
                lines.append("Too few samples: select a longer time range.")
            else:
                gain = np.abs(h)
                scale = 1.0 if relative else (res * coil / vpc) * (2 * math.pi * f if velocity else 1.0)
                good = coh > 0.9
                lines.append(f"coherence            {np.median(coh):.3f} median; above 0.9 at "
                             f"{good.mean() * 100:.0f} % of the frequencies")
                if good.any():
                    scaled = gain * scale
                    flat = good & (scaled >= 0.707 * float(np.max(scaled[good])))   # within 3 dB of the top
                    mid = float(np.median(scaled[flat]))
                    lines.append(f"usable band          {f[good].min():.4g} to {f[good].max():.4g} Hz")
                    lines.append(f"flat part (-3 dB)    {f[flat].min():.4g} to {f[flat].max():.4g} Hz")
                    lines.append(f"gain in that band    {mid:.6g} " + ("output counts per calibration count (median)"
                                                                       if relative else f"counts per {unit} (median)"))
                    if not relative:
                        self._nominal(lines, mid, nominal, nominal_unit)
                else:
                    lines.append("The two channels are not related (low coherence): is this the right calibration "
                                 "channel?")
                self.plot.setLogMode(x=True, y=False)
                ref = float(np.median(gain[flat])) if good.any() else (float(np.max(gain)) or 1.0)
                self.plot.plot(f, 20 * np.log10(np.maximum(gain / ref, 1e-12)), pen=pg.mkPen(c["accent"], width=1.5),
                               name="gain (dB, relative to the band's median)")
                self.plot.plot(f, np.degrees(np.angle(h)) / 10.0, pen=pg.mkPen(c["accent2"], width=1),
                               name="phase (degrees / 10)")
                self.plot.plot(f, coh * 10.0 - 10.0, pen=pg.mkPen(c["muted"], width=1), name="coherence (x10 - 10)")
                self.plot.setLabel("bottom", "Frequency (Hz)", color=c["muted"])
        if relative:
            lines += ["", "The three calibration values are all 1: results are relative (per calibration count). "
                          "Enter the values of the calibration sheet for physical units."]
        self.text.setPlainText("\n".join(lines))
