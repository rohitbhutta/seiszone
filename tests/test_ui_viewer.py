"""Offscreen tests of the viewer windows (ui.viewer_window, ui.waveform_view, ui.filter_dialog,
ui.viewer_dialogs) on FakeEngine's synthetic channels: the window filter and every channel's own
filter, the filter design and saved filters, offsets, scale factors, spectrograms, overlay, lane
lines, background, sorting, cursors, details, calibration, menus and keys, drag and drop, the
saved state and the window functions. Saves build/screens/viewer.png for a visual check.

Nothing here touches the network: FakeEngine never connects anywhere.
"""

import os
import sys
import time

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent, QFont
from PySide6.QtWidgets import QApplication, QInputDialog

from fake_engine import FakeEngine
from seiszone.config import Config
from seiszone.ui import theme
from seiszone.ui.channels_panel import C_SID, ChannelsPanel
from seiszone.ui.filter_dialog import (FilterDesignDialog, delete_filter, filter_catalogue, filter_label,
                                       save_filter, saved_filters)
from seiszone.ui.viewer_dialogs import (CalibrationDialog, ChannelPickerDialog, DetailsWindow, ScaleDialog,
                                        parse_scale)
from seiszone.ui.viewer_window import ViewerWindow
from seiszone.ui.waveform_view import CHANNEL_MIME, SPEC_SIZES, WaveformView

if sys.platform == "win32" and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS = os.path.join(ROOT, "build", "screens")
SIDS = ["WB.KUM.0J.HNZ", "WB.KUM.0J.HNN", "WB.KUM.0J.HNE", "SS.SMR03..HHZ"]
_APP = []


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    _APP.append(app)
    theme.apply(app, "dark")
    app.setFont(QFont("Segoe UI", 9))
    return app


@pytest.fixture(scope="module")
def engine(qapp, tmp_path_factory):
    return FakeEngine(config=Config(str(tmp_path_factory.mktemp("cfg") / "config.json")), seconds=600)


