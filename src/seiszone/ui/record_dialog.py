"""RecordDialog: watch, stop and start recordings (core.recorder.RecordingManager, plan.md 5.11).

Top: every recording with its state, channels, files, size, last write, folder and errors, with
Stop and Open folder. Bottom: "New recording" - name, folder, channels (all channels, also ones
that appear later, or a chosen list), file length and file layout, with the free disk space of
the folder. Recordings run until the user stops them and SeisZone never deletes their files;
low disk space only warns.
"""

import logging
import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from .. import paths
from . import theme

log = logging.getLogger("seiszone")

FILE_MINUTES = [(15, "15 minutes"), (30, "30 minutes"), (60, "60 minutes")]
LAYOUTS = [("channel", "One file per channel"), ("station", "One file per station (all its channels)")]
COLUMNS = ["Name", "State", "Started (UTC)", "Channels", "Files", "Size", "Last write", "Folder", "Errors"]
ID_ROLE = Qt.UserRole + 1


def fmt_utc(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t)) if t else ""


def human_bytes(n):
    from .main_window import human_bytes as hb  # one formatting for the whole program
    return hb(n)


def open_folder(folder):
    from .main_window import open_folder as of
    of(folder)


def channels_text(sids):
    if sids is None:
        return "all channels"
    if len(sids) <= 3:
        return ", ".join(sids)
    return f"{len(sids)} channels"


def disk_text(free, warning):
    return warning or f"Free space on this drive: {human_bytes(free)}"


