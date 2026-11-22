"""Offscreen tests of the data widgets: ChannelsPanel (table, latency colours, filter, ticks),
WaveformView (rendering, speed, filters / units / scale, pause and live, visible range; the
viewer features are in test_ui_viewer.py) and
SpectrumDialog, all on FakeEngine's synthetic channels. Also saves the screenshots
build/screens/waveforms.png and build/screens/channels.png for a visual check.

Nothing here touches the network: FakeEngine never connects anywhere.
"""

import math
import os
import sys
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from fake_engine import FakeEngine
from seiszone.config import Config
from seiszone.core.hub import DataHub, StreamBuffer
from seiszone.ui import theme
from seiszone.ui.channels_panel import (C_LAT, C_RATE, C_SHOW, C_SID, ChannelsPanel, fmt_latency, latency_level,
                                        packet_interval)
from seiszone.ui.filter_dialog import filter_catalogue
from seiszone.ui.spectrum_view import SpectrumDialog
from seiszone.ui.waveform_view import WINDOWS, WaveformView, nearest_window, pre_roll

if sys.platform == "win32" and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")   # offscreen Qt has no fonts of its own

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS = os.path.join(ROOT, "build", "screens")
SIDS = ["WB.KUM.0J.HNZ", "WB.KUM.0J.HNN", "WB.KUM.0J.HNE", "SS.SMR03..HHZ"]


_APP = []   # the QApplication must outlive every widget, also those freed by the final gc run


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    _APP.append(app)
    theme.apply(app, "dark")
    app.setFont(QFont("Segoe UI", 9))   # what Windows uses (offscreen Qt would pick any font)
    return app


@pytest.fixture(scope="module")
def engine(qapp, tmp_path_factory):
    """Ten minutes of the four fake channels, ending now (built once: encoding takes seconds)."""
    return FakeEngine(config=Config(str(tmp_path_factory.mktemp("cfg") / "config.json")), seconds=600)


