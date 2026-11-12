"""SpectrumDialog: log-log amplitude spectrum (Welch, dsp.spectrum) of a time window, one curve
per channel.

Welch's method needs evenly spaced samples, so a channel with gaps in the window is analysed on
its longest gap-free part; the caption says so. Units are counts / sqrt(Hz), or physical units
when the station's StationXML gives a sensitivity (the dialog offers the same choice as the
viewer windows). The window is fixed when the dialog opens - it does not follow live data.
"""

import logging
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout

from ..core import dsp
from . import theme
from .waveform_view import UNIT_NAMES

log = logging.getLogger("seiszone")


def _alpha(color, alpha):
    q = QColor(color)
    q.setAlpha(alpha)
    return q


def longest_segment(segments):
    """(start, rate, samples) of the longest gap-free piece, or None."""
    return max(segments, key=lambda s: len(s[2]), default=None)


class SpectrumDialog(QDialog):
    def __init__(self, engine, sids, t0, t1, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.sids = list(sids)
        self.t0, self.t1 = float(t0), float(t1)
        self.curves = {}      # sid -> PlotDataItem
        self.notes = []
        self.setWindowTitle("Spectrum")
        self.resize(900, 600)

        c = theme.COLORS
        span = self.t1 - self.t0
        head = QLabel(f"{len(self.sids)} channel{'s' if len(self.sids) != 1 else ''} - "
                      f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(self.t0))} to "
                      f"{time.strftime('%H:%M:%S', time.gmtime(self.t1))} UTC ({span:.0f} s)")
        head.setStyleSheet("font-weight: 600;")
        self.units_box = QComboBox()
        self.units_box.addItem("Counts", "counts")
        self.units_box.addItem("Physical units", "physical")
        self.units_box.setToolTip("Physical units need the station's StationXML response")
        self.units_box.currentIndexChanged.connect(self._units_changed)
        top = QHBoxLayout()
        top.addWidget(head, 1)
        top.addWidget(QLabel("Units"))
        top.addWidget(self.units_box)

        self.plot = pg.PlotWidget(background=c["plot_bg"])
        self.plot.setLogMode(x=True, y=True)
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setMenuEnabled(False)
        self.plot.setLabel("bottom", "Frequency", units="Hz", color=c["muted"])
        for name in ("left", "bottom"):
            ax = self.plot.getAxis(name)
            ax.setPen(pg.mkPen(c["border"]))
            ax.setTextPen(pg.mkPen(c["muted"]))
            ax.enableAutoSIPrefix(False)
        self.legend = self.plot.addLegend(offset=(-10, 10), labelTextColor=c["text"],
                                          brush=pg.mkBrush(_alpha(c["panel"], 210)),
                                          pen=pg.mkPen(c["border"]))
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {c['muted']};")
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.plot, 1)
        lay.addWidget(self.note)
        lay.addWidget(buttons)
        self.compute()

    def _units_changed(self, _index):
        # a bound method, not a lambda: a lambda holding self would keep the dialog alive
        self.compute()

    def compute(self):
        """(Re)draw every channel's spectrum in the current units."""
        physical = self.units_box.currentData() == "physical"
        responses = getattr(self.engine, "responses", None)
        self.plot.clear()
        self.legend.clear()
        self.curves = {}
        notes = []
        units = set()
        for i, sid in enumerate(self.sids):
            seg = longest_segment(self.engine.hub.window(sid, self.t0, self.t1))
            if seg is None:
                notes.append(f"{sid}: no data in this time range")
                continue
            start, rate, y = seg
            y = y[:max(0, int(np.ceil((self.t1 - start) * rate)))]   # the hub adds one sample past t1
            freq, amp = dsp.spectrum(y, rate)
            if not len(freq):
                notes.append(f"{sid}: too few samples for a spectrum")
                continue
            unit = "counts"
            if physical:
                sens = None
                if responses is not None:
                    try:
                        sens = responses.sensitivity(sid, start)
                    except Exception:
                        log.exception("StationXML sensitivity of %s failed", sid)
                if sens and sens[0]:
                    amp = amp / abs(sens[0])
                    unit = UNIT_NAMES.get(str(sens[1]).upper(), str(sens[1]) or "units")
                else:
                    notes.append(f"{sid}: no StationXML response - shown in counts")
            units.add(unit)
            seconds = len(y) / rate
            if seconds < (self.t1 - self.t0) * 0.98:
                notes.append(f"{sid}: gaps in the window - longest gap-free part used ({seconds:.0f} s)")
            good = (freq > 0) & (amp > 0) & np.isfinite(amp)
            curve = self.plot.plot(freq[good], amp[good], pen=pg.mkPen(theme.trace_color(i, sid), width=1.2),
                                   name=sid)
            self.curves[sid] = curve
        label = " or ".join(sorted(units)) if units else "counts"
        self.plot.setLabel("left", f"Amplitude ({label} / √Hz)", color=theme.COLORS["muted"])
        self.notes = notes
        self.note.setText("\n".join(notes) if notes else
                          "Welch amplitude spectral density, mean removed, no filter applied.")

