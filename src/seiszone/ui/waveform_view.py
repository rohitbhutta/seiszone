"""WaveformView: the live multi-channel seismogram display inside a viewer window
(ui.viewer_window). A window can hold any channels; any number of viewer windows can be open,
each with its own settings, and the same channel can be in several of them.

One plot row per channel, stacked vertically, X axes linked, UTC time axis under the bottom row,
a caption per trace (channel, site, filter, min/max of what is visible). Above the plots a tool
bar: window length (time zoom), amplitude zoom, filter on/off with its choice, units, scale mode,
mean removal, null offsets, time / amplitude cursors and Live/Pause.

Whole-window settings: time window, filter (switched with F, chosen from filter_dialog's list or
designed), units (counts, physical, physical with acceleration in g), scale mode (each trace
fills its row / one range per instrument / one range for all / fixed ± / per pixel 1:N - the
earlier viewer's zoom: N units per pixel for the whole window, each trace divided by its own
factor), amplitude zoom (x2 steps), mean removal, lane lines, overlay (all traces in one row),
clean display (no row axes, captions, frames or gaps), overlap (a loud trace may draw over its
neighbours), background colour, spectrogram height.
Per-channel settings (the viewer window's right-click menu): colour, scale factor, own filter
(or none), offset (follow the mean removal / fixed: null = the mean now, zero = absolute values),
locked offset (Null offsets leaves it), spectrogram on/off and its contrast.

Mouse: the wheel zooms the amplitude, Shift+wheel steps the window length (both stay live),
Ctrl+wheel scrolls in time and dragging pans (both pause); Shift+drag selects a time range on
the rows from the one where the drag starts to the one under the mouse, Ctrl+drag on exactly two
rows in the order picked (selectionMade; selected_channels(); the selection stays drawn - hatched
over a gap - until clear_selection(), which the viewer calls when its menu closes); a double-click goes
live again. Channels dragged from the Channels table are added (CHANNEL_MIME).

Speed is the main design constraint (12 channels x 200 sps x 60-minute windows, redrawn four
times a second on an ordinary office PC):

  * plots are built only when the channel list or the layout changes, and from light parts (a
    ViewBox, a left and a bottom axis in one shared grid layout) instead of pyqtgraph's
    PlotItem, which costs ~40 ms each; a refresh only calls setData on the existing curves;
  * only the visible time range is read from the hub, and it is reduced with min/max decimation
    (dsp.envelope) to about two points per screen pixel - no peak is ever lost on screen;
  * a trace is recomputed only when the view moved by at least one pixel (in a 60-minute window
    that is every ~2.5 s) or a setting changed, and one refresh spends at most BUDGET seconds
    (FORCED_BUDGET right after a change) on recomputing, round-robin over the traces - filters
    and spectrograms on long windows never freeze the window;
  * nothing is done while the window is hidden or minimized.

Gaps in the data are NaN between the hub's segments, so the line breaks there.

Live and paused: the live right edge follows the data, not the PC clock (see _live_range), so
the newest sample touches the right edge whatever the clock offset. A paused view is shown by an
amber "Paused – back to live" button and follows the newest data again by itself after AUTO_LIVE
seconds without mouse use on the plots (the button or a double-click return at once). A viewer
always starts live.

Every trace keeps at least MIN_ROW pixels: when more channels are shown than fit, the plots
scroll vertically (the time axis is under the last trace).
"""

import collections
import html
import json
import logging
import math
import time
import weakref

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QBrush, QColor, QCursor, QDoubleValidator, QFont, QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGraphicsGridLayout, QGraphicsWidget,
                               QLabel, QLineEdit, QMenu, QScrollArea, QSizePolicy, QStackedWidget, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

from ..core import dsp
from . import theme
from .trace_cache import TraceCache, bin_size
from .filter_dialog import (SAVED_PREFIX, FilterDesignDialog, delete_filter, filter_catalogue, filter_label,
                            filter_spec, saved_filters)

log = logging.getLogger("seiszone")

CHANNEL_MIME = "application/x-seiszone-channels"   # channel ids, one per line (Channels table -> viewer)
WINDOWS = [(10, "10 s"), (30, "30 s"), (60, "1 min"), (120, "2 min"), (300, "5 min"), (600, "10 min"),
           (1800, "30 min"), (3600, "60 min"), (7200, "2 h"), (21600, "6 h"), (43200, "12 h"),
           (86400, "24 h")]   # (the default window of a new viewer, in Settings)
# the time scale, as the user's earlier viewer shows it: pixels per second, stepped with the
# arrows; the window shown is (plot width / scale), from MIN_WINDOW up to one day
PPS_STEPS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500]
MIN_WINDOW = 2.0
MAX_WINDOW = 24 * 3600.0   # 1440 minutes (history.HISTORY_SECONDS)
SUMMARY_PX = 2.0           # seconds per pixel from which a trace is drawn from per-second summaries
UNITS = [("counts", "Counts"), ("physical", "Physical"), ("physical_g", "Physical, g")]
# "common" is shared per instrument (sensor): instruments of different types and gains (an
# accelerometer next to a broadband seismometer) differ by orders of magnitude, so one range for all
# channels showed only the largest instrument and flattened the rest - with a band-pass on it looked
# as if only that instrument was filtered (2026-11-22). "common_all" keeps the old behaviour.
# "pixel" is the zoom of the user's earlier viewer: one value for the whole window, 1:N = N units
# (counts) per pixel of row height, stepped x2; each trace is divided by its own scale factor
# (shown in its label box), which "Scale others to match" sets so every trace has the same height.
SCALES = [("auto", "Auto"), ("common", "Common per instrument"), ("common_all", "Common, all channels"),
          ("fixed", "Fixed ±"), ("pixel", "Per pixel (1:N)")]
CPP_RANGE = (2.0 ** -60, 2.0 ** 50)   # units per pixel of the "pixel" scale
MATCH_FILL = 0.9                      # "Scale others to match": the reference fills this part of its row
SPEC_SIZES = [32, 64, 128, 256]
GAIN_RANGE = (1.0 / 64, 4096.0)   # amplitude zoom of the whole window, in x2 steps
G = 9.80665                      # m/s² per g
ACCEL_UNITS = {"M/S**2", "M/S2", "M/SEC**2", "M/S/S"}
BUDGET = 0.030          # seconds of recomputing per refresh; the rest waits for the next refresh
FORCED_BUDGET = 0.25    # the same right after a setting / channel change (the user waits for it)
PAUSED_RELOAD = 2.0     # re-read a trace at least this often (backfilled data, paused views)
LIVE_STEP_PX = 1.0      # a live view moves when its range is at least this many pixels behind
SENS_TTL = 30.0         # seconds a looked-up sensitivity is trusted (StationXML files may be added)
AXIS_WIDTH = 66         # left axis width: the same on every plot so the traces line up
PANEL_WIDTH = 86        # the label panel left of the axis (station, channel, units)
TIME_AXIS_HEIGHT = 26   # the time axis under the bottom plot
ROW_GAP = 4             # space between two plots (the hidden bottom axis of the upper one)
MIN_ROW = 24            # pixels of a trace at least (more channels than fit: the plots scroll)
LABEL_STEP = 16         # pixels between the stacked captions of overlaid traces
AUTO_LIVE = 120.0       # a paused view follows live data again after this many seconds without mouse use
LAG_MEMORY = 60.0       # seconds an observed arrival lag counts for the live right edge
LAG_LIMIT = 300.0       # larger arrival lags are not followed (old data: the view anchors to it instead)
LAG_GROWTH = 0.5        # seconds per second a growing arrival lag may grow (the live edge never steps back)
WHEEL_NOTCH = 120       # one mouse wheel notch in Qt's angle-delta units (touchpads send parts of it)
SPEC_RANGE_DB = 50.0    # colour range of a spectrogram below its brightest level
SPEC_STEP_DB = 6.0      # one contrast step (+/- keys): the data x2 or /2
UNIT_NAMES = {"M/S": "m/s", "M/S**2": "m/s²", "M/S2": "m/s²", "M/SEC": "m/s", "M": "m", "PA": "Pa",
              "V": "V", "COUNTS": "counts", "G": "g"}
TRACE_DEFAULTS = {"color": None, "scale": 1.0, "filter": None, "custom": None, "offset": None, "locked": False,
                  "spectrogram": False, "contrast": 0, "label": ""}


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


def fmt_gain(g):
    return f"×{g:g}" if g >= 1 else f"×1/{1 / g:g}"


def fmt_cpp(n):
    """The "pixel" zoom as the earlier viewer shows it: 1:16, 1:8192, 1:2.4e-09."""
    if n >= 1 and abs(n - round(n)) < 1e-9:
        return f"1:{round(n)}"
    return f"1:{n:.3g}"


def clamp_cpp(n):
    """A "pixel" zoom: a power of two within CPP_RANGE (None for nonsense)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n) or n <= 0:
        return None
    return min(CPP_RANGE[1], max(CPP_RANGE[0], 2.0 ** round(math.log2(n))))


def fit_cpp(spans, row_px, fill=MATCH_FILL):
    """The smallest power-of-two "pixel" zoom at which a trace `spans` units high (peak to peak,
    factor applied) still fits `fill` of a row of `row_px` pixels."""
    spans = [s for s in spans if s and math.isfinite(s) and s > 0]
    if not spans or row_px <= 0:
        return None
    need = float(np.median(spans)) / (max(1.0, row_px) * fill)
    return min(CPP_RANGE[1], max(CPP_RANGE[0], 2.0 ** math.ceil(math.log2(need))))


def pre_roll(spec, rate=0.0):
    """Seconds read before the visible start so a filter's edge transient stays off screen."""
    return dsp.pre_roll(spec, rate)


def component(sid):
    """Z, N, E, 1, 2, ... (the last letter of the channel code) for sorting."""
    return sid.rsplit(".", 1)[-1][-1:] if sid else ""


def instrument(sid):
    """The sensor a channel belongs to: NET.STA.LOC plus band and instrument code
    ("IS.PAN.0J.BH" for IS.PAN.0J.BHZ) - its components share the "common" amplitude range."""
    if not sid:
        return ""
    head, _dot, cha = sid.rpartition(".")
    return f"{head}.{cha[:2]}" if head else sid


SORTS = {
    "channel": ("Channel code", lambda sid, rate: sid),
    "component": ("Component (Z, N, E, then others)",
                  lambda sid, rate: ("ZNE".find(component(sid)) % 4 if component(sid) in "ZNE" else 3,
                                     component(sid), sid)),
    "station": ("Station, then component", lambda sid, rate: (".".join(sid.split(".")[:2]), sid)),
    "rate": ("Sample rate (highest first)", lambda sid, rate: (-rate, sid)),
}


def spectrogram_lut():
    """Colours of a spectrogram, quiet to loud: deep purple, blue, cyan, green, yellow, red."""
    cmap = pg.ColorMap([0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                       [(26, 5, 51), (44, 63, 209), (24, 192, 216), (76, 208, 76), (242, 225, 76), (232, 64, 42)])
    return cmap.getLookupTable(0.0, 1.0, 256)


class _TraceLabel(pg.LabelItem):
    """Trace caption pinned to the top-left corner of its plot, on a translucent background so
    the trace under it stays readable; the channel under the mouse gets a frame."""

    def __init__(self, vb):
        super().__init__("", justify="left")
        self.bg = QColor(0, 0, 0, 0)
        self.frame = None
        self.setParentItem(vb)
        self.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(4, 2))
        self.setZValue(50)

    def paint(self, p, *args):
        p.fillRect(self.boundingRect(), self.bg)
        if self.frame is not None:
            p.setPen(pg.mkPen(self.frame))
            p.drawRect(self.boundingRect().adjusted(0, 0, -1, -1))


class _LanePanel(QGraphicsWidget):
    """The label panel left of a trace, like the user's earlier viewer: a box in the trace's
    colour with the station, the channel and the units ("g", "m/s²", "counts") - pale, and in
    full colour for the channel under the mouse. In overlay mode the first row's panel shows a
    box for every channel."""

    BOX_LINES = 3

    def __init__(self, font):
        super().__init__()
        self.font = QFont(font)
        self.bold = QFont(font)
        self.bold.setBold(True)
        self.entries = []            # [(lines, colour, active)]
        self.bg = QColor(0, 0, 0)
        self.setMinimumWidth(PANEL_WIDTH)
        self.setMaximumWidth(PANEL_WIDTH)

    def set_entries(self, entries, bg):
        entries = [(tuple(lines), str(color), bool(active)) for lines, color, active in entries]
        bg = QColor(bg)
        if entries != self.entries or bg != self.bg:
            self.entries, self.bg = entries, bg
            self.update()

    def paint(self, p, *args):
        r = self.rect()
        p.fillRect(r, self.bg)
        line_h = 13
        box_h = line_h * self.BOX_LINES + 6
        y = 2.0
        for lines, color, active in self.entries:
            if y >= r.height():
                break
            h = min(box_h, r.height() - y - 1)
            full = QColor(color)
            pale = QColor(int(full.red() * 0.45 + 255 * 0.55), int(full.green() * 0.45 + 255 * 0.55),
                          int(full.blue() * 0.45 + 255 * 0.55))
            box = QRectF(2, y, r.width() - 5, h)
            p.fillRect(box, full if active else pale)
            if active:
                p.setPen(pg.mkPen("#000000", width=2))
                p.drawRect(box)
            p.setPen(pg.mkPen("#000000"))
            for k, text in enumerate(lines[:self.BOX_LINES]):
                if (k + 1) * line_h > h:
                    break
                p.setFont(self.bold if k < 2 else self.font)
                p.drawText(QRectF(5, y + 2 + k * line_h, r.width() - 9, line_h), Qt.AlignLeft | Qt.AlignVCenter,
                           text)
            y += box_h + 3


