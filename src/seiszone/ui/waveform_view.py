"""WaveformView: the live multi-channel seismogram display.

One plot row per channel, stacked vertically, X axes linked, UTC time axis under the
bottom row, a caption per trace (channel, site, min/max of what is visible). Above the plots a
bar with window length, filter, units, scale, mean removal and Live/Pause.

Speed is the main design constraint (12 channels x 200 sps x 60-minute windows, redrawn four
times a second on an ordinary office PC):

  * plots are built only when the channel list changes, and from light parts (a ViewBox, a
    left and a bottom axis in one shared grid layout) instead of pyqtgraph's PlotItem, which
    costs ~40 ms each (context menu, four axes, a layout pass per plot): twelve channels appear
    in well under half a second; a refresh only calls setData on the existing curves;
  * only the visible time range is read from the hub, and it is reduced with min/max decimation
    (dsp.envelope) to about two points per screen pixel - no peak is ever lost on screen;
  * a trace is recomputed only when the view moved by at least one pixel (in a 60-minute window
    that is every ~2.5 s) or a setting changed, and one refresh spends at most BUDGET seconds
    (FORCED_BUDGET right after a change) on recomputing, round-robin over the traces - filters
    on long windows never freeze the window;
  * nothing is done while the window is hidden or minimized.

Gaps in the data are NaN between the hub's segments, so the line breaks there. Zooming or
panning with the mouse pauses the live view; the Live button (or a double-click) returns.
Every trace keeps at least MIN_ROW pixels: when more channels are shown than fit, the plots
scroll vertically (the time axis is under the last trace).
"""

import logging
import math
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGraphicsGridLayout, QGraphicsWidget, QHBoxLayout,
                               QLabel, QLineEdit, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout,
                               QWidget)

from ..core import dsp
from . import theme

log = logging.getLogger("seiszone")

WINDOWS = [(30, "30 s"), (60, "1 min"), (120, "2 min"), (300, "5 min"), (600, "10 min"), (1800, "30 min"),
           (3600, "60 min")]
UNITS = [("counts", "Counts"), ("physical", "Physical units")]
SCALES = [("auto", "Auto (each trace)"), ("common", "Common (all traces)"), ("fixed", "Fixed ±")]
BUDGET = 0.030          # seconds of recomputing per refresh; the rest waits for the next refresh
FORCED_BUDGET = 0.25    # the same right after a setting / channel change (the user waits for it)
PAUSED_RELOAD = 2.0     # re-read a trace at least this often (backfilled data, paused views)
SENS_TTL = 30.0         # seconds a looked-up StationXML sensitivity is trusted (files may be added)
AXIS_WIDTH = 66         # left axis width: the same on every plot so the traces line up
TIME_AXIS_HEIGHT = 26   # the time axis under the bottom plot
ROW_GAP = 4             # space between two plots (the hidden bottom axis of the upper one)
MIN_ROW = 24            # pixels of a trace at least (more channels than fit: the plots scroll)
UNIT_NAMES = {"M/S": "m/s", "M/S**2": "m/s²", "M/S2": "m/s²", "M/SEC": "m/s", "M": "m", "PA": "Pa",
              "V": "V", "COUNTS": "counts"}


def nearest_window(seconds):
    """The offered window length closest to `seconds` (config may hold any number)."""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        s = 300.0
    return min((w for w, _ in WINDOWS), key=lambda w: abs(w - s))


def fmt_value(v, physical):
    """Compact number for trace captions: counts as integers, physical units in e-notation."""
    if v is None or not math.isfinite(v):
        return "-"
    if physical:
        return f"{v:.3e}"
    return f"{v:.0f}" if abs(v) >= 10 else f"{v:.2f}"


def pre_roll(name):
    """Seconds of data read before the visible start so a filter's edge transient stays
    off screen: three periods of the lowest corner frequency, at most 60 s."""
    spec = dsp.FILTERS.get(name) or {}
    freq = spec.get("freq")
    if not spec.get("type") or freq is None:
        return 0.0
    low = min(freq) if isinstance(freq, (tuple, list)) else freq
    return min(60.0, 3.0 / low) if low > 0 else 0.0


