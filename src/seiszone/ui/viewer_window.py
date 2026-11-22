"""ViewerWindow: a window of its own (own taskbar button, minimize, maximize) around a
WaveformView. SeisZone's main window holds the sources and channels; the user ticks channels
there and opens a viewer with them (or drags channels into a viewer). Any number of viewers can
be open; MainWindow saves them (config "viewers") and opens them again at the next start.

The right-click menu has two parts, like the keyboard: the channel under the mouse (colour,
scale, details, filter, offset, spectrogram, calibration, remove) and the whole window
(background, lane lines, overlay, spectrogram size, sorting, channels, label, clear, no caption,
duplicate). Shift+drag selects a time range on the channels from the one where the drag starts
to the one where it ends (Ctrl+drag: exactly those two, in the order picked) and offers save,
zoom, save on selection, spectrum, PSD, peaks, orientation, strong motion, the calibrations,
external tools and the filter design - each on the selected channels only. The status bar shows
the time and value under the mouse.

Keys: Up/Down amplitude, Left/Right window length, Ctrl+Left/Right scroll, Home amplitude x1,
Pause/Space live, F filter, Ctrl+F filter design, N null offsets, Z zero offset, P spectrogram
(Ctrl+P all), +/- spectrogram contrast (Ctrl: all), S scale, Ctrl+S scale others to match, 1
scale x1 (Ctrl+1 all), D details, C colour, Del remove channel, Ctrl+Del clear, L lane lines,
O overlay, F5 redraw, F11 no caption (full screen), Ctrl+D duplicate, Ctrl+Shift+P print,
Ctrl+W close the viewer.
"""

import copy
import logging
import os
import time

from PySide6.QtCore import QByteArray, QPoint, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QPageLayout, QPainter
from PySide6.QtWidgets import (QColorDialog, QFileDialog, QInputDialog, QLabel, QMainWindow, QMenu,
                               QMessageBox)

from .. import APP_NAME, paths
from ..core import dsp
from . import theme
from .viewer_dialogs import CalibrationDialog, ChannelPickerDialog, DetailsWindow, ScaleDialog
from .waveform_view import SORTS, SPEC_SIZES, WaveformView

log = logging.getLogger("seiszone")


