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
import weakref

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
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
        self.order.setSuffix(" th order")
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


class _FreqAxis(pg.AxisItem):
    """Frequency axis of the design graph: "0.01 Hz" with "100 s" under it (log mode)."""

    def __init__(self):
        super().__init__("bottom")
        self.log = True
        self.setHeight(38)

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            f = 10 ** v if self.log else v
            if f <= 0:
                out.append("")
                continue
            hz = f"{f:g} Hz"
            out.append(f"{hz}\n{1 / f:g} s" if self.log else hz)
        return out


class _DesignBox(pg.ViewBox):
    """The design graph's mouse, like the user's earlier viewer: the left button (click or drag)
    sets the high-pass corner, the right button the low-pass corner; a corner line itself can
    be grabbed and dragged with the left button."""

    GRAB_PX = 6

    def __init__(self, dialog):
        super().__init__(enableMenu=False)
        self._dialog = weakref.ref(dialog)
        self._dragging = None            # "hp" / "lp" while a drag moves that corner
        self.setMouseEnabled(x=False, y=False)

    def _freq(self, pos):
        d = self._dialog()
        x = self.mapToView(pos).x()
        return 10 ** x if d is not None and d.log_box.currentData() else x

    def _near_line(self, pos):
        d = self._dialog()
        if d is None:
            return None
        for which, line in (("lp", d.lp_line), ("hp", d.hp_line)):
            if line.isVisible():
                px = self.mapViewToScene(pg.Point(line.value(), 0)).x()
                if abs(self.mapToScene(pos).x() - px) <= self.GRAB_PX:
                    return which
        return None

    def _which(self, ev):
        if ev.button() == Qt.LeftButton:
            return self._near_line(ev.buttonDownPos() if hasattr(ev, "buttonDownPos") else ev.pos()) or "hp"
        if ev.button() == Qt.RightButton:
            return "lp"
        return None

    def mouseClickEvent(self, ev):
        d = self._dialog()
        which = self._which(ev)
        if d is None or which is None:
            return
        ev.accept()
        d.drag_corner(which, self._freq(ev.pos()))

    def mouseDragEvent(self, ev, axis=None):
        d = self._dialog()
        if d is None:
            return
        if ev.isStart():
            self._dragging = self._which(ev)
        if self._dragging is None:
            return
        ev.accept()
        d.drag_corner(self._dragging, self._freq(ev.pos()), final=ev.isFinish())
        if ev.isFinish():
            self._dragging = None

    def wheelEvent(self, ev, axis=None):
        ev.ignore()