def pump(app, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def win(qapp, engine):
    engine.responses = None
    engine.config.set_value("calibration", {})
    w = ViewerWindow(engine, engine.config, {"sids": SIDS, "window": 600})
    w.resize(1400, 860)
    w.show()
    pump(qapp, 0.15)
    w.view.refresh()
    yield w
    w.quitting = True
    w.close()
    w.deleteLater()
    pump(qapp)


def span(tr):
    return tr.ykey[1] - tr.ykey[0]


# ------------------------------------------------------------------------------------------
# filters
# ------------------------------------------------------------------------------------------
def test_every_channel_can_have_its_own_filter(win):
    v = win.view
    v.set_filter("bp1-10")
    assert v.filter_on() and v.window_filter_text() == "Band-pass 1-10 Hz"
    v.set_trace_filter(SIDS[1], "off")
    v.set_trace_filter(SIDS[2], "custom", {"hp": 2.0, "hp_order": 2})
    v.set_trace_filter(SIDS[3], "default")
    assert v.trace_spec(SIDS[0]) == {"hp": 1.0, "hp_order": 4, "lp": 10.0, "lp_order": 4}
    assert v.trace_spec(SIDS[1]) is None
    assert v.trace_spec(SIDS[2])["hp"] == 2.0 and v.trace_spec(SIDS[3]) == {"default": True}
    trs = {tr.sid: tr for tr in v._traces}
    assert "Band-pass 1-10 Hz" in trs[SIDS[0]].label.text and "(own)" not in trs[SIDS[0]].label.text
    assert "Band-pass" not in trs[SIDS[1]].label.text
    assert "High-pass 2 Hz (order 2) (own)" in trs[SIDS[2]].label.text
    # the window filter off (F): channels with their own filter keep it
    v.toggle_filter()
    assert v.trace_spec(SIDS[0]) is None and v.trace_spec(SIDS[2])["hp"] == 2.0
    assert v.trace_filter_text(SIDS[0]) == "no filter" and v.trace_filter_text(SIDS[1]) == "no filter (own setting)"
    v.toggle_filter()
    # the channel's filter menu: same as the window / none / the list / design
    from PySide6.QtWidgets import QMenu
    m = QMenu()
    v.fill_filter_menu(m, SIDS[0])
    texts = [a.text() for a in m.actions() if a.text()]
    assert texts[0] == "Same as the window (Band-pass 1-10 Hz)" and texts[1] == "No filter for this channel"
    assert "High-pass 1 Hz" in texts and "Design for this channel..." in texts
    [a for a in m.actions() if a.text() == "No filter for this channel"][0].trigger()
    assert v.trace_spec(SIDS[0]) is None
    v.fill_filter_menu(m, SIDS[0])
    [a for a in m.actions() if a.text().startswith("Same as the window")][0].trigger()
    assert v.trace_spec(SIDS[0])["lp"] == 10.0
    # filtering removes the 1.3 Hz sine: the filtered channel is smaller than the unfiltered one
    assert trs[SIDS[0]].hi < trs[SIDS[1]].hi
    st = v.state()
    assert st["traces"][SIDS[1]]["filter"] == "off" and st["traces"][SIDS[2]]["custom"]["hp_order"] == 2


def test_saved_filters_are_offered_and_deleted(win, engine):
    v = win.view
    assert save_filter(engine.config, "Quake band", {"hp": 0.5, "lp": 8.0})
    assert not save_filter(engine.config, "nothing", {"hp": None})
    assert [f["name"] for f in saved_filters(engine.config)] == ["Quake band"]
    keys = [k for k, _l, _s in filter_catalogue(engine.config)]
    assert keys[0] == "default" and "saved:Quake band" in keys and "none" not in keys
    v.filter_menu.aboutToShow.emit()
    texts = [a.text() for a in v.filter_menu.actions()]
    assert "Saved filters" in texts and "Quake band" in texts and "Delete a saved filter" in texts
    [a for a in v.filter_menu.actions() if a.text() == "Quake band"][0].trigger()
    assert v.filter_key() == "saved:Quake band" and v.window_spec() == {"hp": 0.5, "hp_order": 4, "lp": 8.0,
                                                                        "lp_order": 4}
    assert v.filter_button.text() == "Filter: Quake band"
    v.delete_saved_filter("Quake band")
    assert saved_filters(engine.config) == [] and v.window_spec() is None
    delete_filter(engine.config, "not there")
    assert filter_label(engine.config, "custom", {"hp": 1.0, "lp": 5.0}) == "Band-pass 1-5 Hz"


def test_filter_design_dialog(win, qapp, monkeypatch):
    v = win.view
    d = v.open_filter_design(None)
    assert isinstance(d, FilterDesignDialog) and d.isVisible() and "all channels" in d.windowTitle()
    assert sorted(d.rate_box.itemData(i) for i in range(d.rate_box.count())) == [100.0, 200.0]
    d.click_corner("hp", 0.5)
    d.click_corner("lp", 8.0)
    assert d.spec() == {"hp": 0.5, "hp_order": 4, "lp": 8.0, "lp_order": 4}
    # a high-pass click beyond the low-pass corner moves both (the band keeps its width)
    d.click_corner("hp", 20.0)
    hp, lp = d.spec()["hp"], d.spec()["lp"]
    assert hp == 20.0 and lp > hp
    d.click_corner("hp", 0.5)
    d.lp.set(8.0, 4, True)
    d.hp.hz.setValue(9.0)                                  # typed: not a band any more
    assert d.spec() is False and not d.ok_button.isEnabled() and "below" in d.message.text()
    d.hp.hz.setValue(1.0)
    assert d.ok_button.isEnabled()
    assert d.hp.secs.value() == pytest.approx(1.0)
    d.hp.secs.setValue(4.0)                                # seconds and Hz stay in step
    assert d.hp.hz.value() == pytest.approx(0.25)
    d.auto.setChecked(True)                                # auto-apply: every change goes to the viewer
    d.lp.order.setValue(6)
    assert v.filter_key() == "custom" and v.window_spec()["lp_order"] == 6 and v.filter_on()
    d.set_spectrum("noise", np.array([0.1, 1, 10]), np.array([1.0, 2.0, 1.0]))
    assert len(d.noise_curve.getData()[0]) == 3
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("My band", True)))
    assert d.save_preset() and "saved:My band" in [k for k, _l, _s in filter_catalogue(v.config)]
    d.lp.on.setChecked(False)
    d.hp.on.setChecked(False)                              # both off: no filter
    assert d.spec() is None and not v.filter_on()
    d.close()
    pump(qapp)
    # a design for one channel only
    d = v.open_filter_design(SIDS[3])
    assert SIDS[3] in d.windowTitle()
    d.hp.set(2.0, 3, True)
    d.lp.set(0, 4, False)
    d.accept_design()
    assert v.trace_settings(SIDS[3])["filter"] == "custom" and v.trace_spec(SIDS[3])["hp_order"] == 3
    delete_filter(v.config, "My band")