class _TimeAxis(pg.DateAxisItem):
    """The time axis of a row. Every row shows the same time range (linked X) at the same width,
    so the tick times are worked out once per range and shared by all rows (`shared`, a dict of
    the view): with 12 rows that was 12 times the same calendar arithmetic at each scroll."""

    SHARED_MAX = 64

    def __init__(self, shared, *args, **kw):
        super().__init__(*args, **kw)
        self._shared = shared

    def tickValues(self, minVal, maxVal, size):
        self.setZoomLevelForDensity((maxVal - minVal) / size)   # (tickStrings uses the level)
        key = (minVal, maxVal, size, id(self.zoomLevel), self.minSpacing)
        hit = self._shared.get(key)
        if hit is None:
            hit = self.zoomLevel.tickValues(minVal, maxVal, minSpc=self.minSpacing)
            if len(self._shared) >= self.SHARED_MAX:
                self._shared.clear()
            self._shared[key] = hit
        return hit


class _PlotBox(pg.ViewBox):
    """ViewBox that offers its mouse wheel and drags to the view first (the wheel zooms the
    amplitude, Shift+drag selects). The view is held weakly: no reference cycle through Qt."""

    def __init__(self, view):
        super().__init__(enableMenu=False)
        self._view = weakref.ref(view)

    def wheelEvent(self, ev, axis=None):
        view = self._view()
        if view is not None and view._on_wheel(ev, self):
            return
        super().wheelEvent(ev, axis)

    def mouseDragEvent(self, ev, axis=None):
        view = self._view()
        if view is not None and view._on_drag(ev, self):
            return
        super().mouseDragEvent(ev, axis)


class _Trace:
    """One plot row on screen - its plot parts and what was last computed for the channel in it."""

    __slots__ = ("sid", "vb", "left", "bottom", "curve", "label", "panel", "host", "svb", "saxis", "image", "t0",
                 "t1",
                 "version", "computed", "lo", "hi", "yrange", "text", "units", "has_sens", "stats", "mean_pre",
                 "filter_text", "rate", "nyq", "lines", "tcur", "ykey", "pending", "hist_gen", "unfiltered",
                 "cache")

    def __init__(self, vb, left, bottom, curve, label, panel):
        self.vb, self.left, self.bottom, self.curve, self.label = vb, left, bottom, curve, label
        self.panel = panel
        self.host = vb          # the ViewBox drawing the curve (the first row's in overlay mode)
        self.svb = self.saxis = self.image = None   # spectrogram row, made when first needed
        self.lines = {}         # details lines: "mean" / "min" / "max" -> InfiniteLine
        self.tcur = []          # this row's two time cursor lines
        self.reset(None)

    def items(self):
        """The layout items of this row."""
        return [x for x in (self.panel, self.left, self.vb, self.bottom, self.saxis, self.svb) if x is not None]

    def reset(self, sid):
        """Show another channel in this plot: forget everything computed for the old one."""
        self.sid = sid
        self.t0 = self.t1 = None
        self.version = -1
        self.computed = 0.0
        self.lo = self.hi = None
        self.yrange = None      # the Y range shown (with padding)
        self.ykey = None        # the Y range last asked for (unchanged: nothing to do)
        self.text = None
        self.units = "counts"
        self.has_sens = True
        self.stats = None
        self.mean_pre = None
        self.filter_text = ""
        self.pending = False    # recorded history still being read: computed again when it is there
        self.hist_gen = -1
        self.unfiltered = False  # older data shown without its filter (only the day summary had it)
        self.rate = 0.0
        self.nyq = None
        self.cache = TraceCache()   # filtered samples / decimation kept between refreshes
        self.curve.setData([], [])
        self.label.setText("")
        if self.image is not None:
            self.image.clear()


