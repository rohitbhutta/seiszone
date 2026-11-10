"""MainWindow: toolbar, sources (left), live waveforms (centre), channels + log (bottom tabs),
status bar. It only wires the panels together and drives them with two timers; all data comes
from engine.status(), engine.hub and engine.recorder (plan.md 5.15).

ui.app sets `tray_available` and `quit_callback`; without them (tests) closing the window just
closes it.
"""

import logging
import os
import sys
import time

from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDockWidget, QGridLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from .. import APP_NAME, VERSION, paths
from . import theme
from .channels_panel import ChannelsPanel
from .export_dialog import ExportDialog
from .log_panel import LogPanel
from .record_dialog import RecordDialog
from .settings_dialog import SettingsDialog
from .sources_panel import SourcesPanel
from .spectrum_view import SpectrumDialog
from .waveform_view import WaveformView

log = logging.getLogger("seiszone")

FIRST_CHANNELS = 6          # channels shown automatically on the very first start
DOT = chr(0x25CF)           # status dot in the status bar
STANDARDS = ("SeedLink 3 (client and server)", "miniSEED 2 (Steim1/Steim2)", "FDSN StationXML",
             "Guralp GCF / Scream protocol (UDP, TCP/BTP)", "Kelunji Gecko telemetry framing")


def human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def open_folder(folder):
    """Open a folder in Explorer (created first, so the button never fails on a fresh install)."""
    try:
        paths.ensure(folder)
    except OSError as e:
        log.warning("cannot create folder %s: %s", folder, e)
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(folder))):
        log.warning("could not open %s", folder)


def user_guide_path():
    """docs/user_guide.md next to the program (installed) or at the project root (development)."""
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [getattr(sys, "_MEIPASS", None), os.path.dirname(sys.executable),
             os.path.join(here, "..", "..", "..")]
    for r in roots:
        if r:
            p = os.path.normpath(os.path.join(r, "docs", "user_guide.md"))
            if os.path.exists(p):
                return p
    return None


class AboutDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setWindowIcon(theme.app_icon())
        c = theme.COLORS
        lay = QVBoxLayout(self)
        head = QLabel()
        head.setPixmap(theme.app_icon().pixmap(56, 56))
        title = QLabel(f"<span style='font-size:16pt; font-weight:600'>{APP_NAME}</span>"
                       f"&nbsp;&nbsp;<span style='color:{c['muted']}'>version {VERSION}</span><br>"
                       "Live seismic data acquisition, viewing and recording")
        top = QGridLayout()
        top.addWidget(head, 0, 0, Qt.AlignTop)
        top.addWidget(title, 0, 1)
        top.setColumnStretch(1, 1)
        lay.addLayout(top)
        lay.addWidget(QLabel("<b>Open standards</b><br>" + "<br>".join(f"&bull; {s}" for s in STANDARDS)))

        grid = QGridLayout()
        folders = [("Settings and state", paths.config_dir()),
                   ("Log files", paths.log_dir()),
                   ("Recordings", config.get("recording", "folder") or paths.recordings_dir()),
                   ("Exports", paths.exports_dir()),
                   ("StationXML", paths.stationxml_dir())]
        grid.addWidget(QLabel("<b>Folders</b>"), 0, 0, 1, 3)
        for i, (label, folder) in enumerate(folders, start=1):
            grid.addWidget(QLabel(label), i, 0)
            path = QLabel(folder)
            path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            path.setStyleSheet(f"color: {c['muted']};")
            grid.addWidget(path, i, 1)
            btn = QPushButton("Open")
            btn.setAutoDefault(False)
            btn.clicked.connect(lambda _=False, f=folder: open_folder(f))
            grid.addWidget(btn, i, 2)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.button(QDialogButtonBox.Close).setDefault(True)
        box.rejected.connect(self.reject)
        lay.addWidget(box)


