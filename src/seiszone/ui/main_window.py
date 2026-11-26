"""MainWindow: toolbar, sources (left), channels (centre), log (bottom), status bar. The live
waveforms are in viewer windows of their own (ui.viewer_window): the user ticks channels and
opens a viewer with them (Open viewer, a double-click, the Sources panel's "Open in a viewer"),
as many as wanted. MainWindow keeps the list of open viewers, drives them with its timer, saves
them (config "viewers") and opens them again at the next start. It only wires the panels
together and drives them with two timers; all data comes from engine.status(), engine.hub and
engine.recorder (plan.md 5.15).

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
                               QMessageBox, QToolBar, QVBoxLayout)

from .. import APP_NAME, VERSION, paths
from ..core import utc_clock
from . import theme
from .channels_panel import ChannelsPanel
from .export_dialog import ExportDialog
from .log_panel import LogPanel
from .record_dialog import RecordDialog
from .screen_fit import keep_on_screen
from .update_dialog import UpdateDialog, check_for_updates
from .server_window import DataServerWindow
from .settings_dialog import SettingsDialog
from .sources_panel import SourcesPanel
from .viewer_window import ViewerWindow

log = logging.getLogger("seiszone")

DOT = chr(0x25CF)           # status dot in the status bar
LAYOUT_VERSION = 2          # saveState version: 2 = channels in the centre, viewers in windows of their own


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
    """The program's name, version and one line about it (nothing else)."""

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
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.title = title
        top = QGridLayout()
        top.addWidget(head, 0, 0, Qt.AlignTop)
        top.addWidget(title, 0, 1)
        top.setColumnStretch(1, 1)
        lay.addLayout(top)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.button(QDialogButtonBox.Close).setDefault(True)
        box.rejected.connect(self.reject)
        lay.addWidget(box)



SYNC_HINT = "To set the PC clock right: Windows Settings > Time & language > Date & time > Sync now."


def clock_status(clk):
    """(text, colour, tooltip) of the status-bar clock field from Engine.status()["clock"]."""
    c = theme.COLORS
    state, offset = clk.get("state"), clk.get("offset")
    if state == "ok" and offset is not None:
        when = time.strftime("%H:%M:%S", time.gmtime((clk.get("checked") or time.time()) + offset))
        tip = (f"Checked against {clk.get('server', '')} at {when} UTC "
               f"(round trip {1000 * (clk.get('delay') or 0):.0f} ms).\n"
               "Latency uses UTC from the time server, not the PC clock.")
        if abs(offset) < utc_clock.TOLERANCE:
            return "Clock: UTC ok", None, tip
        return utc_clock.describe(offset) + " - corrected", c["warn"], tip + "\n" + SYNC_HINT
    if state == "failed":
        return "PC clock not checked", c["muted"], (f"No time server answered ({clk.get('error', '')}).\n"
                                                    "Latency uses the PC clock.")
    if state == "off":
        return "PC clock (time check off)", c["muted"], "Latency uses the PC clock (settings: time check off)."
    return "Checking PC clock ...", c["muted"], "Asking internet time servers for UTC"