class WaveformView(QWidget):
    rangeChanged = Signal(float, float)
    changed = Signal()                        # a setting worth saving changed (the viewer saves its state)
    contextRequested = Signal(object, object)   # channel id (or None), global position
    selectionMade = Signal(float, float, object, object)   # t0, t1, channel id (or None), global position
    hoverChanged = Signal(str)                # mouse readout: time, channel, value
    channelsDropped = Signal(list)            # channel ids dropped on the plots (already added)
    channelsButton = Signal()                 # the Channels button: the viewer offers its channel list
    filtersSaved = Signal()                   # a saved filter was added, replaced or deleted (all viewers follow)

    def __init__(self, engine, config, parent=None, state=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        st = state if isinstance(state, dict) else {}
        v = config.get("view") or {}
        self._pps = self._initial_pps(st, v)
        self._filter_on, self._filter_key, self._custom = self._initial_filter(st, v)
        units = st.get("units", v.get("units"))
        self._units = units if units in dict(UNITS) else "counts"
        # a new viewer: one amplitude scale for all its channels (a quiet vertical stays smaller
        # than a noisy horizontal, as it is - each trace filling its row is the "Auto" choice)
        scale = st.get("scale", "common")
        self._scale = scale if scale in dict(SCALES) else "auto"
        try:
            self._fixed = abs(float(st.get("fixed", v.get("fixed_scale", 1000.0)))) or 1000.0
        except (TypeError, ValueError):
            self._fixed = 1000.0
        self._cpp = clamp_cpp(st.get("cpp"))   # "pixel" zoom (None: picked from the data when first used)
        self._clean = bool(st.get("clean", False))       # no row axes, captions, frames or gaps
        self._overlap = bool(st.get("overlap", False))   # a loud trace may draw over its neighbours
        self._row_px = 100.0       # data height of one row in pixels (_fit_rows)
        self._demean = bool(st.get("demean", v.get("demean", True)))
        self._gain = self._clamp_gain(st.get("gain", 1.0))
        self._lanes = bool(st.get("lanes", True))
        self._overlay = bool(st.get("overlay", False))
        self._bg = st.get("background") if QColor.isValidColorName(str(st.get("background") or "")) else None
        self._spec_size = st.get("spec_size") if st.get("spec_size") in SPEC_SIZES else 64
        self._tset = {}            # channel id -> its own settings (only those differing from TRACE_DEFAULTS)
        for sid, ts in (st.get("traces") or {}).items():
            if isinstance(sid, str) and isinstance(ts, dict):
                self._tset[sid] = {k: ts[k] for k in TRACE_DEFAULTS if k in ts}
        self._live = True          # always starts live (never saved)
        self._anchored = False     # live range ends at the newest (old) data, not at now
        self._lags = collections.deque()   # (PC time, arrival lag) of the newest-sample advances
        self._lag = None           # the arrival lag the live edge uses (None: never seen)
        self._lag_at = 0.0         # PC time it was last worked out
        self._relag = False        # another channel set: the next lag measured replaces _lag at once
        self._latest = None        # newest sample end of the shown channels at the last look
        self._latest_at = 0.0      # PC time that value last changed
        self._touched = time.monotonic()   # last mouse use on the plots (a paused view returns AUTO_LIVE later)
        self._wheel = 0            # wheel delta not yet turned into a step
        self._sids = []
        self._traces = []
        self._time_ticks = {}      # tick times per (range, width), shared by the rows' time axes
        self._version = 0          # bumped by every setting change: every trace recomputes
        self._rr = 0               # round-robin start when the budget runs out
        self._setting_range = False
        self._range = None         # (t0, t1) while no plot exists
        self._emitted = None
        self._sens = {}            # sid -> (looked up at, (value, units) | None)
        self._rates = {}           # sid -> sample rate (from the hub statistics)
        self._specs = {}           # filter key cache: (key, custom json) -> spec
        self._active = None        # channel under the mouse
        self._menu_sid = None      # channel of the last right click
        self._hovered = None       # the last channel the mouse was over (kept when it leaves the plots)
        self._selecting = False    # a Shift+drag selection is under way (decided when the drag starts)
        self._tcur = None          # time cursors: [fraction, fraction] of the window, or None
        self._acur = None          # amplitude cursors: [sid, fraction, fraction] of its range, or None
        self._acur_lines = []
        self._moving_cursor = False
        self._sel_items = []       # selection regions while Shift+dragging (and while its menu is open)
        self._sel_sids = []        # the channels they are drawn on
        self._sel_pair = False     # Ctrl+drag: exactly two channels, in the order picked
        self.selection = None      # (t0, t1, [channels]) of the last selection
        self._details = set()      # channels whose Details window shows mean / min / max lines
        self._design = None        # the open filter design dialog
        self._built = False
        self._colors = {}          # the theme colours last applied
        self._lut = spectrogram_lut()

        pg.setConfigOptions(antialias=False, background=theme.COLORS["plot_bg"],
                            foreground=theme.COLORS["text"])
        self._font = QFont()
        self._font.setPointSize(8)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.bar = self._build_bar()
        lay.addWidget(self.bar)
        self.stack = QStackedWidget()
        self.empty = QLabel("No channels in this viewer.\nDrag channels here from the Channels table, or use "
                            "the Channels button.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setAcceptDrops(True)
        self.empty.installEventFilter(self)
        self.glw = pg.GraphicsView()
        root = QGraphicsWidget()
        self.grid = QGraphicsGridLayout(root)
        self.grid.setContentsMargins(2, 6, 10, 2)
        self.grid.setSpacing(0)
        self.glw.setCentralItem(root)
        self.glw.setFocusPolicy(Qt.StrongFocus)
        self.glw.setAcceptDrops(True)
        self.glw.viewport().setAcceptDrops(True)
        self.glw.installEventFilter(self)
        self.glw.viewport().installEventFilter(self)   # mouse use (auto-return to live), drops
        self.glw.scene().sigMouseClicked.connect(self._on_click)
        self.glw.scene().sigMouseMoved.connect(self._on_mouse_moved)
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
        sids = [s for s in (st.get("sids") or []) if isinstance(s, str)]
        if sids:
            self.set_channels(sids)

    @staticmethod
    def _initial_filter(st, v):
        """(on, key, custom) from a saved viewer, or from the old single-view setting."""
        if "filter_on" in st:
            key = st.get("filter") if isinstance(st.get("filter"), str) else "default"
            return bool(st.get("filter_on")), key, dsp.normalize_spec(st.get("custom"))
        old = v.get("filter")
        if old in dsp.FILTERS and old != "none":
            return True, old, None
        return False, "default", None

    @staticmethod
    def _initial_pps(st, v):
        """The time scale of a saved viewer, or the one for the default window length."""
        try:
            pps = float(st.get("pps"))
            if math.isfinite(pps) and pps > 0:
                return pps
        except (TypeError, ValueError):
            pass
        window = nearest_window(st.get("window", v.get("window_seconds", 300)))
        return min(PPS_STEPS, key=lambda p: abs(math.log(p) - math.log(1400.0 / window)))

    @property
    def _window(self):
        """Seconds shown: the plot width at the time scale (pixels per second), at most a day."""
        return min(MAX_WINDOW, max(MIN_WINDOW, self._plot_width() / self._pps))

    @_window.setter
    def _window(self, seconds):
        self._pps = self._plot_width() / max(MIN_WINDOW, min(MAX_WINDOW, float(seconds)))

    def pps(self):
        return self._pps

    @staticmethod
    def _clamp_gain(g):
        try:
            g = float(g)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(g) or g <= 0:
            return 1.0
        return min(GAIN_RANGE[1], max(GAIN_RANGE[0], 2.0 ** round(math.log2(g))))

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
        cb.setFocusPolicy(Qt.NoFocus)   # arrow keys stay with the plots (time / amplitude zoom)
        return cb

    @staticmethod
    def _tool(text, tip, checkable=False):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setFocusPolicy(Qt.NoFocus)
        b.setAutoRaise(True)
        return b

    def _build_bar(self):
        """The tool bar above the traces: a QToolBar, so a narrow window gets its "»" overflow
        button instead of being forced wider than the screen by the bar."""
        bar = QToolBar()
        bar.setObjectName("waveformBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setContentsMargins(4, 1, 4, 1)

        def caption(text):
            lab = QLabel(text)
            lab.setObjectName("barCaption")
            return lab

        self.channels_button = self._tool("Channels", "Choose the channels of this viewer (you can also drag "
                                                      "channels here from the Channels table)")
        self.channels_button.clicked.connect(self.channelsButton)
        self.time_in = self._tool("◂▸", "Expand the traces in time: more pixels per second, a shorter window "
                                        "(Left arrow, Shift+wheel up)")
        self.time_out = self._tool("▸◂", "Squash the traces in time: fewer pixels per second, a longer window - "
                                         "up to 1440 minutes, one day (Right arrow, Shift+wheel down)")
        self.pps_button = self._tool("", "")
        self.pps_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pps_button.clicked.connect(lambda: self.step_window(-1))                 # left click: expand
        self.pps_button.customContextMenuRequested.connect(lambda _p: self.step_window(1))   # right: squash
        self.time_in.clicked.connect(lambda: self.step_window(-1))
        self.time_out.clicked.connect(lambda: self.step_window(1))
        self.amp_out = self._tool("−", "Smaller traces (Down arrow, mouse wheel down)")
        self.amp_in = self._tool("+", "Larger traces (Up arrow, mouse wheel up)")
        self.gain_label = self._tool(fmt_gain(self._gain), "Amplitude zoom of every trace - click to reset it "
                                                           "to ×1 (Home)")
        self.amp_out.clicked.connect(lambda: self.zoom_amplitude(-1))
        self.amp_in.clicked.connect(lambda: self.zoom_amplitude(1))
        self.gain_label.clicked.connect(self.reset_amplitude)

        self.filter_button = self._tool("Filter", "", checkable=True)
        self.filter_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.filter_menu = QMenu(self.filter_button)
        self.filter_menu.aboutToShow.connect(self._fill_filter_menu)
        self.filter_button.setMenu(self.filter_menu)
        self.filter_button.setChecked(self._filter_on)
        self.filter_button.toggled.connect(self.set_filter_on)
        self.units_box = self._combo(UNITS, self._units,
                                     "Counts, or physical units from the station's StationXML response (or the "
                                     "calibration you entered for a channel)")
        self.scale_box = self._combo(SCALES, self._scale,
                                     "Auto: each trace fills its row. Common per instrument: the components of "
                                     "one sensor share one amplitude range, every instrument has its own. Common, "
                                     "all channels: one range for every trace (the largest one sets it). Fixed: ± "
                                     "the value given. Per pixel (1:N): one zoom for the whole window, N counts "
                                     "per pixel, each trace divided by its own scale factor (Scale others to "
                                     "match sets the factors). The amplitude zoom applies to all of them")
        # bound methods only (no lambdas holding self: they would keep the widget alive forever)
        self.units_box.currentIndexChanged.connect(self._set_units)
        self.scale_box.currentIndexChanged.connect(self._set_scale)
        self.fixed_edit = QLineEdit(f"{self._fixed:g}")
        self.fixed_edit.setValidator(QDoubleValidator(1e-12, 1e15, 12, self))
        self.fixed_edit.setFixedWidth(80)
        self.fixed_edit.setToolTip("Half height of every row, in the units shown")
        self.fixed_edit.editingFinished.connect(self._fixed_edited)
        self.demean_box = QCheckBox("Remove mean")
        self.demean_box.setChecked(self._demean)
        self.demean_box.setFocusPolicy(Qt.NoFocus)
        self.demean_box.setToolTip("Subtract the mean of the visible data from each trace (channels with a "
                                   "fixed offset keep theirs)")
        self.demean_box.toggled.connect(self._set_demean)
        self.null_button = self._tool("Null", "Null offsets (N): fix each trace's offset at its mean now, so it "
                                              "sits centred in its row (channels with a locked offset keep "
                                              "theirs)")
        self.null_button.clicked.connect(lambda: self.null_offsets())
        self.tcur_button = self._tool("Time cursors", "Two time cursors: drag them; the time between them and "
                                                      "its frequency are shown here", checkable=True)
        self.tcur_button.toggled.connect(self.set_time_cursors)
        self.acur_button = self._tool("Ampl cursors", "Two amplitude cursors on the channel under the mouse "
                                                      "(or the first): drag them; the difference is shown here",
                                      checkable=True)
        self.acur_button.toggled.connect(lambda on: self.set_amp_cursors(on))
        self.live_button = QToolButton()
        self.live_button.setCheckable(True)
        self.live_button.setChecked(True)
        self.live_button.setFocusPolicy(Qt.NoFocus)
        self.live_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.live_button.toggled.connect(self.set_live)
        self.note = QLabel()
        self.note.setObjectName("barNote")
        self.note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.range_label = QLabel()
        self.range_label.setObjectName("barRange")

        sep = None
        for w in (self.channels_button, sep, caption("Time"), self.time_in, self.pps_button, self.time_out,
                  sep, caption("Amplitude"), self.amp_out, self.gain_label, self.amp_in, sep,
                  self.filter_button, caption("Units"), self.units_box, caption("Scale"), self.scale_box,
                  self.fixed_edit, self.demean_box, self.null_button, sep, self.tcur_button, self.acur_button,
                  sep, self.live_button):
            if w is None:
                bar.addSeparator()
            else:
                action = bar.addWidget(w)
                if w is self.fixed_edit:
                    self._fixed_action = action
        self.note.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.note.setMinimumWidth(0)
        bar.addWidget(self.note)   # (the time range is in the viewer window's status bar)
        self._fixed_action.setVisible(self._scale == "fixed")
        self._update_gain_label()
        self._update_filter_button()
        self._update_live_button()
        self._update_pps_button()
        return bar

    def _new_trace(self, row):
        vb = _PlotBox(self)
        vb.setMouseEnabled(x=True, y=False)
        vb.disableAutoRange()
        vb.sigRangeChangedManually.connect(self._on_user_range)
        left = pg.AxisItem("left")
        left.setWidth(AXIS_WIDTH)
        left.setStyle(tickFont=self._font, tickTextOffset=4)
        bottom = _TimeAxis(self._time_ticks, "bottom", utcOffset=0)
        bottom.setStyle(tickFont=self._font, tickTextOffset=3)
        for axis in (left, bottom):
            axis.enableAutoSIPrefix(False)   # else every range change re-renders an unused HTML label
            axis.linkToView(vb)
        if row == 0:   # the first plot holds the X range; the others follow it
            vb.sigXRangeChanged.connect(self._on_xrange)
        else:
            vb.setXLink(self._traces[0].vb)
        curve = pg.PlotDataItem([], [], connect="finite")
        vb.addItem(curve)
        panel = _LanePanel(self._font)
        self.grid.addItem(panel, 3 * row, 0)
        self.grid.addItem(left, 3 * row, 1)
        self.grid.addItem(vb, 3 * row, 2)
        self.grid.addItem(bottom, 3 * row + 2, 2)
        return _Trace(vb, left, bottom, curve, _TraceLabel(vb), panel)

    def _ensure_spec_row(self, tr, row):
        """The spectrogram row of a plot (made the first time a channel in it wants one)."""
        if tr.svb is not None:
            return
        svb = pg.ViewBox(enableMenu=False)
        svb.setMouseEnabled(x=False, y=True)   # wheel / drag zoom the frequency axis
        svb.disableAutoRange()
        svb.setXLink(self._traces[0].vb)
        saxis = pg.AxisItem("left")
        saxis.setWidth(AXIS_WIDTH)
        saxis.setStyle(tickFont=self._font, tickTextOffset=4)
        saxis.enableAutoSIPrefix(False)
        saxis.linkToView(svb)
        image = pg.ImageItem()
        image.setLookupTable(self._lut)
        svb.addItem(image)
        tr.svb, tr.saxis, tr.image = svb, saxis, image
        self.grid.addItem(saxis, 3 * row + 1, 1)
        self.grid.addItem(svb, 3 * row + 1, 2)
        c = theme.COLORS
        saxis.setPen(pg.mkPen(c["border"]))
        saxis.setTextPen(pg.mkPen(c["muted"]))

    def _rebuild(self):
        """Plot rows for the current channel list and layout (called only when they change).
        Existing rows are reused for the new channels and only the difference in number is
        added or removed at the end. Row i uses grid rows 3i (left axis, ViewBox), 3i+1 (its
        spectrogram, when on) and 3i+2 (its time axis: with values under the last row only, a
        thin gap under the others - where it still draws the vertical grid lines)."""
        n = len(self._sids)
        while len(self._traces) > n:
            tr = self._traces.pop()
            for line in tr.lines.values():
                tr.host.removeItem(line)
            if tr.host is not tr.vb:          # overlaid: its curve and caption sit in the first row
                tr.host.removeItem(tr.curve)
                tr.label.setParentItem(None)
                if tr.label.scene() is not None:
                    tr.label.scene().removeItem(tr.label)
            for item in tr.items():
                self.grid.removeItem(item)
                self.glw.scene().removeItem(item)
        while len(self._traces) < n:
            self._traces.append(self._new_trace(len(self._traces)))
        self._clear_cursor_lines()
        overlay = self._overlay and n > 1
        first = self._traces[0].vb if self._traces else None
        for i, (tr, sid) in enumerate(zip(self._traces, self._sids)):
            for line in tr.lines.values():
                tr.host.removeItem(line)
            tr.lines = {}
            if tr.sid != sid:
                tr.reset(sid)
            tr.ykey = None
            host = first if overlay else tr.vb
            if tr.host is not host:
                tr.host.removeItem(tr.curve)
                host.addItem(tr.curve)
                tr.host = host
            tr.label.setParentItem(host)
            tr.label.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(4, 2 + (LABEL_STEP * i if overlay else 0)))
            visible = i == 0 or not overlay
            for item in (tr.vb, tr.panel):
                item.setVisible(visible)
            tr.left.setVisible(visible and not self._clean)      # clean: no amplitude axis, no caption
            tr.left.setWidth(1 if self._clean else AXIS_WIDTH)
            tr.label.setVisible(not self._clean)
            tr.vb.setFlag(tr.vb.GraphicsItemFlag.ItemClipsChildrenToShape, not self._overlap)
            tr.vb.setZValue(i if self._overlap else 0)          # overlap: a lower row draws over the upper
            spec = self._spec_on(sid)
            if spec:
                self._ensure_spec_row(tr, i)
            if tr.svb is not None:
                tr.svb.setVisible(spec)
                tr.saxis.setVisible(spec)
            last = i == (0 if overlay else n - 1)
            tr.bottom.setVisible(visible)
            tr.bottom.setStyle(showValues=last, tickLength=5 if last else 0)
            tr.bottom.setHeight(TIME_AXIS_HEIGHT if last else self._row_gap())
        self.stack.setCurrentWidget(self.scroll if self._traces else self.empty)
        self._color_traces()
        self._size_canvas()
        self._fit_rows()
        self.grid.activate()   # geometry now, not at the next event: the decimation needs the width
        self._make_cursor_lines()

    def _spec_on(self, sid):
        return bool(self._ts(sid)["spectrogram"]) and not (self._overlay and len(self._sids) > 1)

    def _row_plan(self):
        """[(trace visible, spectrogram height, gap height)] for every row."""
        n = len(self._traces)
        overlay = self._overlay and n > 1
        plan = []
        for i, tr in enumerate(self._traces):
            visible = i == 0 or not overlay
            spec = self._spec_size if visible and tr.sid and self._spec_on(tr.sid) else 0
            last = i == (0 if overlay else n - 1)
            gap = TIME_AXIS_HEIGHT if last else (self._row_gap() if visible else 0)
            plan.append((visible, spec, gap))
        return plan

    def _row_gap(self):
        """Pixels between two rows: none in the clean display (rows packed like the earlier viewer)."""
        return 0 if self._clean else ROW_GAP

    def _size_canvas(self, width=None, height=None):
        """The plot canvas fills the scroll area, and is taller when the rows need more room
        (then the vertical scroll bar appears). width/height: the new viewport size."""
        vp = self.scroll.viewport()
        width = vp.width() if width is None else width
        height = vp.height() if height is None else height
        plan = self._row_plan()
        _l, top, _r, bottom = self.grid.getContentsMargins()
        need = (sum(MIN_ROW + s + g for v, s, g in plan if v) + sum(g for v, s, g in plan if not v)
                + top + bottom + 2 * self.glw.frameWidth()) if plan else 0
        w, h = max(1, width), max(1, height, math.ceil(need))
        if (self.glw.width(), self.glw.height()) != (w, h):
            self.glw.setFixedSize(w, h)

    def _fit_rows(self, height=None):
        """Equal data heights for every visible trace; spectrograms, time axis and gaps keep
        their fixed heights. `height` is the new viewport height during a resize (the viewport
        itself is resized only after the event filter has seen the event)."""
        plan = self._row_plan()
        if not plan:
            return
        lay = self.grid
        _l, top, _r, bottom = lay.getContentsMargins()
        avail = (self.glw.viewport().height() if height is None else height) - top - bottom
        fixed = sum(s + g for _v, s, g in plan)
        shown = sum(1 for v, _s, _g in plan if v) or 1
        each = max(float(MIN_ROW), (avail - fixed) / shown)
        if abs(each - self._row_px) > 0.5:
            self._row_px = each
            if self._scale == "pixel" and self._traces:
                self._apply_y_ranges()   # the zoom is per pixel: a taller row shows more units
        rows = 3 * len(plan)
        for i in range(max(rows, lay.rowCount())):   # rows of removed plots give their height back
            if i >= rows:
                h = 0
            else:
                visible, spec, gap = plan[i // 3]
                h = (each if visible else 0, spec, gap)[i % 3]
            lay.setRowFixedHeight(i, h)

    def _apply_colors(self):
        """Colours from the current theme (called again when the application palette changes)."""
        c = theme.COLORS
        self._colors = dict(c)
        self.glw.setBackground(self._background())
        self.empty.setStyleSheet(f"color: {c['muted']}; font-size: 11pt; background: {c['plot_bg']};")
        self.bar.setStyleSheet(
            f"QToolBar#waveformBar {{ background: {c['panel']}; border: none; "
            f"border-bottom: 1px solid {c['border']}; spacing: 3px; }}"
            f"QLabel#barCaption, QLabel#barRange {{ color: {c['muted']}; }}"
            f"QLabel#barNote {{ color: {c['warn']}; }}"
            f"QToolButton:checked {{ background: {c['select']}; border-radius: 3px; }}")
        self._color_traces()
        self._update_live_button()

    def _background(self):
        return self._bg or theme.COLORS["plot_bg"]

    def trace_color(self, i, sid=None):
        own = self._ts(sid)["color"] if sid else None
        return own if own and QColor.isValidColorName(own) else theme.trace_color(i, sid)

    def _color_traces(self):
        """Pens, caption colours, lane lines (also after a reorder). The data stay."""
        c = theme.COLORS
        bg = QColor(self._background())
        bg.setAlpha(200)
        for i, tr in enumerate(self._traces):
            tr.curve.setPen(pg.mkPen(self.trace_color(i, tr.sid), width=1))
            for ax in (tr.left, tr.bottom):
                ax.setPen(pg.mkPen(c["border"]))
                ax.setTextPen(pg.mkPen(c["muted"]))
            tr.vb.setBorder(pg.mkPen(c["grid"]) if self._lanes and not self._clean else None)
            tr.bottom.setGrid(128 if self._lanes and not self._clean else False)
            tr.label.bg = bg
            tr.label.frame = c["accent"] if tr.sid and tr.sid == self._active else None
            tr.label.update()
            if tr.t0 is not None:
                self._update_label(tr)
        self._update_panels()

    @staticmethod
    def panel_lines(sid, units, label="", scale=1.0):
        """The three lines of a channel's label box: its label (else the station),
        location.channel, and its units - or its scale factor ("×3067") when it has one, as the
        earlier viewer shows it."""
        parts = sid.split(".")
        loc_cha = f"{parts[2]}.{parts[3]}" if len(parts) == 4 and parts[2] else parts[-1]
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = 1.0
        return (label or ".".join(parts[:2])), loc_cha, (units if scale == 1.0 else fmt_gain(scale))

    def set_trace_label(self, sid, text):
        """A name for the channel, shown in its box on the left and in its caption."""
        self._set_ts(sid, label=str(text or "").strip()[:40])
        self._update_panels()

    def _update_panels(self):
        """The label boxes left of the traces (all channels in the first row's panel when
        overlaid)."""
        bg = theme.COLORS["panel2"]
        entries = [(self.panel_lines(tr.sid, tr.units, self._ts(tr.sid)["label"], self._ts(tr.sid)["scale"]),
                    self.trace_color(i, tr.sid), tr.sid == self._active)
                   for i, tr in enumerate(self._traces) if tr.sid]
        overlay = self._overlay and len(self._traces) > 1
        for i, tr in enumerate(self._traces):
            if overlay:
                tr.panel.set_entries(entries if i == 0 else [], bg)
            else:
                tr.panel.set_entries(entries[i:i + 1], bg)

    # ------------------------------------------------------------------------------------
    # public contract
    # ------------------------------------------------------------------------------------
    def set_channels(self, sids):
        """Show these channels (in this order). Plots are rebuilt only if the list changed; the
        time spent building counts against the recompute budget, so the call stays short and
        the traces the budget did not cover follow within the next refreshes. Settings of
        channels no longer shown are dropped."""
        sids = list(dict.fromkeys(s for s in sids if isinstance(s, str) and s))
        if sids == self._sids:
            return
        started = time.perf_counter()
        t0, t1 = self.visible_range()
        self._sids = sids
        self._tset = {k: v for k, v in self._tset.items() if k in sids}
        self._details &= set(sids)
        if self._acur and self._acur[0] not in sids:
            self.acur_button.setChecked(False)
        self._latest = None   # another channel set: its newest sample is a new reference, not an arrival
        self._lags.clear()    # and its own arrival lag (the old set's may be much smaller: a gap at the edge)
        self._relag = True
        self._rebuild()
        self._set_range(*(self._live_range() if self._live else (t0, t1)))
        if self._traces:
            self._update_traces(urgent=True, started=started)
        self.changed.emit()

    def channels(self):
        return list(self._sids)

    def add_channels(self, sids):
        """Append channels not shown yet; returns the ones added."""
        new = [s for s in dict.fromkeys(sids) if isinstance(s, str) and s and s not in self._sids]
        if new:
            self.set_channels(self._sids + new)
        return new

    def remove_channel(self, sid):
        if sid in self._sids:
            self.set_channels([s for s in self._sids if s != sid])

    def clear(self):
        self.set_channels([])

    def refresh(self):
        """Follow live data and recompute traces that are out of date (every 250 ms).
        A view paused for AUTO_LIVE seconds without mouse use on the plots goes live again."""
        if not self._traces or not self._on_screen():
            return
        if not self._live and time.monotonic() - self._touched >= AUTO_LIVE:
            self.set_live(True)
        if self._live:
            t0, t1 = self._live_range()
            if self._moves_a_pixel(t0, t1):
                self._set_range(t0, t1)
        self._update_traces(urgent=False)

    def _moves_a_pixel(self, t0, t1):
        """True when (t0, t1) is at least LIVE_STEP_PX pixels away from the range on screen. A
        live view follows the data every refresh (250 ms), but in a long window that is a small
        part of a pixel (60 min: ~2.5 s per pixel): moving it then only re-ranges every linked
        plot and redraws every curve and axis to the same pixels."""
        if not self._traces:
            return True
        c0, c1 = self.visible_range()
        px = (t1 - t0) / self._plot_width()
        return abs(t1 - c1) >= LIVE_STEP_PX * px or abs(t0 - c0) >= LIVE_STEP_PX * px

    def redraw(self):
        """Recompute everything now (F5)."""
        self._version += 1
        self._reload_now()

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
        self._wheel = 0
        if not live:
            self._touched = time.monotonic()   # the auto-return counts from the pause
        self._update_live_button()
        if live and changed:
            self._set_range(*self._live_range())
            self._reload_now()

    def is_live(self):
        return self._live

    def show_range(self, t0, t1):
        """Pause and show this time range (a selection's "Zoom to this time range")."""
        if t1 <= t0:
            return
        self.set_live(False)
        self._touched = time.monotonic()
        self._pps = self._plot_width() / (t1 - t0)
        self._set_range(t0, t1)
        self._update_pps_button()
        self._reload_now()

    def state(self):
        """Everything a viewer saves (config "viewers"); WaveformView(state=...) restores it."""
        traces = {}
        for sid in self._sids:
            own = {k: v for k, v in self._tset.get(sid, {}).items() if v != TRACE_DEFAULTS[k]}
            if own:
                traces[sid] = own
        return {"sids": list(self._sids), "pps": self._pps, "window": self._window, "filter_on": self._filter_on,
                "filter": self._filter_key, "custom": self._custom, "units": self._units, "scale": self._scale,
                "fixed": self._fixed, "demean": self._demean, "gain": self._gain, "lanes": self._lanes,
                "overlay": self._overlay, "background": self._bg, "spec_size": self._spec_size,
                "cpp": self._cpp, "clean": self._clean, "overlap": self._overlap, "traces": traces}

    # ------------------------------------------------------------------------------------
    # whole-window settings
    # ------------------------------------------------------------------------------------
    def _changed(self, rebuild=False):
        """A setting changed: recompute every trace now and tell the viewer to save."""
        self._version += 1
        self._specs.clear()
        if rebuild:
            self._rebuild()
        self._reload_now()
        self.changed.emit()

    def set_pps(self, pps):
        """A new time scale (pixels per second): live, the window ends at the newest data; paused,
        the right edge the user chose stays."""
        self._pps = float(pps)
        self._anchored = False
        if self._live:
            self._set_range(*self._live_range())
        else:  # keep the right edge the user chose, change only the length
            _t0, t1 = self.visible_range()
            self._set_range(t1 - self._window, t1)
        self._update_pps_button()
        self._changed()

    def set_window(self, seconds):
        """Show this many seconds (the time scale follows the plot width)."""
        self.set_pps(self._plot_width() / max(MIN_WINDOW, min(MAX_WINDOW, float(seconds))))

    def step_window(self, steps):
        """Longer (steps > 0: fewer pixels per second) or shorter time window, in the steps of
        PPS_STEPS; never longer than a day."""
        i = min(range(len(PPS_STEPS)), key=lambda k: abs(math.log(PPS_STEPS[k]) - math.log(self._pps)))
        j = min(len(PPS_STEPS) - 1, max(0, i - int(steps)))
        if steps > 0 and self._window >= MAX_WINDOW - 1e-6:
            return                                  # a whole day is shown already
        new = PPS_STEPS[j]
        if j == i and abs(new - self._pps) < 1e-9:
            return
        if self._plot_width() / new > MAX_WINDOW:  # the next step would pass a day: exactly a day
            new = self._plot_width() / MAX_WINDOW
        self.set_pps(new)

    def _update_pps_button(self):
        pps = self._pps
        text = f"{pps:.3g}"
        w = self._window
        span = (f"{w / 3600:.3g} h" if w >= 7200 else f"{w / 60:.3g} min" if w >= 120 else f"{w:.3g} s")
        self.pps_button.setText(text)
        self.pps_button.setToolTip(f"Time scale: {text} pixels per second - this window shows {span}. Left "
                                   "click (or Left arrow): expand; right click (or Right arrow): squash. Up to "
                                   "1440 minutes (one day), with the history of the last day.")

    def zoom_amplitude(self, steps):
        """Larger (steps > 0) or smaller traces, x2 per step; the view stays live. In the
        "pixel" scale it halves (larger) or doubles (smaller) the units per pixel."""
        if self._scale == "pixel":
            n = clamp_cpp((self._cpp or self._fitted_cpp() or 1.0) / 2.0 ** int(steps))
            if n != self._cpp:
                self.set_cpp(n)
            return
        g = self._clamp_gain(self._gain * 2.0 ** int(steps))
        if g != self._gain:
            self._gain = g
            self._update_gain_label()
            self._apply_y_ranges()
            self.changed.emit()

    def reset_amplitude(self):
        """Amplitude zoom x1 (Home); in the "pixel" scale: the zoom at which the traces fit."""
        if self._scale == "pixel":
            n = self._fitted_cpp()
            if n is not None and n != self._cpp:
                self.set_cpp(n)
            return
        if self._gain != 1.0:
            self._gain = 1.0
            self._update_gain_label()
            self._apply_y_ranges()
            self.changed.emit()

    def _update_gain_label(self):
        if self._scale == "pixel":
            n = self._cpp
            unit = "counts" if self._units == "counts" else "units"
            self.gain_label.setText(fmt_cpp(n) if n else "1:?")
            self.gain_label.setToolTip(f"Zoom of every trace: 1 pixel = {n:g} {unit} (each trace divided by its "
                                       "own scale factor). Up / Down or the mouse wheel halve / double it; click "
                                       "(or Home) to fit the traces again." if n else "Zoom of every trace")
        else:
            self.gain_label.setText(fmt_gain(self._gain))
            self.gain_label.setToolTip("Amplitude zoom of every trace - click to reset it to ×1 (Home)")

    def cpp(self):
        """The "pixel" zoom: units (counts) per pixel of row height, or None before it is set."""
        return self._cpp

    def set_cpp(self, value):
        """The "pixel" zoom (a power of two; switches to that scale mode)."""
        n = clamp_cpp(value)
        if n is None:
            return
        self._cpp = n
        if self._scale != "pixel":
            self.scale_box.setCurrentIndex(self.scale_box.findData("pixel"))   # _set_scale: recompute
            return
        self._update_gain_label()
        self._apply_y_ranges()
        self.changed.emit()

    def _fitted_cpp(self):
        """The "pixel" zoom at which the typical trace (factor applied) fills its row."""
        spans = [(tr.hi - tr.lo) * float(self._ts(tr.sid)["scale"]) for tr in self._traces
                 if tr.sid and tr.lo is not None]
        return fit_cpp(spans, self._row_px)

    def reset_scales(self):
        """Every channel's own scale factor and the amplitude zoom back to 1 (Ctrl+1)."""
        for ts in self._tset.values():
            ts.pop("scale", None)
        self._gain = 1.0
        self._update_gain_label()
        self._changed()

    def pan(self, fraction):
        """Scroll by a part of the window (negative: back in time); pauses."""
        t0, t1 = self.visible_range()
        d = (t1 - t0) * fraction
        self.show_range(t0 + d, t1 + d)

    def gain(self):
        return self._gain

    def set_filter_on(self, on):
        on = bool(on)
        if on != self._filter_on:
            self._filter_on = on
            self._update_filter_button()
            self._changed()
        elif self.filter_button.isChecked() != on:
            self._update_filter_button()

    def toggle_filter(self):
        self.set_filter_on(not self._filter_on)

    def filter_on(self):
        return self._filter_on

    def set_filter(self, key, custom=None):
        """Choose the window's filter (a filter_catalogue key, or "custom" with a design) and
        switch it on. A custom design of None switches the filter off."""
        if key == "custom":
            custom = dsp.normalize_spec(custom)
            if custom is None:
                self.set_filter_on(False)
                return
            if custom.get("default"):   # the design left as the default band-pass
                key = "default"
            else:
                self._custom = custom
        self._filter_key = key
        self._filter_on = True
        self._update_filter_button()
        self._changed()

    def filter_key(self):
        return self._filter_key

    def _resolve(self, key, custom):
        """Spec of a filter key, cached until the next setting change (a saved filter is looked
        up each time: another viewer or the design may replace or delete it)."""
        if key.startswith(SAVED_PREFIX):
            return filter_spec(self.config, key, custom)
        k = (key, json.dumps(custom, sort_keys=True) if key == "custom" else "")
        if k not in self._specs:
            self._specs[k] = filter_spec(self.config, key, custom)
        return self._specs[k]

    def window_spec(self):
        return self._resolve(self._filter_key, self._custom) if self._filter_on else None

    def _update_filter_button(self):
        b = self.filter_button
        b.blockSignals(True)
        b.setChecked(self._filter_on)
        b.blockSignals(False)
        label = filter_label(self.config, self._filter_key, self._custom)
        b.setText(f"Filter: {label}" if self._filter_on else "Filter off")
        b.setToolTip(f"Filter of every channel: {label} (zero phase). Click to switch it "
                     f"{'off' if self._filter_on else 'on'} (F); the arrow chooses another filter or opens "
                     "the design (Ctrl+F). A channel can have its own filter (right-click it).")

    def _fill_filter_menu(self):
        self.fill_filter_menu(self.filter_menu, None)

    def fill_filter_menu(self, menu, sid):
        """The filter choices: for the whole window (sid None) or for one channel."""
        menu.clear()
        for old in menu.findChildren(QActionGroup) + menu.findChildren(QMenu):   # left from the last fill
            old.setParent(None)
            old.deleteLater()
        group = QActionGroup(menu)
        if sid is None:
            current = self._filter_key if self._filter_on else None
            off = menu.addAction("No filter")
            off.setCheckable(True)
            off.setChecked(not self._filter_on)
            off.triggered.connect(lambda: self.set_filter_on(False))
        else:
            own = self._ts(sid)["filter"]
            current = own
            same = menu.addAction("Same as the window" + (f" ({self.window_filter_text()})"))
            same.setCheckable(True)
            same.setChecked(own is None)
            same.triggered.connect(lambda: self.set_trace_filter(sid, None))
            off = menu.addAction("No filter for this channel")
            off.setCheckable(True)
            off.setChecked(own == "off")
            off.triggered.connect(lambda: self.set_trace_filter(sid, "off"))
            group.addAction(same)
        group.addAction(off)
        menu.addSeparator()
        section = False
        for key, label, _spec in filter_catalogue(self.config):
            if key.startswith(SAVED_PREFIX) and not section:
                menu.addSection("Saved filters")
                section = True
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(current == key)
            group.addAction(a)
            if sid is None:
                a.triggered.connect(lambda _=False, k=key: self.set_filter(k))
            else:
                a.triggered.connect(lambda _=False, k=key: self.set_trace_filter(sid, k))
        custom = self._custom if sid is None else self._ts(sid)["custom"]
        if dsp.normalize_spec(custom):
            a = menu.addAction(f"Own design: {dsp.describe(custom)}")
            a.setCheckable(True)
            a.setChecked(current == "custom")
            group.addAction(a)
            if sid is None:
                a.triggered.connect(lambda: self.set_filter("custom", self._custom))
            else:
                a.triggered.connect(lambda: self.set_trace_filter(sid, "custom", self._ts(sid)["custom"]))
        menu.addSeparator()
        design = menu.addAction("Design..." if sid is None else "Design for this channel...")
        if sid is None:   # the key belongs to the viewer window's own action: here it is only shown
            design.setShortcut("Ctrl+F")
            design.setShortcutContext(Qt.WidgetShortcut)
            design.setShortcutVisibleInContextMenu(True)
        design.triggered.connect(lambda: self.open_filter_design(sid))
        saved = saved_filters(self.config)
        if saved and sid is None:
            sub = menu.addMenu("Delete a saved filter")
            for f in saved:
                sub.addAction(f["name"], lambda n=f["name"]: self.delete_saved_filter(n))

    def delete_saved_filter(self, name):
        delete_filter(self.config, name)
        self.filters_changed()
        self.filtersSaved.emit()

    def filters_changed(self):
        """A saved filter was added, replaced or deleted (here or in another viewer)."""
        self._specs.clear()
        self._update_filter_button()
        self._changed()

    def window_filter_text(self):
        return filter_label(self.config, self._filter_key, self._custom) if self._filter_on else "no filter"

    def open_filter_design(self, sid=None, spectrum=None):
        """The filter design dialog for the whole window (sid None) or one channel; spectrum:
        ("noise" | "signal", freqs, amplitudes) to show in it."""
        d = self._design
        target = sid if sid else "all channels"
        if d is None or not d.isVisible() or d.property("target") != target:
            if d is not None:
                d.destroyed.disconnect(self._design_closed)   # it goes later: it must not forget the new one
                d.close()
            if sid:
                spec = self.trace_spec(sid)
                rates = [self._rates.get(sid) or 100.0]
                apply_fn = (lambda spec, s=sid: self.set_trace_filter(s, "custom", spec))
            else:
                spec = self.window_spec()
                rates = [self._rates.get(s) or 0.0 for s in self._sids] or [100.0]
                apply_fn = (lambda spec: self.set_filter("custom", spec))
            d = FilterDesignDialog(self.config, apply_fn, spec, rates, target, self)
            d.setProperty("target", target)
            d.setAttribute(Qt.WA_DeleteOnClose)
            d.destroyed.connect(self._design_closed)
            d.saved.connect(self._design_saved)
            self._design = d
            d.show()
        d.raise_()
        d.activateWindow()
        if spectrum:
            d.set_spectrum(*spectrum)
        return d

    def _design_closed(self, *_args):
        self._design = None

    def _design_saved(self, *_args):
        self.filters_changed()
        self.filtersSaved.emit()

    def _set_units(self, _index):
        self._units = self.units_box.currentData()
        self._sens.clear()
        if self._scale == "pixel":
            self._cpp = None          # counts and physical units differ by orders: fit the zoom again
        self._update_gain_label()
        self._changed()

    def set_units(self, units):
        i = self.units_box.findData(units)
        if i >= 0:
            self.units_box.setCurrentIndex(i)

    def _set_scale(self, _index):
        self._scale = scale = self.scale_box.currentData()
        self._fixed_action.setVisible(scale == "fixed")
        if scale == "pixel" and self._cpp is None:
            self._cpp = self._fitted_cpp()     # (None without data yet: picked when the data come)
        self._update_gain_label()
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
        self.glw.setFocus()

    def set_fixed_scale(self, value):
        """Fixed scale mode with this half height (Scale others to match)."""
        self._fixed = float(value)
        self.fixed_edit.setText(f"{self._fixed:.6g}")
        if self._scale != "fixed":
            self.scale_box.setCurrentIndex(self.scale_box.findData("fixed"))   # _set_scale: recompute
        else:
            self._changed()

    def _set_demean(self, on):
        self._demean = bool(on)
        self._changed()

    def set_lanes(self, on):
        self._lanes = bool(on)
        self._color_traces()
        self.changed.emit()

    def lanes(self):
        return self._lanes

    def set_clean(self, on):
        """Clean display: no amplitude axis, caption, frame or gap per row - the traces packed
        like the earlier viewer's (the label boxes on the left stay)."""
        if bool(on) != self._clean:
            self._clean = bool(on)
            self._changed(rebuild=True)

    def clean(self):
        return self._clean

    def set_overlap(self, on):
        """Let a loud trace draw over the rows next to it instead of being cut at its row."""
        if bool(on) != self._overlap:
            self._overlap = bool(on)
            self._changed(rebuild=True)

    def overlap(self):
        return self._overlap

    def set_overlay(self, on):
        if bool(on) != self._overlay:
            self._overlay = bool(on)
            self._changed(rebuild=True)

    def overlay(self):
        return self._overlay

    def set_background(self, color):
        self._bg = color if color and QColor.isValidColorName(color) else None
        self.glw.setBackground(self._background())
        self._color_traces()
        self.changed.emit()

    def set_spec_size(self, size):
        if size in SPEC_SIZES and size != self._spec_size:
            self._spec_size = size
            self._changed(rebuild=True)

    def spec_size(self):
        return self._spec_size

    def sort_channels(self, key, reverse=False):
        """Put the channels in order once (SORTS: channel, component, station, rate)."""
        fn = SORTS.get(key, SORTS["channel"])[1]
        self.set_channels(sorted(self._sids, key=lambda s: fn(s, self._rates.get(s, 0.0)), reverse=reverse))

    def calibration_changed(self):
        """A channel's calibration was entered or removed: look the sensitivities up again."""
        self._sens.clear()
        self._changed()

    def _update_live_button(self):
        b = self.live_button
        b.blockSignals(True)
        b.setChecked(self._live)
        b.blockSignals(False)
        if self._live:
            b.setIcon(theme.icon("live"))
            b.setText("Live")
            b.setToolTip("Following the newest data (Pause key). The mouse wheel changes the amplitude, "
                         "Shift+wheel the window length; dragging a plot pauses.")
            b.setStyleSheet("")
        else:
            c = theme.COLORS
            # amber, so a paused view is never mistaken for a live one; dark text on the bright dark-theme
            # amber, white on the deeper light-theme amber (both readable)
            fg = c["bg"] if theme.MODE == "dark" else "#ffffff"
            b.setIcon(QIcon())
            b.setText("Paused – back to live")
            b.setToolTip(f"Paused: the view does not follow the newest data. Click here (or double-click a "
                         f"plot) to go live again; it does so by itself after {AUTO_LIVE / 60:g} minutes "
                         f"without mouse use on the plots.")
            b.setStyleSheet(f"QToolButton, QToolButton:hover {{ background: {c['warn']}; color: {fg}; "
                            f"font-weight: 600; border-radius: 4px; padding: 4px 10px; }}")

    # ------------------------------------------------------------------------------------
    # per-channel settings
    # ------------------------------------------------------------------------------------
    def _ts(self, sid):
        """A channel's settings (defaults filled in; a copy - change them with _set_ts)."""
        out = dict(TRACE_DEFAULTS)
        out.update(self._tset.get(sid, {}))
        return out

    def trace_settings(self, sid):
        return self._ts(sid)

    def _set_ts(self, sid, rebuild=False, **values):
        if sid not in self._sids:
            return
        own = self._tset.setdefault(sid, {})
        for k, v in values.items():
            if v == TRACE_DEFAULTS[k]:
                own.pop(k, None)
            else:
                own[k] = v
        self._changed(rebuild=rebuild)

    def set_trace_color(self, sid, color):
        self._set_ts(sid, color=color if color and QColor.isValidColorName(color) else None)
        self._color_traces()

    def set_trace_scale(self, sid, value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if math.isfinite(v) and v > 0:
            self._set_ts(sid, scale=v)

    def match_factor(self, sid, ref):
        """The scale factor at which channel `sid` shows the same peak-to-peak height as `ref`
        does with its own factor (visible data), or None without data."""
        a, b = self._trace(sid), self._trace(ref)
        if a is None or b is None or a.lo is None or b.lo is None or a.hi <= a.lo or b.hi <= b.lo:
            return None
        f = float(self._ts(ref)["scale"]) * (b.hi - b.lo) / (a.hi - a.lo)
        return float(f"{f:.4g}")

    def scale_others_to_match(self, sid):
        """One-time scaling like the earlier viewer: every other channel gets the scale factor at
        which its visible peak-to-peak range is as high as this one's, and the window goes to the
        "pixel" scale with the zoom at which this channel fills most of its row. Channels without
        data keep their factor."""
        ref = self._trace(sid)
        if ref is None or ref.lo is None or ref.hi <= ref.lo:
            return
        for tr in self._traces:
            if tr.sid and tr.sid != sid:
                f = self.match_factor(tr.sid, sid)
                if f is not None and math.isfinite(f) and f > 0:
                    own = self._tset.setdefault(tr.sid, {})
                    if f == 1.0:
                        own.pop("scale", None)
                    else:
                        own["scale"] = f
        self._gain = 1.0
        self._cpp = fit_cpp([(ref.hi - ref.lo) * float(self._ts(sid)["scale"])], self._row_px)
        if self._scale != "pixel":
            self.scale_box.setCurrentIndex(self.scale_box.findData("pixel"))   # _set_scale: recompute
        else:
            self._update_gain_label()
            self._changed()

    def set_trace_filter(self, sid, key, custom=None):
        """A channel's own filter: None = the window's, "off" = none, a catalogue key, or
        "custom" with a design (a design of None: no filter for this channel)."""
        if key == "custom":
            custom = dsp.normalize_spec(custom)
            if custom is None:
                key = "off"
            elif custom.get("default"):
                key = "default"
        self._set_ts(sid, filter=key, custom=custom if key == "custom" else self._ts(sid)["custom"])

    def trace_spec(self, sid):
        """The filter applied to a channel (None: unfiltered)."""
        ts = self._ts(sid)
        f = ts["filter"]
        if f == "off":
            return None
        if f:
            return self._resolve(f, ts["custom"])
        return self.window_spec()

    def trace_filter_text(self, sid):
        ts = self._ts(sid)
        f = ts["filter"]
        if f == "off":
            return "no filter (own setting)"
        if f:
            return filter_label(self.config, f, ts["custom"])
        return self.window_filter_text()

    def null_offsets(self, sid=None):
        """Fix the offset at the mean of the visible data now: one channel, or every channel
        whose offset is not locked."""
        for tr in self._traces:
            if tr.sid and tr.mean_pre is not None and (tr.sid == sid if sid else not self._ts(tr.sid)["locked"]):
                self._tset.setdefault(tr.sid, {})["offset"] = tr.mean_pre
        self._changed()

    def zero_offset(self, sid=None):
        """Offset 0 (absolute values): one channel, or all channels (locked ones too)."""
        for s in ([sid] if sid else self._sids):
            self._tset.setdefault(s, {})["offset"] = 0.0
        self._changed()

    def set_offset(self, sid, value):
        """A channel's offset in counts (None: follow the window's mean removal)."""
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                return
            if not math.isfinite(value):
                return
        self._set_ts(sid, offset=value)

    def set_locked(self, sid, on):
        self._set_ts(sid, locked=bool(on))

    def set_spectrogram(self, sid, on):
        self._set_ts(sid, rebuild=True, spectrogram=bool(on))

    def toggle_all_spectrograms(self):
        """All on, or all off when every channel shows one (Ctrl+P)."""
        on = not all(self._ts(s)["spectrogram"] for s in self._sids)
        for s in self._sids:
            own = self._tset.setdefault(s, {})
            if on:
                own["spectrogram"] = True
            else:
                own.pop("spectrogram", None)
        self._changed(rebuild=True)

    def spectrogram_contrast(self, sid, steps):
        """Brighter (steps > 0) or darker spectrogram; sid None: every channel."""
        for s in ([sid] if sid else self._sids):
            c = int(self._ts(s)["contrast"]) + int(steps)
            own = self._tset.setdefault(s, {})
            if c:
                own["contrast"] = max(-10, min(10, c))
            else:
                own.pop("contrast", None)
        self._changed()

    def reset_spectrogram_zoom(self, sid):
        tr = self._trace(sid)
        if tr is not None and tr.svb is not None and tr.nyq:
            tr.svb.setYRange(0, tr.nyq, padding=0)

    def show_details_lines(self, sid, on):
        """Mean (dotted), minimum and maximum (solid) lines on a channel while its Details
        window is open."""
        if on:
            self._details.add(sid)
        else:
            self._details.discard(sid)
        tr = self._trace(sid)
        if tr is not None:
            self._update_detail_lines(tr)
            if on:
                tr.version = -1          # computed again now, with its RMS
                self._reload_now()

    def trace_stats(self, sid):
        """{"n", "mean", "min", "max", "rms", "units", "offset", "filter", "rate"} of the visible
        data of a channel as shown (units after calibration), or None."""
        tr = self._trace(sid)
        if tr is None or tr.stats is None:
            return None
        out = dict(tr.stats)
        out.update(units=tr.units, offset=self._ts(sid)["offset"], filter=self.trace_filter_text(sid),
                   rate=tr.rate, mean_counts=tr.mean_pre)
        return out

    def active_sid(self):
        """The channel the keyboard shortcuts act on: under the mouse, else the last one
        right-clicked, else the first."""
        for s in (self._active, self._menu_sid, self._hovered):
            if s in self._sids:
                return s
        return self._sids[0] if self._sids else None

    def _trace(self, sid):
        return next((tr for tr in self._traces if tr.sid == sid), None)

    # ------------------------------------------------------------------------------------
    # cursors
    # ------------------------------------------------------------------------------------
    def set_time_cursors(self, on):
        self._tcur = [0.4, 0.6] if on else None
        if self.tcur_button.isChecked() != bool(on):
            self.tcur_button.blockSignals(True)
            self.tcur_button.setChecked(bool(on))
            self.tcur_button.blockSignals(False)
        self._clear_cursor_lines()
        self._make_cursor_lines()

    def set_amp_cursors(self, on, sid=None):
        """Amplitude cursors on a channel (default: the one the mouse was last over)."""
        sid = sid or self.active_sid()
        self._acur = [sid, 0.3, 0.7] if on and sid else None
        if self.acur_button.isChecked() != bool(self._acur):
            self.acur_button.blockSignals(True)
            self.acur_button.setChecked(bool(self._acur))
            self.acur_button.blockSignals(False)
        self._clear_cursor_lines()
        self._make_cursor_lines()

    def cursor_readout(self):
        """(seconds between the time cursors or None, amplitude difference or None, units)."""
        dt = da = None
        units = ""
        if self._tcur:
            t0, t1 = self.visible_range()
            dt = abs(self._tcur[1] - self._tcur[0]) * (t1 - t0)
        if self._acur:
            tr = self._trace(self._acur[0])
            if tr is not None and tr.yrange is not None:
                da = abs(self._acur[2] - self._acur[1]) * (tr.yrange[1] - tr.yrange[0])
                units = tr.units
        return dt, da, units

    def _clear_cursor_lines(self):
        for tr in self._traces:
            for line in tr.tcur:
                tr.vb.removeItem(line)
            tr.tcur = []
        for vb, line in self._acur_lines:
            vb.removeItem(line)
        self._acur_lines = []

    def _make_cursor_lines(self):
        c = theme.COLORS
        if self._tcur:
            pen = pg.mkPen(c["accent2"], width=1)
            for tr in self._traces:
                if not tr.vb.isVisible():
                    continue
                for k in range(2):
                    line = pg.InfiniteLine(angle=90, movable=True, pen=pen, hoverPen=pg.mkPen(c["text"], width=2))
                    line.setZValue(60)
                    line.sigPositionChanged.connect(lambda ln, k=k: self._tcur_moved(ln, k))
                    tr.vb.addItem(line, ignoreBounds=True)
                    tr.tcur.append(line)
        if self._acur:
            tr = self._trace(self._acur[0])
            if tr is not None:
                pen = pg.mkPen(c["accent"], width=1)
                for k in range(2):
                    line = pg.InfiniteLine(angle=0, movable=True, pen=pen, hoverPen=pg.mkPen(c["text"], width=2))
                    line.setZValue(60)
                    line.sigPositionChanged.connect(lambda ln, k=k: self._acur_moved(ln, k))
                    tr.host.addItem(line, ignoreBounds=True)
                    self._acur_lines.append((tr.host, line))
        self._place_cursors()

    def _place_cursors(self):
        """Cursors keep their place on screen (a fraction of the window / the row) while the
        view scrolls or rescales; the readout is on the cursor buttons."""
        self._moving_cursor = True
        try:
            if self._tcur and self._traces:
                t0, t1 = self.visible_range()
                for tr in self._traces:
                    for k, line in enumerate(tr.tcur):
                        line.setValue(t0 + self._tcur[k] * (t1 - t0))
            if self._acur:
                tr = self._trace(self._acur[0])
                if tr is not None and tr.yrange is not None:
                    lo, hi = tr.yrange
                    for k, (_vb, line) in enumerate(self._acur_lines):
                        line.setValue(lo + self._acur[k + 1] * (hi - lo))
        finally:
            self._moving_cursor = False
        dt, da, units = self.cursor_readout()
        self.tcur_button.setText(f"Δt {dt:.3f} s · {1 / dt:.3g} Hz" if dt else "Time cursors")
        self.acur_button.setText(f"ΔA {fmt_value(da, units != 'counts')} {units}" if da is not None
                                 else "Ampl cursors")

    def _tcur_moved(self, line, k):
        if self._moving_cursor or not self._tcur:
            return
        t0, t1 = self.visible_range()
        if t1 > t0:
            self._tcur[k] = (line.value() - t0) / (t1 - t0)
        self._touched = time.monotonic()
        self._place_cursors()

    def _acur_moved(self, line, k):
        if self._moving_cursor or not self._acur:
            return
        tr = self._trace(self._acur[0])
        if tr is not None and tr.yrange is not None and tr.yrange[1] > tr.yrange[0]:
            lo, hi = tr.yrange
            self._acur[k + 1] = (line.value() - lo) / (hi - lo)
        self._place_cursors()

    # ------------------------------------------------------------------------------------
    # time range
    # ------------------------------------------------------------------------------------
    def _live_range(self):
        """The window ending where the newest data arrive: now minus the arrival lag of the shown
        channels' newest sample (the smallest seen in the last LAG_MEMORY s, see _note_latest).
        So each record touches the right edge as it arrives and the view scrolls smoothly in
        between, whatever the PC clock's offset (a PC clock 18.8 s fast and 2.5 s records used to
        leave the right ~20 s always empty); when the data stop, the edge keeps moving in real
        time and the growing gap shows it. Never seen data arrive: the edge is now (or the newest
        sample, when that is ahead). When only old data is held (newest sample more than 80 % of
        the window before that edge) it ends just after that sample, so the traces are never off
        screen; it follows the data again once they are back within half a window (hysteresis:
        a latency near the threshold never flips it)."""
        now = time.time()
        stats = self.engine.hub.stats_snapshot()
        ends = []
        for s in self._sids:
            st = stats.get(s)
            if st:
                ends.append(st["last_end"])
                if st.get("rate"):
                    self._rates[s] = float(st["rate"])
        if ends:
            self._note_latest(max(ends), now)
        live = now - self._live_lag(now)
        end = live
        if ends:
            latest = max(ends)
            limit = 0.5 if self._anchored else 0.8
            self._anchored = latest < live - limit * self._window
            end = latest + 0.02 * self._window if self._anchored else max(live, latest)
        return end - self._window, end

    def _note_latest(self, latest, now):
        """Record the arrival lag (now - newest sample end) each time the newest sample of the
        shown channels advances. Not counted: the first look and a change of the channel set
        (a reference only - that data may have arrived long ago; after a change the first lag
        measured replaces the old set's at once, see _live_lag), lags beyond LAG_LIMIT, and
        advances much faster than real time (a backfill of the instrument's buffer: its lags
        only shrink towards the real one and would make the view race through old data)."""
        prev, self._latest = self._latest, latest
        if prev is not None and latest > prev:
            lag = now - latest
            realtime = latest - prev <= 2.0 * (now - self._latest_at) + 5.0
            if abs(lag) <= LAG_LIMIT and realtime:
                if not self._lags and self._lag is not None and not self._relag:
                    # data back after a silence: the lag known before stays a candidate for
                    # LAG_MEMORY s, so the backfilled records do not pull the view back
                    self._lags.append((now, self._lag))
                self._lags.append((now, lag))
        if latest != prev:
            self._latest_at = now

    def _live_lag(self, now):
        """The smallest arrival lag of the last LAG_MEMORY s; none in that time: the last one
        used (data stopped: the view keeps scrolling in real time); never any: 0. A lag that
        grows (the smallest one expired) grows by at most LAG_GROWTH s per second, so the right
        edge never steps back - it only slows down for a moment. The first lag of another
        channel set is taken at once (the plots were rebuilt anyway)."""
        lags = self._lags
        while lags and lags[0][0] < now - LAG_MEMORY:
            lags.popleft()
        if lags:
            best = min(lag for _t, lag in lags)
            if self._lag is not None and best > self._lag and not self._relag:
                best = min(best, self._lag + LAG_GROWTH * max(0.0, now - self._lag_at))
            self._lag = best
            self._relag = False
        self._lag_at = now
        return self._lag or 0.0

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
            if self._tcur:
                self._place_cursors()
        text = (f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t0))} – "
                f"{time.strftime('%H:%M:%S', time.gmtime(t1))} UTC")
        if self.range_label.text() != text:
            self.range_label.setText(text)

    def _on_user_range(self, *_args):
        """Mouse pan (or a zoom while paused): stop following live data."""
        self._touched = time.monotonic()
        if self._live:
            self.set_live(False)

    def _on_wheel(self, ev, vb):
        """The mouse wheel over a plot (or its time axis): zooms the amplitude; with Shift it
        steps the window length (both stay live), with Ctrl it scrolls in time (pauses).
        Returns True (handled)."""
        self._touched = time.monotonic()
        self._wheel += ev.delta()
        steps = int(self._wheel / WHEEL_NOTCH)    # whole notches (towards zero for both directions)
        if steps:
            self._wheel -= steps * WHEEL_NOTCH
            mods = ev.modifiers() if hasattr(ev, "modifiers") else Qt.NoModifier
            if mods & Qt.ShiftModifier:
                self.step_window(-steps)           # up: shorter
            elif mods & Qt.ControlModifier:
                self.pan(-0.1 * steps)             # up: back in time
            else:
                self.zoom_amplitude(steps)         # up: larger
        ev.accept()
        return True

    def _drag_row(self, vb, pos):
        """The trace whose row holds the mouse (pos in vb's coordinates; above the first row:
        the first, below the last: the last), or None."""
        rows = [tr for tr in self._traces if tr.vb.isVisible()]
        if not rows:
            return None
        y = vb.mapToScene(pos).y()
        for tr in rows:
            r = tr.vb.sceneBoundingRect()
            if r.top() <= y <= r.bottom():
                return tr
        return rows[0] if y < rows[0].vb.sceneBoundingRect().top() else rows[-1]

    def _selected_rows(self, vb, ev):
        """The traces a drag selects, in order: Shift = every row from the one where the drag
        started to the one under the mouse now (top to bottom); Ctrl = those two rows only, in
        the order they were picked (calibration: signal first, then the sensor output; relative
        orientation: the reference first). Overlay: every channel (one row holds them all)."""
        start = next((tr for tr in self._traces if tr.vb is vb), None)
        if start is None or (self._overlay and len(self._traces) > 1):
            return [tr for tr in self._traces if tr.sid]
        here = self._drag_row(vb, ev.pos()) or start
        if self._sel_pair:
            return [start] if here is start else [start, here]
        i, j = self._traces.index(start), self._traces.index(here)
        return [tr for tr in self._traces[min(i, j):max(i, j) + 1] if tr.vb.isVisible()]

    @staticmethod
    def _selection_brush(gap):
        """The selected part: a light band behind the traces (they stay readable on it),
        hatched when a selected channel has a gap or an overlap there."""
        c = QColor(theme.COLORS["text"])
        c.setAlpha(80 if gap else 55)
        return QBrush(c, Qt.BDiagPattern) if gap else QBrush(c)

    def _has_gap(self, sid, lo, hi):
        """True when the live buffer does not hold [lo, hi) of a channel in one piece."""
        try:
            segs = self.engine.hub.window(sid, lo, hi)
        except Exception:  # noqa: BLE001 - drawing must never fail
            return False
        if len(segs) != 1:
            return True
        start, rate, y = segs[0]
        return start > lo + 1.0 / rate or start + len(y) / rate < hi - 1.0 / rate

    def _on_drag(self, ev, vb):
        """Shift+drag with the left button selects a time range on the rows from the one where
        the drag started to the one under the mouse; Ctrl+drag selects it on exactly two rows
        (the first and the one where the drag ends). Returns True when the drag was taken."""
        if ev.isStart():   # the modifier at the start decides; letting it go changes nothing
            mods = ev.modifiers() if hasattr(ev, "modifiers") else Qt.NoModifier
            left = ev.button() == Qt.LeftButton
            self._sel_pair = left and bool(mods & Qt.ControlModifier) and not bool(mods & Qt.ShiftModifier)
            self._selecting = left and (bool(mods & Qt.ShiftModifier) or self._sel_pair)
        if not self._selecting:
            return False
        self._touched = time.monotonic()
        a = vb.mapToView(ev.buttonDownPos()).x()
        b = vb.mapToView(ev.pos()).x()
        lo, hi = min(a, b), max(a, b)
        rows = self._selected_rows(vb, ev)
        if ev.isStart() or not self._sel_items or [tr.sid for tr in rows] != self._sel_sids:
            self._clear_selection()
            self.selection = None
            self._sel_sids = [tr.sid for tr in rows]
            for tr in rows:
                item = pg.LinearRegionItem((lo, hi), movable=False, brush=self._selection_brush(False))
                item.setZValue(-5)                      # behind the trace
                item.sid = tr.sid
                tr.vb.addItem(item, ignoreBounds=True)
                self._sel_items.append((tr.vb, item))
                count = pg.TextItem("", color=theme.COLORS["text"], anchor=(0, 0))   # samples selected
                count.setZValue(41)
                count.rate = tr.rate or self._rates.get(tr.sid, 0.0)
                count.sid = tr.sid
                tr.vb.addItem(count, ignoreBounds=True)
                self._sel_items.append((tr.vb, count))
        for svb, item in self._sel_items:
            if isinstance(item, pg.TextItem):
                item.setText(str(int(round((hi - lo) * item.rate))) if item.rate else "")
                item.setPos(lo, svb.viewRange()[1][1])
            else:
                item.setRegion((lo, hi))
        if ev.isFinish():
            self._selecting = False
            if hi - lo > 1e-3 and self._sel_sids:
                for _svb, item in self._sel_items:      # hatched where a channel has a gap
                    if isinstance(item, pg.LinearRegionItem) and self._has_gap(item.sid, lo, hi):
                        item.setBrush(self._selection_brush(True))
                self.selection = (lo, hi, list(self._sel_sids))
                # stays drawn until clear_selection() (the viewer: when its menu closes)
                self.selectionMade.emit(lo, hi, self._sel_sids[0], QCursor.pos())
            else:
                self.clear_selection()
        ev.accept()
        return True

    def selected_channels(self):
        """The channels of the last selection (in their order), or [] when there is none."""
        return list(self.selection[2]) if self.selection else []

    def clear_selection(self):
        """Remove the drawn selection (the viewer calls it when its menu closes)."""
        self._clear_selection()
        self.selection = None

    def _clear_selection(self):
        for vb, item in self._sel_items:
            vb.removeItem(item)
        self._sel_items = []
        self._sel_sids = []

    def _on_xrange(self, *_args):
        if self._setting_range:
            return
        t0, t1 = self.visible_range()
        if t1 > t0 and abs(self._plot_width() / (t1 - t0) - self._pps) > 1e-9 * self._pps:
            self._pps = self._plot_width() / (t1 - t0)   # a mouse zoom changes the time scale
            self._update_pps_button()
        self._range_moved()
        self._reload.start()  # coalesce the many range changes of one drag

    def _row_at(self, pos):
        """The trace (and whether over its spectrogram) at a scene position, or (None, False)."""
        overlay = self._overlay and len(self._traces) > 1
        for tr in self._traces:
            if tr.svb is not None and tr.svb.isVisible():
                r = tr.svb.sceneBoundingRect()
                r.setLeft(0)
                if r.contains(pos):
                    return tr, True
            if not tr.vb.isVisible():
                continue
            r = tr.vb.sceneBoundingRect()
            r.setLeft(0)
            if r.contains(pos):
                return (self._nearest_overlaid(pos) if overlay else tr), False
        return None, False

    def _nearest_overlaid(self, pos):
        """In overlay mode: the trace whose curve passes closest to the mouse."""
        vb = self._traces[0].vb
        p = vb.mapSceneToView(pos)
        best, dist = self._traces[0], math.inf
        for tr in self._traces:
            x, y = tr.curve.getData()
            if x is None or not len(x):
                continue
            i = min(len(x) - 1, int(np.searchsorted(x, p.x())))
            if math.isfinite(y[i]) and abs(y[i] - p.y()) < dist:
                best, dist = tr, abs(y[i] - p.y())
        return best

    def _on_click(self, ev):
        if ev.double() and ev.button() == Qt.LeftButton:
            self.set_live(True)
        elif ev.button() == Qt.RightButton:
            tr, _spec = self._row_at(ev.scenePos())
            self._menu_sid = tr.sid if tr is not None else None
            ev.accept()
            self.contextRequested.emit(self._menu_sid, QCursor.pos())

    def _on_mouse_moved(self, pos):
        tr, over_spec = self._row_at(pos)
        sid = tr.sid if tr is not None else None
        if sid != self._active:
            self._active = sid
            if sid:
                self._hovered = sid
            self._color_traces()
        if tr is None:
            self.hoverChanged.emit("")
            return
        if over_spec:
            p = tr.svb.mapSceneToView(pos)
            text = f"{self._fmt_time(p.x())}   {sid}   {p.y():.3g} Hz"
        else:
            p = tr.host.mapSceneToView(pos)
            text = f"{self._fmt_time(p.x())}   {sid}   {fmt_value(p.y(), tr.units != 'counts')} {tr.units}"
        self.hoverChanged.emit(text)

    @staticmethod
    def _fmt_time(t):
        if not math.isfinite(t):
            return ""
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d} UTC"

    def _on_screen(self):
        win = self.window()
        return self.isVisible() and not (win is not None and win.isMinimized())

    def _width_changed(self):
        """The plot got wider or narrower: at the same time scale it shows more or less time."""
        if not self._traces:
            return
        t0, t1 = self.visible_range()
        if abs((t1 - t0) - self._window) > 1e-3:
            if self._live:
                self._set_range(*self._live_range())
            else:
                self._set_range(t1 - self._window, t1)
        self._update_pps_button()

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
        history = getattr(self.engine, "history", None)
        if tr.pending and history is not None and history.generation != tr.hist_gen:
            return True          # recorded history it waited for has been read
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
            self._update_panels()

    def _sensitivity(self, sid):
        """(counts per unit, units) from the calibration the user entered for the channel, else
        from StationXML, or None. Looked up again after SENS_TTL seconds."""
        now = time.monotonic()
        hit = self._sens.get(sid)
        if hit and now - hit[0] < SENS_TTL:
            return hit[1]
        value = None
        cal = (self.config.get("calibration") or {}).get(sid)
        if isinstance(cal, dict):
            try:
                v = float(cal.get("sensitivity"))
                if math.isfinite(v) and v != 0:
                    value = (v, str(cal.get("units") or "units"))
            except (TypeError, ValueError):
                value = None
        responses = getattr(self.engine, "responses", None)
        if value is None and responses is not None:
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
        sid = tr.sid
        ts = self._ts(sid)
        spec = self.trace_spec(sid)
        rate_hint = self._rates.get(sid, 0.0)
        span = t1 - t0
        points = 2.0 * width
        hub = self.engine.hub
        history = getattr(self.engine, "history", None)
        tr.pending, tr.unfiltered = False, False
        tr.hist_gen = history.generation if history is not None else -1
        pre = pre_roll(spec, rate_hint)
        hub_first = hub.first_time(sid) if hasattr(hub, "first_time") else None
        summary = span / width >= SUMMARY_PX   # long window: older data from per-second summaries
        if summary:
            full_t0 = t0 if hub_first is None and history is None else max(t0, hub_first if hub_first else t1)
        else:
            full_t0 = t0
        segs = hub.window(sid, full_t0 - pre, t1) if full_t0 < t1 else []
        n_older = 0                            # segments from the recordings (not cached)
        if not summary and history is not None and (hub_first is None or t0 < hub_first):
            older, pending = history.segments(sid, t0 - pre, min(t1, hub_first) if hub_first else t1)
            tr.pending = pending
            n_older = len(older)
            segs = older + list(segs)          # the recordings before the live buffer
        bins = None
        if summary and full_t0 > t0:
            bins = self._summary_bins(tr, sid, t0, full_t0, spec, span / width)
        fixed = ts["offset"] is not None
        parts = []
        total, count, sumsq = 0.0, 0, 0.0
        if bins is not None:
            total, count = bins[4], bins[5]
        want_rms = sid in self._details          # one more pass over the data: only for Details
        cache = tr.cache
        cache.begin(spec)
        handles = []                             # per part: (run handle, clipped start index) or None
        for k, (start, rate, y) in enumerate(segs):
            handle = None
            if k >= n_older:   # the live buffer: filtered only where new data change it
                y, handle = cache.piece(start, rate, y, spec)
            elif spec is not None:   # the mean is taken out below (a low-pass keeps it here)
                y = dsp.apply_spec(y, rate, spec, demean=False)
            else:
                y = np.asarray(y, dtype=np.float64)
            i0 = max(0, int(math.ceil((t0 - start) * rate - 1e-6)))   # drop the filter pre-roll
            if i0 >= len(y):
                continue
            if i0:
                start += i0 / rate
                y = y[i0:]
            handles.append(None if handle is None else (handle, handle[1] + i0))
            total_y = float(np.sum(y))
            if math.isfinite(total_y):           # no NaN in it (the usual case): one pass
                good = y
            else:
                good = y[np.isfinite(y)]
                total_y = float(np.sum(good))
            total += total_y
            count += len(good)
            if want_rms:
                sumsq += float(np.dot(good, good))
            parts.append((start, rate, y))
        cache.end()
        tr.mean_pre = total / count if count else None   # what Null makes the offset
        shift = 0.0
        if fixed:
            shift = float(ts["offset"])
        elif self._demean and count:
            shift = total / count
        factor, tr.units, tr.has_sens = 1.0, "counts", True
        if self._units != "counts":
            sens = self._sensitivity(sid)
            if sens:
                factor = float(sens[0])
                name = str(sens[1])
                tr.units = UNIT_NAMES.get(name.upper(), name or "units")
                if self._units == "physical_g" and name.upper() in ACCEL_UNITS:
                    factor *= G
                    tr.units = "g"
            else:
                tr.has_sens = False
        tr.filter_text = dsp.describe(spec) if spec is not None else ""
        tr.rate = parts[0][1] if parts else rate_hint
        xs, ys = [], []
        if bins is not None and len(bins[0]):
            xs.append(bins[0])
            ys.append(bins[1].copy())
        for (start, rate, y), h in zip(parts, handles):
            max_points = max(4, int(points * len(y) / rate / span))
            if h is None or len(y) <= max_points:
                x, e = dsp.envelope(start, rate, y, max_points)
            else:   # bins on the run's own grid: those still holding the same samples are reused
                x, e = cache.envelope(h[0], h[1], y, bin_size(rate, span, width))
            if xs:   # a gap between hub segments: break the line
                xs.append(np.array([xs[-1][-1]]))
                ys.append(np.array([np.nan]))
            xs.append(x)
            ys.append(e)
        tr.stats = None
        tr.lo = tr.hi = None
        if xs:
            # offset and units on the decimated points only (the same as on every sample: the
            # envelope keeps each bin's extremes, which an offset and a factor only move)
            x = np.concatenate(xs)
            y = np.concatenate(ys)
            if shift:
                y -= shift
            if factor != 1.0:
                y /= factor
            finite = np.isfinite(y)
            if count and finite.any():
                tr.lo, tr.hi = float(np.min(y[finite])), float(np.max(y[finite]))
                rms = None
                if want_rms and bins is None:   # (per-second summaries keep no squares)
                    rms = math.sqrt(max(0.0, sumsq / count - tr.mean_pre ** 2)) / abs(factor)
                tr.stats = {"n": count, "mean": (tr.mean_pre - shift) / factor, "min": tr.lo, "max": tr.hi,
                            "rms": rms}
            tr.curve.setData(x, y, connect="finite")
        else:
            if self._units != "counts":
                tr.has_sens = self._sensitivity(sid) is not None
            tr.curve.setData([], [])
        if self._spec_on(sid) and tr.image is not None:
            self._compute_spectrogram(tr, parts, shift, factor, t0, t1, width, ts["contrast"])
        tr.t0, tr.t1, tr.version, tr.computed = t0, t1, self._version, now
        self._update_label(tr)
        self._update_detail_lines(tr)

    def _summary_bins(self, tr, sid, t0, t1, spec, px):
        """Points for [t0, t1) from per-second summaries, about two per pixel: the recordings
        (filtered as asked, read in the background) where they hold data, else the hub's
        summary of the last day (raw: it cannot be filtered - noted). Returns (x, y, ..., sum,
        count) - x / y the points (NaN breaks the line), sum / count of the samples (for the
        mean)."""
        b0 = int(math.floor(t0))
        b1 = max(b0 + 1, int(math.ceil(t1)))
        n = b1 - b0
        lo = np.full(n, np.nan)
        hi = np.full(n, np.nan)
        sm = np.zeros(n)
        ct = np.zeros(n, dtype=np.int64)
        history = getattr(self.engine, "history", None)
        if history is not None:
            from ..core.history import CHUNK
            hour = (b0 // CHUNK) * CHUNK
            while hour < b1:
                res, pending = history.chunk(sid, hour, spec)
                tr.pending = tr.pending or pending
                if res is not None:
                    a, b = max(b0, hour), min(b1, hour + CHUNK)
                    src = slice(a - hour, b - hour)
                    dst = slice(a - b0, b - b0)
                    have = res["cnt"][src] > 0
                    lo[dst] = np.where(have, res["lo"][src], lo[dst])
                    hi[dst] = np.where(have, res["hi"][src], hi[dst])
                    sm[dst] = np.where(have, res["sum"][src], sm[dst])
                    ct[dst] = np.where(have, res["cnt"][src], ct[dst])
                hour += CHUNK
        q = self.engine.hub.summary(sid, b0, b1) if hasattr(self.engine.hub, "summary") else None
        if q is not None:
            qb0, qlo, qhi, qsum, qcnt = q
            off = qb0 - b0
            if 0 <= off < n:
                m = min(len(qcnt), n - off)
                dst = slice(off, off + m)
                use = (ct[dst] == 0) & (qcnt[:m] > 0)
                if use.any():
                    tr.unfiltered = tr.unfiltered or spec is not None
                    lo[dst] = np.where(use, qlo[:m], lo[dst])
                    hi[dst] = np.where(use, qhi[:m], hi[dst])
                    sm[dst] = np.where(use, qsum[:m], sm[dst])
                    ct[dst] = np.where(use, qcnt[:m], ct[dst])
        g = max(1, int(round(px)))                      # bins per pixel: two points each
        pad = (-n) % g
        if pad:
            lo = np.concatenate([lo, np.full(pad, np.nan)])
            hi = np.concatenate([hi, np.full(pad, np.nan)])
        glo = np.where(np.isnan(lo), np.inf, lo).reshape(-1, g).min(axis=1)
        ghi = np.where(np.isnan(hi), -np.inf, hi).reshape(-1, g).max(axis=1)
        glo[~np.isfinite(glo)] = np.nan
        ghi[~np.isfinite(ghi)] = np.nan
        starts = b0 + np.arange(len(glo)) * g
        x = np.empty(2 * len(glo))
        y = np.empty(2 * len(glo))
        x[0::2], x[1::2] = starts, starts + g / 2.0
        y[0::2], y[1::2] = glo, ghi
        return x, y, None, None, float(sm.sum()), int(ct.sum())

    def _compute_spectrogram(self, tr, parts, shift, factor, t0, t1, width, contrast):
        """The spectrogram of the visible data: the segments are laid on one evenly sampled
        array (gaps quiet), cut into Hann-windowed pieces; colours on a log scale from the
        brightest level down SPEC_RANGE_DB (contrast steps shift it by SPEC_STEP_DB)."""
        if not parts:
            tr.image.clear()
            return
        rate = parts[0][1]
        n = int(round((t1 - t0) * rate))
        if n < 2 * self._spec_size:
            tr.image.clear()
            return
        arr = np.full(n, np.nan)
        for start, r, y in parts:
            if r != rate:
                continue
            i = int(round((start - t0) * rate))
            a, b = max(0, i), min(n, i + len(y))
            if b > a:
                arr[a:b] = (y[a - i:b - i] - shift) / factor
        res = dsp.spectrogram(arr, rate, self._spec_size, max(16, int(width)))
        if res is None:
            tr.image.clear()
            return
        power, step, first = res
        db = 10.0 * np.log10(power + 1e-30)
        top = float(np.percentile(db, 99.5)) - SPEC_STEP_DB * contrast
        nyq = rate / 2.0
        tr.image.setImage(db, autoLevels=False, levels=(top - SPEC_RANGE_DB, top))
        tr.image.setRect(QRectF(t0 + first - step / 2.0, 0.0, step * db.shape[0], nyq))
        if tr.nyq != nyq:
            tr.nyq = nyq
            tr.svb.setLimits(yMin=0.0, yMax=nyq)
            tr.svb.setYRange(0.0, nyq, padding=0)

    def _update_label(self, tr):
        c = theme.COLORS
        i = self._traces.index(tr)
        sta = ".".join(tr.sid.split(".")[:2])
        desc = self.engine.hub.descriptions.get(sta, "")
        physical = tr.units != "counts"
        ts = self._ts(tr.sid)
        if tr.lo is None:
            values = "no data in this time range"
        else:
            values = (f"min {fmt_value(tr.lo, physical)} &nbsp;max {fmt_value(tr.hi, physical)} "
                      f"&nbsp;{tr.units}")
        extra = []
        if tr.filter_text:
            extra.append(tr.filter_text + (" (own)" if ts["filter"] else ""))
        if ts["scale"] != 1.0:
            extra.append(fmt_gain(ts["scale"]))
        if ts["offset"] is not None:
            extra.append("offset fixed" + (", locked" if ts["locked"] else ""))
        elif ts["locked"]:
            extra.append("offset locked")
        name = html.escape(ts["label"]) + f" ({tr.sid})" if ts["label"] else tr.sid
        text = (f"<span style='color:{self.trace_color(i, tr.sid)}; font-weight:600'>{name}</span>"
                + (f" &nbsp;<span style='color:{c['text']}'>{desc}</span>" if desc else "")
                + f" &nbsp;<span style='color:{c['muted']}'>{values}</span>"
                + (f" &nbsp;<span style='color:{c['accent2']}'>{' · '.join(extra)}</span>" if extra else ""))
        if text != tr.text:
            tr.text = text
            tr.label.setText(text, size="8pt")

    def _update_detail_lines(self, tr):
        want = tr.sid in self._details and tr.stats is not None
        if not want:
            for line in tr.lines.values():
                tr.host.removeItem(line)
            tr.lines = {}
            return
        c = theme.COLORS
        for key, style in (("mean", Qt.DotLine), ("min", Qt.SolidLine), ("max", Qt.SolidLine)):
            line = tr.lines.get(key)
            if line is None:
                line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(c["text"], style=style))
                line.setZValue(55)
                tr.host.addItem(line, ignoreBounds=True)
                tr.lines[key] = line
            line.setValue(tr.stats[key])

    def _apply_y_ranges(self):
        mode = self._scale
        if mode == "pixel" and self._cpp is None:
            self._cpp = self._fitted_cpp()     # the first data in a new "pixel" window
            if self._cpp is not None:
                self._update_gain_label()
                self.changed.emit()
        spans = [tr.hi - tr.lo for tr in self._traces if tr.lo is not None]
        half_common = max(spans) / 2 if spans else 1.0
        half_inst = {}                     # instrument -> half of its largest component's range
        for tr in self._traces:
            if tr.lo is not None:
                k = instrument(tr.sid)
                half_inst[k] = max(half_inst.get(k, 0.0), (tr.hi - tr.lo) / 2)
        overlay = self._overlay and len(self._traces) > 1
        for tr in self._traces:
            if overlay and tr is not self._traces[0]:
                continue
            ts = self._ts(tr.sid) if tr.sid else TRACE_DEFAULTS
            lo_, hi_ = tr.lo, tr.hi
            if overlay:
                los = [t.lo for t in self._traces if t.lo is not None]
                his = [t.hi for t in self._traces if t.hi is not None]
                lo_, hi_ = (min(los), max(his)) if los else (None, None)
            # traces centred around 0: mean removed, a high- / band-pass, or a fixed offset
            spec = self.trace_spec(tr.sid) if tr.sid else None
            centred = (self._demean or ts["offset"] is not None
                       or (spec is not None and (spec.get("default") or spec.get("hp") is not None)))
            magnify = self._gain * (1.0 if overlay else float(ts["scale"]))
            if lo_ is None:
                lo, hi = -1.0, 1.0
            elif mode == "pixel":   # N units per pixel for the whole window, divided by the trace's factor
                mid = 0.0 if centred else (lo_ + hi_) / 2
                half = (self._cpp or 1.0) * self._row_px / 2.0 / (1.0 if overlay else float(ts["scale"]))
                lo, hi = mid - half, mid + half
            elif mode == "fixed":
                mid = 0.0 if centred else (lo_ + hi_) / 2
                lo, hi = mid - self._fixed / magnify, mid + self._fixed / magnify
            elif mode in ("common", "common_all"):
                mid = (lo_ + hi_) / 2
                shared = half_common if mode == "common_all" else half_inst.get(instrument(tr.sid), half_common)
                half = (shared if not overlay else (hi_ - lo_) / 2) / magnify
                lo, hi = mid - half, mid + half
            else:
                mid, half = (lo_ + hi_) / 2, (hi_ - lo_) / 2 / magnify
                lo, hi = mid - half, mid + half
            if hi - lo <= 0:   # a flat trace: give it some height around its value
                d = max(abs(lo) * 1e-3, 1e-12 if self._units != "counts" else 1.0)
                lo, hi = lo - d, hi + d
            if (lo, hi, mode) != tr.ykey:
                tr.ykey = (lo, hi, mode)
                tr.vb.setYRange(lo, hi, padding=0 if mode in ("fixed", "pixel") else 0.06)
                tr.yrange = tuple(float(v) for v in tr.vb.viewRange()[1])
        if overlay:   # the others are drawn in the first row: they share its range
            for tr in self._traces[1:]:
                tr.ykey, tr.yrange = self._traces[0].ykey, self._traces[0].yrange
        if self._acur:
            self._place_cursors()

    def _update_note(self):
        if self._units == "counts":
            text = ""
        else:
            missing = [tr.sid for tr in self._traces if tr.sid and not tr.has_sens]
            text = ""
            if missing:
                more = f" and {len(missing) - 3} more" if len(missing) > 3 else ""
                text = (f"No StationXML response or calibration for {', '.join(missing[:3])}{more} - "
                        "shown in counts")
        notes = [text] if text else []
        if any(tr.pending for tr in self._traces):
            notes.append("Reading the recorded history...")
        raw = [tr.sid for tr in self._traces if tr.sid and tr.unfiltered]
        if raw:
            notes.append(f"Older data of {', '.join(raw[:2])}{' and more' if len(raw) > 2 else ''} not recorded: "
                         "shown without the filter")
        text = " · ".join(notes)
        if self.note.text() != text:
            self.note.setText(text)
            self.note.setToolTip(text)

    # ------------------------------------------------------------------------------------
    # Qt events
    # ------------------------------------------------------------------------------------
    def eventFilter(self, obj, ev):
        t = ev.type()
        if obj is self.glw and t == QEvent.Resize:
            self._fit_rows(ev.size().height() - 2 * self.glw.frameWidth())
            self._version += 1   # more / fewer pixels: decimate again
            QTimer.singleShot(0, self._width_changed)   # the time scale stays: the window follows
        elif obj is self.scroll.viewport() and t == QEvent.Resize:
            self._size_canvas(ev.size().width(), ev.size().height())
        elif obj in (self.glw.viewport(), self.empty) and t in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            if not ev.mimeData().hasFormat(CHANNEL_MIME):
                return False
            ev.acceptProposedAction()
            if t == QEvent.Drop:
                sids = bytes(ev.mimeData().data(CHANNEL_MIME)).decode("utf-8").split()
                added = self.add_channels(sids)
                if added:
                    self.channelsDropped.emit(added)
            return True
        elif obj is self.glw.viewport() and (
                t in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.Wheel)
                or (t == QEvent.MouseMove and ev.buttons() != Qt.NoButton)):
            self._touched = time.monotonic()   # the user is working with the plots: no auto-return yet
        elif obj is self.glw.viewport() and t == QEvent.Leave and self._active is not None:
            self._active = None
            self._color_traces()
            self.hoverChanged.emit("")
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
