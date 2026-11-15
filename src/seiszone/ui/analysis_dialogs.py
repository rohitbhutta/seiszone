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
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from ..core import dsp
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