class _TraceLabel(pg.LabelItem):
    """Trace caption pinned to the top-left corner of its plot, on a translucent background so
    the trace under it stays readable."""

    def __init__(self, vb):
        super().__init__("", justify="left")
        self.bg = QColor(0, 0, 0, 0)
        self.setParentItem(vb)
        self.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(4, 2))
        self.setZValue(50)

    def paint(self, p, *args):
        p.fillRect(self.boundingRect(), self.bg)


class _Trace:
    """One channel on screen - its plot parts and what was last computed for it."""

    __slots__ = ("sid", "vb", "left", "bottom", "curve", "label", "t0", "t1", "version", "computed", "lo", "hi",
                 "yrange", "text", "units", "has_sens")

    def __init__(self, vb, left, bottom, curve, label):
        self.vb, self.left, self.bottom, self.curve, self.label = vb, left, bottom, curve, label
        self.reset(None)

    def items(self):
        """The layout items of this row."""
        return self.left, self.vb, self.bottom

    def reset(self, sid):
        """Show another channel in this plot: forget everything computed for the old one."""
        self.sid = sid
        self.t0 = self.t1 = None
        self.version = -1
        self.computed = 0.0
        self.lo = self.hi = None
        self.yrange = None
        self.text = None
        self.units = "counts"
        self.has_sens = True
        self.curve.setData([], [])
        self.label.setText("")