class MainWindow(QMainWindow):
    hiddenToTray = Signal()          # the close button hid the window (ui.app shows a one-time hint)

    def __init__(self, engine, config):
        super().__init__()
        self.engine = engine
        self.config = config
        self.tray_available = False      # set by ui.app when a tray icon exists
        self.quit_callback = None        # set by ui.app: confirm, stop engine, quit
        self.dialogs = {}                # name -> open (non-blocking) dialog
        self.viewers = []                # open viewer windows (each a window of its own)
        self._quitting = False
        self._restored = False           # saved viewers opened again (at the first show)
        self._errors = {}                # timer task -> last time its exception was logged
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.setWindowIcon(theme.app_icon())
        self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowTabbedDocks)

        self.sources = SourcesPanel(engine, config, self)
        self.channels = ChannelsPanel(engine, config, self)
        self.channels.viewer_titles = lambda: [v.title() for v in self.viewers]
        self.setCentralWidget(self.channels)
        self.log_panel = LogPanel(self)
        self.sources_dock = self._dock("Sources", "dock_sources", self.sources, Qt.LeftDockWidgetArea)
        self.log_dock = self._dock("Log", "dock_log", self.log_panel, Qt.BottomDockWidgetArea)

        self._build_actions()
        self._build_toolbar()
        self._build_menus()
        self._build_status_bar()

        self.channels.shownChanged.connect(self._on_shown_changed)
        self.channels.viewRequested.connect(self.open_viewer)
        self.channels.addRequested.connect(self.add_to_viewer)
        self.sources.channelsRequested.connect(self.open_viewer)
        self.sources.sourceSelected.connect(lambda _sid: self._update_source_actions())

        saved = [s for s in (config.get("view", "channels") or []) if isinstance(s, str)]
        if saved:
            self.channels.set_shown(saved)

        self._restore_layout()

        self._save_timer = QTimer(self)      # viewer settings are saved half a second after a change
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(lambda: self._safe("save viewers", self.save_viewers))
        self.fast = QTimer(self)
        self.fast.setInterval(250)
        self.fast.timeout.connect(self._refresh_viewers)
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
        self.act_view = A("Open viewer", self.open_viewer, "channel",
                          "Open a viewer window with the ticked channels (or the selected rows)", "Ctrl+Shift+N")
        self.act_record = A("Record", self.open_record_dialog, "record",
                            "Start, watch and stop recordings to miniSEED files", "Ctrl+R")
        self.act_export = A("Export", self.open_export_dialog, "export",
                            "Export a time window of the ticked channels to miniSEED", "Ctrl+E")
        self.act_server = A("Data server", self.open_data_server, "server",
                            "Send all data to other computers on the network (UDP port 1567): see who is "
                            "connected")
        self.act_close_viewers = A("Close all viewers", self.close_viewers, None, "Close every viewer window")
        self.act_settings = A("Settings", self.open_settings, "settings",
                              "Receiver, server, recording and display settings", "Ctrl+,")
        self.act_about = A("About", self.open_about, "app", f"About {APP_NAME}")
        self.act_quit = A("Quit", self.request_quit, None, f"Stop {APP_NAME} completely", "Ctrl+Q")
        self.act_guide = A("User guide", self.open_user_guide, None, "Open the SeismoVerse user guide", "F1")
        self.act_logs = A("Log folder", lambda: open_folder(paths.log_dir()), None, paths.log_dir())
        self.act_updates = A("Check for updates...", lambda: self.check_updates(manual=True), None,
                             f"Look for a newer {APP_NAME} on GitHub (nothing about this PC is sent)")

    def _build_toolbar(self):
        tb = QToolBar("Main toolbar", self)
        tb.setObjectName("toolbar_main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for a in (self.act_add, self.act_remove, self.act_reconnect, None, self.act_view, self.act_record,
                  self.act_export, self.act_server, None, self.act_settings, self.act_about):
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
        m.addActions([self.act_add, self.act_export, self.act_server, self.act_settings])
        m.addSeparator()
        m.addAction(self.act_quit)
        m = mb.addMenu("&View")
        for d in (self.sources_dock, self.log_dock):
            m.addAction(d.toggleViewAction())
        self.window_menu = mb.addMenu("&Window")
        self.window_menu.aboutToShow.connect(self._fill_window_menu)
        self._fill_window_menu()
        m = mb.addMenu("&Recording")
        m.addAction(self._action("New recording...", self.open_record_dialog, "record"))
        m.addAction(self._action("Manage recordings...", self.open_record_dialog))
        m = mb.addMenu("&Help")
        m.addAction(self.act_guide)
        m.addAction(self.act_logs)
        m.addSeparator()
        m.addAction(self.act_updates)
        m.addAction(self.act_about)
        self.act_guide.setEnabled(user_guide_path() is not None)

    def _build_status_bar(self):
        sb = self.statusBar()
        self.st = {}
        for key in ("sources", "gecko", "server", "gcf_server", "recording", "disk", "clock"):
            lab = QLabel()
            sb.addPermanentWidget(lab)
            self.st[key] = lab

    def _fill_window_menu(self):
        m = self.window_menu
        m.clear()
        m.addAction(self.act_view)
        if self.viewers:
            m.addSeparator()
            for v in self.viewers:
                m.addAction(v.title(), lambda w=v: self._bring(w))
            m.addSeparator()
            m.addAction(self.act_close_viewers)

    @staticmethod
    def _bring(w):
        if w.isMinimized():
            w.showNormal()
        w.raise_()
        w.activateWindow()

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
        """Once a second: tables, log, status bar, record button, newly arrived channels."""
        self._safe("sources", self.sources.refresh)
        self._safe("channels", self.channels.refresh)
        self._safe("log", self.log_panel.refresh)
        status = self._safe("status", self.engine.status) or {}
        sessions = self._safe("sessions", self.engine.recorder.sessions) or []
        self._safe("statusbar", self._update_status, status, sessions)
        self._safe("record", self._update_record_button, sessions)
        self._safe("source actions", self._update_source_actions)

    def _refresh_viewers(self):
        """Four times a second: every open viewer follows the live data."""
        for v in list(self.viewers):
            self._safe("waveforms", v.view.refresh)

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

        gs = status.get("gcf_server") or {}
        if gs.get("error"):
            self._set(self.st["gcf_server"], f"Data server :{gs.get('port')} - not sending", c["danger"], gs["error"])
        elif gs.get("listening"):
            k = int(gs.get("clients") or 0)
            self._set(self.st["gcf_server"], f"Data server :{gs.get('port')} - {k} client{'s' if k != 1 else ''}",
                      c["ok"] if k else None, "\n".join(p.get("address", "") for p in gs.get("peers") or [])
                      or "Waiting for other computers (Data server window)")
        else:
            self.st["gcf_server"].hide()
        if gs.get("error") or gs.get("listening"):
            self.st["gcf_server"].show()

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
        self._set(self.st["clock"], *clock_status(status.get("clock") or {}))

    def _update_record_button(self, sessions):
        n = sum(1 for x in sessions if x.get("state") == "recording")
        self.act_record.setText(f"Recording ({n})" if n else "Record")
        if self.record_button is not None:
            self.record_button.setStyleSheet(
                f"QToolButton {{ background: {theme.COLORS['danger']}; color: #ffffff; }}" if n else "")

    # ------------------------------------------------------------------------------------
    # channels and viewers
    # ------------------------------------------------------------------------------------
    def _on_shown_changed(self, sids):
        """The user ticked / unticked channels in the Channels table (kept for the next start)."""
        self.config.set("view", "channels", list(sids))

    def open_viewer(self, sids=None, state=None):
        """A new viewer window with these channels (default: the ticked ones, else the selected
        rows), or one saved before (state). Returns it, or None when there is nothing to show."""
        if state is None:
            sids = [s for s in (sids or []) if isinstance(s, str)] or self.channels.to_view()
            if not sids:
                self.statusBar().showMessage("Tick channels in the Channels table first (or double-click one)",
                                             6000)
                return None
            state = {"sids": sids}
        w = ViewerWindow(self.engine, self.config, state, host=self)
        w.setAttribute(Qt.WA_DeleteOnClose)
        w.closedByUser.connect(self._viewer_closed)
        w.stateChanged.connect(self._save_timer.start)
        self.viewers.append(w)
        w.show()
        w.raise_()
        w.activateWindow()
        self._save_timer.start()
        log.info("viewer opened: %s", ", ".join(w.view.channels()))
        return w

    def add_to_viewer(self, sids, n):
        """Add channels to open viewer number n (the Channels table's "Add to viewer")."""
        if 0 <= n < len(self.viewers):
            w = self.viewers[n]
            w.view.add_channels(sids)
            self._bring(w)

    def _viewer_closed(self, w):
        if w in self.viewers:
            self.viewers.remove(w)
            self._save_timer.start()

    def close_viewers(self):
        for w in list(self.viewers):
            w.close()

    def save_viewers(self):
        """The open viewers (channels, settings, size and place) for the next start."""
        self.config.set_value("viewers", [w.state() for w in self.viewers])

    def restore_viewers(self):
        """Open the viewers that were open when SeisZone was last closed (once, at the first
        show - a start in the tray opens them when the window is first shown) - only when the
        user ticked that in Settings: by default SeisZone starts with its main window alone."""
        if self._restored:
            return
        self._restored = True
        if not self.config.get("ui", "reopen_viewers", False):
            return
        saved = [v for v in (self.config.get("viewers") or []) if isinstance(v, dict) and v.get("sids")]
        for st in saved:
            self._safe("restore viewer", self.open_viewer, None, st)

    def filters_changed(self, source=None):
        """A saved filter was added, replaced or deleted in one viewer: the others follow."""
        for w in self.viewers:
            if w is not source:
                self._safe("filters", w.view.filters_changed)

    def calibration_changed(self):
        """A channel's calibration was entered or removed: every viewer looks it up again."""
        for w in self.viewers:
            self._safe("calibration", w.view.calibration_changed)

    # ------------------------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------------------------
    def _open(self, name, factory, on_accept=None, modal=True):
        """Open a dialog without blocking the timers (live data keeps flowing behind it);
        modal=False: a window of its own the main window stays usable beside."""
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
        if modal:
            dlg.open()
        else:
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        keep_on_screen(dlg)          # never larger than the screen, title bar always reachable
        return dlg

    def add_source(self, initial_text=""):
        self.sources.add_source(initial_text)

    def open_record_dialog(self, sids=None, only=False):
        """The Recordings window (a window of its own: it can be minimized and kept open beside
        SeisZone); sids: channels to tick for a new recording (default: the ticked ones);
        only: "Only the channels ticked below" chosen (a viewer's "Record these channels")."""
        sids = list(sids) if sids else self.channels.to_view()
        dlg = self.dialogs.get("record")
        if dlg is not None and dlg.isVisible() and dlg.isMinimized():
            dlg.showNormal()
        dlg = self._open("record", lambda: RecordDialog(self.engine, self.config, None, sids=sids), modal=False)
        if only and sids:
            dlg.select_channels(sids)
        return dlg

    def open_export_dialog(self, sids=None, t0=None, t1=None):
        """Export; from a viewer with its channels and visible time range. An Export window
        already open is replaced by one with the new channels and range (unless it is still
        writing: then it is only brought forward)."""
        given = bool(sids) or t0 is not None
        sids = list(sids) if sids else self.channels.to_view()
        dlg = self.dialogs.get("export")
        if given and dlg is not None and dlg.isVisible():
            thread = getattr(dlg, "_thread", None)
            if thread is not None and thread.isRunning():
                self.statusBar().showMessage("An export is still being written - see the Export window", 6000)
                return self._open("export", lambda: None)
            dlg.reject()
        return self._open("export", lambda: ExportDialog(self.engine, self.config, self,
                                                         sids=sids, t0=t0, t1=t1))

    def open_data_server(self):
        return self._open("data_server", lambda: DataServerWindow(self.engine, self.config, self), modal=False)

    def open_settings(self):
        return self._open("settings", lambda: SettingsDialog(self.engine, self.config, self),
                          lambda _d: self._settings_applied())

    def _settings_applied(self):
        """The settings dialog saved and applied everything; follow the theme and the
        start-with-Windows choice here (the default time window is for new viewers)."""
        self.refresh_icons()
        from .app import autostart_enabled, set_autostart  # app imports this module
        want = bool(self.config.get("ui", "start_with_windows", False))
        if want != autostart_enabled():
            set_autostart(want)

    def open_about(self):
        return self._open("about", lambda: AboutDialog(self.config, self))

    # ------------------------------------------------------------------ updates
    def check_updates(self, manual=True):
        """Ask GitHub for a newer version (core/updater.py, ui/update_dialog.py)."""
        return check_for_updates(self, self.config, manual)

    def check_updates_at_start(self):
        """The automatic check: a little after start and then every hour while SeismoVerse keeps
        running (ui.app UPDATE_RECHECK_MS, for PCs that run for weeks), but GitHub is asked at
        most once a day (updater.CHECK_EVERY), only by the installed program and only when it is
        switched on (Settings > Program). Not while a check runs or the update window is open."""
        from ..core import updater
        if getattr(self, "_update_check", None) is not None or "update" in self.dialogs:
            return None
        if updater.automatic_check_allowed(self.config.get("updates") or {}):
            return self.check_updates(manual=False)
        return None

    def open_update(self, release):
        return self._open("update", lambda: UpdateDialog(release, self.config, self), modal=False)

    def open_user_guide(self):
        p = user_guide_path()
        if p:
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))

    # ------------------------------------------------------------------------------------
    # layout and closing
    # ------------------------------------------------------------------------------------
    def _restore_layout(self):
        """Size, place and docks as last left (a layout of an older version: docks as new)."""
        geo = self.config.get("ui", "geometry") or ""
        state = self.config.get("ui", "state") or ""
        restored = docks = False
        try:
            if geo:
                restored = self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            if state:
                docks = self.restoreState(QByteArray.fromBase64(state.encode("ascii")), LAYOUT_VERSION)
        except (ValueError, UnicodeError):
            log.warning("saved window layout ignored (damaged)")
        if not restored:
            self.resize(1400, 820)
        if not docks:
            self.resizeDocks([self.sources_dock], [430], Qt.Horizontal)
            self.resizeDocks([self.log_dock], [190], Qt.Vertical)

    def save_layout(self):
        geo = bytes(self.saveGeometry().toBase64()).decode("ascii")
        state = bytes(self.saveState(LAYOUT_VERSION).toBase64()).decode("ascii")
        self.config.update_section("ui", {"geometry": geo, "state": state})

    def active_recordings(self):
        return [x for x in (self._safe("sessions", self.engine.recorder.sessions) or [])
                if x.get("state") == "recording"]

    def confirm_quit(self):
        """True when quitting is fine (no recordings, or the user agrees to interrupt them)."""
        if not self.active_recordings():
            return True
        ans = QMessageBox.question(self, f"Quit {APP_NAME}",
                                   "Recordings stop being written while SeismoVerse is closed. Quit?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ans == QMessageBox.Yes

    def prepare_quit(self):
        """Stop the timers, save and close the viewers, close dialogs and save the layout: the
        window may now really close. The viewers open again at the next start."""
        self._quitting = True
        self.fast.stop()
        self.slow.stop()
        self._save_timer.stop()
        if self._restored:   # never restored (started in the tray): the saved list stays as it is
            self._safe("save viewers", self.save_viewers)
        for w in list(self.viewers):
            w.quitting = True
            w.close()
        self.viewers = []
        for d in list(self.dialogs.values()):
            d.reject()
        self._safe("save layout", self.save_layout)

    def request_quit(self):
        if self.quit_callback:
            self.quit_callback()
        elif self.confirm_quit():
            self.prepare_quit()
            self.close()

    def showEvent(self, ev):
        super().showEvent(ev)
        if not self._restored:
            QTimer.singleShot(0, self.restore_viewers)

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