# ------------------------------------------------------------------------------------------
# offsets, scale
# ------------------------------------------------------------------------------------------
def test_offsets_null_zero_and_locked(win):
    v = win.view
    v.demean_box.setChecked(False)                         # raw values: the offsets matter
    v.set_locked(SIDS[1], True)
    v.null_offsets()
    st = {s: v.trace_settings(s) for s in SIDS}
    assert st[SIDS[0]]["offset"] is not None and st[SIDS[1]]["offset"] is None     # locked: left alone
    tr0 = v._traces[0]
    assert abs(tr0.stats["mean"]) < 0.05 * (tr0.hi - tr0.lo)                        # centred
    assert "offset fixed" in tr0.label.text and "offset locked" in v._traces[1].label.text
    v.null_offsets(SIDS[1])                                # one channel: even when locked
    assert v.trace_settings(SIDS[1])["offset"] is not None
    v.zero_offset(SIDS[0])
    assert v.trace_settings(SIDS[0])["offset"] == 0.0
    v.set_offset(SIDS[0], 123.0)
    assert v.trace_stats(SIDS[0])["offset"] == 123.0
    v.set_offset(SIDS[0], "junk")
    assert v.trace_settings(SIDS[0])["offset"] == 123.0
    for s in SIDS:
        v.set_offset(s, None)
        v.set_locked(s, False)
    v.demean_box.setChecked(True)
    assert all(v.trace_settings(s)["offset"] is None for s in SIDS)


def test_scale_factors_and_scale_others_to_match(win, qapp):
    v = win.view
    tr = v._traces[3]
    before = span(tr)
    v.set_trace_scale(SIDS[3], 2.0)
    assert span(tr) == pytest.approx(before / 2) and "×2" in tr.label.text
    assert parse_scale("*2", 2.0) == 4.0 and parse_scale("/4", 2.0) == 0.5 and parse_scale("x3", 1.0) == 3.0
    assert parse_scale("0", 1.0) is None and parse_scale("abc", 1.0) is None and parse_scale("1", 3.0) == 1.0
    d = ScaleDialog(v, SIDS[0])
    d.station.setChecked(True)
    d.edit.setText("3")
    assert d.apply()
    assert [v.trace_settings(s)["scale"] for s in SIDS] == [3.0, 3.0, 3.0, 2.0]
    d = ScaleDialog(v, SIDS[0])
    d.all.setChecked(True)
    d.edit.setText("nonsense")
    assert not d.apply() and "number" in d.message.text()
    d.edit.setText("1")
    assert d.apply() and all(v.trace_settings(s)["scale"] == 1.0 for s in SIDS)
    # every channel gets the amplitude range the reference shows
    v.zoom_amplitude(1)
    ref = v._traces[1]
    shown = (ref.yrange[1] - ref.yrange[0]) / (1 + 2 * 0.06)
    v.scale_others_to_match(SIDS[1])
    assert v._scale == "fixed" and v.scale_box.currentData() == "fixed" and v.gain() == 1.0
    assert {round(t.yrange[1] - t.yrange[0], 3) for t in v._traces} == {round(shown, 3)}
    v.reset_scales()
    v.scale_box.setCurrentIndex(v.scale_box.findData("auto"))


# ------------------------------------------------------------------------------------------
# spectrograms, layout
# ------------------------------------------------------------------------------------------
def test_spectrograms(win, qapp):
    v = win.view
    v.set_spectrogram(SIDS[3], True)
    tr = v._traces[3]
    assert tr.svb is not None and tr.svb.isVisible() and tr.image.image is not None
    cols, bins = tr.image.image.shape
    assert bins == v.spec_size() + 1 and 16 <= cols <= v._plot_width() + 1
    assert v.grid.rowMaximumHeight(3 * 3 + 1) == v.spec_size()
    assert tr.nyq == 50.0 and tr.svb.viewRange()[1] == pytest.approx([0, 50.0])
    levels = tr.image.levels
    v.spectrogram_contrast(SIDS[3], 1)                    # brighter: the top level 6 dB lower
    assert tr.image.levels[1] == pytest.approx(levels[1] - 6.0, abs=1.0)
    v.spectrogram_contrast(SIDS[3], -1)
    v.set_spec_size(128)
    assert v.spec_size() == 128 and tr.image.image.shape[1] == 129 and 128 in SPEC_SIZES
    v.toggle_all_spectrograms()                            # all on
    assert all(v.trace_settings(s)["spectrogram"] for s in SIDS)
    v.toggle_all_spectrograms()                            # all off
    assert not any(v.trace_settings(s)["spectrogram"] for s in SIDS)
    assert not tr.svb.isVisible() and v.grid.rowMaximumHeight(3 * 3 + 1) == 0
    v.set_spec_size(64)