class RecordDialog(QDialog):
    def __init__(self, engine, config, parent=None, sids=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.setWindowTitle("Recordings")
        self.setWindowIcon(theme.icon("record"))
        self.resize(980, 720)
        c = theme.COLORS

        # --- recordings --------------------------------------------------------------------
        self.table = QTreeWidget()
        self.table.setHeaderLabels(COLUMNS)
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.header().setSectionResizeMode(QHeaderView.Interactive)
        self.table.header().setStretchLastSection(True)
        for col, w in enumerate((150, 90, 140, 130, 50, 80, 110, 220)):
            self.table.setColumnWidth(col, w)
        self.table.currentItemChanged.connect(lambda *_: self._update_buttons())
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected_folder())
        self.empty = QLabel("No recordings yet. Start one below - it runs until you stop it, also across "
                            "restarts of SeisZone.")
        self.empty.setStyleSheet(f"color: {c['muted']};")
        self.stop_button = QPushButton(theme.icon("stop"), "Stop...")
        self.stop_button.clicked.connect(self.stop_selected)
        self.open_button = QPushButton("Open folder")
        self.open_button.clicked.connect(self.open_selected_folder)
        buttons = QHBoxLayout()
        buttons.addWidget(self.empty, 1)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.stop_button)
        box = QGroupBox("Recordings")
        bl = QVBoxLayout(box)
        bl.addWidget(self.table, 1)
        bl.addLayout(buttons)

        # --- new recording -----------------------------------------------------------------
        rec = config.get("recording") or {}
        self.name = QLineEdit(time.strftime("Recording %Y-%m-%d %H:%M", time.gmtime()))
        self.name.setPlaceholderText("A name for this recording")
        self.folder = QLineEdit(rec.get("folder") or paths.recordings_dir())
        self.folder.textChanged.connect(lambda *_: self._update_disk())
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        frow = QHBoxLayout()
        frow.addWidget(self.folder, 1)
        frow.addWidget(browse)

        self.all_radio = QRadioButton("All channels (also channels that appear later)")
        self.chosen_radio = QRadioButton("Only the channels ticked below")
        group = QButtonGroup(self)
        group.addButton(self.all_radio)
        group.addButton(self.chosen_radio)
        self.all_radio.setChecked(True)
        self.all_radio.toggled.connect(lambda *_: self._update_channel_list())
        self.channel_list = QListWidget()
        self.channel_list.setMinimumHeight(120)
        self.channel_list.setAlternatingRowColors(True)
        self.channel_list.setUniformItemSizes(True)
        pre = set(sids or [])
        for sid in sorted(set(engine.hub.stream_ids()) | pre):
            item = QListWidgetItem(sid)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if sid in pre else Qt.Unchecked)
            desc = engine.hub.descriptions.get(".".join(sid.split(".")[:2]), "")
            if desc:
                item.setText(f"{sid}    {desc}")
            item.setData(ID_ROLE, sid)
            self.channel_list.addItem(item)
        self.channel_list.itemChanged.connect(lambda *_: self._update_count())
        self.tick_all = QPushButton("All")
        self.tick_all.clicked.connect(lambda: self._tick(True))
        self.tick_none = QPushButton("None")
        self.tick_none.clicked.connect(lambda: self._tick(False))
        self.count = QLabel()
        self.count.setStyleSheet(f"color: {c['muted']};")
        crow = QHBoxLayout()
        crow.addWidget(self.chosen_radio)
        crow.addStretch(1)
        crow.addWidget(self.count)
        crow.addWidget(self.tick_all)
        crow.addWidget(self.tick_none)

        self.minutes = QComboBox()
        for m, text in FILE_MINUTES:
            self.minutes.addItem(text, m)
        self.minutes.setCurrentIndex(max(0, self.minutes.findData(int(rec.get("file_minutes") or 60))))
        self.layout_box = QComboBox()
        for key, text in LAYOUTS:
            self.layout_box.addItem(text, key)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(rec.get("layout") or "channel")))
        self.disk = QLabel()
        self.disk.setWordWrap(True)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.start_button = QPushButton(theme.icon("record"), "Start recording")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start_recording)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)

        new = QGroupBox("New recording")
        form = QFormLayout(new)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.addRow("Name", self.name)
        form.addRow("Folder", frow)
        form.addRow("", self.disk)
        form.addRow("Channels", self.all_radio)
        form.addRow("", crow)
        form.addRow("", self.channel_list)
        form.addRow("File length", self.minutes)
        form.addRow("Files", self.layout_box)
        hint = QLabel("Files are named NET.STA/YYYY-MM-DD/NET.STA.LOC.CHA__<start>.mseed, aligned to the clock "
                      "(UTC). SeisZone never deletes recordings.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {c['muted']};")
        form.addRow("", hint)

        bottom = QHBoxLayout()
        bottom.addWidget(self.message, 1)
        bottom.addWidget(close)
        bottom.addWidget(self.start_button)

        lay = QVBoxLayout(self)
        lay.addWidget(box, 2)
        lay.addWidget(new, 3)
        lay.addLayout(bottom)

        self._update_channel_list()
        self._update_disk()
        self.refresh()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    # ------------------------------------------------------------------------------------
    # recordings list
    # ------------------------------------------------------------------------------------
    def refresh(self):
        """Rebuild the list (a handful of rows, once a second), keeping the selection."""
        sessions = self.engine.recorder.sessions()
        current = self.selected_id()
        now = time.time()
        danger = QColor(theme.COLORS["danger"])
        self.table.clear()
        for s in sorted(sessions, key=lambda s: (s.get("state") != "recording", -(s.get("started") or 0))):
            last = s.get("last_write") or 0
            item = QTreeWidgetItem(self.table, [
                s.get("name", ""), s.get("state", ""), fmt_utc(s.get("started")), channels_text(s.get("sids")),
                str(s.get("files", 0)), human_bytes(s.get("bytes", 0)),
                f"{now - last:.0f} s ago" if last else "nothing yet", s.get("folder", ""),
                s.get("last_error") or ""])
            item.setData(0, ID_ROLE, s["id"])
            item.setIcon(1, theme.icon("record" if s.get("state") == "recording" else "stop"))
            item.setForeground(8, danger)
            tip = [f"{s.get('name', '')} ({s['id']})", f"Folder: {s.get('folder', '')}",
                   "Channels: " + ("all (also new ones)" if s.get("sids") is None else ", ".join(s["sids"])),
                   f"File length: {s.get('file_minutes')} min, "
                   + ("one file per channel" if s.get("layout") == "channel" else "one file per station")]
            if s.get("stopped"):
                tip.append(f"Stopped: {fmt_utc(s['stopped'])} UTC")
            item.setToolTip(0, "\n".join(tip))
            item.setToolTip(7, s.get("folder", ""))
        if current:
            self._select(current)
        self.empty.setVisible(not sessions)
        self._update_buttons()
        self._update_disk()

    def selected_id(self):
        item = self.table.currentItem()
        return item.data(0, ID_ROLE) if item is not None else None

    def _select(self, rid):
        for i in range(self.table.topLevelItemCount()):
            if self.table.topLevelItem(i).data(0, ID_ROLE) == rid:
                self.table.setCurrentItem(self.table.topLevelItem(i))
                return

    def _session(self, rid):
        return next((s for s in self.engine.recorder.sessions() if s["id"] == rid), None)

    def _update_buttons(self):
        s = self._session(self.selected_id()) if self.selected_id() else None
        self.stop_button.setEnabled(bool(s and s.get("state") == "recording"))
        self.open_button.setEnabled(bool(s))

    def stop_selected(self):
        s = self._session(self.selected_id())
        if not s or s.get("state") != "recording":
            return False
        ans = QMessageBox.question(
            self, "Stop recording",
            f"Stop the recording '{s.get('name')}'?\n\nThe files written so far stay in\n{s.get('folder')}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return False
        self.engine.recorder.stop(s["id"])
        self._say(f"Recording '{s.get('name')}' stopped.")
        self.refresh()
        return True

    def open_selected_folder(self):
        s = self._session(self.selected_id())
        if s:
            open_folder(s.get("folder", ""))

    # ------------------------------------------------------------------------------------
    # new recording
    # ------------------------------------------------------------------------------------
    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder for the recordings", self.folder.text())
        if d:
            self.folder.setText(os.path.normpath(d))

    def _tick(self, on):
        for i in range(self.channel_list.count()):
            self.channel_list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def ticked(self):
        return [self.channel_list.item(i).data(ID_ROLE) for i in range(self.channel_list.count())
                if self.channel_list.item(i).checkState() == Qt.Checked]

    def _update_channel_list(self):
        chosen = self.chosen_radio.isChecked()
        for w in (self.channel_list, self.tick_all, self.tick_none):
            w.setEnabled(chosen)
        self._update_count()

    def _update_count(self):
        n, total = len(self.ticked()), self.channel_list.count()
        self.count.setText(f"{n} of {total} ticked")

    def _update_disk(self):
        folder = self.folder.text().strip()
        if not folder:
            self.disk.setText("")
            return
        try:
            free, warning = self.engine.recorder.disk_status(folder)
        except OSError as e:
            free, warning = 0, f"The free space of {folder} cannot be read ({e})."
        self.disk.setText(disk_text(free, warning))
        self.disk.setStyleSheet(f"color: {theme.COLORS['danger' if warning else 'muted']};"
                                + (" font-weight: 600;" if warning else ""))

    def _say(self, text, error=False):
        self.message.setText(text)
        self.message.setStyleSheet(f"color: {theme.COLORS['danger' if error else 'ok']};")

    def start_recording(self):
        """Start a recording from the form; returns its id or None (the reason is shown)."""
        folder = self.folder.text().strip()
        if not folder:
            self._say("Choose a folder for the recording.", error=True)
            return None
        sids = None
        if self.chosen_radio.isChecked():
            sids = self.ticked()
            if not sids:
                self._say("Tick at least one channel, or choose \"All channels\".", error=True)
                return None
        name = self.name.text().strip()
        try:
            rid = self.engine.recorder.start(name, folder, sids=sids, file_minutes=self.minutes.currentData(),
                                             layout=self.layout_box.currentData())
        except (ValueError, OSError) as e:
            self._say(f"The recording could not start: {e}", error=True)
            return None
        self._say(f"Recording '{name or rid}' started: {channels_text(sids)} -> {folder}")
        self.name.setText(time.strftime("Recording %Y-%m-%d %H:%M", time.gmtime()))
        self.refresh()
        self._select(rid)
        return rid