class WaveformView(QWidget):
    rangeChanged = Signal(float, float)

    def __init__(self, engine, config, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        v = config.get("view") or {}
        self._window = nearest_window(v.get("window_seconds", 300))
        self._filter = v.get("filter") if v.get("filter") in dsp.FILTERS else "none"
        self._units = "physical" if v.get("units") == "physical" else "counts"
        self._scale = v.get("scale") if v.get("scale") in dict(SCALES) else "auto"
        try:
            self._fixed = abs(float(v.get("fixed_scale", 1000.0))) or 1000.0
        except (TypeError, ValueError):
            self._fixed = 1000.0
        self._demean = bool(v.get("demean", True))
        self._live = True
        self._anchored = False     # live range ends at the newest (old) data, not at now
        self._sids = []
        self._traces = []
        self._version = 0          # bumped by every setting change: every trace recomputes
        self._rr = 0               # round-robin start when the budget runs out
        self._setting_range = False
        self._range = None         # (t0, t1) while no plot exists
        self._emitted = None
        self._sens = {}            # sid -> (looked up at, (value, units) | None)
        self._built = False
        self._colors = {}          # the theme colours last applied

        pg.setConfigOptions(antialias=False, background=theme.COLORS["plot_bg"],
                            foreground=theme.COLORS["text"])
        self._font = QFont()
        self._font.setPointSize(8)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_bar())
        self.stack = QStackedWidget()
        self.empty = QLabel("No channels shown.\nTick channels in the Channels table to see them here.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.glw = pg.GraphicsView()
        root = QGraphicsWidget()
        self.grid = QGraphicsGridLayout(root)
        self.grid.setContentsMargins(2, 6, 10, 2)
        self.grid.setSpacing(0)
        self.glw.setCentralItem(root)
        self.glw.installEventFilter(self)
        self.glw.scene().sigMouseClicked.connect(self._on_click)
        self.scroll = QScrollArea()          # the plots scroll when they do not fit
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setWidget(self.glw)
        self.scroll.viewport().installEventFilter(self)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.scroll)
        lay.addWidget(self.stack, 1)

        self._reload = QTimer(self)
        self._reload.setSingleShot(True)
        self._reload.setInterval(40)
        self._reload.timeout.connect(self._reload_now)
        self._built = True
        self._apply_colors()

    # ------------------------------------------------------------------------------------
    # building
    # ------------------------------------------------------------------------------------
    @staticmethod
    def _combo(items, current, tip):
        cb = QComboBox()
        for key, text in items:
            cb.addItem(text, key)
        cb.setCurrentIndex(max(0, cb.findData(current)))
        cb.setToolTip(tip)
        return cb

    def _build_bar(self):
        bar = QFrame()
        bar.setObjectName("waveformBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(6)

        def caption(text):
            lab = QLabel(text)
            lab.setObjectName("barCaption")
            return lab

        self.window_box = self._combo([(w, t) for w, t in WINDOWS], self._window,
                                      "Length of the time window shown")
        self.filter_box = self._combo([(k, f["label"]) for k, f in dsp.FILTERS.items()], self._filter,
                                      "Zero-phase Butterworth filter applied to every trace")
        self.units_box = self._combo(UNITS, self._units,
                                     "Counts, or physical units from the station's StationXML response")
        self.scale_box = self._combo(SCALES, self._scale,
                                     "Auto: each trace fills its plot. Common: the same amplitude scale for "
                                     "all traces. Fixed: ± the value given")
        # bound methods only (no lambdas holding self: they would keep the widget alive forever)
        self.window_box.currentIndexChanged.connect(self._set_window)
        self.filter_box.currentIndexChanged.connect(self._set_filter)
        self.units_box.currentIndexChanged.connect(self._set_units)
        self.scale_box.currentIndexChanged.connect(self._set_scale)
        self.fixed_edit = QLineEdit(f"{self._fixed:g}")
        self.fixed_edit.setValidator(QDoubleValidator(1e-12, 1e15, 12, self))
        self.fixed_edit.setFixedWidth(80)
        self.fixed_edit.setToolTip("Half height of every plot, in the units shown")
        self.fixed_edit.editingFinished.connect(self._fixed_edited)
        self.demean_box = QCheckBox("Remove mean")
        self.demean_box.setChecked(self._demean)
        self.demean_box.setToolTip("Subtract the mean of the visible data from each trace")
        self.demean_box.toggled.connect(self._set_demean)
        self.live_button = QToolButton()
        self.live_button.setCheckable(True)
        self.live_button.setChecked(True)
        self.live_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.live_button.toggled.connect(self.set_live)
        self.note = QLabel()
        self.note.setObjectName("barNote")
        self.range_label = QLabel()
        self.range_label.setObjectName("barRange")

        for w in (caption("Window"), self.window_box, caption("Filter"), self.filter_box, caption("Units"),
                  self.units_box, caption("Scale"), self.scale_box, self.fixed_edit, self.demean_box,
                  self.live_button):
            h.addWidget(w)
        h.addSpacing(8)
        h.addWidget(self.note, 1)
        h.addWidget(self.range_label)
        self.fixed_edit.setVisible(self._scale == "fixed")
        self._update_live_button()
        return bar

    def _rebuild(self):
        """Plot rows for the current channel list (called only when that list changes).
        Existing rows are reused for the new channels and only the difference in number is
        added or removed at the end. Trace i uses grid rows 2i (left axis, ViewBox) and 2i+1
        (its time axis: with values under the last trace only, a thin gap under the others -
        where it still draws the vertical grid lines)."""
        n = len(self._sids)
        while len(self._traces) > n:
            for item in self._traces.pop().items():
                self.grid.removeItem(item)
                self.glw.scene().removeItem(item)
        while len(self._traces) < n:
            self._traces.append(self._new_trace(len(self._traces)))
        for i, (tr, sid) in enumerate(zip(self._traces, self._sids)):
            if tr.sid != sid:
                tr.reset(sid)
            last = i == n - 1
            tr.bottom.setStyle(showValues=last, tickLength=5 if last else 0)
            tr.bottom.setHeight(TIME_AXIS_HEIGHT if last else ROW_GAP)
        self.stack.setCurrentWidget(self.scroll if self._traces else self.empty)
        self._color_traces()
        self._size_canvas()
        self._fit_rows()
        self.grid.activate()   # geometry now, not at the next event: the decimation needs the width

    def _new_trace(self, row):
        vb = pg.ViewBox(enableMenu=False)
        vb.setMouseEnabled(x=True, y=False)
        vb.disableAutoRange()
        vb.sigRangeChangedManually.connect(self._on_user_range)
        left = pg.AxisItem("left")
        left.setWidth(AXIS_WIDTH)
        left.setStyle(tickFont=self._font, tickTextOffset=4)
        bottom = pg.DateAxisItem("bottom", utcOffset=0)
        bottom.setStyle(tickFont=self._font, tickTextOffset=3)
        bottom.setGrid(128)
        for axis in (left, bottom):
            axis.enableAutoSIPrefix(False)   # else every range change re-renders an unused HTML label
            axis.linkToView(vb)
        if row == 0:   # the first plot holds the X range; the others follow it
            vb.sigXRangeChanged.connect(self._on_xrange)
        else:
            vb.setXLink(self._traces[0].vb)
        curve = pg.PlotDataItem([], [], connect="finite")
        vb.addItem(curve)
        self.grid.addItem(left, 2 * row, 0)
        self.grid.addItem(vb, 2 * row, 1)
        self.grid.addItem(bottom, 2 * row + 1, 1)
        return _Trace(vb, left, bottom, curve, _TraceLabel(vb))

    def _size_canvas(self, width=None, height=None):
        """The plot canvas fills the scroll area, and is taller when the rows need more room
        (then the vertical scroll bar appears). width/height: the new viewport size."""
        vp = self.scroll.viewport()
        width = vp.width() if width is None else width
        height = vp.height() if height is None else height
        n = len(self._traces)
        _l, top, _r, bottom = self.grid.getContentsMargins()
        need = (n * MIN_ROW + ROW_GAP * max(0, n - 1) + TIME_AXIS_HEIGHT + top + bottom
                + 2 * self.glw.frameWidth()) if n else 0
        w, h = max(1, width), max(1, height, math.ceil(need))
        if (self.glw.width(), self.glw.height()) != (w, h):
            self.glw.setFixedSize(w, h)

    def _fit_rows(self, height=None):
        """Equal data heights for every trace; the time axis and the gaps keep their fixed height.
        `height` is the new viewport height during a resize (the viewport itself is resized
        only after the event filter has seen the event)."""
        n = len(self._traces)
        if not n:
            return
        lay = self.grid
        _l, top, _r, bottom = lay.getContentsMargins()
        avail = (self.glw.viewport().height() if height is None else height) - top - bottom
        each = max(float(MIN_ROW), (avail - TIME_AXIS_HEIGHT - ROW_GAP * (n - 1)) / n)
        for i in range(max(2 * n, lay.rowCount())):   # rows of removed plots give their height back
            if i >= 2 * n:
                h = 0
            elif i % 2 == 0:
                h = each
            else:
                h = TIME_AXIS_HEIGHT if i == 2 * n - 1 else ROW_GAP
            lay.setRowFixedHeight(i, h)

    def _apply_colors(self):
        """Colours from the current theme (called again when the application palette changes)."""
        c = theme.COLORS
        self._colors = dict(c)
        self.glw.setBackground(c["plot_bg"])
        self.empty.setStyleSheet(f"color: {c['muted']}; font-size: 11pt; background: {c['plot_bg']};")
        self.findChild(QFrame, "waveformBar").setStyleSheet(
            f"QFrame#waveformBar {{ background: {c['panel']}; border-bottom: 1px solid {c['border']}; }}"
            f"QLabel#barCaption, QLabel#barRange {{ color: {c['muted']}; }}"
            f"QLabel#barNote {{ color: {c['warn']}; }}")
        self._color_traces()

    def _color_traces(self):
        """Pens and caption colours by trace position (also after a reorder). The data stay."""
        c = theme.COLORS
        bg = QColor(c["plot_bg"])
        bg.setAlpha(200)
        for i, tr in enumerate(self._traces):
            tr.curve.setPen(pg.mkPen(theme.trace_color(i), width=1))
            for ax in (tr.left, tr.bottom):
                ax.setPen(pg.mkPen(c["border"]))
                ax.setTextPen(pg.mkPen(c["muted"]))
            tr.vb.setBorder(pg.mkPen(c["grid"]))
            tr.label.bg = bg
            tr.label.update()
            if tr.t0 is not None:
                self._update_label(tr)

    # ------------------------------------------------------------------------------------
    # public contract
    # ------------------------------------------------------------------------------------
    def set_channels(self, sids):
        """Show these channels (in this order). Plots are rebuilt only if the list changed; the
        time spent building counts against the recompute budget, so the call stays short and
        the traces the budget did not cover follow within the next refreshes."""
        sids = list(dict.fromkeys(s for s in sids if isinstance(s, str) and s))
        if sids == self._sids:
            return
        started = time.perf_counter()
        t0, t1 = self.visible_range()
        self._sids = sids
        self._rebuild()
        self._set_range(*(self._live_range() if self._live else (t0, t1)))
        if self._traces:
            self._update_traces(urgent=True, started=started)

    def channels(self):
        return list(self._sids)

    def refresh(self):
        """Follow live data and recompute traces that are out of date (MainWindow: every 250 ms)."""
        if not self._traces or not self._on_screen():
            return
        if self._live:
            self._set_range(*self._live_range())
        self._update_traces(urgent=False)

    def visible_range(self):
        """(t0, t1) epoch seconds of the time range on screen."""
        if self._traces:
            x0, x1 = self._traces[0].vb.viewRange()[0]
            return float(x0), float(x1)
        return self._range or self._live_range()

    def set_live(self, live):
        live = bool(live)
        changed = live != self._live
        self._live = live
        self._anchored = False
        self._update_live_button()
        if live and changed:
            self._set_range(*self._live_range())
            self._reload_now()

    def is_live(self):
        return self._live

    # ------------------------------------------------------------------------------------
    # settings
    # ------------------------------------------------------------------------------------
    def _save(self):
        self.config.update_section("view", {"window_seconds": self._window, "filter": self._filter,
                                            "units": self._units, "scale": self._scale,
                                            "fixed_scale": self._fixed, "demean": self._demean})

    def _changed(self):
        self._version += 1
        self._save()
        self._reload_now()

    def _set_window(self, _index):
        self._window = int(self.window_box.currentData())
        self._anchored = False
        if self._live:
            self._set_range(*self._live_range())
        else:  # keep the right edge the user chose, change only the length
            _t0, t1 = self.visible_range()
            self._set_range(t1 - self._window, t1)
        self._changed()

    def _set_filter(self, _index):
        name = self.filter_box.currentData()
        self._filter = name if name in dsp.FILTERS else "none"
        self._changed()

    def _set_units(self, _index):
        self._units = self.units_box.currentData()
        self._sens.clear()
        self._changed()

    def _set_scale(self, _index):
        self._scale = scale = self.scale_box.currentData()
        self.fixed_edit.setVisible(scale == "fixed")
        self._changed()

    def _fixed_edited(self):
        try:
            value = abs(float(self.fixed_edit.text()))
        except ValueError:
            value = 0.0
        if value <= 0:
            self.fixed_edit.setText(f"{self._fixed:g}")
            return
        if value != self._fixed:
            self._fixed = value
            self._changed()

    def _set_demean(self, on):
        self._demean = bool(on)
        self._changed()

    def _update_live_button(self):
        b = self.live_button
        b.blockSignals(True)
        b.setChecked(self._live)
        b.blockSignals(False)
        if self._live:
            b.setIcon(theme.icon("live"))
            b.setText("Live")
            b.setToolTip("Following the newest data. Zooming or panning pauses it.")
        else:
            b.setIcon(theme.icon("pause"))
            b.setText("Paused")
            b.setToolTip("Paused - click (or double-click a plot) to follow the newest data again")

    # ------------------------------------------------------------------------------------
    # time range
    # ------------------------------------------------------------------------------------
    def _live_range(self):
        """The window ending now, or at the shown channels' newest sample when that is ahead of
        the PC clock (instrument clock ahead: continuous, no jump when it crosses now). When only
        old data is held (newest sample more than 80 % of the window ago) it ends just after
        that sample, so the traces are never off screen; it follows now again once the data is
        back within half a window (hysteresis: a latency near the threshold never flips it)."""
        now = time.time()
        stats = self.engine.hub.stats_snapshot()
        ends = [stats[s]["last_end"] for s in self._sids if s in stats]
        end = now
        if ends:
            latest = max(ends)
            limit = 0.5 if self._anchored else 0.8
            self._anchored = latest < now - limit * self._window
            end = latest + 0.02 * self._window if self._anchored else max(now, latest)
        return end - self._window, end

    def _set_range(self, t0, t1):
        if not self._traces:
            self._range = (t0, t1)
        else:
            self._setting_range = True
            try:
                self._traces[0].vb.setXRange(t0, t1, padding=0)
            finally:
                self._setting_range = False
        self._range_moved()

    def _range_moved(self):
        t0, t1 = self.visible_range()
        if self._emitted is None or abs(self._emitted[0] - t0) > 1e-3 or abs(self._emitted[1] - t1) > 1e-3:
            self._emitted = (t0, t1)
            self.rangeChanged.emit(t0, t1)
        text = (f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t0))} – "
                f"{time.strftime('%H:%M:%S', time.gmtime(t1))} UTC")
        if self.range_label.text() != text:
            self.range_label.setText(text)

    def _on_user_range(self, *_args):
        """Mouse zoom / pan: stop following live data."""
        if self._live:
            self._live = False
            self._update_live_button()

    def _on_xrange(self, *_args):
        if self._setting_range:
            return
        self._range_moved()
        self._reload.start()  # coalesce the many range changes of one drag

    def _on_click(self, ev):
        if ev.double() and ev.button() == Qt.LeftButton:
            self.set_live(True)

    def _on_screen(self):
        win = self.window()
        return self.isVisible() and not (win is not None and win.isMinimized())

    def _plot_width(self):
        w = self._traces[0].vb.width() if self._traces else 0
        return max(200.0, float(w) or 1000.0)

    # ------------------------------------------------------------------------------------
    # computing the traces
    # ------------------------------------------------------------------------------------
    def _reload_now(self):
        """Recompute what a change made stale, now and with the larger budget."""
        if self._traces:
            self._update_traces(urgent=True)

    def _stale(self, tr, t0, t1, px, now):
        """Settings changed, the view moved by a pixel or more, or the data are getting old."""
        if tr.version != self._version or tr.t0 is None:
            return True
        if abs(t1 - tr.t1) >= px or abs(t0 - tr.t0) >= px:
            return True
        return now - tr.computed >= PAUSED_RELOAD

    def _update_traces(self, urgent, started=None):
        t0, t1 = self.visible_range()
        if t1 <= t0:
            return
        width = self._plot_width()
        px = (t1 - t0) / width
        now = time.monotonic()
        deadline = (started or time.perf_counter()) + (FORCED_BUDGET if urgent else BUDGET)
        n = len(self._traces)
        done = 0
        for k in range(n):
            i = (self._rr + k) % n
            tr = self._traces[i]
            if not self._stale(tr, t0, t1, px, now):
                continue
            if done and time.perf_counter() > deadline:
                self._rr = i   # the next refresh continues here (those traces are still stale)
                break
            self._compute(tr, t0, t1, width, now)
            done += 1
        if done:
            self._apply_y_ranges()
            self._update_note()

    def _sensitivity(self, sid):
        """(value, units) from StationXML, or None. Looked up again after SENS_TTL seconds."""
        responses = getattr(self.engine, "responses", None)
        if responses is None:
            return None
        now = time.monotonic()
        hit = self._sens.get(sid)
        if hit and now - hit[0] < SENS_TTL:
            return hit[1]
        try:
            value = responses.sensitivity(sid)
        except Exception:
            log.exception("StationXML sensitivity of %s failed", sid)
            value = None
        if value is not None and not value[0]:
            value = None
        self._sens[sid] = (now, value)
        return value

    def _compute(self, tr, t0, t1, width, now):
        filt = self._filter
        filtered = dsp.FILTERS[filt]["type"] is not None
        segs = self.engine.hub.window(tr.sid, t0 - pre_roll(filt), t1)
        span = t1 - t0
        points = 2.0 * width
        xs, ys = [], []
        total, count = 0.0, 0
        for start, rate, y in segs:
            if filtered:
                y = dsp.apply(y, rate, filt, demean=self._demean)
            i0 = max(0, int(math.ceil((t0 - start) * rate - 1e-6)))   # drop the filter pre-roll
            if i0 >= len(y):
                continue
            if i0:
                start += i0 / rate
                y = y[i0:]
            x, e = dsp.envelope(start, rate, y, max(4, int(points * len(y) / rate / span)))
            if not filtered and self._demean:
                total += float(np.sum(y))
                count += len(y)
            if xs:   # a gap between hub segments: break the line
                xs.append(np.array([xs[-1][-1]]))
                ys.append(np.array([np.nan]))
            xs.append(x)
            ys.append(e)
        tr.units, tr.has_sens = "counts", True
        if xs:
            x = np.concatenate(xs)
            y = np.concatenate(ys)
            if count:
                y -= total / count
            if self._units == "physical":
                sens = self._sensitivity(tr.sid)
                if sens:
                    y /= sens[0]
                    tr.units = UNIT_NAMES.get(str(sens[1]).upper(), str(sens[1]) or "units")
                else:
                    tr.has_sens = False
            finite = np.isfinite(y)
            tr.lo, tr.hi = (float(np.min(y[finite])), float(np.max(y[finite]))) if finite.any() else (None, None)
            tr.curve.setData(x, y, connect="finite")
        else:
            tr.has_sens = self._units != "physical" or self._sensitivity(tr.sid) is not None
            tr.lo = tr.hi = None
            tr.curve.setData([], [])
        tr.t0, tr.t1, tr.version, tr.computed = t0, t1, self._version, now
        self._update_label(tr)

    def _update_label(self, tr):
        c = theme.COLORS
        i = self._traces.index(tr)
        sta = ".".join(tr.sid.split(".")[:2])
        desc = self.engine.hub.descriptions.get(sta, "")
        physical = tr.units != "counts"
        if tr.lo is None:
            values = "no data in this time range"
        else:
            values = (f"min {fmt_value(tr.lo, physical)} &nbsp;max {fmt_value(tr.hi, physical)} "
                      f"&nbsp;{tr.units}")
        text = (f"<span style='color:{theme.trace_color(i)}; font-weight:600'>{tr.sid}</span>"
                + (f" &nbsp;<span style='color:{c['text']}'>{desc}</span>" if desc else "")
                + f" &nbsp;<span style='color:{c['muted']}'>{values}</span>")
        if text != tr.text:
            tr.text = text
            tr.label.setText(text, size="8pt")

    def _apply_y_ranges(self):
        mode = self._scale
        spans = [tr.hi - tr.lo for tr in self._traces if tr.lo is not None]
        half_common = max(spans) / 2 if spans else 1.0
        # only these filters take the mean out (a low-pass keeps it when 'Remove mean' is off)
        centred = self._demean or dsp.FILTERS[self._filter]["type"] in ("highpass", "bandpass")
        for tr in self._traces:
            if tr.lo is None:
                lo, hi = -1.0, 1.0
            elif mode == "fixed":
                mid = 0.0 if centred else (tr.lo + tr.hi) / 2
                lo, hi = mid - self._fixed, mid + self._fixed
            elif mode == "common":
                mid = (tr.lo + tr.hi) / 2
                lo, hi = mid - half_common, mid + half_common
            else:
                lo, hi = tr.lo, tr.hi
            if hi - lo <= 0:   # a flat trace: give it some height around its value
                d = max(abs(lo) * 1e-3, 1e-12 if self._units == "physical" else 1.0)
                lo, hi = lo - d, hi + d
            if (lo, hi) != tr.yrange:
                tr.yrange = (lo, hi)
                tr.vb.setYRange(lo, hi, padding=0 if mode == "fixed" else 0.06)

    def _update_note(self):
        if self._units != "physical":
            text = ""
        elif getattr(self.engine, "responses", None) is None:
            text = "No StationXML responses available - shown in counts"
        else:
            missing = [tr.sid for tr in self._traces if not tr.has_sens]
            text = ""
            if missing:
                more = f" and {len(missing) - 3} more" if len(missing) > 3 else ""
                text = f"No StationXML response for {', '.join(missing[:3])}{more} - shown in counts"
        if self.note.text() != text:
            self.note.setText(text)
            self.note.setToolTip(text)

    # ------------------------------------------------------------------------------------
    # Qt events
    # ------------------------------------------------------------------------------------
    def eventFilter(self, obj, ev):
        if obj is self.glw and ev.type() == QEvent.Resize:
            self._fit_rows(ev.size().height() - 2 * self.glw.frameWidth())
            self._version += 1   # more / fewer pixels: decimate again
        elif obj is self.scroll.viewport() and ev.type() == QEvent.Resize:
            self._size_canvas(ev.size().width(), ev.size().height())
        return super().eventFilter(obj, ev)

    def changeEvent(self, ev):
        super().changeEvent(ev)
        # Qt also sends palette events when a child is first shown: recolour only on a theme change
        if (self._built and ev.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange)
                and theme.COLORS != self._colors):
            self._apply_colors()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._traces:
            QTimer.singleShot(0, self.refresh)