def pump(app, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


class FakeResponses:
    """ResponseStore stand-in: only the Fortimus channels have a sensitivity."""

    def __init__(self):
        self.asked = []

    def has(self, net, sta):
        return net == "WB"

    def sensitivity(self, sid, t=None):
        self.asked.append(sid)
        return (4.0e5, "M/S**2") if sid.startswith("WB.") else None


# ------------------------------------------------------------------------------------------
# channels panel
# ------------------------------------------------------------------------------------------
def test_latency_rules():
    assert latency_level(1.0) == "ok"
    assert latency_level(29.0) == "ok"
    assert latency_level(45.0) == "late"
    assert latency_level(45.0, interval=25.0) == "ok"       # within 2 packet intervals
    assert latency_level(299.0) == "late"
    assert latency_level(301.0) == "old"
    assert latency_level(float("inf")) == "old"
    assert packet_interval({"packets": 10, "first": 0.0, "last_end": 25.0}) == 2.5
    assert packet_interval({"packets": 1, "first": 0.0, "last_end": 25.0}) == 0.0
    assert fmt_latency(0.84) == "0.8 s"
    assert fmt_latency(252) == "4 min 12 s"
    assert fmt_latency(3 * 3600 + 300) == "3 h 05 min"
    assert fmt_latency(2 * 86400 + 4 * 3600) == "2 d 04 h"


def test_channels_table_lists_fake_channels(engine):
    panel = ChannelsPanel(engine, engine.config)
    m, proxy = panel.model, panel.proxy
    assert sorted(r["sid"] for r in m.rows) == sorted(SIDS)
    rates = {r["sid"]: r["text"][C_RATE] for r in m.rows}
    assert rates == {"WB.KUM.0J.HNZ": "200", "WB.KUM.0J.HNN": "200", "WB.KUM.0J.HNE": "200",
                     "SS.SMR03..HHZ": "100"}
    texts = {r["sid"]: r["text"] for r in m.rows}
    assert texts["WB.KUM.0J.HNZ"][2] == "KUMARI"
    assert texts["WB.KUM.0J.HNZ"][8].startswith("KUMARI (10.8.0.182:18000)")
    assert texts["SS.SMR03..HHZ"][8] == "Gecko 0000A1B2"
    # fresh data -> green; the table is sorted by channel code
    assert {r["level"] for r in m.rows} == {"ok"}
    first = proxy.data(proxy.index(0, C_SID))
    assert first == "SS.SMR03..HHZ"
    assert proxy.data(proxy.index(0, C_LAT), Qt.ForegroundRole).name() == theme.COLORS["ok"]
    # sorting by rate puts the 100 sps channel first
    panel.table.sortByColumn(C_RATE, Qt.AscendingOrder)
    assert proxy.data(proxy.index(0, C_SID)) == "SS.SMR03..HHZ"
    panel.table.sortByColumn(C_RATE, Qt.DescendingOrder)
    assert proxy.data(proxy.index(3, C_SID)) == "SS.SMR03..HHZ"
    assert "4 channels, 0 ticked" in panel.count.text()


@pytest.mark.parametrize("age, level, color", [(120, "late", "warn"), (900, "old", "danger")])
def test_latency_colours(qapp, tmp_path, age, level, color):
    eng = FakeEngine(config=Config(str(tmp_path / "config.json")), seconds=60, now=time.time() - age)
    panel = ChannelsPanel(eng, eng.config)
    assert {r["level"] for r in panel.model.rows} == {level}
    idx = panel.proxy.index(0, C_LAT)
    assert panel.proxy.data(idx, Qt.ForegroundRole).name() == theme.COLORS[color]
    assert "min" in panel.proxy.data(idx)
    assert not panel.proxy.data(panel.proxy.index(0, C_SID), Qt.DecorationRole).isNull()


def test_filter_box(engine):
    panel = ChannelsPanel(engine, engine.config)
    panel.filter.setText("smr03")
    assert panel.proxy.rowCount() == 1
    assert "1 of 4 channels" in panel.count.text()
    panel.filter.setText("KUMARI")          # the description matches too
    assert panel.proxy.rowCount() == 3
    panel.filter.setText("gecko")           # and the source
    assert panel.proxy.rowCount() == 1
    panel.filter.setText("")
    assert panel.proxy.rowCount() == 4


def test_shown_changed(engine):
    panel = ChannelsPanel(engine, engine.config)
    got = []
    panel.shownChanged.connect(got.append)
    proxy = panel.proxy
    row = [proxy.data(proxy.index(r, C_SID)) for r in range(4)].index("WB.KUM.0J.HNZ")
    assert proxy.setData(proxy.index(row, C_SHOW), Qt.Checked.value, Qt.CheckStateRole)
    assert got[-1] == ["WB.KUM.0J.HNZ"]
    assert proxy.data(proxy.index(row, C_SHOW), Qt.CheckStateRole) == Qt.Checked
    # filter + Show all ticks only the listed rows, appended in table order
    panel.filter.setText("SMR03")
    panel.show_listed()
    assert got[-1] == ["WB.KUM.0J.HNZ", "SS.SMR03..HHZ"]
    panel.filter.setText("")
    panel.show_listed()
    assert len(got[-1]) == 4 and panel.shown() == got[-1]
    panel.hide_listed()
    assert got[-1] == []
    # double-click: a viewer with only that channel (the ticks stay as they are)
    asked = []
    panel.viewRequested.connect(asked.append)
    panel._double_clicked(proxy.index(row, C_SID))
    assert asked == [["WB.KUM.0J.HNZ"]] and got[-1] == []
    # set_shown does not emit (MainWindow already knows); a refresh keeps the ticks
    n = len(got)
    panel.set_shown(["WB.KUM.0J.HNE", "XX.NEW..HHZ"])
    panel.refresh()
    assert len(got) == n
    assert panel.shown() == ["WB.KUM.0J.HNE", "XX.NEW..HHZ"]
    # selection survives a refresh
    panel.table.selectRow(row)
    panel.refresh()
    assert panel.selected() == ["WB.KUM.0J.HNZ"]


# ------------------------------------------------------------------------------------------
# waveform view
# ------------------------------------------------------------------------------------------
@pytest.fixture
def view(qapp, engine):
    engine.responses = None
    v = WaveformView(engine, engine.config)
    v.resize(1400, 800)
    v.show()
    pump(qapp)
    v.set_channels(SIDS)
    pump(qapp, 0.1)
    v.set_window(600)      # once the plots have their width (the time scale follows it)
    v.refresh()
    yield v
    v.close()
    v.deleteLater()
    pump(qapp)


def test_helpers():
    assert nearest_window(290) == 300
    assert nearest_window("junk") == 300
    assert nearest_window(10 ** 6) == WINDOWS[-1][0]
    assert pre_roll("none") == 0.0
    assert pre_roll("hp0.1") == 30.0
    assert pre_roll("bp1-10") == 3.0


def test_waveform_renders(view, engine, qapp):
    assert view.channels() == SIDS
    assert len(view._traces) == 4
    t0, t1 = view.visible_range()
    assert t1 - t0 == pytest.approx(600, abs=0.01)
    assert abs(t1 - time.time()) < 5
    width = view._plot_width()
    for tr in view._traces:
        x, y = tr.curve.getData()
        assert x is not None and len(x) > 100
        assert len(x) <= 2 * width + 50                    # decimated to ~2 points per pixel
        assert x[0] >= t0 - 1 and x[-1] <= t1 + 1
        assert tr.hi > 30000                              # the burst is visible
        assert tr.sid in tr.label.text and "min" in tr.label.text
    assert "KUMARI" in view._traces[0].panel.entries[0][0]       # the site is in the label box
    # the same channels again: nothing rebuilt
    plots = [tr.vb for tr in view._traces]
    view.set_channels(list(SIDS))
    assert [tr.vb for tr in view._traces] == plots
    view.set_channels(SIDS[:2])
    assert len(view._traces) == 2
    assert [tr.vb for tr in view._traces] == plots[:2]         # plots are reused
    assert all(vb.scene() is None for vb in plots[2:])        # the others are gone
    pump(qapp, 0.1)
    assert view._traces[0].vb.height() > 300                  # and fill the freed height
    view.set_channels(SIDS[::-1])
    assert view._traces[0].sid == SIDS[-1] and SIDS[-1] in view._traces[0].label.text
    view.set_channels([])
    assert view.stack.currentWidget() is view.empty
    assert view.visible_range()[1] > view.visible_range()[0]


def test_gaps_break_the_line(qapp, tmp_path):
    eng = FakeEngine(config=Config(str(tmp_path / "config.json")), seconds=60)  # (the fake burst needs >= 40 s)
    sid = "SS.SMR03..HHZ"
    last = eng.hub.stats_snapshot()[sid]["last_end"]
    y = np.zeros(1000, dtype=np.int32)
    from seiszone.core import mseed
    for rec in mseed.encode_records("SS", "SMR03", "", "HHZ", 100, last + 5.0, y):   # a 5 s gap
        eng.hub.add_record(rec, "gecko:0000A1B2")
    v = WaveformView(eng, eng.config)
    v.resize(900, 300)
    v.show()
    v.set_window(60)
    v.set_channels([sid])
    x, y = v._traces[0].curve.getData()
    assert np.isnan(y).sum() == 1
    v.close()
    v.deleteLater()


def test_refresh_speed(view, qapp):
    """4 channels x 10-minute window: refresh() well under 50 ms, also when every trace has to
    be recomputed (a setting changed / the view moved by a pixel each time)."""
    times = []
    for _ in range(12):
        pump(qapp, 0.02)
        t = time.perf_counter()
        view.refresh()
        times.append(time.perf_counter() - t)
    forced = []
    for _ in range(8):
        view._version += 1
        t = time.perf_counter()
        view.refresh()
        forced.append(time.perf_counter() - t)
    avg, avg_forced = sum(times) / len(times), sum(forced) / len(forced)
    print(f"refresh avg {avg * 1000:.1f} ms, all traces recomputed {avg_forced * 1000:.1f} ms")
    assert avg < 0.050
    assert avg_forced < 0.050


def test_refresh_stays_smooth_with_12_channels_60_minutes(qapp, tmp_path):
    """12 channels x 200 sps x 60 min with a band-pass filter: choosing the channels and every
    refresh after it stay short (the work is spread over refreshes), and all traces get drawn."""
    class Eng:
        pass
    eng = Eng()
    eng.config = Config(str(tmp_path / "config.json"))
    eng.responses = None
    eng.hub = hub = DataHub(buffer_seconds=3600)
    now = time.time()
    rng = np.random.default_rng(1)
    sids = [f"XX.S{i:02d}..HHZ" for i in range(12)]
    for sid in sids:
        net, sta, loc, cha = sid.split(".")
        buf = hub.buffers[sid] = StreamBuffer(200.0, 3600)
        buf.add(now - 3600, rng.normal(0, 100, 720000))
        hub.stats[sid] = {"sid": sid, "net": net, "sta": sta, "loc": loc, "cha": cha, "rate": 200.0,
                          "source": "", "packets": 1800, "first": now - 3600, "last_end": now,
                          "last_arrival": now, "latency": 0.0, "gaps": 0, "gap_seconds": 0.0, "last_gap": None,
                          "backfilled": 0, "last_value": 0.0}
    eng.config.update_section("view", {"window_seconds": 3600, "filter": "bp1-10"})
    v = WaveformView(eng, eng.config)
    v.resize(1400, 900)
    v.show()
    pump(qapp)
    import gc

    def measure():
        """(seconds of set_channels, seconds of the slowest refresh until all traces are drawn)."""
        v.set_channels([])
        gc.collect()      # the timing is of the view, not of a collection of earlier tests' objects
        gc.disable()
        try:
            t = time.perf_counter()
            v.set_channels(sids)
            first = time.perf_counter() - t
            worst = 0.0
            for _ in range(40):
                if all(tr.version == v._version for tr in v._traces):
                    break
                t = time.perf_counter()
                v.refresh()
                worst = max(worst, time.perf_counter() - t)
        finally:
            gc.enable()
        return first, worst

    first, worst = measure()
    if first >= 0.5 or worst >= 0.15:   # a busy PC (another program) for a moment: measure once more
        again = measure()
        first, worst = min(first, again[0]), min(worst, again[1])
    print(f"12 ch x 60 min band-pass: set_channels {first * 1000:.0f} ms, worst refresh {worst * 1000:.0f} ms")
    assert all(tr.version == v._version for tr in v._traces)
    assert first < 0.5
    assert worst < 0.15
    v.close()
    v.deleteLater()


def test_theme_change_recolours(view, qapp):
    tr = view._traces[0]
    try:
        theme.apply(qapp, "light")
        pump(qapp)
        assert view.glw.backgroundBrush().color().name() == theme.LIGHT["plot_bg"]
        # colour-coded components: the vertical channel is green
        assert tr.curve.opts["pen"].color().name() == theme.COMPONENTS_LIGHT["Z"]
        assert theme.COMPONENTS_LIGHT["Z"] in tr.label.text
    finally:
        theme.apply(qapp, "dark")
        pump(qapp)
    assert tr.curve.opts["pen"].color().name() == theme.COMPONENTS_DARK["Z"]


def test_filters_units_scale(view, engine):
    tr = view._traces[0]
    raw_hi = tr.hi
    for key, _label, _spec in filter_catalogue(engine.config):
        view.set_filter(key)
        assert view.filter_on() and tr.version == view._version and tr.hi is not None, key
    view.set_filter("hp1")
    assert tr.hi < raw_hi                     # the 1.3 Hz sine and the offset are reduced
    assert view.state()["filter"] == "hp1" and view.state()["filter_on"] is True
    assert view.filter_button.isChecked() and view.filter_button.text() == "Filter: High-pass 1 Hz"
    view.toggle_filter()                      # F: off, the choice is kept for the next F
    assert not view.filter_on() and view.filter_key() == "hp1" and view.filter_button.text() == "Filter off"
    assert tr.hi == pytest.approx(raw_hi, rel=0.2)
    view.filter_button.click()                # the button switches it on again
    assert view.filter_on() and view.filter_key() == "hp1"
    view.set_filter_on(False)

    # physical units without a response store: counts plus a note
    view.units_box.setCurrentIndex(view.units_box.findData("physical"))
    assert "counts" in view.note.text()
    assert "counts" in tr.label.text
    # with StationXML for the WB channels only
    engine.responses = FakeResponses()
    view._sens.clear()
    view._version += 1
    view._reload_now()
    assert "m/s²" in tr.label.text
    assert tr.hi == pytest.approx(raw_hi / 4.0e5, rel=0.05)
    assert "SS.SMR03..HHZ" in view.note.text() and "WB.KUM" not in view.note.text()
    engine.responses = None
    view.units_box.setCurrentIndex(view.units_box.findData("counts"))
    assert view.note.text() == ""

    # scale modes
    view.scale_box.setCurrentIndex(view.scale_box.findData("common"))     # per instrument
    spans = {round(tr.yrange[1] - tr.yrange[0], 3) for tr in view._traces if tr.sid.startswith("WB.KUM.")}
    assert len(spans) == 1                                                  # one sensor: one range
    view.scale_box.setCurrentIndex(view.scale_box.findData("common_all"))
    spans = {round(tr.yrange[1] - tr.yrange[0], 3) for tr in view._traces}
    assert len(spans) == 1                                                  # one range for all
    view.scale_box.setCurrentIndex(view.scale_box.findData("fixed"))
    assert view._fixed_action.isVisible()     # (in the bar, or in its » menu when narrow)
    view.fixed_edit.setText("5000")
    view.fixed_edit.editingFinished.emit()
    assert all(tr.yrange == (-5000.0, 5000.0) for tr in view._traces)
    assert view.state()["fixed"] == 5000.0
    view.scale_box.setCurrentIndex(view.scale_box.findData("auto"))
    assert not view._fixed_action.isVisible()
    assert tr.ykey[:2] == (tr.lo, tr.hi)

    # demean off keeps the raw offset; on removes it
    view.demean_box.setChecked(False)
    assert view.state()["demean"] is False
    view.demean_box.setChecked(True)
    assert abs(tr.lo + tr.hi) < 0.5 * (tr.hi - tr.lo)

    # settings come back in a new view made from the saved state
    view.set_window(120)
    again = WaveformView(engine, engine.config, state=view.state())
    assert again.pps() == pytest.approx(view.pps()) and again._scale == "auto" and again._demean is True
    assert again.channels() == SIDS and again.filter_key() == "hp1" and not again.filter_on()
    again.deleteLater()
    view.set_window(600)


def test_pause_live_and_range(view, qapp):
    ranges = []
    view.rangeChanged.connect(lambda a, b: ranges.append((a, b)))
    assert view.is_live()
    t0, t1 = view.visible_range()
    # a mouse zoom: the view box changes its range and reports a manual change
    vb = view._traces[2].vb
    vb.setXRange(t0 + 200, t0 + 260, padding=0)
    vb.sigRangeChangedManually.emit([True, False])
    assert not view.is_live()
    assert view.live_button.text() == "Paused – back to live"
    pump(qapp, 0.1)                       # the coalesced reload runs
    assert view.visible_range() == pytest.approx((t0 + 200, t0 + 260), abs=0.01)
    assert ranges and ranges[-1] == pytest.approx((t0 + 200, t0 + 260), abs=0.01)
    tr = view._traces[0]
    assert tr.t0 == pytest.approx(t0 + 200, abs=0.01)
    x, _y = tr.curve.getData()
    assert x[0] >= t0 + 199 and x[-1] <= t0 + 261
    # refresh while paused does not move the range
    view.refresh()
    assert view.visible_range() == pytest.approx((t0 + 200, t0 + 260), abs=0.01)
    # window length while paused keeps the right edge
    view.set_window(120)
    assert view.visible_range() == pytest.approx((t0 + 140, t0 + 260), abs=0.01)
    # back to live
    view.live_button.setChecked(True)
    assert view.is_live()
    a, b = view.visible_range()
    assert b - a == pytest.approx(120, abs=0.01) and abs(b - time.time()) < 5
    view.set_live(False)
    assert not view.is_live() and not view.live_button.isChecked()
    view.set_live(True)
    view.set_window(600)


def test_hidden_view_does_no_work(view):
    view.hide()
    view._version += 1
    view.refresh()
    assert all(tr.version != view._version for tr in view._traces)
    view.show()


# ------------------------------------------------------------------------------------------
# spectrum
# ------------------------------------------------------------------------------------------
def test_spectrum_dialog(qapp, engine):
    t1 = engine.hub.latest_time()
    dlg = SpectrumDialog(engine, SIDS + ["XX.NONE..HHZ"], t1 - 120, t1)
    assert set(dlg.curves) == set(SIDS)
    f, a = dlg.curves["WB.KUM.0J.HNZ"].getData()
    assert len(f) > 10
    assert any("XX.NONE..HHZ" in n for n in dlg.notes)
    dlg.units_box.setCurrentIndex(1)            # physical without responses -> counts + note
    assert any("shown in counts" in n for n in dlg.notes)
    assert set(dlg.curves) == set(SIDS)
    dlg.close()


# ------------------------------------------------------------------------------------------
# screenshots
# ------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------
# review fixes
# ------------------------------------------------------------------------------------------

class _Clock:
    """time for waveform_view only (the rest of the program keeps the real clock)."""

    def __init__(self, t):
        self.t = t
        self.perf_counter, self.strftime, self.gmtime = time.perf_counter, time.strftime, time.gmtime

    def time(self):
        return self.t


class _StatsHub:
    def __init__(self, sid):
        self.sid, self.last_end = sid, 0.0

    def stats_snapshot(self):
        return {self.sid: {"last_end": self.last_end}}


def test_live_range_does_not_jump(view, monkeypatch):
    from seiszone.ui import waveform_view as wv
    sid = SIDS[0]
    clock = _Clock(1.8e9)
    monkeypatch.setattr(wv, "time", clock)
    hub = _StatsHub(sid)
    monkeypatch.setattr(view, "engine", type("E", (), {"hub": hub})())
    # 60-min window, GPS-timed instrument, PC clock 0.5 s behind: a record ends "in the future"
    view._window = 3600
    view._anchored = False
    ends = []
    for k in range(40):
        clock.t = 1.8e9 + k * 0.25
        hub.last_end = 1.8e9 + 0.5 + (k // 4)            # a new record every second
        ends.append(view._live_range()[1])
    steps = np.diff(ends)
    assert steps.max() < 1.5 and steps.min() > -0.01, steps
    # 30-s window, latency right at the 80 % threshold: no flipping back and forth
    view._window = 30
    view._anchored = False
    clock.t = 2e9
    picked = []
    for k in range(20):
        hub.last_end = clock.t - 24 + (0.2 if k % 2 else -0.2)
        picked.append(view._live_range()[1] - clock.t)
    assert max(picked[1:]) - min(picked[1:]) < 1, picked      # (it used to flip by ~24 s)
    view._window = 600
    view._anchored = False


def test_fixed_scale_low_pass_keeps_the_offset(qapp, tmp_path):
    eng = FakeEngine(config=Config(str(tmp_path / "config.json")), seconds=60)
    from seiszone.core import mseed
    now = time.time()
    rng = np.random.default_rng(3)
    y = (50000 + rng.normal(0, 100, 6000)).astype(int)    # a sensor with a DC offset
    for rec in mseed.encode_records("XX", "OFF", "", "HHZ", 100, now - 60, y):
        eng.hub.add_record(rec, "test")
    eng.config.update_section("view", {"filter": "lp5", "demean": False, "scale": "fixed",
                                       "fixed_scale": 1000.0, "window_seconds": 60})
    v = WaveformView(eng, eng.config, state={"scale": "fixed"})
    v.resize(900, 300)
    v.show()
    v.set_channels(["XX.OFF..HHZ"])
    pump(qapp, 0.1)
    v.refresh()
    tr = v._traces[0]
    lo, hi = tr.yrange
    assert hi - lo == pytest.approx(2000) and lo < tr.lo and tr.hi < hi, (tr.yrange, tr.lo, tr.hi)
    v.close()
    v.deleteLater()


def test_many_channels_scroll(qapp, tmp_path):
    from seiszone.core import mseed
    eng = FakeEngine(config=Config(str(tmp_path / "config.json")), seconds=60)
    now = time.time()
    sids = []
    for k in range(30):
        sta = f"S{k:02d}"
        for rec in mseed.encode_records("XX", sta, "", "HHZ", 10, now - 60, list(range(600))):
            eng.hub.add_record(rec, "test")
        sids.append(f"XX.{sta}..HHZ")
    v = WaveformView(eng, eng.config)
    v.resize(1200, 650)
    v.show()
    v.set_channels(sids)
    pump(qapp, 0.2)
    bar = v.scroll.verticalScrollBar()
    assert bar.maximum() > 0, "30 channels must scroll in a 650 px view"
    assert v.glw.height() >= 30 * 24 + 26
    last = v._traces[-1].vb
    assert last.height() >= 23
    bar.setValue(bar.maximum())
    pump(qapp, 0.1)
    bottom = v.glw.mapTo(v.scroll.viewport(), v.glw.rect().bottomLeft()).y()
    assert bottom <= v.scroll.viewport().height() + 1          # the time axis can be reached
    v.set_channels(sids[:3])
    pump(qapp, 0.1)
    assert v.scroll.verticalScrollBar().maximum() == 0
    v.close()
    v.deleteLater()


# ------------------------------------------------------------------------------------------
# live edge follows the data, wheel, pause and auto-return
# ------------------------------------------------------------------------------------------

def _fresh_live(view, window):
    """The view as just started (no arrival lag seen yet), with this window length."""
    view._window = window
    view._anchored = False
    view._lags.clear()
    view._lag = view._latest = None
    view._relag = False


def _fortimus_feed(view, clock, hub, t_start, seconds, offset=18.8, rec=2.56, base=0.4, seed=5, stop=None):
    """Simulate a Fortimus: 512-sample records at 200 sps (2.56 s each), available `base` s plus
    0-0.3 s of network jitter after their last sample, seen through a PC clock `offset` s fast,
    refreshed every 0.25 s. Records ending after `stop` (true time) never come. Returns per
    refresh (pc time, newest sample end, live right edge, a record arrived, a lag was known)."""
    rng = np.random.default_rng(seed)
    ends = t_start + rec * np.arange(1, int(seconds / rec) + 2)
    arrive = ends + base + rng.uniform(0, 0.3, len(ends))
    out = []
    latest = None
    for k in range(int(seconds / 0.25)):
        true = t_start + k * 0.25
        got = ends[(arrive <= true) & (ends <= (stop if stop is not None else np.inf))]
        new = len(got) and got[-1] != latest
        if len(got):
            latest = hub.last_end = float(got[-1])
        clock.t = true + offset
        end = view._live_range()[1]
        out.append((clock.t, latest, end, bool(new), view._lag is not None))
    return out


def test_live_edge_follows_the_data_not_the_pc_clock(view, monkeypatch):
    """PC clock 18.8 s fast, 2.5 s Fortimus records: the newest sample stays within 1 % of the
    window from the right edge (it used to sit ~21 s = 7 % left of it) and touches the edge as
    each record arrives; the view scrolls smoothly in between and never jumps back."""
    from seiszone.ui import waveform_view as wv
    clock = _Clock(1.8e9)
    monkeypatch.setattr(wv, "time", clock)
    hub = _StatsHub(SIDS[0])
    monkeypatch.setattr(view, "engine", type("E", (), {"hub": hub})())
    _fresh_live(view, 300)
    rows = _fortimus_feed(view, clock, hub, 1.8e9, 180)
    before = [end - latest for _pc, latest, end, _new, known in rows if latest is not None and not known]
    assert before and min(before) > 18        # (the PC clock's edge: the data ~21 s left of it)
    warm = next(i for i, r in enumerate(rows) if r[4]) + 1
    gaps = [end - latest for _pc, latest, end, _new, _k in rows[warm:]]
    assert min(gaps) >= -1e-6, "the newest sample must never be past the right edge"
    assert max(gaps) <= 0.01 * 300, max(gaps)
    assert max(end - latest for _pc, latest, end, new, _k in rows[warm:] if new) < 0.6
    steps = np.diff([r[2] for r in rows[warm:]])
    assert steps.min() > -1e-6 and steps.max() < 1.0, (steps.min(), steps.max())   # smooth, never back
    assert view._lag == pytest.approx(18.8 + 0.4 + 0.25, abs=0.35)
    _fresh_live(view, 600)


def test_live_edge_pc_clock_slow(view, monkeypatch):
    """PC clock 18.8 s slow (negative lag): the data are "in the future"; the edge used to stop
    at the newest sample and jump a whole record (2.56 s) each time one came. Now it scrolls
    smoothly with the data, never past them and never back."""
    from seiszone.ui import waveform_view as wv
    clock = _Clock(1.8e9)
    monkeypatch.setattr(wv, "time", clock)
    hub = _StatsHub(SIDS[0])
    monkeypatch.setattr(view, "engine", type("E", (), {"hub": hub})())
    _fresh_live(view, 300)
    rows = _fortimus_feed(view, clock, hub, 1.8e9, 120, offset=-18.8)
    warm = next(i for i, r in enumerate(rows) if r[4]) + 1
    gaps = [end - latest for _pc, latest, end, _new, _k in rows[warm:]]
    assert min(gaps) >= -1e-6 and max(gaps) <= 0.01 * 300, (min(gaps), max(gaps))
    steps = np.diff([r[2] for r in rows[warm:]])
    assert steps.min() > -1e-6 and steps.max() < 1.0, (steps.min(), steps.max())
    assert view._lag == pytest.approx(-18.8 + 0.4 + 0.25, abs=0.35)
    _fresh_live(view, 600)


class _TwoHub:
    def __init__(self, sids):
        self.ends = dict.fromkeys(sids, 0.0)

    def stats_snapshot(self):
        return {sid: {"last_end": end} for sid, end in self.ends.items() if end}


def test_live_edge_takes_the_lag_of_a_new_channel_set(view, monkeypatch):
    """A quick Gecko (0.3 s) and a Fortimus 15 s late shown together: the edge follows the
    Gecko. Showing only the Fortimus then: within two records its newest sample is at the
    right edge again (the Gecko's small lag used to leave a 15 s gap for over a minute)."""
    from seiszone.ui import waveform_view as wv
    clock = _Clock(1.8e9)
    monkeypatch.setattr(wv, "time", clock)
    fort, gecko = SIDS[0], SIDS[1]
    hub = _TwoHub([fort, gecko])
    monkeypatch.setattr(view, "engine", type("E", (), {"hub": hub})())
    monkeypatch.setattr(view, "_rebuild", lambda: None)            # the plots are not needed here
    monkeypatch.setattr(view, "_update_traces", lambda *a, **k: None)
    _fresh_live(view, 300)
    view.set_channels([fort, gecko])
    t0 = 1.8e9
    gaps = []
    for k in range(int(150 / 0.25)):
        true = t0 + k * 0.25
        hub.ends[fort] = t0 + 2.56 * math.floor((true - 15.0 - t0) / 2.56) if true - 15.0 > t0 + 2.56 else 0.0
        hub.ends[gecko] = math.floor(true - 0.3) if k * 0.25 < 90 else hub.ends[gecko]
        clock.t = true + 18.8
        if k * 0.25 == 90:
            view.set_channels([fort])                               # the user shows the Fortimus only
        end = view._live_range()[1]
        if k * 0.25 >= 95:
            gaps.append(end - hub.ends[fort])
    assert min(gaps) >= -1e-6 and max(gaps) <= 0.01 * 300, (min(gaps), max(gaps))
    view.set_channels(SIDS)
    _fresh_live(view, 600)


def test_live_edge_keeps_scrolling_when_data_stop(view, monkeypatch):
    from seiszone.ui import waveform_view as wv
    clock = _Clock(1.8e9)
    monkeypatch.setattr(wv, "time", clock)
    hub = _StatsHub(SIDS[0])
    monkeypatch.setattr(view, "engine", type("E", (), {"hub": hub})())
    _fresh_live(view, 300)
    t0 = 1.8e9
    rows = _fortimus_feed(view, clock, hub, t0, 200, stop=t0 + 60)     # the link drops after a minute
    lag = view._lag
    stopped = [r for r in rows if r[0] - 18.8 > t0 + 62]
    steps = np.diff([r[2] for r in stopped])
    assert steps.min() > 0.125 - 1e-6 and steps.max() < 0.25 + 1e-6    # keeps scrolling in real time
    # (while the last minute's lags expire, a moment at half speed at worst: LAG_GROWTH) ...
    after = [r for r in rows if r[0] - 18.8 > t0 + 125]
    assert all(r[2] == pytest.approx(r[0] - lag) for r in after)   # ... then with the last known lag
    assert after[-1][2] - after[-1][1] > 120               # and the gap grows (visible)
    assert not view._anchored                               # (less than 80 % of the window: not anchored)
    # the link comes back and the instrument backfills the missing two minutes faster than
    # real time: the edge never jumps back (the lag known before stays a candidate)
    stats_end = after[-1][1]
    ends = []
    for k in range(60):
        clock.t += 0.25
        hub.last_end = min(stats_end + 20.0 * (k + 1), clock.t - 18.8 - 0.5)   # 80x real time, then live
        ends.append(view._live_range()[1])
    assert np.diff(ends).min() > -1e-6
    assert ends[-1] - hub.last_end < 0.01 * 300
    _fresh_live(view, 600)


def test_live_edge_never_seen_data_arrive_is_now(view, monkeypatch):
    from seiszone.ui import waveform_view as wv
    clock = _Clock(1.8e9)
    monkeypatch.setattr(wv, "time", clock)
    hub = _StatsHub(SIDS[0])
    hub.last_end = 1.8e9 - 30
    monkeypatch.setattr(view, "engine", type("E", (), {"hub": hub})())
    _fresh_live(view, 600)
    for _ in range(8):           # data that never moves: no arrival lag, the edge is the PC clock
        clock.t += 0.25
        assert view._live_range()[1] == pytest.approx(clock.t)
    # a backfill after the start races through old data (lags 280 s shrinking to 30 s within
    # 2.5 s): those lags are not followed
    clock.t, hub.last_end = 1.8e9 + 100, 1.8e9 + 100 - 280
    view._live_range()
    for k in range(10):
        clock.t += 0.25
        hub.last_end += 25.0
        assert view._live_range()[1] == pytest.approx(clock.t)
    assert view._lag is None
    _fresh_live(view, 600)


class _Wheel:
    """A wheel event as the ViewBox gets it (delta in eighths of a degree: 120 = one notch)."""

    def __init__(self, delta, vb, mods=Qt.NoModifier):
        self._delta, self._vb, self._mods, self.accepted = delta, vb, mods, False

    def delta(self):
        return self._delta

    def modifiers(self):
        return self._mods

    def pos(self):
        from PySide6.QtCore import QPointF
        return QPointF(self._vb.width() / 2, 10)

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def test_wheel_zooms_amplitude_shift_steps_the_window_ctrl_scrolls(view, engine):
    assert view.is_live() and view._window == pytest.approx(600)
    vb = view._traces[1].vb
    tr = view._traces[1]
    span = tr.ykey[1] - tr.ykey[0]
    ev = _Wheel(120, vb)
    vb.wheelEvent(ev)                                    # wheel up: larger traces, still live
    assert ev.accepted and view.is_live() and view.gain() == 2.0 and view.gain_label.text() == "×2"
    assert tr.ykey[1] - tr.ykey[0] == pytest.approx(span / 2)
    vb.wheelEvent(_Wheel(-240, vb))                       # two notches down
    assert view.gain() == 0.5 and view.gain_label.text() == "×1/2" and view.state()["gain"] == 0.5
    view.reset_amplitude()
    assert view.gain() == 1.0
    # Shift+wheel: the time scale (pixels per second), still live
    width = view._plot_width()
    pps = view.pps()                                      # about 2.2 px/s: 600 s on the plot
    vb.wheelEvent(_Wheel(120, vb, Qt.ShiftModifier))      # up: expand, the next step of the scale
    assert view.pps() == 5 and view.pps_button.text() == "5" and view.is_live()
    t0, t1 = view.visible_range()
    assert t1 - t0 == pytest.approx(width / 5, abs=0.01)
    vb.wheelEvent(_Wheel(-60, vb, Qt.ShiftModifier))      # half a notch (a touchpad): not yet
    assert view.pps() == 5
    vb.wheelEvent(_Wheel(-60, vb, Qt.ShiftModifier))
    assert view.pps() == 2 and view.is_live()
    for _ in range(20):                                   # squashes until a whole day is shown
        vb.wheelEvent(_Wheel(-120, vb, Qt.ShiftModifier))
    assert view._window == pytest.approx(86400) and view.is_live()
    t0, t1 = view.visible_range()
    assert t1 - t0 == pytest.approx(86400, abs=1)
    assert pps != view.pps()
    view.set_window(600)
    # Ctrl+wheel: scrolls back in time (a tenth of the window per notch) and pauses
    t0, t1 = view.visible_range()
    vb.wheelEvent(_Wheel(120, vb, Qt.ControlModifier))
    assert not view.is_live()
    assert view.visible_range() == pytest.approx((t0 - 60, t1 - 60), abs=1.0)
    view.set_live(True)


def test_drag_pauses_and_the_wheel_keeps_the_time_range(view, qapp):
    vb = view._traces[0].vb
    vb.translateBy(x=-30)                                # a drag (pan) ...
    vb.sigRangeChangedManually.emit([True, False])
    assert not view.is_live()                            # ... pauses
    a, b = view.visible_range()
    vb.wheelEvent(_Wheel(240, vb))                       # paused: the wheel still zooms the amplitude
    assert not view.is_live() and view.visible_range() == pytest.approx((a, b)) and view.gain() == 4.0
    view.reset_amplitude()
    assert view._window == pytest.approx(600)
    view.set_live(True)
    assert view.visible_range()[1] - view.visible_range()[0] == pytest.approx(600, abs=0.01)


def test_paused_is_amber_and_returns_to_live_by_itself(view, qapp):
    from PySide6.QtTest import QTest
    from seiszone.ui import waveform_view as wv
    b = view.live_button
    assert view.is_live() and b.text() == "Live" and b.styleSheet() == ""
    view._traces[0].vb.sigRangeChangedManually.emit([True, False])
    assert not view.is_live() and not b.isChecked()
    assert b.text() == "Paused – back to live"
    assert theme.COLORS["warn"] in b.styleSheet() and theme.COLORS["bg"] in b.styleSheet()
    assert "2 minutes" in b.toolTip()
    # still paused just before AUTO_LIVE seconds without mouse use, live after them
    view._touched = time.monotonic() - wv.AUTO_LIVE + 1.0
    view.refresh()
    assert not view.is_live()
    # mouse use on the plots restarts the count
    view._touched -= 100.0
    QTest.mousePress(view.glw.viewport(), Qt.LeftButton, Qt.NoModifier, view.glw.viewport().rect().center())
    QTest.mouseRelease(view.glw.viewport(), Qt.LeftButton, Qt.NoModifier, view.glw.viewport().rect().center())
    view.refresh()
    assert not view.is_live()
    view._touched = time.monotonic() - wv.AUTO_LIVE - 0.1
    view.refresh()
    assert view.is_live() and b.isChecked() and b.text() == "Live" and b.styleSheet() == ""
    t0, t1 = view.visible_range()
    assert t1 - t0 == pytest.approx(600, abs=0.01) and abs(t1 - time.time()) < 5
    # light theme: the deeper amber with white text
    try:
        view.set_live(False)
        theme.apply(qapp, "light")
        pump(qapp)
        assert theme.LIGHT["warn"] in b.styleSheet() and "#ffffff" in b.styleSheet()
    finally:
        theme.apply(qapp, "dark")
        pump(qapp)
        view.set_live(True)
    # a new view always starts live
    again = WaveformView(view.engine, view.config)
    assert again.is_live() and again.live_button.isChecked()
    again.deleteLater()


def test_screenshots(view, engine, qapp):
    os.makedirs(SCREENS, exist_ok=True)
    pump(qapp, 0.2)
    view.refresh()
    pump(qapp, 0.1)
    path = os.path.join(SCREENS, "waveforms.png")
    assert view.grab().save(path)
    assert os.path.getsize(path) > 20000

    panel = ChannelsPanel(engine, engine.config)
    panel.set_shown(SIDS[:3])
    panel.resize(1200, 230)
    panel.show()
    pump(qapp, 0.1)
    path = os.path.join(SCREENS, "channels.png")
    assert panel.grab().save(path)
    panel.close()

    dlg = SpectrumDialog(engine, SIDS, *view.visible_range())
    dlg.show()
    pump(qapp, 0.1)
    assert dlg.grab().save(os.path.join(SCREENS, "spectrum.png"))
    dlg.close()


def test_common_scale_is_per_instrument_so_every_filtered_trace_stays_visible(qapp, tmp_path):
    """The field complaint of 2026-11-22: with several instruments in one viewer and a band-pass
    on, only one instrument seemed filtered. The filter reached every channel; the Common scale
    gave every row the range of the largest instrument, so the others were flat lines. Common is
    now per instrument (the components of one sensor share a range); "common_all" is the old way."""
    from seiszone.core import mseed
    eng = FakeEngine(config=Config(str(tmp_path / "config.json")), seconds=600)
    rng = np.random.default_rng(3)
    t0 = time.time() - 600
    quiet = ["IS.PAN.0J.BHZ", "IS.PAN.0J.BHN", "IS.PAN.0J.BHE"]       # a quiet broadband at 50 sps
    for sid in quiet:
        n = 600 * 50
        t = np.arange(n) / 50.0
        y = (30000 + 8000 * np.sin(2 * np.pi * 0.01 * t) + 20 * np.sin(2 * np.pi * 3 * t)
             + rng.normal(0, 2, n)).astype(int)                        # offset + drift + a 3 Hz signal
        net, sta, loc, cha = sid.split(".")
        for rec in mseed.encode_records(net, sta, loc, cha, 50, t0, y):
            eng.hub.add_record(rec, "pan")
    sids = SIDS + quiet
    v = WaveformView(eng, eng.config, state={"scale": "common"})
    v.resize(1400, 900)
    v.show()
    pump(qapp)
    v.set_channels(sids)
    pump(qapp, 0.1)
    v.set_window(600)
    v.set_filter("bp1-10")
    v.refresh()
    pump(qapp, 0.1)

    def used(tr):          # the part of its row a trace fills
        return (tr.hi - tr.lo) / (tr.yrange[1] - tr.yrange[0])

    try:
        assert all(tr.filter_text == "Band-pass 1-10 Hz" for tr in v._traces)   # every channel is filtered
        for group in (SIDS[:3], [SIDS[3]], quiet):       # the largest component of each sensor fills its row
            assert max(used(v._trace(s)) for s in group) > 0.8, group
        v.scale_box.setCurrentIndex(v.scale_box.findData("common_all"))
        assert max(used(v._trace(s)) for s in quiet) < 0.01      # the old way: the quiet one is a flat line
    finally:
        v.close()
        v.deleteLater()
        pump(qapp)