def test_overlay_lanes_background_and_sorting(win, qapp):
    v = win.view
    v.set_overlay(True)
    first = v._traces[0].vb
    assert all(tr.host is first for tr in v._traces)
    assert not v._traces[2].vb.isVisible() and first.isVisible()
    assert v._traces[0].bottom.style["showValues"]
    v.set_overlay(False)
    assert all(tr.host is tr.vb and tr.vb.isVisible() for tr in v._traces)
    v.set_lanes(False)
    assert v._traces[0].vb.border is None or v._traces[0].vb.border.style() == Qt.NoPen
    v.set_lanes(True)
    v.set_background("#202020")
    assert v.glw.backgroundBrush().color().name() == "#202020" and v.state()["background"] == "#202020"
    v.set_background(None)
    v.sort_channels("component")
    assert v.channels() == ["SS.SMR03..HHZ", "WB.KUM.0J.HNZ", "WB.KUM.0J.HNN", "WB.KUM.0J.HNE"]
    v.sort_channels("rate", reverse=True)
    assert v.channels()[0] == "SS.SMR03..HHZ"
    v.sort_channels("channel")
    assert v.channels() == sorted(SIDS)
    v.set_channels(SIDS)


# ------------------------------------------------------------------------------------------
# cursors, details, calibration
# ------------------------------------------------------------------------------------------
def test_time_and_amplitude_cursors(win):
    v = win.view
    v.set_time_cursors(True)
    assert v.tcur_button.isChecked()
    dt, da, _u = v.cursor_readout()
    assert dt == pytest.approx(0.2 * 600, rel=1e-3) and da is None
    assert v.tcur_button.text().startswith("Δt 120.000 s")
    line = v._traces[1].tcur[1]
    t0, t1 = v.visible_range()
    line.setValue(t0 + 0.9 * (t1 - t0))                    # dragged: every row's cursor follows
    assert v.cursor_readout()[0] == pytest.approx(0.5 * 600, rel=1e-3)
    assert v._traces[3].tcur[1].value() == pytest.approx(line.value())
    v.set_amp_cursors(True, SIDS[2])
    tr = v._traces[2]
    _dt, da, units = v.cursor_readout()
    assert da == pytest.approx(0.4 * (tr.yrange[1] - tr.yrange[0])) and units == "counts"
    assert v.acur_button.text().startswith("ΔA ")
    v.remove_channel(SIDS[2])                              # its channel goes: the cursors go
    assert not v.acur_button.isChecked() and v.cursor_readout()[1] is None
    v.set_time_cursors(False)
    assert v.tcur_button.text() == "Time cursors"
    v.set_channels(SIDS)


def test_details_window(win, qapp):
    v = win.view
    d = win.show_details(SIDS[3])
    assert isinstance(d, DetailsWindow) and win.show_details(SIDS[3]) is d
    tr = v._traces[3]
    assert set(tr.lines) == {"mean", "min", "max"}
    assert tr.lines["max"].value() == pytest.approx(tr.hi) and tr.lines["min"].value() == pytest.approx(tr.lo)
    assert d.values["rate"].text() == "100 sps" and d.values["min"].text().endswith("counts")
    st = v.trace_stats(SIDS[3])
    assert st["n"] > 50000 and st["min"] <= st["mean"] <= st["max"] and st["rms"] > 0
    d.offset.setText("250")
    d._offset_typed()
    assert v.trace_settings(SIDS[3])["offset"] == 250.0
    d.offset.setText("")
    d._offset_typed()
    assert v.trace_settings(SIDS[3])["offset"] is None
    d.close()
    pump(qapp)
    assert tr.lines == {} and SIDS[3] not in win.details


def test_calibration_gives_physical_units(win, engine, qapp):
    v = win.view
    dlg = CalibrationDialog(engine.config, engine, SIDS[3], win)
    assert "No StationXML" in dlg.findChildren(type(dlg.message))[0].text()
    dlg.value.setText("0")
    assert not dlg.save() and "not 0" in dlg.message.text()
    dlg.value.setText("4e5")
    dlg.units.setCurrentIndex(dlg.units.findData("M/S**2"))
    assert dlg.save()
    assert engine.config.get("calibration")[SIDS[3]] == {"sensitivity": 4e5, "units": "M/S**2"}
    v.calibration_changed()
    v.set_units("physical")
    trs = {tr.sid: tr for tr in v._traces}
    assert trs[SIDS[3]].units == "m/s²" and abs(trs[SIDS[3]].hi) < 1.0
    assert SIDS[0] in v.note.text() and SIDS[3] not in v.note.text()   # the others: still counts
    v.set_units("physical_g")
    assert trs[SIDS[3]].units == "g"
    dlg = CalibrationDialog(engine.config, engine, SIDS[3], win)
    assert dlg.remove_button.isEnabled()
    dlg.remove()
    assert SIDS[3] not in engine.config.get("calibration")
    v.calibration_changed()
    assert trs[SIDS[3]].units == "counts"
    v.set_units("counts")


