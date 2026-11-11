"""Filters of the viewer windows: the list a viewer offers (filter_catalogue), the user's saved
filters (config "filters") and the FilterDesignDialog.

A viewer window has one filter for all its channels (switched on and off with F) and every
channel can have its own instead (or none). The choices are the default band-pass (0.1-0.9 of
the Nyquist frequency of each channel), the ready-made filters of core.dsp, the filters the user
saved, and a design of their own ("custom").

FilterDesignDialog: high-pass and low-pass corner (Hz or seconds) and order, each on or off -
both on is a band-pass. The graph shows the response as applied (forwards and backwards, so
zero phase: -6 dB at a corner) for a chosen sample rate; a left click in the graph sets the
high-pass corner, a right click the low-pass corner. The amplitude spectra of up to two time
ranges selected in the viewer (Shift+drag: "noise" and "signal") are drawn under it, each scaled
to its own peak, to place the corners between them. Apply / OK hand the spec to the viewer;
Auto-apply does it at every change. Save keeps the design under a name.
"""

import logging
import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout)

from ..core import dsp
from . import theme

log = logging.getLogger("seiszone")

SAVED_PREFIX = "saved:"
MIN_HZ, MAX_HZ = 0.0001, 1000.0
FLOOR_DB = -100.0      # the bottom of the response graph


# ------------------------------------------------------------------------------------------
# the filter list
# ------------------------------------------------------------------------------------------
def saved_filters(config):
    """[{"name", "spec"}] the user saved (junk entries left out)."""
    out = []
    for f in config.get("filters") or []:
        if isinstance(f, dict) and isinstance(f.get("name"), str) and f["name"].strip():
            spec = dsp.normalize_spec(f.get("spec"))
            if spec is not None and not spec.get("default"):
                out.append({"name": f["name"].strip(), "spec": spec})
    return out


def save_filter(config, name, spec):
    """Keep a design under a name (an existing one of that name is replaced)."""
    name = name.strip()
    spec = dsp.normalize_spec(spec)
    if not name or spec is None:
        return False
    keep = [f for f in saved_filters(config) if f["name"] != name]
    config.set_value("filters", keep + [{"name": name, "spec": spec}])
    return True


def delete_filter(config, name):
    config.set_value("filters", [f for f in saved_filters(config) if f["name"] != name])


def filter_catalogue(config):
    """[(key, label, spec)]: the default band-pass, the ready-made filters, the saved ones."""
    items = [("default", "Default band-pass (0.1-0.9 x Nyquist)", dict(dsp.DEFAULT_SPEC))]
    items += [(k, f["label"], dsp.spec_of(k)) for k, f in dsp.FILTERS.items() if f["type"]]
    items += [(SAVED_PREFIX + f["name"], f["name"], f["spec"]) for f in saved_filters(config)]
    return items


def filter_spec(config, key, custom=None):
    """The spec of a catalogue key ("custom": the given design), or None (no filter)."""
    if key == "custom":
        return dsp.normalize_spec(custom)
    for k, _label, spec in filter_catalogue(config):
        if k == key:
            return spec
    return None


def filter_label(config, key, custom=None):
    """Short text for a key: the saved filter's name, the ready-made filter's label, or the
    design itself ("Band-pass 1-10 Hz")."""
    if key == "custom":
        return dsp.describe(custom)
    if key == "default":
        return "Default band-pass"
    for k, label, _spec in filter_catalogue(config):
        if k == key:
            return label
    return "No filter"


