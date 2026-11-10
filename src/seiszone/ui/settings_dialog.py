"""SettingsDialog: Gecko receiver, SeedLink server, recording, display and program settings.

OK checks every field first (in plain words, on the tab with the problem), then saves the
sections with config.update_section, applies a theme change at once and calls
engine.apply_settings() so the listeners restart with the new ports. Nothing is saved when a
field is wrong.
"""

import ipaddress
import logging
import os
import re

from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import paths
from . import theme
from .record_dialog import FILE_MINUTES, LAYOUTS
from .waveform_view import WINDOWS, nearest_window

log = logging.getLogger("seiszone")

TAB_GECKO, TAB_SERVER, TAB_RECORDING, TAB_DISPLAY, TAB_PROGRAM = range(5)
BUFFER_RANGE = (10, 720)
NAME_RE = re.compile(r"^[A-Za-z0-9]*$")
SERIAL_RE = re.compile(r"^(0[xX])?[0-9A-Fa-f]{1,8}$")
RENAME_COLUMNS = ["Serial number", "Network", "Station", "Location"]
NAME_LIMITS = (("Network", 2), ("Station", 5), ("Location", 2))


class SettingsError(ValueError):
    """A field that cannot be saved: which tab, which widget, what to tell the user."""

    def __init__(self, tab, message, widget=None):
        super().__init__(message)
        self.tab = tab
        self.widget = widget


def parse_port(text, what="The port"):
    s = str(text).strip()
    if not s:
        raise ValueError(f"{what} is empty: type a number from 1 to 65535.")
    if not s.isdigit():
        raise ValueError(f"{what} '{s}' is not a number: type a number from 1 to 65535.")
    port = int(s)
    if not 1 <= port <= 65535:
        raise ValueError(f"{what} {port} is not possible: ports go from 1 to 65535.")
    return port


def parse_ports(text):
    """'58772, 58773' -> [58772, 58773]; ValueError in plain words for anything else."""
    parts = [p.strip() for p in str(text).replace(";", ",").split(",")]
    if not any(parts):
        raise ValueError("Type at least one port, for example 58772.")
    ports = []
    for p in parts:
        if not p:
            continue
        port = parse_port(p, "Port")
        if port in ports:
            raise ValueError(f"Port {port} is listed twice.")
        ports.append(port)
    return ports


def rename_from_rows(rows):
    """[(serial, net, sta, loc)] -> {SERIAL: {net, sta, loc}} for the Gecko receiver.
    An empty name keeps what the Gecko sends; location '--' means an empty location code.
    ValueError (message, row index) for a bad row."""
    out = {}
    for i, (serial, net, sta, loc) in enumerate(rows):
        serial, net, sta, loc = (str(x or "").strip() for x in (serial, net, sta, loc))
        if not (serial or net or sta or loc):
            continue
        where = f"Row {i + 1} of the naming table"
        if not SERIAL_RE.match(serial):
            raise ValueError(f"{where}: '{serial}' is not a Gecko serial number (hexadecimal, e.g. 0000A1B2).", i)
        key = serial.upper().removeprefix("0X")
        if key in out:
            raise ValueError(f"{where}: serial number {key} is listed twice.", i)
        entry = {}
        for (label, limit), value, field in zip(NAME_LIMITS, (net, sta, loc), ("net", "sta", "loc")):
            if field == "loc" and value == "--":
                entry["loc"] = ""
                continue
            if not value:
                continue
            if not NAME_RE.match(value) or len(value) > limit:
                raise ValueError(f"{where}: {label.lower()} '{value}' must be 1-{limit} letters or digits.", i)
            entry[field] = value.upper()
        if not entry:
            raise ValueError(f"{where}: give a new network, station or location for {key}.", i)
        out[key] = entry
    return out