# ------------------------------------------------------------------------------------------
# menus, keys, drag and drop, state, window functions
# ------------------------------------------------------------------------------------------
def test_context_menu_two_parts(win):
    m = win.build_context_menu(SIDS[0])
    texts = [a.text() for a in m.actions()]
    sep = texts.index("")
    channel, window = texts[:sep], texts[sep + 1:]
    assert channel[0] == "WB.KUM.0J.HNZ  -  KUMARI"
    for t in ("Colour...", "Scale...", "Scale others to match", "Details...", "Offset", "Locked offset",
              "Spectrogram", "Calibration...", "Remove from this viewer"):
        assert t in channel, t
    assert any(t.startswith("Filter (") for t in channel)
    for t in ("Background colour...", "Show lane lines", "Overlay channels", "Spectrogram size",
              "All spectrograms on / off", "Sort by", "Channels...", "Label...", "Clear window",
              "No caption (full screen)", "Duplicate"):
        assert t in window, t
    shortcuts = {a.text(): a.shortcut().toString() for a in m.actions()}
    assert shortcuts["Colour..."] == "C" and shortcuts["Details..."] == "D" and shortcuts["Duplicate"] == "Ctrl+D"
    # outside any channel: only the window part
    m = win.build_context_menu(None)
    assert m.actions()[0].text() == "Background colour..."


def test_selection_menu_and_filter_design_spectra(win, qapp):
    v = win.view
    t1 = time.time() - 250
    t0 = t1 - 100
    m = win.build_selection_menu(t0, t1, SIDS[0])
    texts = [a.text() for a in m.actions() if a.text()]
    assert texts[1:4] == ["Zoom to this time range", "Spectrum of this time range...",
                          "Export this time range to miniSEED..."]
    assert texts[-1] == f"Use as signal in the filter design ({SIDS[0]})"
    d = win.use_in_design("noise", SIDS[0], t0, t1)
    assert d is not None and len(d.noise_curve.getData()[0]) > 10
    d.close()
    [a for a in m.actions() if a.text() == "Zoom to this time range"][0].trigger()
    assert not v.is_live() and v.visible_range() == pytest.approx((t0, t1))
    v.set_live(True)
    spec = win.open_spectrum(t0, t1)
    assert spec.sids == SIDS and (spec.t0, spec.t1) == (t0, t1)
    spec.close()


def test_shift_drag_selects_a_time_range(win):
    v = win.view
    got = []
    v.selectionMade.disconnect(win.show_selection_menu)   # the menu would wait for a click
    v.selectionMade.connect(lambda a, b, sid, pos: got.append((a, b, sid)))
    vb = v._traces[1].vb

    class Drag:
        def __init__(self, x0, x1, start, finish):
            self.x0, self.x1, self.start, self.finish, self.accepted = x0, x1, start, finish, False

        def button(self):
            return Qt.LeftButton

        def modifiers(self):
            return Qt.ShiftModifier

        def buttonDownPos(self):
            return vb.mapFromView(QPointF(self.x0, 0))

        def pos(self):
            return vb.mapFromView(QPointF(self.x1, 0))

        def isStart(self):
            return self.start

        def isFinish(self):
            return self.finish

        def accept(self):
            self.accepted = True

    t0, t1 = v.visible_range()
    a, b = t0 + 100, t0 + 200
    vb.mouseDragEvent(Drag(a, a + 50, True, False))
    assert len(v._sel_items) == 4
    ev = Drag(a, b, False, True)
    vb.mouseDragEvent(ev)
    assert ev.accepted and v.is_live()                     # a selection does not pause
    assert got and got[0][2] == SIDS[1] and got[0][0] == pytest.approx(a, abs=1) and got[0][1] == pytest.approx(b, abs=1)


def test_keys_are_on_the_window(win):
    keys = {a.text(): [s.toString() for s in a.shortcuts()] for a in win.actions()}
    expect = {"Larger traces": ["Up"], "Smaller traces": ["Down"], "Shorter time window": ["Left"],
              "Longer time window": ["Right"], "Live / pause": ["Pause", "Space"], "Filter on / off": ["F"],
              "Filter design...": ["Ctrl+F"], "Null offsets": ["N"], "Spectrogram": ["P"],
              "All spectrograms on / off": ["Ctrl+P"], "Scale...": ["S"], "Scale others to match": ["Ctrl+S"],
              "Details...": ["D"], "Colour...": ["C"], "Remove from this viewer": ["Del"],
              "Clear window": ["Ctrl+Del"], "Show lane lines": ["L"], "Overlay channels": ["O"],
              "Redraw": ["F5"], "No caption (full screen)": ["F11"], "Duplicate": ["Ctrl+D"]}
    for text, seq in expect.items():
        assert keys.get(text) == seq, text
    v = win.view
    win.a_amp_in.trigger()
    assert v.gain() == 2.0
    win.a_amp_reset.trigger()
    win.a_time_in.trigger()
    assert v._window == 300
    win.a_time_out.trigger()
    win.a_filter.trigger()
    assert v.filter_on()
    win.a_filter.trigger()
    win.a_live.trigger()
    assert not v.is_live()
    win.a_live.trigger()
    assert v.is_live()