class ViewerWindow(QMainWindow):
    closedByUser = Signal(object)    # this window, closed by the user (MainWindow forgets it)
    stateChanged = Signal()          # something worth saving changed

    def __init__(self, engine, config, state=None, host=None):
        super().__init__(None)       # no parent: a window of its own on the taskbar
        self.engine = engine
        self.config = config
        self.host = host             # MainWindow (record / export / duplicate / calibration), or None
        self.quitting = False        # set by MainWindow: closing then keeps the viewer saved
        state = state if isinstance(state, dict) else {}
        self._label = str(state.get("title") or "")
        self._caption = True
        self._was_maximized = False
        self.details = {}            # sid -> DetailsWindow
        self.save_on_selection = bool(state.get("save_on_selection"))   # every selection saved at once
        self.setWindowIcon(theme.app_icon())
        self.view = WaveformView(engine, config, self, state)
        self.setCentralWidget(self.view)
        self.readout = QLabel()
        self.statusBar().addWidget(self.readout, 1)
        self.statusBar().addPermanentWidget(self.view.range_label)   # the time range shown
        self.view.hoverChanged.connect(self.readout.setText)
        self.view.changed.connect(self._changed)
        self.view.contextRequested.connect(self.show_context_menu)
        self.view.selectionMade.connect(self.show_selection_menu)
        self.view.channelsButton.connect(self.choose_channels)
        self.view.filtersSaved.connect(self._filters_saved)
        self._build_actions()
        self._build_menus()
        self._update_title()
        self._sync_checks()
        geo = state.get("geometry") or ""
        restored = False
        if geo:
            try:
                restored = self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            except (ValueError, UnicodeError):
                restored = False
        if not restored:
            self.resize(1400, 820)

    # ------------------------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------------------------
    def state(self):
        st = self.view.state()
        st["title"] = self._label
        st["save_on_selection"] = self.save_on_selection
        if not self._caption and self.isFullScreen():
            st["geometry"] = ""     # full screen is not a size to come back to
        else:
            st["geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        return st

    def label(self):
        return self._label

    def title(self):
        """The window's name: the label the user gave it, else its channels."""
        if self._label:
            return self._label
        sids = self.view.channels()
        if not sids:
            return "Viewer (no channels)"
        stations = list(dict.fromkeys(".".join(s.split(".")[:2]) for s in sids))
        text = ", ".join(stations[:3]) + (f" +{len(stations) - 3}" if len(stations) > 3 else "")
        return f"Viewer - {text} ({len(sids)} channel{'s' if len(sids) != 1 else ''})"

    def _update_title(self):
        self.setWindowTitle(f"{self.title()} - {APP_NAME}")

    def _changed(self):
        self._update_title()
        self._sync_checks()
        self.stateChanged.emit()

    def _filters_saved(self):
        """A saved filter changed in this viewer: the other viewers look it up again too."""
        if self.host is not None and hasattr(self.host, "filters_changed"):
            self.host.filters_changed(self)

    # ------------------------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------------------------
    def _act(self, text, slot, shortcut=None, checkable=False, tip=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcuts([QKeySequence(s) for s in (shortcut if isinstance(shortcut, (list, tuple))
                                                      else [shortcut])])
        a.setCheckable(checkable)
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.triggered.connect(lambda _=False: slot())
        self.addAction(a)            # the shortcut works also while the menu bar is hidden
        return a

    def _active(self):
        return self.view.active_sid()

    def _on_active(self, fn):
        sid = self._active()
        if sid:
            fn(sid)

    def _build_actions(self):
        v = self.view
        A = self._act
        self.a_amp_in = A("Larger traces", lambda: v.zoom_amplitude(1), "Up")
        self.a_amp_out = A("Smaller traces", lambda: v.zoom_amplitude(-1), "Down")
        self.a_amp_reset = A("Amplitude zoom ×1", v.reset_amplitude, "Home")
        self.a_time_in = A("Shorter time window", lambda: v.step_window(-1), "Left")
        self.a_time_out = A("Longer time window", lambda: v.step_window(1), "Right")
        self.a_back = A("Scroll back in time", lambda: v.pan(-0.25), "Ctrl+Left")
        self.a_fwd = A("Scroll forward in time", lambda: v.pan(0.25), "Ctrl+Right")
        self.a_live = A("Live / pause", lambda: v.set_live(not v.is_live()), ["Pause", "Space"])
        self.a_filter = A("Filter on / off", v.toggle_filter, "F")
        self.a_design = A("Filter design...", lambda: v.open_filter_design(None), "Ctrl+F")
        self.a_null = A("Null offsets", lambda: v.null_offsets(), "N",
                        tip="Fix each channel's offset at its mean now (locked channels keep theirs)")
        self.a_zero = A("Zero offset", lambda: self._on_active(v.zero_offset), "Z")
        self.a_spec = A("Spectrogram", lambda: self._on_active(
            lambda s: v.set_spectrogram(s, not v.trace_settings(s)["spectrogram"])), "P")
        self.a_spec_all = A("All spectrograms on / off", v.toggle_all_spectrograms, "Ctrl+P")
        self.a_bright = A("Spectrogram brighter", lambda: self._on_active(lambda s: v.spectrogram_contrast(s, 1)),
                          ["+", "="])
        self.a_dark = A("Spectrogram darker", lambda: self._on_active(lambda s: v.spectrogram_contrast(s, -1)), "-")
        self.a_bright_all = A("All spectrograms brighter", lambda: v.spectrogram_contrast(None, 1),
                              ["Ctrl++", "Ctrl+="])
        self.a_dark_all = A("All spectrograms darker", lambda: v.spectrogram_contrast(None, -1), "Ctrl+-")
        self.a_scale = A("Scale...", lambda: self._on_active(self.scale_channel), "S")
        self.a_match = A("Scale others to match", lambda: self._on_active(v.scale_others_to_match), "Ctrl+S")
        self.a_unity = A("Scale ×1", lambda: self._on_active(lambda s: v.set_trace_scale(s, 1.0)), "1")
        self.a_unity_all = A("All scales ×1", v.reset_scales, "Ctrl+1")
        self.a_details = A("Details...", lambda: self._on_active(self.show_details), "D")
        self.a_color = A("Colour...", lambda: self._on_active(self.choose_color), "C")
        self.a_remove = A("Remove from this viewer", lambda: self._on_active(v.remove_channel), "Del")
        self.a_clear = A("Clear window", self.clear_window, "Ctrl+Del")
        # toggled from the view's own state (the action's check mark only follows it)
        self.a_lanes = A("Show lane lines", lambda: v.set_lanes(not v.lanes()), "L", checkable=True)
        self.a_overlay = A("Overlay channels", lambda: v.set_overlay(not v.overlay()), "O",
                           checkable=True, tip="All channels drawn in one row, on top of each other")
        self.a_redraw = A("Redraw", v.redraw, "F5")
        self.a_caption = A("No caption (full screen)", self.toggle_caption, "F11", checkable=True)
        self.a_duplicate = A("Duplicate", self.duplicate, "Ctrl+D")
        self.a_channels = A("Channels...", self.choose_channels)
        self.a_label = A("Window title...", self.ask_label)
        self.a_background = A("Background colour...", self.choose_background)
        self.a_spectrum = A("Spectrum of the visible range...", self.open_spectrum)
        self.a_export = A("Export the visible range...", self.open_export)
        self.a_record = A("Record these channels...", self.open_record)
        self.a_picture = A("Save picture...", self.save_picture)
        self.a_print = A("Print...", self.print_view, "Ctrl+Shift+P")
        self.a_close = A("Close this viewer", self.close, "Ctrl+W")

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&Channels")
        m.addActions([self.a_channels, self.a_clear])
        self.sort_menu = m.addMenu("Sort by")
        self._fill_sort_menu(self.sort_menu)
        m = mb.addMenu("&View")
        m.addActions([self.a_live, self.a_amp_in, self.a_amp_out, self.a_amp_reset, self.a_unity_all])
        m.addSeparator()
        m.addActions([self.a_time_in, self.a_time_out, self.a_back, self.a_fwd])
        m.addSeparator()
        m.addActions([self.a_null, self.a_lanes, self.a_overlay, self.a_background, self.a_spec_all])
        self.size_menu = m.addMenu("Spectrogram size")
        self.size_menu.aboutToShow.connect(lambda: self._fill_size_menu(self.size_menu))
        m.addSeparator()
        m.addActions([self.a_redraw, self.a_caption])
        self.filter_menu = mb.addMenu("&Filter")
        self.filter_menu.aboutToShow.connect(lambda: self._fill_window_filter_menu(self.filter_menu))
        self._fill_window_filter_menu(self.filter_menu)
        m = mb.addMenu("&Tools")
        m.addActions([self.a_spectrum, self.a_export, self.a_record])
        m.addSeparator()
        m.addActions([self.a_picture, self.a_print])
        m = mb.addMenu("&Window")
        m.addActions([self.a_label, self.a_duplicate])
        m.addSeparator()
        m.addAction(self.a_close)
        mb.addMenu("&Help").addAction("Viewer keys and mouse", self.show_keys)
        for menu in mb.findChildren(QMenu):
            menu.aboutToShow.connect(self._sync_checks)

    def _sync_checks(self):
        self.a_lanes.setChecked(self.view.lanes())
        self.a_overlay.setChecked(self.view.overlay())
        self.a_caption.setChecked(not self._caption)

    def _fill_window_filter_menu(self, menu):
        self.view.fill_filter_menu(menu, None)
        first = menu.actions()[0] if menu.actions() else None
        menu.insertAction(first, self.a_filter)
        menu.insertSeparator(first)

    def _fill_sort_menu(self, menu):
        for key, (text, _fn) in SORTS.items():
            menu.addAction(text, lambda k=key: self.view.sort_channels(k))
        rev = menu.addMenu("Reversed")
        for key, (text, _fn) in SORTS.items():
            rev.addAction(text, lambda k=key: self.view.sort_channels(k, reverse=True))

    def _fill_size_menu(self, menu):
        menu.clear()
        group = QActionGroup(menu)
        for size in SPEC_SIZES:
            a = menu.addAction(f"{size} pixels", lambda s=size: self.view.set_spec_size(s))
            a.setCheckable(True)
            a.setChecked(self.view.spec_size() == size)
            group.addAction(a)

    # ------------------------------------------------------------------------------------
    # menus
    # ------------------------------------------------------------------------------------
    def build_context_menu(self, sid):
        """The right-click menu: the channel (sid, may be None) and the whole window."""
        v = self.view
        m = QMenu(self)
        if sid:
            ts = v.trace_settings(sid)
            sta = ".".join(sid.split(".")[:2])
            desc = self.engine.hub.descriptions.get(sta, "")
            head = m.addAction(f"{sid}" + (f"  -  {desc}" if desc else ""))
            head.setEnabled(False)
            m.addAction(self._item("Colour...", self.a_color, lambda: self.choose_color(sid)))
            m.addAction(self._item("Scale...", self.a_scale, lambda: self.scale_channel(sid)))
            m.addAction(self._item("Scale others to match", self.a_match, lambda: v.scale_others_to_match(sid)))
            m.addAction(self._item("Details...", self.a_details, lambda: self.show_details(sid)))
            fm = m.addMenu(f"Filter ({v.trace_filter_text(sid)})")
            v.fill_filter_menu(fm, sid)
            om = m.addMenu("Offset")
            a = om.addAction("Follow the window's mean removal", lambda: v.set_offset(sid, None))
            a.setCheckable(True)
            a.setChecked(ts["offset"] is None)
            om.addAction("Null now (fix at the mean)", lambda: v.null_offsets(sid))
            om.addAction(self._item("Zero (absolute values)", self.a_zero, lambda: v.zero_offset(sid)))
            locked = m.addAction("Locked offset", lambda: v.set_locked(sid, not ts["locked"]))
            locked.setCheckable(True)
            locked.setChecked(bool(ts["locked"]))
            locked.setToolTip("Null offsets (N) leaves this channel's offset as it is")
            spec = self._item("Spectrogram", self.a_spec, lambda: v.set_spectrogram(sid, not ts["spectrogram"]))
            spec.setCheckable(True)
            spec.setChecked(bool(ts["spectrogram"]))
            m.addAction(spec)
            if ts["spectrogram"]:
                sm = m.addMenu("Spectrogram contrast")
                sm.addAction(self._item("Brighter", self.a_bright, lambda: v.spectrogram_contrast(sid, 1)))
                sm.addAction(self._item("Darker", self.a_dark, lambda: v.spectrogram_contrast(sid, -1)))
                sm.addAction("Full frequency range", lambda: v.reset_spectrogram_zoom(sid))
            acur = m.addAction("Amplitude cursors on this channel",
                               lambda: v.set_amp_cursors(not on_this, sid))
            on_this = bool(v._acur and v._acur[0] == sid)
            acur.setCheckable(True)
            acur.setChecked(on_this)
            m.addAction("Label..." + (f" ({ts['label']})" if ts["label"] else ""),
                        lambda: self.ask_channel_label(sid))
            m.addAction("Calibration...", lambda: self.calibrate(sid))
            m.addAction(self._item("Remove from this viewer", self.a_remove, lambda: v.remove_channel(sid)))
            m.addSeparator()
        self._sync_checks()
        m.addAction(self.a_background)
        m.addAction(self.a_lanes)
        m.addAction(self.a_overlay)
        size = m.addMenu("Spectrogram size")
        self._fill_size_menu(size)
        m.addAction(self.a_spec_all)
        sort = m.addMenu("Sort by")
        self._fill_sort_menu(sort)
        m.addAction(self.a_channels)
        m.addAction(self.a_label)
        m.addAction(self.a_clear)
        m.addAction(self.a_caption)
        m.addAction(self.a_duplicate)
        m.addSeparator()
        m.addActions([self.a_spectrum, self.a_export, self.a_record, self.a_picture])
        return m

    def _item(self, text, shortcut_action, slot):
        """A menu entry for one channel that shows the key of the matching shortcut (the
        context menus are built anew each time: the entries go with the window's next menu)."""
        old = getattr(self, "_items", [])
        if len(old) > 200:
            for a in old[:100]:
                a.deleteLater()
            del old[:100]
        a = QAction(text, self)
        self._items = old + [a]
        a.setShortcut(shortcut_action.shortcut())
        a.setShortcutVisibleInContextMenu(True)
        a.setShortcutContext(Qt.WidgetShortcut)   # shown only: the window's action does the key
        a.triggered.connect(lambda _=False: slot())
        return a

    def show_context_menu(self, sid, pos):
        m = self.build_context_menu(sid)
        m.exec(pos)
        m.deleteLater()

    def build_selection_menu(self, t0, t1, sid, sids=None):
        """What to do with a selection: sids are the selected channels (in their order; None:
        every channel of the viewer), sid the first of them."""
        sel = list(sids) if sids else None
        m = QMenu(self)
        what = f"{len(sel)} channel{'s' if len(sel) != 1 else ''}" if sel else "all channels"
        head = m.addAction(f"{time.strftime('%H:%M:%S', time.gmtime(t0))} - "
                           f"{time.strftime('%H:%M:%S', time.gmtime(t1))} UTC ({t1 - t0:.2f} s), {what}")
        head.setEnabled(False)
        m.addAction("Save... (miniSEED)", lambda: self.open_export(t0, t1, sel))
        m.addAction("Zoom In", lambda: self.view.show_range(t0, t1))
        a = m.addAction("Save on selection")
        a.setCheckable(True)
        a.setChecked(self.save_on_selection)
        a.setToolTip("Every new selection is saved at once (miniSEED, to the Exports folder)")
        a.toggled.connect(self.set_save_on_selection)
        m.addSeparator()
        m.addAction("Spectrum...", lambda: self.open_spectrum(t0, t1, sel))
        m.addAction("PSD in Acceleration Units", lambda: self.open_psd(t0, t1, "acceleration", sel))
        m.addAction("PSD in Velocity Units", lambda: self.open_psd(t0, t1, "velocity", sel))
        m.addAction("Peak values (acceleration, velocity)", lambda: self.open_report("peaks", t0, t1, sid, sel))
        m.addAction("Relative Orientation", lambda: self.open_report("orientation", t0, t1, sid, sel))
        m.addSeparator()
        m.addAction("Strong-motion analysis...", lambda: self.open_analysis("strong", t0, t1, sid, sel))
        m.addAction("Sinewave Calibration...", lambda: self.open_analysis("sine", t0, t1, sid, sel))
        m.addAction("Step Calibration...", lambda: self.open_analysis("step", t0, t1, sid, sel))
        m.addAction("Broadband Noise Calibration...", lambda: self.open_analysis("noise", t0, t1, sid, sel))
        m.addSeparator()
        tm = m.addMenu("External tools")
        from .external_tools import tools
        for tool in tools(self.config):
            tm.addAction(tool["name"], lambda t=tool: self.run_tool(t, t0, t1, sel))
        if tm.actions():
            tm.addSeparator()
        tm.addAction("Set up external tools...", self.setup_tools)
        if sid:
            m.addSeparator()
            m.addAction(f"Use as noise in the filter design ({sid})",
                        lambda: self.use_in_design("noise", sid, t0, t1))
            m.addAction(f"Use as signal in the filter design ({sid})",
                        lambda: self.use_in_design("signal", sid, t0, t1))
        return m

    def open_psd(self, t0, t1, want, sids=None):
        from .analysis_dialogs import PsdDialog
        sids = self._pick(sids)
        if not sids:
            return None
        dlg = PsdDialog(self.view, sids, t0, t1, want, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
        return dlg

    def open_analysis(self, kind, t0, t1, sid=None, sids=None):
        """Strong-motion analysis, or one of the calibration checks, of a selection (sids: the
        selected channels). A calibration with exactly two selected channels (Ctrl+drag) takes
        the first picked as the calibration signal and the second as the sensor output; with one
        or more than two it gets the rest of their stations from this viewer and the dialog
        guesses the signal (it can be changed there)."""
        from .analysis_dialogs import CalibrationCheckDialog, StrongMotionDialog
        chosen = self._pick(sids)
        if not chosen:
            return None
        if kind == "strong":
            dlg = StrongMotionDialog(self.view, chosen, t0, t1, sid, self)
        else:
            if sids and len(chosen) == 2:
                sids, sid = [chosen[1], chosen[0]], chosen[1]       # the output, then the signal
            elif sids:
                sids, sid = self._with_station(chosen), chosen[0]
            else:
                sids = chosen
            if len(sids) < 2:
                self.statusBar().showMessage("A calibration needs two channels in this viewer: the sensor's "
                                             "output and the calibration signal", 8000)
                return None
            dlg = CalibrationCheckDialog(self.view, sids, t0, t1, kind, sid, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
        return dlg

    def open_report(self, kind, t0, t1, sid=None, sids=None):
        """Peak values of the selected channels, or the relative orientation of their stations
        (the N and E channels of those stations in this viewer; the first selected station is
        the reference)."""
        from .analysis_dialogs import ReportDialog, orientation_report, peak_report
        chosen = self._pick(sids)
        if not chosen:
            return None
        if kind == "peaks":
            title, text = "Peak values", peak_report(self.view, chosen, t0, t1)
        else:
            ref = chosen[0] if sids else sid
            title, text = "Relative orientation", orientation_report(
                self.view, self._with_station(chosen) if sids else chosen, t0, t1, ref)
        dlg = ReportDialog(title, text, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
        return dlg

    def show_selection_menu(self, t0, t1, sid, pos):
        """The menu of a selection; the selection stays drawn while it is open."""
        sids = self.view.selected_channels() or None
        if self.save_on_selection:
            self.save_selection(t0, t1, sids)
        m = self.build_selection_menu(t0, t1, sid, sids)
        m.exec(pos)
        m.deleteLater()
        self.view.clear_selection()

    def set_save_on_selection(self, on):
        self.save_on_selection = bool(on)
        self.stateChanged.emit()

    def save_selection(self, t0, t1, sids=None):
        """Save a selection at once (Save on selection): miniSEED, one file per channel, into the
        Exports folder. Returns the written files."""
        from ..core import export
        sids = self._pick(sids)
        if not sids:
            return []
        try:
            written, _report = export.export_window(sids, t0, t1, paths.ensure(paths.exports_dir()), mode="separate",
                                                    hub=self.engine.hub, log=lambda _m: None)
        except (OSError, ValueError) as e:
            self.statusBar().showMessage(f"Selection not saved: {e}", 8000)
            return []
        files = [p for p in written if p.lower().endswith(".mseed")]
        self.statusBar().showMessage(f"Selection saved: {len(files)} file(s) in {paths.exports_dir()}"
                                     if files else "Selection not saved: no data in it", 8000)
        return files

    def run_tool(self, tool, t0, t1, sids=None):
        from .external_tools import run
        sids = self._pick(sids)
        if not sids:
            return False
        ok, text, _args = run(tool, self.engine, sids, t0, t1)
        self.statusBar().showMessage(text, 10000)
        return ok

    def setup_tools(self):
        from .external_tools import ExternalToolsDialog
        dlg = ExternalToolsDialog(self.config, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.open()
        return dlg

    # ------------------------------------------------------------------------------------
    # channel actions
    # ------------------------------------------------------------------------------------
    def choose_color(self, sid):
        i = self.view.channels().index(sid) if sid in self.view.channels() else 0
        c = QColorDialog.getColor(QColor(self.view.trace_color(i, sid)), self, f"Colour of {sid}")
        if c.isValid():
            self.view.set_trace_color(sid, c.name())

    def scale_channel(self, sid):
        dlg = ScaleDialog(self.view, sid, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.open()
        return dlg

    def show_details(self, sid):
        d = self.details.get(sid)
        if d is not None:
            try:
                d.raise_()
                d.activateWindow()
                return d
            except RuntimeError:   # closed and deleted
                pass
        d = DetailsWindow(self.view, sid, self)
        d.destroyed.connect(lambda _=None, s=sid: self.details.pop(s, None))
        self.details[sid] = d
        d.show()
        return d

    def calibrate(self, sid):
        dlg = CalibrationDialog(self.config, self.engine, sid, self)
        if dlg.exec():
            if self.host is not None and hasattr(self.host, "calibration_changed"):
                self.host.calibration_changed()
            else:
                self.view.calibration_changed()

    def use_in_design(self, kind, sid, t0, t1):
        """The amplitude spectrum of a channel's raw data in a time range, into the filter
        design (the longest gap-free piece)."""
        from .spectrum_view import longest_segment
        seg = longest_segment(self.engine.hub.window(sid, t0, t1))
        if seg is None:
            self.statusBar().showMessage(f"No data of {sid} in that time range", 5000)
            return None
        f, a = dsp.spectrum(seg[2], seg[1])
        if not len(f):
            self.statusBar().showMessage("Too few samples for a spectrum - select a longer time range", 5000)
            return None
        return self.view.open_filter_design(None, (kind, f, a))

    # ------------------------------------------------------------------------------------
    # window actions
    # ------------------------------------------------------------------------------------
    def choose_channels(self):
        dlg = ChannelPickerDialog(self.engine, self.view.channels(), self)
        if dlg.exec():
            self.view.set_channels(dlg.chosen())

    def clear_window(self):
        if not self.view.channels():
            return
        ans = QMessageBox.question(self, "Clear window", "Remove every channel from this viewer?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans == QMessageBox.Yes:
            self.view.clear()

    def ask_channel_label(self, sid):
        """A name for one channel: shown in its box in the panel on the left (empty: the station)."""
        text, ok = QInputDialog.getText(self, "Label", f"Label of {sid} (shown on the left; empty: none):",
                                        text=self.view.trace_settings(sid)["label"])
        if ok:
            self.view.set_trace_label(sid, text)

    def ask_label(self):
        text, ok = QInputDialog.getText(self, "Window title", "Name of this viewer window (empty: its channels):",
                                        text=self._label)
        if ok:
            self._label = text.strip()
            self._changed()

    def choose_background(self):
        c = QColorDialog.getColor(QColor(self.view._background()), self, "Background colour")
        if c.isValid():
            self.view.set_background(None if c.name() == theme.COLORS["plot_bg"] else c.name())

    def toggle_caption(self):
        """No caption: menu, tool bar and status bar hidden, full screen; again: back."""
        self._caption = not self._caption
        for w in (self.menuBar(), self.statusBar(), self.view.bar):
            w.setVisible(self._caption)
        if self._caption:
            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        self.a_caption.setChecked(not self._caption)

    def duplicate(self):
        st = copy.deepcopy(self.state())
        st["geometry"] = ""
        if self.host is not None and hasattr(self.host, "open_viewer"):
            w = self.host.open_viewer(state=st)
        else:
            w = ViewerWindow(self.engine, self.config, st, None)
            w.show()
            self._dup = w
        if w is not None:
            w.move(self.pos() + QPoint(40, 40))
        return w

    def _sids_or_message(self):
        sids = self.view.channels()
        if not sids:
            self.statusBar().showMessage("Add channels to this viewer first", 5000)
        return sids

    def _pick(self, sids):
        """The selected channels that are still in this viewer, else every channel of it."""
        have = self.view.channels()
        chosen = [s for s in (sids or []) if s in have]
        return chosen or self._sids_or_message()

    def _with_station(self, sids):
        """The channels plus the other channels of their stations in this viewer (a calibration
        signal, the N and E pair of a station), the chosen ones first."""
        stations = {".".join(s.split(".")[:2]) for s in sids}
        return list(sids) + [s for s in self.view.channels()
                             if s not in sids and ".".join(s.split(".")[:2]) in stations]

    def open_spectrum(self, t0=None, t1=None, sids=None):
        from .spectrum_view import SpectrumDialog
        sids = self._pick(sids)
        if not sids:
            return None
        if t0 is None:
            t0, t1 = self.view.visible_range()
        dlg = SpectrumDialog(self.engine, sids, t0, t1, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
        return dlg

    def open_export(self, t0=None, t1=None, sids=None):
        sids = self._pick(sids)
        if not sids:
            return None
        if t0 is None:
            t0, t1 = self.view.visible_range()
        if self.host is not None and hasattr(self.host, "open_export_dialog"):
            return self.host.open_export_dialog(sids, t0, t1)
        from .export_dialog import ExportDialog
        dlg = ExportDialog(self.engine, self.config, self, sids=sids, t0=t0, t1=t1)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
        return dlg

    def open_record(self):
        sids = self._sids_or_message()
        if not sids:
            return None
        if self.host is not None and hasattr(self.host, "open_record_dialog"):
            return self.host.open_record_dialog(sids, only=True)
        from .record_dialog import RecordDialog
        dlg = RecordDialog(self.engine, self.config, None, sids=sids)
        dlg.select_channels(sids)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
        return dlg

    def picture(self):
        """The plots as an image (what Save picture and Print use)."""
        return self.view.glw.grab()

    def save_picture(self, path=None):
        if path is None:
            name = time.strftime("SeisZone_%Y-%m-%d_%H%M%S.png", time.gmtime())
            path, _ = QFileDialog.getSaveFileName(self, "Save picture", f"{paths.exports_dir()}\\{name}",
                                                  "PNG image (*.png);;JPEG image (*.jpg)")
        if not path:
            return False
        try:
            paths.ensure(os.path.dirname(path) or ".")
        except OSError:
            pass
        ok = self.picture().save(path)
        self.statusBar().showMessage(f"Saved {path}" if ok else f"Could not save {path}", 6000)
        return ok

    def print_view(self):
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageOrientation(QPageLayout.Landscape)
        dlg = QPrintDialog(printer, self)
        if dlg.exec() != QPrintDialog.Accepted:
            return False
        pix = self.picture()
        t0, t1 = self.view.visible_range()
        head = (f"{self.title()}   {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(t0))} - "
                f"{time.strftime('%H:%M:%S', time.gmtime(t1))} UTC   filter: {self.view.window_filter_text()}")
        p = QPainter(printer)
        try:
            page = p.viewport()
            p.drawText(page.adjusted(0, 0, 0, -page.height() + 200), Qt.AlignLeft | Qt.AlignTop, head)
            area = page.adjusted(0, 220, 0, 0)
            size = pix.size()
            size.scale(area.size(), Qt.KeepAspectRatio)
            p.drawPixmap(area.x(), area.y(), size.width(), size.height(), pix)
        finally:
            p.end()
        return True

    def show_keys(self):
        QMessageBox.information(self, "Viewer keys and mouse", (__doc__ or "").split("Keys:", 1)[-1].strip()
                                + "\n\nMouse: wheel = amplitude, Shift+wheel = window length, Ctrl+wheel = "
                                  "scroll in time, drag = pan (pauses), Shift+drag = select a time range on the channels "
                                  "dragged over, Ctrl+drag = on exactly two channels (first picked first), "
                                  "double-click = live, right-click = menu. Drag channels here from the "
                                  "Channels table of the main window.")

    # ------------------------------------------------------------------------------------
    def closeEvent(self, ev):
        for d in list(self.details.values()):
            try:
                d.close()
            except RuntimeError:
                pass
        super().closeEvent(ev)
        if ev.isAccepted() and not self.quitting:
            self.closedByUser.emit(self)