class FilterDesignDialog(QDialog):
    """Design a filter for a viewer (its whole window, or one channel). `apply_fn(spec)` gets
    the design (None: no filter) on Apply, OK and - with Auto-apply - every change.

    Laid out like the user's earlier viewer's filter design: the high-pass part on red, the
    low-pass part on green (each: on/off, corner in Hz and in seconds, order), the gain in dB,
    the response graph (left button: high-pass corner, right button: low-pass corner, click or
    drag; the corner lines can also be dragged), Log / Lin and the sample rate below it, and
    Auto-Apply, Save, OK, Cancel, Apply."""

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
        self.resize(820, 580)
        c = theme.COLORS
        self.hp = _CornerRow("Highpass", "#b00000", self._edited)
        self.lp = _CornerRow("Lowpass", "#006400", self._edited)

        def part(row, bg):
            box = QFrame()
            box.setObjectName("designPart")
            box.setStyleSheet(f"QFrame#designPart {{ background: {bg}; border-radius: 4px; }}"
                              "QFrame#designPart QLabel { color: #101010; }")
            g = QGridLayout(box)
            g.setContentsMargins(8, 6, 8, 6)
            g.addWidget(row.on, 0, 0)
            g.addWidget(row.hz, 0, 1)
            g.addWidget(QLabel("Hz"), 0, 2)
            g.addWidget(row.order, 1, 0)
            g.addWidget(row.secs, 1, 1)
            g.addWidget(QLabel("Secs"), 1, 2)
            return box

        mouse = QLabel("Left button\n= high-pass\nRight button\n= low-pass")
        mouse.setAlignment(Qt.AlignCenter)
        mouse.setStyleSheet(f"color: {c['muted']}; font-size: 8pt;")
        self.gain = QDoubleSpinBox()
        self.gain.setRange(-dsp.MAX_GAIN_DB, dsp.MAX_GAIN_DB)
        self.gain.setDecimals(1)
        self.gain.setSuffix(" dB")
        self.gain.setToolTip("Gain of the filtered trace (also when both parts are off)")
        self.gain.valueChanged.connect(lambda _v: self._edited())
        gain_box = QVBoxLayout()
        gain_box.addWidget(QLabel("Gain (dB)"))
        gain_box.addWidget(self.gain)
        top = QHBoxLayout()
        top.addWidget(part(self.hp, "#f4b4b4"), 3)
        top.addWidget(mouse)
        top.addWidget(part(self.lp, "#a8e6a8"), 3)
        top.addLayout(gain_box)

        self.rate_box = QComboBox()
        rates = sorted({float(r) for r in rates if r and r > 0}, reverse=True) or [100.0]
        for r in rates:
            self.rate_box.addItem(f"{r:g} sps", r)
        self.rate_box.setToolTip("The graph's frequency range: up to the Nyquist frequency of this sample rate "
                                 "(display only - every channel is filtered at its own rate)")
        self.rate_box.currentIndexChanged.connect(self._rate_changed)
        self.log_box = QComboBox()
        self.log_box.addItem("Log", True)
        self.log_box.addItem("Lin", False)
        self.log_box.currentIndexChanged.connect(self._set_log)

        self.box = _DesignBox(self)
        self.freq_axis = _FreqAxis()
        self.plot = pg.PlotWidget(background=c["plot_bg"], viewBox=self.box, axisItems={"bottom": self.freq_axis})
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "Magnitude (dB)", color=c["muted"])
        for name in ("left", "bottom"):
            ax = self.plot.getAxis(name)
            ax.setPen(pg.mkPen(c["border"]))
            ax.setTextPen(pg.mkPen(c["muted"]))
            ax.enableAutoSIPrefix(False)
        self.noise_curve = self.plot.plot([], [], pen=pg.mkPen(c["muted"], width=1))
        self.signal_curve = self.plot.plot([], [], pen=pg.mkPen(c["text"], width=1))
        self.curve = self.plot.plot([], [], pen=pg.mkPen("#3b7dff", width=2))
        self.hp_line = pg.InfiniteLine(angle=90, pen=pg.mkPen("#ff3030", width=2))
        self.lp_line = pg.InfiniteLine(angle=90, pen=pg.mkPen("#20c020", width=2))
        self.half_line = pg.InfiniteLine(angle=0, pos=-6.0, pen=pg.mkPen(c["grid"], style=Qt.DotLine))
        for line in (self.hp_line, self.lp_line, self.half_line):
            self.plot.addItem(line)
        self.plot.setLogMode(x=True, y=False)
        self.box.disableAutoRange()
        self.plot.setYRange(FLOOR_DB, 6, padding=0)

        self.message = QLabel()
        self.message.setWordWrap(True)
        hint = QLabel("Click or drag in the graph: left button = high-pass corner (red), right button = low-pass "
                      "corner (green); a corner line can also be grabbed and dragged. Shift+drag over a channel in "
                      "the viewer and choose \"Use as noise / signal in the filter design\" to see its spectrum here.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {c['muted']};")
        self.auto = QCheckBox("Auto-Apply")
        self.auto.setToolTip("Every change is applied to the viewer at once")
        save = QPushButton("Save")
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

        bottom = QHBoxLayout()
        bottom.addWidget(self.log_box)
        bottom.addWidget(self.rate_box)
        bottom.addStretch(1)
        bottom.addWidget(self.auto)
        for b in (save, self.ok_button, cancel, self.apply_button):
            bottom.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.plot, 1)
        lay.addLayout(bottom)
        lay.addWidget(hint)
        lay.addWidget(self.message)

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
        self.gain.blockSignals(True)
        self.gain.setValue(dsp.spec_gain(spec))
        self.gain.blockSignals(False)
        self._changed(apply=False)

    def rate(self):
        return float(self.rate_box.currentData() or 100.0)

    def spec(self):
        """The design (None: both parts off). Not valid (high-pass >= low-pass): False. The
        default band-pass not changed yet: the default itself."""
        if self._default:
            return dict(dsp.DEFAULT_SPEC)
        hp, lp = self.hp.freq(), self.lp.freq()
        gain = round(self.gain.value(), 1)
        if hp is None and lp is None and not gain:
            return None
        if hp is not None and lp is not None and hp >= lp:
            return False
        return dsp.make_spec(hp, lp, self.hp.order.value(), self.lp.order.value(), gain)

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
        self.freq_axis.log = bool(self.log_box.currentData())
        self.plot.setLogMode(x=self.freq_axis.log, y=False)
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

    def drag_corner(self, which, f, final=True):
        """The mouse in the graph (a click, or every step of a drag): that corner follows it,
        kept inside the graph's frequency range."""
        nyq = self.rate() / 2.0
        f = min(max(f, MIN_HZ, nyq / 1e5), nyq * 0.999, MAX_HZ)
        self.click_corner(which, f)

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