def test_drop_channels_from_the_channels_table(win, engine, qapp):
    v = win.view
    v.set_channels(SIDS[:2])
    panel = ChannelsPanel(engine, engine.config)
    proxy = panel.proxy
    rows = [proxy.data(proxy.index(r, C_SID)) for r in range(proxy.rowCount())]
    idx = [proxy.mapToSource(proxy.index(rows.index(s), C_SID)) for s in (SIDS[3], SIDS[0])]
    data = panel.model.mimeData(idx + idx)                 # several columns of a row: one channel
    assert bytes(data.data(CHANNEL_MIME)).decode().split() == sorted([SIDS[3], SIDS[0]], key=rows.index)
    dropped = []
    v.channelsDropped.connect(dropped.append)
    ev = QDropEvent(QPointF(20, 20), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
    assert v.eventFilter(v.glw.viewport(), ev)
    assert v.channels() == SIDS[:2] + [SIDS[3]] and dropped == [[SIDS[3]]]
    other = QMimeData()
    other.setText("hello")
    assert not v.eventFilter(v.glw.viewport(), QDropEvent(QPointF(5, 5), Qt.CopyAction, other, Qt.LeftButton,
                                                          Qt.NoModifier))
    v.set_channels(SIDS)


def test_channel_picker(win, engine):
    d = ChannelPickerDialog(engine, [SIDS[2], SIDS[0]])
    assert d.list.count() == 4
    d.filter.setText("smr03")
    d._tick(True)                                         # only the listed (filtered) channel
    d.filter.setText("")
    assert d.chosen() == [SIDS[2], SIDS[0], SIDS[3]]      # kept order, the new one appended
    d._tick(False)
    assert d.chosen() == []


def test_state_round_trip_and_window_functions(win, engine, qapp, tmp_path):
    v = win.view
    v.set_filter("hp1")
    v.set_trace_color(SIDS[0], "#ff00ff")
    v.set_trace_scale(SIDS[1], 4.0)
    v.set_spectrogram(SIDS[2], True)
    v.zoom_amplitude(2)
    v.set_overlay(False)
    v.set_lanes(False)
    win._label = "North array"
    win._changed()
    assert win.windowTitle() == "North array - SeisZone"
    st = win.state()
    assert st["title"] == "North array" and st["gain"] == 4.0 and st["lanes"] is False
    assert st["traces"][SIDS[0]] == {"color": "#ff00ff"} and st["traces"][SIDS[2]] == {"spectrogram": True}
    w2 = ViewerWindow(engine, engine.config, st)
    v2 = w2.view
    assert v2.channels() == SIDS and v2.filter_key() == "hp1" and v2.gain() == 4.0 and not v2.lanes()
    assert v2.trace_settings(SIDS[1])["scale"] == 4.0 and w2.title() == "North array"
    w2.quitting = True
    w2.close()
    # a saved state with junk in it still opens
    w3 = ViewerWindow(engine, engine.config, {"sids": [SIDS[0], 7], "gain": "x", "spec_size": 3, "background": "nope",
                                               "traces": {SIDS[0]: {"color": "zzz", "bogus": 1}}, "units": "?"})
    assert w3.view.channels() == [SIDS[0]] and w3.view.gain() == 1.0 and w3.view.spec_size() == 64
    assert w3.view._bg is None and w3.view._units == "counts"
    w3.quitting = True
    w3.close()
    # no caption: menu, tool bar and status bar go, full screen; again: back
    win.toggle_caption()
    assert not win.menuBar().isVisible() and not v.bar.isVisible() and win.isFullScreen()
    win.toggle_caption()
    assert win.menuBar().isVisible() and v.bar.isVisible() and not win.isFullScreen()
    # duplicate without a main window: an independent copy
    dup = win.duplicate()
    assert dup is not win and dup.view.channels() == v.channels() and dup.view.filter_key() == "hp1"
    dup.quitting = True
    dup.close()
    # picture
    path = str(tmp_path / "pic" / "view.png")
    assert win.save_picture(path) and os.path.getsize(path) > 10000
    v.reset_scales()
    v.set_lanes(True)
    v.set_spectrogram(SIDS[2], False)
    v.set_filter_on(False)


def test_closing_by_the_user_is_reported(qapp, engine):
    w = ViewerWindow(engine, engine.config, {"sids": SIDS[:1]})
    got = []
    w.closedByUser.connect(got.append)
    w.show()
    w.close()
    assert got == [w]
    w2 = ViewerWindow(engine, engine.config, {"sids": SIDS[:1]})
    w2.closedByUser.connect(got.append)
    w2.quitting = True                                     # SeisZone quits: the viewer stays saved
    w2.show()
    w2.close()
    assert got == [w]


def test_empty_viewer_and_clear(qapp, engine, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    w = ViewerWindow(engine, engine.config, {})
    assert w.view.stack.currentWidget() is w.view.empty and w.title() == "Viewer (no channels)"
    assert w.open_spectrum() is None and "first" in w.statusBar().currentMessage()
    w.view.add_channels(SIDS[:2])
    assert w.view.channels() == SIDS[:2]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    w.clear_window()
    assert w.view.channels() == []
    w.quitting = True
    w.close()


def test_viewer_screenshot(win, qapp):
    v = win.view
    v.set_filter("bp1-10")
    v.set_spectrogram(SIDS[0], True)
    v.set_time_cursors(True)
    pump(qapp, 0.2)
    v.refresh()
    pump(qapp, 0.1)
    os.makedirs(SCREENS, exist_ok=True)
    path = os.path.join(SCREENS, "viewer.png")
    assert win.grab().save(path) and os.path.getsize(path) > 30000
    v.set_time_cursors(False)
    v.set_spectrogram(SIDS[0], False)
    v.set_filter_on(False)


def test_a_view_made_alone_still_works(qapp, engine):
    """WaveformView without a viewer window (as the tests of test_ui_data use it)."""
    v = WaveformView(engine, engine.config)
    v.resize(800, 400)
    v.show()
    v.set_channels(SIDS[:1])
    pump(qapp, 0.05)
    v.refresh()
    assert v._traces[0].hi is not None
    v.close()
    v.deleteLater()


# ------------------------------------------------------------------------------------------
# found by the independent review of 1.1.0 (each test failed before its fix)
# ------------------------------------------------------------------------------------------
def test_ctrl_f_has_one_owner_and_opens_the_design(win, qapp):
    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtTest import QTest
    win.filter_menu.aboutToShow.emit()
    live = [a for a in win.findChildren(QAction) if a.shortcut() == QKeySequence("Ctrl+F")
            and a.shortcutContext() != Qt.WidgetShortcut]
    assert live == [win.a_design]
    win.activateWindow()
    win.view.glw.setFocus()
    pump(qapp, 0.1)
    QTest.keyClick(win.view.glw, Qt.Key_F, Qt.ControlModifier)
    pump(qapp, 0.1)
    if qapp.activeWindow() is win:        # (offscreen Qt does not always activate a window)
        assert win.view._design is not None and win.view._design.isVisible()
        win.view._design.close()


def test_overlay_rows_removed_leave_nothing_behind(win, qapp):
    from seiszone.ui.waveform_view import _TraceLabel
    import pyqtgraph as pg
    v = win.view
    v.show_details_lines(SIDS[3], True)
    v.set_overlay(True)
    v.remove_channel(SIDS[3])
    first = v._traces[0].vb
    curves = [i for i in first.addedItems if isinstance(i, pg.PlotDataItem)]
    labels = [i for i in first.childItems() if isinstance(i, _TraceLabel)]
    assert len(curves) == 3 and len(labels) == 3
    assert not [i for i in first.addedItems if isinstance(i, pg.InfiniteLine)]
    v.set_overlay(False)
    assert len([i for i in first.addedItems if isinstance(i, pg.PlotDataItem)]) == 1
    v.show_details_lines(SIDS[3], False)
    v.set_channels(SIDS)


def test_overlay_traces_share_the_y_range(win):
    v = win.view
    v.set_trace_scale(SIDS[3], 8.0)
    v.set_overlay(True)
    host = tuple(v._traces[0].vb.viewRange()[1])
    assert all(tr.yrange == pytest.approx(host) for tr in v._traces)
    v.set_amp_cursors(True, SIDS[3])
    lines = [line.value() for _vb, line in v._acur_lines]
    assert lines[0] == pytest.approx(host[0] + 0.3 * (host[1] - host[0]))
    v.set_amp_cursors(False)
    v.set_overlay(False)
    v.reset_scales()


def test_selection_survives_letting_go_of_shift_first(win):
    v = win.view
    got = []
    v.selectionMade.disconnect(win.show_selection_menu)
    v.selectionMade.connect(lambda a, b, sid, pos: got.append((a, b)))
    vb = v._traces[0].vb
    t0, t1 = v.visible_range()

    class Drag:
        def __init__(self, x1, mods, start, finish):
            self.x1, self.mods, self.start, self.finish = x1, mods, start, finish

        def button(self):
            return Qt.LeftButton

        def modifiers(self):
            return self.mods

        def buttonDownPos(self):
            return vb.mapFromView(QPointF(t0 + 100, 0))

        def pos(self):
            return vb.mapFromView(QPointF(self.x1, 0))

        def isStart(self):
            return self.start

        def isFinish(self):
            return self.finish

        def accept(self):
            pass

    vb.mouseDragEvent(Drag(t0 + 150, Qt.ShiftModifier, True, False))
    vb.mouseDragEvent(Drag(t0 + 200, Qt.NoModifier, False, True))      # Shift let go first
    assert len(got) == 1 and v.is_live()
    # a plain drag that gets Shift later stays a pan (left to the ViewBox)
    assert not v._on_drag(Drag(t0 + 150, Qt.NoModifier, True, False), vb)
    assert not v._on_drag(Drag(t0 + 160, Qt.ShiftModifier, False, False), vb)
    v.set_live(True)


def test_one_design_dialog_after_switching_target(win, qapp):
    v = win.view
    d1 = v.open_filter_design(None)
    d2 = v.open_filter_design(SIDS[0])
    pump(qapp, 0.1)                                         # the first one is deleted now
    assert v._design is d2
    assert v.open_filter_design(SIDS[0]) is d2
    d2.close()
    pump(qapp, 0.1)
    assert v._design is None and d1 is not d2


def test_saved_filter_changes_reach_every_viewer(qapp, engine):
    a = ViewerWindow(engine, engine.config, {"sids": SIDS[:1]})
    b = ViewerWindow(engine, engine.config, {"sids": SIDS[:1]})
    save_filter(engine.config, "Q", {"hp": 1.0, "lp": 5.0})
    b.view.set_filter("saved:Q")
    assert b.view.window_spec()["lp"] == 5.0
    save_filter(engine.config, "Q", {"hp": 1.0, "lp": 8.0})   # replaced elsewhere
    assert b.view.window_spec()["lp"] == 8.0                  # saved filters are never cached
    a.view.delete_saved_filter("Q")
    b.view.filters_changed()
    assert b.view.window_spec() is None
    for w in (a, b):
        w.quitting = True
        w.close()


def test_filter_menus_do_not_grow(win, engine):
    from PySide6.QtWidgets import QMenu
    save_filter(engine.config, "Leak check", {"hp": 1.0})
    v = win.view
    for _ in range(3):
        v.filter_menu.aboutToShow.emit()
        qapp_ = QApplication.instance()
        qapp_.processEvents()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert len(v.filter_menu.findChildren(QMenu)) == 1
    delete_filter(engine.config, "Leak check")


def test_details_closed_with_escape_removes_its_lines(win, qapp):
    v = win.view
    d = win.show_details(SIDS[1])
    tr = v._traces[1]
    assert tr.lines
    d.reject()                                               # Esc
    pump(qapp, 0.1)
    assert tr.lines == {} and SIDS[1] not in v._details


def test_l_and_o_keys_follow_the_view(win):
    v = win.view
    v.set_lanes(False)                    # changed elsewhere (the right-click menu)
    win.a_lanes.trigger()                 # L: the opposite of what the view shows
    assert v.lanes() and win.a_lanes.isChecked()
    win.a_overlay.trigger()
    assert v.overlay() and win.a_overlay.isChecked()
    win.a_overlay.trigger()
    assert not v.overlay()
    assert win.a_caption.isCheckable()


def test_amplitude_cursors_on_the_channel_the_mouse_was_over(win):
    v = win.view
    v._hovered = SIDS[2]
    v.acur_button.click()
    assert v._acur[0] == SIDS[2]
    v.acur_button.click()
    m = win.build_context_menu(SIDS[1])
    [a for a in m.actions() if a.text() == "Amplitude cursors on this channel"][0].trigger()
    assert v._acur[0] == SIDS[1]
    v.set_amp_cursors(False)


def test_design_keeps_typed_seconds_and_the_untouched_default(win, qapp):
    v = win.view
    v.set_filter("default")
    d = v.open_filter_design(None)
    assert d.spec() == {"default": True}                      # untouched: still the per-rate default
    d.rate_box.setCurrentIndex(d.rate_box.findData(100.0))
    assert d.hp.freq() == pytest.approx(5.0) and d.lp.freq() == pytest.approx(45.0)
    d.apply()
    assert v.filter_key() == "default"
    d.hp.secs.setValue(3.0)                                    # typed in seconds
    assert d.hp.freq() == pytest.approx(1 / 3, rel=1e-9) and d.spec()["hp"] == pytest.approx(1 / 3, rel=1e-9)
    d.close()
    pump(qapp)
    v.set_filter_on(False)