# ------------------------------------------------------------------------------------------
# the design dialog
# ------------------------------------------------------------------------------------------
class _CornerRow:
    """One filter part: on/off, corner in Hz and in seconds (kept in step), order. The corner
    is kept at full precision from the box last typed in (a corner typed in seconds is not
    rounded to the Hz box's decimals)."""

    def __init__(self, title, color, on_change):
        self.on = QCheckBox(title)
        self.on.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: 600; }}")
        self.hz = QDoubleSpinBox()
        self.hz.setDecimals(5)
        self.hz.setRange(MIN_HZ, MAX_HZ)
        self.hz.setSuffix(" Hz")
        self.secs = QDoubleSpinBox()
        self.secs.setDecimals(3)
        self.secs.setRange(1.0 / MAX_HZ, 1.0 / MIN_HZ)
        self.secs.setSuffix(" s")
        self.order = QSpinBox()
        self.order.setRange(1, dsp.MAX_ORDER)
        self.order.setPrefix("order ")
        self._busy = False
        self._freq = 1.0
        self._on_change = on_change
        self.on.toggled.connect(self._changed)
        self.hz.valueChanged.connect(self._hz_changed)
        self.secs.valueChanged.connect(self._secs_changed)
        self.order.valueChanged.connect(self._changed)

    def widgets(self):
        return self.on, self.hz, self.secs, self.order

    def set(self, freq, order, on):
        self._busy = True
        try:
            self.on.setChecked(on)
            if freq:
                self._freq = min(MAX_HZ, max(MIN_HZ, float(freq)))
                self.hz.setValue(self._freq)
                self.secs.setValue(1.0 / self._freq)
            self.order.setValue(order)
        finally:
            self._busy = False
        self._update_enabled()

    def freq(self):
        return self._freq if self.on.isChecked() else None

    def _hz_changed(self, v):
        if self._busy or not v:
            return
        self._freq = float(v)
        self._busy = True
        self.secs.setValue(1.0 / v)
        self._busy = False
        self._changed()

    def _secs_changed(self, v):
        if self._busy or not v:
            return
        self._freq = 1.0 / float(v)
        self._busy = True
        self.hz.setValue(self._freq)
        self._busy = False
        self._changed()

    def _update_enabled(self):
        for w in (self.hz, self.secs, self.order):
            w.setEnabled(self.on.isChecked())

    def _changed(self, *_args):
        self._update_enabled()
        if not self._busy:
            self._on_change()