class MainWindow(QMainWindow):
    hiddenToTray = Signal()          # the close button hid the window (ui.app shows a one-time hint)

    def __init__(self, engine, config):
        super().__init__()
        self.engine = engine
        self.config = config
        self.tray_available = False      # set by ui.app when a tray icon exists
        self.quit_callback = None        # set by ui.app: confirm, stop engine, quit
        self.dialogs = {}                # name -> open (non-blocking) dialog
        self._quitting = False
        self._auto_pick = not config.get("view", "channels")
        self._errors = {}                # timer task -> last time its exception was logged
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.setWindowIcon(theme.app_icon())
        self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowTabbedDocks)

        self.view = WaveformView(engine, config, self)
        self.setCentralWidget(self.view)
        self.sources = SourcesPanel(engine, config, self)
        self.channels = ChannelsPanel(engine, config, self)
        self.log_panel = LogPanel(self)
        self.sources_dock = self._dock("Sources", "dock_sources", self.sources, Qt.LeftDockWidgetArea)
        self.channels_dock = self._dock("Channels", "dock_channels", self.channels, Qt.BottomDockWidgetArea)
        self.log_dock = self._dock("Log", "dock_log", self.log_panel, Qt.BottomDockWidgetArea)
        self.setTabPosition(Qt.BottomDockWidgetArea, QTabWidget.North)
        self.tabifyDockWidget(self.channels_dock, self.log_dock)
        for d in (self.channels_dock, self.log_dock):
            d.setTitleBarWidget(QWidget())  # the tab is their title; View menu shows / hides them
        self.channels_dock.raise_()

        self._build_actions()
        self._build_toolbar()
        self._build_menus()
        self._build_status_bar()

        self.channels.shownChanged.connect(self._on_shown_changed)
        self.sources.channelsRequested.connect(self.show_channels)
        self.sources.sourceSelected.connect(lambda _sid: self._update_source_actions())

        saved = [s for s in (config.get("view", "channels") or []) if isinstance(s, str)]
        if saved:
            self.channels.set_shown(saved)
            self.view.set_channels(saved)

        self._restore_layout()

        self.fast = QTimer(self)
        self.fast.setInterval(250)
        self.fast.timeout.connect(lambda: self._safe("waveforms", self.view.refresh))
        self.slow = QTimer(self)
        self.slow.setInterval(1000)
        self.slow.timeout.connect(self.refresh)
        self.fast.start()
        self.slow.start()
        self.refresh()

    # ------------------------------------------------------------------------------------
    # building
    # ------------------------------------------------------------------------------------
    def _dock(self, title, name, widget, area):
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
                      | QDockWidget.DockWidgetClosable)
        self.addDockWidget(area, d)
        return d

    def _action(self, text, slot, icon=None, tip=None, shortcut=None):
        a = QAction(text, self)
        if icon:
            a.setData(icon)
            a.setIcon(theme.icon(icon))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(lambda _=False: slot())
        return a

    def _build_actions(self):
        A = self._action
        self.act_add = A("Add source", self.add_source, "add", "Add an instrument or SeedLink server by IP address",
                         "Ctrl+N")
        self.act_remove = A("Remove", self.sources.remove_selected, "remove", "Remove the selected source")
        self.act_reconnect = A("Reconnect", self.sources.reconnect_selected, "reconnect",
                               "Drop and re-open the connection of the selected source")
        self.act_record = A("Record", self.open_record_dialog, "record",
                            "Start, watch and stop recordings to miniSEED files", "Ctrl+R")
        self.act_export = A("Export", self.open_export_dialog, "export",
                            "Export a time window of the shown channels to miniSEED", "Ctrl+E")
        self.act_spectrum = A("Spectrum", self.open_spectrum, "spectrum",
                              "Amplitude spectrum of the shown channels in the visible time range")
        self.act_settings = A("Settings", self.open_settings, "settings",
                              "Receiver, server, recording and display settings", "Ctrl+,")
        self.act_about = A("About", self.open_about, "app", f"About {APP_NAME}")
        self.act_quit = A("Quit", self.request_quit, None, f"Stop {APP_NAME} completely", "Ctrl+Q")
        self.act_live = A("Live", lambda: self.view.set_live(self.act_live.isChecked()), "live",
                          "Follow the newest data (zooming or panning pauses it)")
        self.act_live.setCheckable(True)
        self.act_live.setChecked(True)
        self.act_guide = A("User guide", self.open_user_guide, None, "Open the SeisZone user guide", "F1")
        self.act_logs = A("Log folder", lambda: open_folder(paths.log_dir()), None, paths.log_dir())

    def _build_toolbar(self):
        tb = QToolBar("Main toolbar", self)
        tb.setObjectName("toolbar_main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for a in (self.act_add, self.act_remove, self.act_reconnect, None, self.act_record, self.act_export,
                  self.act_spectrum, None, self.act_settings, self.act_about):
            if a is None:
                tb.addSeparator()
            else:
                tb.addAction(a)
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.toolbar = tb
        self.record_button = tb.widgetForAction(self.act_record)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addActions([self.act_add, self.act_export, self.act_settings])
        m.addSeparator()
        m.addAction(self.act_quit)
        m = mb.addMenu("&View")
        for d in (self.sources_dock, self.channels_dock, self.log_dock):
            m.addAction(d.toggleViewAction())
        m.addSeparator()
        m.addAction(self.act_live)
        m = mb.addMenu("&Recording")
        m.addAction(self._action("New recording...", self.open_record_dialog, "record"))
        m.addAction(self._action("Manage recordings...", self.open_record_dialog))
        m = mb.addMenu("&Help")
        m.addAction(self.act_guide)
        m.addAction(self.act_logs)
        m.addSeparator()
        m.addAction(self.act_about)
        self.act_guide.setEnabled(user_guide_path() is not None)

    def _build_status_bar(self):
        sb = self.statusBar()
        self.st = {}
        for key in ("sources", "gecko", "server", "recording", "disk"):
            lab = QLabel()
            sb.addPermanentWidget(lab)
            self.st[key] = lab

    def refresh_icons(self):
        """Re-draw the action icons after a theme change (icons are drawn in the theme colours)."""
        for a in self.findChildren(QAction):
            name = a.data()
            if isinstance(name, str) and name:
                a.setIcon(theme.icon(name))

    # ------------------------------------------------------------------------------------
    # timers
    # ------------------------------------------------------------------------------------
    def _safe(self, name, fn, *args):
        """Run a timer task; an exception is logged (at most once a minute per task) instead of
        popping up a message box four times a second."""
        try:
            return fn(*args)
        except Exception:
            now = time.time()
            if now - self._errors.get(name, 0) > 60:
                self._errors[name] = now
                log.exception("display update '%s' failed", name)
        return None

    def refresh(self):
        """Once a second: tables, log, status bar, record button, first-start channel choice."""
        self._safe("sources", self.sources.refresh)
        self._safe("channels", self.channels.refresh)
        self._safe("log", self.log_panel.refresh)
        status = self._safe("status", self.engine.status) or {}
        sessions = self._safe("sessions", self.engine.recorder.sessions) or []
        self._safe("statusbar", self._update_status, status, sessions)
        self._safe("record", self._update_record_button, sessions)
        self._safe("first channels", self._first_channels)
        self._safe("source actions", self._update_source_actions)
        if self.act_live.isChecked() != self.view.is_live():
            self.act_live.setChecked(self.view.is_live())

    def _first_channels(self):
        if not self._auto_pick:
            return
        if self.channels.shown():
            self._auto_pick = False
            return
        sids = sorted(self.engine.hub.stream_ids())[:FIRST_CHANNELS]
        if sids:
            log.info("showing the first channels: %s", ", ".join(sids))
            self.show_channels(sids)

    def _update_source_actions(self):
        has = bool(self.sources.selected_source_id())
        self.act_remove.setEnabled(has)
        self.act_reconnect.setEnabled(has)

    @staticmethod
    def _set(label, text, color=None, tip=""):
        """One status-bar field: text, colour, tooltip; a thin line separates the fields."""
        label.setText(text)
        style = f"padding: 0 10px; border-left: 1px solid {theme.COLORS['border']};"
        label.setStyleSheet(style + (f" color: {color};" if color else ""))
        label.setToolTip(tip)

    def _update_status(self, status, sessions):
        c = theme.COLORS
        srcs = list((status.get("sources") or {}).values())
        active = [s for s in srcs if not str(s.get("state", "")).lower().startswith(("stopped", "disabled"))]
        receiving = [s for s in active if str(s.get("state", "")).lower().startswith("receiving")]
        n, m = len(receiving), len(active)
        color = c["muted"] if m == 0 else (c["ok"] if n == m else c["warn"])
        self._set(self.st["sources"], f"{DOT} {n}/{m} sources receiving", color,
                  "\n".join(f"{s.get('address', '')}: {s.get('state', '')}" for s in srcs))

        g = status.get("gecko") or {}
        ports = ",".join(str(p) for p in g.get("ports") or [])
        if g.get("error"):
            self._set(self.st["gecko"], f"Gecko :{ports} - {g['error']}", c["danger"], g["error"])
        elif g.get("listening"):
            links = g.get("links") or []
            self._set(self.st["gecko"], f"Gecko links: {len(links)}", c["ok"] if links else None,
                      "\n".join(f"{x.get('serial', '?')} {x.get('station', '')} from {x.get('peer', '')}"
                                for x in links) or f"Listening on {ports}, no Gecko connected")
        else:
            self._set(self.st["gecko"], "Gecko receiver off", c["muted"])

        s = status.get("server") or {}
        if s.get("error"):
            self._set(self.st["server"], f"SeedLink server :{s.get('port')} - {s['error']}", c["danger"],
                      s["error"])
        elif s.get("listening"):
            k = int(s.get("clients") or 0)
            self._set(self.st["server"], f"SeedLink server :{s.get('port')} - {k} client{'s' if k != 1 else ''}",
                      None, "\n".join(str(p.get("peer", "")) for p in s.get("peers") or []))
        else:
            self._set(self.st["server"], "SeedLink server off", c["muted"])

        rec = [x for x in sessions if x.get("state") == "recording"]
        if rec:
            self._set(self.st["recording"], f"{DOT} Recording {len(rec)}", c["danger"],
                      "\n".join(f"{x.get('name', '')}: {x.get('folder', '')}" for x in rec))
        else:
            self._set(self.st["recording"], "Not recording", c["muted"])

        folder = self.config.get("recording", "folder") or paths.recordings_dir()
        free, warning = self.engine.recorder.disk_status(folder)
        self._set(self.st["disk"], f"Free {human_bytes(free)}" + (" - low!" if warning else ""),
                  c["danger"] if warning else None, warning or f"Free space on the drive of {folder}")

    def _update_record_button(self, sessions):
        n = sum(1 for x in sessions if x.get("state") == "recording")
        self.act_record.setText(f"Recording ({n})" if n else "Record")
        if self.record_button is not None:
            self.record_button.setStyleSheet(
                f"QToolButton {{ background: {theme.COLORS['danger']}; color: #ffffff; }}" if n else "")

    # ------------------------------------------------------------------------------------
    # channels
    # ------------------------------------------------------------------------------------
    def _on_shown_changed(self, sids):
        self._auto_pick = False
        self.view.set_channels(list(sids))
        self.config.set("view", "channels", list(sids))

    def show_channels(self, sids):
        """Show exactly these channels in the table ticks and the waveform view."""
        sids = list(sids)
        self._auto_pick = False
        self.channels.set_shown(sids)
        self.view.set_channels(sids)
        self.config.set("view", "channels", sids)

    def shown_channels(self):
        return list(self.view.channels()) or list(self.channels.shown())

    # ------------------------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------------------------
    def _open(self, name, factory, on_accept=None):
        """Open a dialog without blocking the timers (live data keeps flowing behind it)."""
        dlg = self.dialogs.get(name)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return dlg
        dlg = factory()
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        self.dialogs[name] = dlg

        def finished(result, n=name, d=dlg):
            if self.dialogs.get(n) is d:
                del self.dialogs[n]
            if result == QDialog.Accepted and on_accept:
                self._safe(n, on_accept, d)

        dlg.finished.connect(finished)
        dlg.open()
        return dlg

    def add_source(self, initial_text=""):
        self.sources.add_source(initial_text)

    def open_record_dialog(self):
        return self._open("record", lambda: RecordDialog(self.engine, self.config, self,
                                                         sids=self.shown_channels()))

    def open_export_dialog(self):
        t0, t1 = self.view.visible_range()
        return self._open("export", lambda: ExportDialog(self.engine, self.config, self,
                                                         sids=self.shown_channels(), t0=t0, t1=t1))

    def open_spectrum(self):
        sids = self.shown_channels()
        if not sids:
            self.statusBar().showMessage("Show at least one channel first (tick it in the Channels table)", 5000)
            return None
        t0, t1 = self.view.visible_range()
        return self._open("spectrum", lambda: SpectrumDialog(self.engine, sids, t0, t1, self))

    def open_settings(self):
        return self._open("settings", lambda: SettingsDialog(self.engine, self.config, self),
                          lambda _d: self._settings_applied())

    def _settings_applied(self):
        """The settings dialog saved and applied everything; follow the theme and the
        start-with-Windows choice and the default time window here (the view would otherwise
        keep its old window and save it back over the new choice)."""
        self.refresh_icons()
        from .waveform_view import nearest_window
        w = nearest_window(self.config.get("view", "window_seconds", 300))
        i = self.view.window_box.findData(w)
        if i >= 0 and i != self.view.window_box.currentIndex():
            self.view.window_box.setCurrentIndex(i)   # the view takes and saves the same value
        from .app import autostart_enabled, set_autostart  # app imports this module
        want = bool(self.config.get("ui", "start_with_windows", False))
        if want != autostart_enabled():
            set_autostart(want)

    def open_about(self):
        return self._open("about", lambda: AboutDialog(self.config, self))

    def open_user_guide(self):
        p = user_guide_path()
        if p:
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))

    # ------------------------------------------------------------------------------------
    # layout and closing
    # ------------------------------------------------------------------------------------
    def _restore_layout(self):
        geo = self.config.get("ui", "geometry") or ""
        state = self.config.get("ui", "state") or ""
        restored = False
        try:
            if geo:
                restored = self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            if state:
                self.restoreState(QByteArray.fromBase64(state.encode("ascii")))
        except (ValueError, UnicodeError):
            log.warning("saved window layout ignored (damaged)")
        if not restored:
            self.resize(1600, 950)
            self.resizeDocks([self.sources_dock], [430], Qt.Horizontal)
            self.resizeDocks([self.channels_dock], [270], Qt.Vertical)

    def save_layout(self):
        geo = bytes(self.saveGeometry().toBase64()).decode("ascii")
        state = bytes(self.saveState().toBase64()).decode("ascii")
        self.config.update_section("ui", {"geometry": geo, "state": state})

    def active_recordings(self):
        return [x for x in (self._safe("sessions", self.engine.recorder.sessions) or [])
                if x.get("state") == "recording"]

    def confirm_quit(self):
        """True when quitting is fine (no recordings, or the user agrees to interrupt them)."""
        if not self.active_recordings():
            return True
        ans = QMessageBox.question(self, f"Quit {APP_NAME}",
                                   "Recordings stop being written while SeisZone is closed. Quit?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ans == QMessageBox.Yes

    def prepare_quit(self):
        """Stop the timers, close dialogs and save the layout: the window may now really close."""
        self._quitting = True
        self.fast.stop()
        self.slow.stop()
        for d in list(self.dialogs.values()):
            d.reject()
        self._safe("save layout", self.save_layout)

    def request_quit(self):
        if self.quit_callback:
            self.quit_callback()
        elif self.confirm_quit():
            self.prepare_quit()
            self.close()

    def closeEvent(self, ev):
        if self._quitting:
            ev.accept()
            return
        self._safe("save layout", self.save_layout)
        if self.tray_available and self.config.get("ui", "minimize_to_tray", True):
            ev.ignore()
            self.hide()
            self.hiddenToTray.emit()
            return
        if self.quit_callback:
            # Qt refuses a nested close() from inside closeEvent: quit right after this event
            ev.ignore()
            QTimer.singleShot(0, self.quit_callback)
        elif self.confirm_quit():
            self.prepare_quit()
            ev.accept()
        else:
            ev.ignore()