class SettingsDialog(QDialog):
    def __init__(self, engine, config, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.setWindowTitle("Settings")
        self.setWindowIcon(theme.icon("settings"))
        self.resize(640, 520)
        c = theme.COLORS
        self.tabs = QTabWidget()
        self.tabs.addTab(self._gecko_tab(), "Gecko receiver")
        self.tabs.addTab(self._server_tab(), "SeedLink server")
        self.tabs.addTab(self._recording_tab(), "Recording")
        self.tabs.addTab(self._display_tab(), "Display")
        self.tabs.addTab(self._program_tab(), "Program")
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {c['danger']}; font-weight: 600;")
        self.error.hide()
        # the message is about the last OK: once the user changes something it is out of date
        for edit in (self.gecko_ports, self.server_port, self.server_org, self.rec_folder):
            edit.textEdited.connect(self.error.hide)
        self.server_bind.editTextChanged.connect(self.error.hide)
        self.rename.itemChanged.connect(self.error.hide)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(self.error)
        lay.addWidget(box)

    # ------------------------------------------------------------------------------------
    # tabs
    # ------------------------------------------------------------------------------------
    @staticmethod
    def _hint(text):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet(f"color: {theme.COLORS['muted']};")
        return lab

    def _gecko_tab(self):
        g = self.config.get("gecko") or {}
        w = QWidget()
        self.gecko_enabled = QCheckBox("Receive Kelunji Gecko recorders")
        self.gecko_enabled.setChecked(bool(g.get("enabled", True)))
        self.gecko_ports = QLineEdit(", ".join(str(p) for p in g.get("ports") or [58772]))
        self.gecko_ports.setPlaceholderText("58772")
        self.rename = QTableWidget(0, len(RENAME_COLUMNS))
        self.rename.setHorizontalHeaderLabels(RENAME_COLUMNS)
        self.rename.verticalHeader().setVisible(False)
        self.rename.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rename.setSelectionBehavior(QAbstractItemView.SelectRows)
        for serial, names in sorted((g.get("rename") or {}).items()):
            loc = names.get("loc")
            self.add_rename_row(serial, names.get("net", ""), names.get("sta", ""),
                                "--" if loc == "" else (loc or ""))
        add = QPushButton(theme.icon("add"), "Add row")
        add.setAutoDefault(False)
        add.clicked.connect(lambda: self.add_rename_row())
        remove = QPushButton(theme.icon("remove"), "Remove row")
        remove.setAutoDefault(False)
        remove.clicked.connect(self._remove_rename_rows)
        brow = QHBoxLayout()
        brow.addStretch(1)
        brow.addWidget(add)
        brow.addWidget(remove)
        form = QFormLayout()
        form.addRow("", self.gecko_enabled)
        form.addRow("TCP ports", self.gecko_ports)
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self._hint("Kelunji Gecko recorders (through their USR-W610 Wi-Fi/serial adapter) connect by "
                                 "themselves to this PC on these ports. Several ports: separate them with commas."))
        lay.addSpacing(6)
        lay.addWidget(QLabel("<b>Naming table</b>: give a Gecko's data other SEED names"))
        lay.addWidget(self.rename, 1)
        lay.addLayout(brow)
        lay.addWidget(self._hint("An empty cell keeps the name the Gecko sends; location -- means an empty "
                                 "location code."))
        return w

    def add_rename_row(self, serial="", net="", sta="", loc=""):
        r = self.rename.rowCount()
        self.rename.insertRow(r)
        for col, text in enumerate((serial, net, sta, loc)):
            self.rename.setItem(r, col, QTableWidgetItem(str(text)))
        return r

    def _remove_rename_rows(self):
        for r in sorted({i.row() for i in self.rename.selectedIndexes()}, reverse=True):
            self.rename.removeRow(r)

    def rename_rows(self):
        rows = []
        for r in range(self.rename.rowCount()):
            rows.append(tuple((self.rename.item(r, c).text() if self.rename.item(r, c) else "")
                              for c in range(len(RENAME_COLUMNS))))
        return rows

    def _server_tab(self):
        s = self.config.get("seedlink_server") or {}
        w = QWidget()
        self.server_enabled = QCheckBox("Share all data with other programs over SeedLink")
        self.server_enabled.setChecked(bool(s.get("enabled", True)))
        self.server_bind = QComboBox()
        self.server_bind.setEditable(True)
        self.server_bind.addItem("0.0.0.0")
        self.server_bind.addItem("127.0.0.1")
        self.server_bind.setCurrentText(str(s.get("bind") or "0.0.0.0"))
        self.server_port = QLineEdit(str(s.get("port") or 18000))
        self.server_org = QLineEdit(str(s.get("organization") or "SeisZone"))
        form = QFormLayout()
        form.addRow("", self.server_enabled)
        form.addRow("Listen on", self.server_bind)
        form.addRow("Port", self.server_port)
        form.addRow("Organization", self.server_org)
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self._hint("0.0.0.0 = every network card of this PC (other computers can connect); "
                                 "127.0.0.1 = programs on this PC only. The organization is shown to SeedLink "
                                 "clients."))
        lay.addStretch(1)
        return w

    def _recording_tab(self):
        r = self.config.get("recording") or {}
        w = QWidget()
        self.rec_folder = QLineEdit(r.get("folder") or paths.recordings_dir())
        browse = QPushButton("Browse...")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.rec_folder, 1)
        row.addWidget(browse)
        self.rec_minutes = QComboBox()
        for m, text in FILE_MINUTES:
            self.rec_minutes.addItem(text, m)
        self.rec_minutes.setCurrentIndex(max(0, self.rec_minutes.findData(int(r.get("file_minutes") or 60))))
        self.rec_layout = QComboBox()
        for key, text in LAYOUTS:
            self.rec_layout.addItem(text, key)
        self.rec_layout.setCurrentIndex(max(0, self.rec_layout.findData(r.get("layout") or "channel")))
        form = QFormLayout()
        form.addRow("Default folder", row)
        form.addRow("File length", self.rec_minutes)
        form.addRow("Files", self.rec_layout)
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self._hint("These are the defaults for new recordings. Running recordings keep their "
                                 "own settings. SeisZone never deletes recordings."))
        lay.addStretch(1)
        return w

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Default folder for recordings", self.rec_folder.text())
        if d:
            self.rec_folder.setText(os.path.normpath(d))

    def _display_tab(self):
        w = QWidget()
        self.buffer = QSpinBox()
        self.buffer.setRange(*BUFFER_RANGE)
        self.buffer.setSuffix(" min")
        self.buffer.setSingleStep(10)
        try:
            self.buffer.setValue(int(self.config.get("buffer_minutes") or 60))
        except (TypeError, ValueError):
            self.buffer.setValue(60)
        self.theme_box = QComboBox()
        self.theme_box.addItem("Dark", "dark")
        self.theme_box.addItem("Light", "light")
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(self.config.get("ui", "theme", "dark"))))
        self.window_box = QComboBox()
        for seconds, text in WINDOWS:
            self.window_box.addItem(text, seconds)
        self.window_box.setCurrentIndex(max(0, self.window_box.findData(
            nearest_window(self.config.get("view", "window_seconds", 300)))))
        form = QFormLayout()
        form.addRow("Live data kept in memory", self.buffer)
        form.addRow("Colours", self.theme_box)
        form.addRow("Default time window", self.window_box)
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self._hint("Each channel keeps this much live data for the waveform view (10-720 min); "
                                 "more needs more memory: 12 channels at 200 sps for 12 h use about 1.5 GB."))
        lay.addStretch(1)
        return w

    def _program_tab(self):
        u = self.config.get("ui") or {}
        w = QWidget()
        self.tray = QCheckBox("Closing the window keeps SeisZone running in the tray")
        self.tray.setChecked(bool(u.get("minimize_to_tray", True)))
        self.start_min = QCheckBox("Start minimized in the tray")
        self.start_min.setChecked(bool(u.get("start_minimized", False)))
        self.autostart = QCheckBox("Start SeisZone when I log in to Windows")
        self.autostart.setChecked(bool(u.get("start_with_windows", False)))
        lay = QVBoxLayout(w)
        for cb in (self.tray, self.start_min, self.autostart):
            lay.addWidget(cb)
        lay.addWidget(self._hint("Data is received, served and recorded only while SeisZone runs: keep it in the "
                                 "tray and let it start with Windows on a station PC."))
        lay.addStretch(1)
        return w

    # ------------------------------------------------------------------------------------
    # checking and saving
    # ------------------------------------------------------------------------------------
    def collect(self):
        """{section: values} from the fields, or SettingsError."""
        try:
            gecko_ports = parse_ports(self.gecko_ports.text())
        except ValueError as e:
            raise SettingsError(TAB_GECKO, f"Gecko receiver: {e}", self.gecko_ports) from None
        try:
            rename = rename_from_rows(self.rename_rows())
        except ValueError as e:
            raise SettingsError(TAB_GECKO, e.args[0], self.rename) from None
        try:
            server_port = parse_port(self.server_port.text(), "The SeedLink server port")
        except ValueError as e:
            raise SettingsError(TAB_SERVER, str(e), self.server_port) from None
        if self.server_enabled.isChecked() and self.gecko_enabled.isChecked() and server_port in gecko_ports:
            raise SettingsError(TAB_SERVER, f"Port {server_port} cannot be used by both the SeedLink server and "
                                "the Gecko receiver.", self.server_port)
        bind = self.server_bind.currentText().strip() or "0.0.0.0"
        try:
            ipaddress.ip_address(bind)
        except ValueError:
            raise SettingsError(TAB_SERVER, f"'{bind}' is not an IP address of this PC (use 0.0.0.0 for all "
                                "network cards).", self.server_bind) from None
        folder = self.rec_folder.text().strip()
        if not folder:
            raise SettingsError(TAB_RECORDING, "Choose a default folder for recordings.", self.rec_folder)
        return {
            "gecko": {"enabled": self.gecko_enabled.isChecked(), "ports": gecko_ports, "rename": rename},
            "seedlink_server": {"enabled": self.server_enabled.isChecked(), "bind": bind, "port": server_port,
                                "organization": self.server_org.text().strip() or "SeisZone"},
            "recording": {"folder": os.path.normpath(folder), "file_minutes": self.rec_minutes.currentData(),
                          "layout": self.rec_layout.currentData()},
            "view": {"window_seconds": self.window_box.currentData()},
            "ui": {"theme": self.theme_box.currentData(), "minimize_to_tray": self.tray.isChecked(),
                   "start_minimized": self.start_min.isChecked(),
                   "start_with_windows": self.autostart.isChecked()},
            "buffer_minutes": self.buffer.value(),
        }

    def show_error(self, err):
        self.tabs.setCurrentIndex(err.tab)
        self.error.setText(str(err))
        self.error.show()
        if err.widget is not None:
            err.widget.setFocus()

    def save(self, values):
        old_theme = self.config.get("ui", "theme", "dark")
        with self.config.lock:
            for section, vals in values.items():
                if section == "buffer_minutes":   # a plain number at the top of config.json
                    self.config.data["buffer_minutes"] = int(vals)
                else:
                    self.config.update_section(section, vals, save=False)
            self.config.save()
        log.info("settings saved")
        mode = values["ui"]["theme"]
        app = QApplication.instance()
        if mode != old_theme and app is not None:
            theme.apply(app, mode)
        try:
            self.engine.apply_settings()
        except Exception:
            log.exception("applying the new settings failed")

    def accept(self):
        try:
            values = self.collect()
        except SettingsError as e:
            self.show_error(e)
            return
        self.error.hide()
        self.save(values)
        super().accept()