class FilterDesignDialog(QDialog):
    """Design a filter for a viewer (its whole window, or one channel). `apply_fn(spec)` gets
    the design (None: no filter) on Apply, OK and - with Auto-apply - every change."""

    applied = Signal(object)
    saved = Signal(str)          # a design was saved under this name (every viewer offers it)

    def __init__(self, config, apply_fn, spec=None, rates=(100.0,), target="all channels", parent=None):
        super().__init__(parent)
        self.config = config
        self.apply_fn = apply_fn
        self.spectra = {}     # "noise" / "signal" -> (freqs, amplitudes)
        self._default = False  # showing the default band-pass, not changed yet: it stays the default
        self.setWindowTitle(f"Filter design - {target}")
        self.setWindowIcon(theme.icon("spectrum"))
        self.resize(760, 560)
        c = theme.COLORS
        self.hp = _CornerRow("High-pass", c["danger"], self._edited)
        self.lp = _CornerRow("Low-pass", c["ok"], self._edited)
        grid = QGridLayout()
        for row, part in enumerate((self.hp, self.lp)):
            for col, w in enumerate(part.widgets()):
                grid.addWidget(w, row, col)
        grid.setColumnStretch(4, 1)

        self.rate_box = QComboBox()
        rates = sorted({float(r) for r in rates if r and r > 0}, reverse=True) or [100.0]
        for r in rates:
            self.rate_box.addItem(f"{r:g} sps", r)
        self.rate_box.setToolTip("The graph's frequency range: up to the Nyquist frequency of this sample rate "
                                 "(display only - every channel is filtered at its own rate)")
        self.rate_box.currentIndexChanged.connect(self._rate_changed)
        self.log_box = QComboBox()
        self.log_box.addItem("Log frequency", True)
        self.log_box.addItem("Linear frequency", False)
        self.log_box.currentIndexChanged.connect(self._set_log)

        self.plot = pg.PlotWidget(background=c["plot_bg"])
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Frequency (Hz)", color=c["muted"])
        self.plot.setLabel("left", "Gain (dB)", color=c["muted"])
        for name in ("left", "bottom"):
            ax = self.plot.getAxis(name)
            ax.setPen(pg.mkPen(c["border"]))
            ax.setTextPen(pg.mkPen(c["muted"]))
            ax.enableAutoSIPrefix(False)
        self.noise_curve = self.plot.plot([], [], pen=pg.mkPen(c["muted"], width=1))
        self.signal_curve = self.plot.plot([], [], pen=pg.mkPen(c["text"], width=1))
        self.curve = self.plot.plot([], [], pen=pg.mkPen(c["accent"], width=2))
        self.hp_line = pg.InfiniteLine(angle=90, pen=pg.mkPen(c["danger"], style=Qt.DashLine))
        self.lp_line = pg.InfiniteLine(angle=90, pen=pg.mkPen(c["ok"], style=Qt.DashLine))
        self.half_line = pg.InfiniteLine(angle=0, pos=-6.0, pen=pg.mkPen(c["grid"], style=Qt.DotLine))
        for line in (self.hp_line, self.lp_line, self.half_line):
            self.plot.addItem(line)
        self.plot.scene().sigMouseClicked.connect(self._clicked)
        self.plot.setLogMode(x=True, y=False)
        self.plot.getPlotItem().vb.disableAutoRange()
        self.plot.setYRange(FLOOR_DB, 6, padding=0)

        self.message = QLabel()
        self.message.setWordWrap(True)
        hint = QLabel("Left click in the graph: high-pass corner. Right click: low-pass corner. "
                      "Shift+drag over a channel in the viewer and choose \"Use as noise / signal in the filter "
                      "design\" to see its spectrum here.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {c['muted']};")
        self.auto = QCheckBox("Auto-apply")
        self.auto.setToolTip("Every change is applied to the viewer at once")
        save = QPushButton("Save...")
        save.setToolTip("Keep this design under a name: it is offered in every viewer's filter list")
        save.clicked.connect(self.save_preset)
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply)
        self.ok_button = QPushButton("OK")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept_design)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        for b in (save, self.apply_button, cancel):
            b.setAutoDefault(False)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Graph for"))
        opts.addWidget(self.rate_box)
        opts.addWidget(self.log_box)
        opts.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.auto)
        buttons.addStretch(1)
        for b in (save, self.apply_button, self.ok_button, cancel):
            buttons.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addLayout(grid)
        lay.addWidget(self.plot, 1)
        lay.addLayout(opts)
        lay.addWidget(hint)
        lay.addWidget(self.message)
        lay.addLayout(buttons)

        self.set_spec(spec)

    # ------------------------------------------------------------------------------------
    def set_spec(self, spec):
        """Show a design. None: both parts off (a 1-10 Hz band to switch on). The default
        band-pass: its corners at the graph's sample rate - and as long as nothing is changed,
        Apply / OK keep the default (each channel's own rate), not these corners."""
        spec = dsp.normalize_spec(spec)
        self._default = bool(spec and spec.get("default"))
        rate = self.rate()
        if spec is None:
            hp, hpo, lp, lpo, hp_on, lp_on = 1.0, dsp.ORDER, min(10.0, 0.4 * rate), dsp.ORDER, False, False
        elif spec.get("default"):
            (hp, lp), hpo, lpo, hp_on, lp_on = dsp.default_band(rate), dsp.ORDER, dsp.ORDER, True, True
        else:
            hp_on, lp_on = spec["hp"] is not None, spec["lp"] is not None
            hp, lp = spec["hp"] or 1.0, spec["lp"] or min(10.0, 0.4 * rate)
            hpo, lpo = spec["hp_order"], spec["lp_order"]
        self.hp.set(hp, hpo, hp_on)
        self.lp.set(lp, lpo, lp_on)
        self._changed(apply=False)

    def rate(self):
        return float(self.rate_box.currentData() or 100.0)

    def spec(self):
        """The design (None: both parts off). Not valid (high-pass >= low-pass): False. The
        default band-pass not changed yet: the default itself."""
        if self._default:
            return dict(dsp.DEFAULT_SPEC)
        hp, lp = self.hp.freq(), self.lp.freq()
        if hp is None and lp is None:
            return None
        if hp is not None and lp is not None and hp >= lp:
            return False
        return dsp.make_spec(hp, lp, self.hp.order.value(), self.lp.order.value())

    def set_spectrum(self, kind, freqs, amps):
        """Show the amplitude spectrum of a selected time range ("noise" or "signal")."""
        self.spectra[kind] = (np.asarray(freqs, dtype=np.float64), np.asarray(amps, dtype=np.float64))
        self._redraw()

    # ------------------------------------------------------------------------------------
    def _edited(self):
        """The user changed a corner, an order or switched a part: a design of their own now."""
        self._default = False
        self._changed()

    def _rate_changed(self, _index):
        if self._default:   # the default's corners at the newly chosen rate
            hp, lp = dsp.default_band(self.rate())
            self.hp.set(hp, dsp.ORDER, True)
            self.lp.set(lp, dsp.ORDER, True)
        self._redraw()

    def _changed(self, apply=True):
        spec = self.spec()
        ok = spec is not False
        self.message.setText("" if ok else "The high-pass corner must be below the low-pass corner.")
        self.message.setStyleSheet(f"color: {theme.COLORS['danger']};")
        self.apply_button.setEnabled(ok)
        self.ok_button.setEnabled(ok)
        self._redraw()
        if apply and ok and self.auto.isChecked():
            self.apply()

    def _set_log(self, _index):
        self.plot.setLogMode(x=bool(self.log_box.currentData()), y=False)
        self._redraw()

    def _redraw(self, *_args):
        rate = self.rate()
        nyq = rate / 2.0
        log_x = bool(self.log_box.currentData())
        lo = nyq / 1e5 if log_x else 0.0
        freqs = np.logspace(math.log10(nyq / 1e5), math.log10(nyq * 0.999), 600) if log_x else \
            np.linspace(nyq / 600, nyq * 0.999, 600)
        spec = self.spec()
        gains = np.maximum(dsp.response(spec or None, rate, freqs), FLOOR_DB - 10)
        self.curve.setData(freqs, gains)
        for line, part in ((self.hp_line, self.hp), (self.lp_line, self.lp)):
            f = part.freq()
            line.setVisible(f is not None)
            if f is not None:
                line.setValue(math.log10(f) if log_x else f)
        for kind, curve in (("noise", self.noise_curve), ("signal", self.signal_curve)):
            f, a = self.spectra.get(kind, (np.zeros(0), np.zeros(0)))
            keep = (f > 0) & (f <= nyq) & np.isfinite(a) & (a > 0)
            if keep.any():
                db = 20.0 * np.log10(a[keep] / np.max(a[keep]))
                curve.setData(f[keep], np.maximum(db, FLOOR_DB + 1))
            else:
                curve.setData([], [])
        if log_x:
            self.plot.setXRange(math.log10(max(lo, 1e-6)), math.log10(nyq), padding=0)
        else:
            self.plot.setXRange(0, nyq, padding=0)

    def _clicked(self, ev):
        vb = self.plot.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        x = vb.mapSceneToView(ev.scenePos()).x()
        f = 10 ** x if self.log_box.currentData() else x
        if not (MIN_HZ <= f <= MAX_HZ):
            return
        if ev.button() == Qt.LeftButton:
            self.click_corner("hp", f)
        elif ev.button() == Qt.RightButton:
            self.click_corner("lp", f)
        ev.accept()

    def click_corner(self, which, f):
        """A click in the graph: set that corner and switch the part on. Clicking beyond the
        other corner moves both (the band keeps its order)."""
        part, other = (self.hp, self.lp) if which == "hp" else (self.lp, self.hp)
        of = other.freq()
        if of is not None and ((which == "hp" and f >= of) or (which == "lp" and f <= of)):
            ratio = (of / part.hz.value()) if part.on.isChecked() and part.hz.value() else 10.0
            ratio = max(ratio, 1.01) if which == "hp" else min(ratio, 1 / 1.01)
            other.set(f * ratio, other.order.value(), True)
        part.set(f, part.order.value(), True)
        self._edited()

    # ------------------------------------------------------------------------------------
    def apply(self):
        spec = self.spec()
        if spec is False:
            return False
        self.apply_fn(spec)
        self.applied.emit(spec)
        return True

    def accept_design(self):
        if self.apply():
            self.accept()

    def save_preset(self):
        spec = self.spec()
        if not spec:
            QMessageBox.information(self, "Save filter", "Switch on the high-pass, the low-pass or both first.")
            return False
        if spec.get("default"):   # saved as the corners shown (a saved filter is a fixed design)
            spec = dsp.make_spec(self.hp.freq(), self.lp.freq())
        name, ok = QInputDialog.getText(self, "Save filter", "Name of this filter:", text=dsp.describe(spec))
        if not ok or not name.strip():
            return False
        save_filter(self.config, name, spec)
        self.saved.emit(name.strip())
        self.message.setStyleSheet(f"color: {theme.COLORS['ok']};")
        self.message.setText(f"Saved as \"{name.strip()}\" - every viewer offers it in its filter list.")
        return True
