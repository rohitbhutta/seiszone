"""ExportDialog: write a time window of chosen channels to miniSEED (core.export, plan.md 5.12).

The data comes from the recording folders first and, for what they do not hold, from the live
buffer. The export runs in a QThread (reading hours of recordings takes a while); its progress
lines and then the report appear in the dialog.

The channel list holds the live channels, the shown ones, the channels of the recordings and
every channel the recording folders hold files of for the chosen window ("recorded only": a
station that is offline now can still be exported). The window is whole seconds: what the
fields show is exactly what is exported.
"""

import logging
import math
import os
import time

from PySide6.QtCore import QDateTime, Qt, QThread, QTimer, QTimeZone, Signal
from PySide6.QtWidgets import (QButtonGroup, QDateTimeEdit, QDialog, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QRadioButton, QVBoxLayout)

from .. import paths
from . import theme

log = logging.getLogger("seiszone")

ID_ROLE = Qt.UserRole + 1
MAX_DAYS = 31     # a longer window is almost surely a typing mistake (and a huge file)

# Every export thread is held here until it has ended, so closing the dialog during an export
# never destroys a running QThread (finished ones are dropped when the next export starts).
_RUNNING = set()


class ExportThread(QThread):
    line = Signal(str)
    done = Signal(list, str)
    failed = Signal(str)

    def __init__(self, export_fn, kwargs, parent=None):
        super().__init__(parent)
        self.export_fn = export_fn
        self.kwargs = kwargs

    def run(self):
        try:
            written, report = self.export_fn(log=self.line.emit, **self.kwargs)
        except Exception as e:  # bad window, unwritable folder, damaged file ...
            log.exception("export failed")
            self.failed.emit(str(e) or e.__class__.__name__)
        else:
            self.done.emit([str(p) for p in written], report or "")


def to_qdt(t):
    return QDateTime.fromMSecsSinceEpoch(int(round(t * 1000)), QTimeZone.utc())


def from_qdt(qdt):
    return qdt.toMSecsSinceEpoch() / 1000.0


class ExportDialog(QDialog):
    def __init__(self, engine, config, parent=None, sids=None, t0=None, t1=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.view_range = (t0, t1) if t0 is not None and t1 is not None and t1 > t0 else None
        self.written = []
        self.report = ""
        self._thread = None
        self.setWindowTitle("Export")
        self.setWindowIcon(theme.icon("export"))
        self.resize(820, 700)
        c = theme.COLORS

        # channels
        self.channel_list = QListWidget()
        self.channel_list.setAlternatingRowColors(True)
        self.channel_list.setUniformItemSizes(True)
        self._pre = pre = set(sids or [])
        self._live = set(engine.hub.stream_ids())
        recorded = {x for s in engine.recorder.sessions() for x in (s.get("sids") or [])}
        for sid in sorted(self._live | pre | recorded):
            self._add_channel(sid)
        self.channel_list.itemChanged.connect(lambda *_: self._update_count())
        self._scan_timer = QTimer(self)        # channels found in the recording folders
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(400)
        self._scan_timer.timeout.connect(self.add_recorded_channels)
        all_b = QPushButton("All")
        all_b.clicked.connect(lambda: self._tick(True))
        none_b = QPushButton("None")
        none_b.clicked.connect(lambda: self._tick(False))
        self.count = QLabel()
        self.count.setStyleSheet(f"color: {c['muted']};")
        crow = QHBoxLayout()
        crow.addWidget(self.count, 1)
        crow.addWidget(all_b)
        crow.addWidget(none_b)
        chbox = QGroupBox("Channels")
        cl = QVBoxLayout(chbox)
        cl.addWidget(self.channel_list, 1)
        cl.addLayout(crow)

        # window
        self.start = self._dt_edit()
        self.end = self._dt_edit()
        for e in (self.start, self.end):
            e.dateTimeChanged.connect(lambda *_: self._update_length())
            e.dateTimeChanged.connect(lambda *_: self._scan_timer.start())
        self.length = QLabel()
        self.length.setStyleSheet(f"color: {c['muted']};")
        quick = QHBoxLayout()
        for text, seconds in (("Last 10 min", 600), ("Last 1 h", 3600)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, s=seconds: self.set_last(s))
            quick.addWidget(b)
        self.view_button = QPushButton("The view's range")
        self.view_button.setAutoDefault(False)
        self.view_button.setEnabled(self.view_range is not None)
        self.view_button.setToolTip("The time range visible in the viewer this export was opened from")
        self.view_button.clicked.connect(lambda: self.set_window(*self.view_range))
        quick.addWidget(self.view_button)
        quick.addStretch(1)

        self.outdir = QLineEdit(paths.exports_dir())
        browse = QPushButton("Browse...")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        orow = QHBoxLayout()
        orow.addWidget(self.outdir, 1)
        orow.addWidget(browse)
        self.separate = QRadioButton("One file per channel")
        self.combined = QRadioButton("One file with all channels")
        group = QButtonGroup(self)
        group.addButton(self.separate)
        group.addButton(self.combined)
        self.separate.setChecked(True)
        mrow = QHBoxLayout()
        mrow.addWidget(self.separate)
        mrow.addWidget(self.combined)
        mrow.addStretch(1)

        wbox = QGroupBox("Time window and output")
        form = QFormLayout(wbox)
        form.addRow("Start (UTC)", self.start)
        form.addRow("End (UTC)", self.end)
        form.addRow("", self.length)
        form.addRow("Quick", quick)
        form.addRow("Folder", orow)
        form.addRow("Files", mrow)

        # progress / report
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("Progress and the export report appear here.")
        self.text.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 8.5pt;")
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.open_button = QPushButton("Open folder")
        self.open_button.setAutoDefault(False)
        self.open_button.clicked.connect(self.open_output)
        self.export_button = QPushButton(theme.icon("export"), "Export")
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self.export)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.message, 1)
        bottom.addWidget(self.open_button)
        bottom.addWidget(close)
        bottom.addWidget(self.export_button)

        top = QHBoxLayout()
        top.addWidget(chbox, 2)
        top.addWidget(wbox, 3)
        lay = QVBoxLayout(self)
        lay.addLayout(top, 3)
        lay.addWidget(self.text, 2)
        lay.addLayout(bottom)

        if self.view_range:
            self.set_window(*self.view_range)
        else:
            self.set_last(600)
        self._scan_timer.stop()
        self.add_recorded_channels()
        self._update_count()

    # ------------------------------------------------------------------------------------
    def _dt_edit(self):
        e = QDateTimeEdit()
        e.setTimeZone(QTimeZone.utc())
        e.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        e.setCalendarPopup(True)
        return e

    def _add_channel(self, sid):
        desc = self.engine.hub.descriptions.get(".".join(sid.split(".")[:2]), "")
        extra = "" if sid in self._live or sid in self._pre else "    (recorded only)"
        item = QListWidgetItem((f"{sid}    {desc}" if desc else sid) + extra)
        item.setData(ID_ROLE, sid)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if sid in self._pre else Qt.Unchecked)
        self.channel_list.addItem(item)

    def add_recorded_channels(self):
        """Add the channels the recording folders hold for the chosen window (sorted in)."""
        try:
            from ..core.export import recorded_sids
            t0, t1 = self.window()
            found = recorded_sids(self.recording_folders(), t0, t1) if t1 > t0 else []
        except Exception:   # an unreadable folder must never break the dialog
            log.exception("listing the recorded channels failed")
            return
        have = {self.channel_list.item(i).data(ID_ROLE) for i in range(self.channel_list.count())}
        new = [sid for sid in found if sid not in have]
        if not new:
            return
        for sid in new:
            self._add_channel(sid)
        self.channel_list.sortItems()
        self._update_count()

    def set_window(self, t0, t1):
        """Whole seconds (start rounded down, end up, so the given range stays covered): the
        fields show no milliseconds, so none may hide in them."""
        self.start.setDateTime(to_qdt(math.floor(t0)))
        self.end.setDateTime(to_qdt(math.ceil(t1)))

    def set_last(self, seconds):
        now = float(int(time.time()))
        self.set_window(now - seconds, now)

    def window(self):
        return (float(math.floor(from_qdt(self.start.dateTime()))),
                float(math.floor(from_qdt(self.end.dateTime()))))

    def _update_length(self):
        t0, t1 = self.window()
        d = t1 - t0
        if d <= 0:
            self.length.setText("The end must be after the start.")
        else:
            h, rest = divmod(int(d), 3600)
            m, s = divmod(rest, 60)
            self.length.setText("Length: " + " ".join(x for x in (f"{h} h" if h else "", f"{m} min" if m else "",
                                                                  f"{s} s" if s else "") if x))

    def _tick(self, on):
        for i in range(self.channel_list.count()):
            self.channel_list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def ticked(self):
        return [self.channel_list.item(i).data(ID_ROLE) for i in range(self.channel_list.count())
                if self.channel_list.item(i).checkState() == Qt.Checked]

    def _update_count(self):
        self.count.setText(f"{len(self.ticked())} of {self.channel_list.count()} ticked")

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder for the exported files", self.outdir.text())
        if d:
            self.outdir.setText(os.path.normpath(d))

    def _say(self, text, level="muted"):
        self.message.setText(text)
        self.message.setStyleSheet(f"color: {theme.COLORS[level]};")

    def recording_folders(self):
        """Where recorded files may be: every recording's folder plus the default folder."""
        folders = [s.get("folder") for s in self.engine.recorder.sessions()]
        folders.append(self.config.get("recording", "folder") or paths.recordings_dir())
        return list(dict.fromkeys(f for f in folders if f))

    # ------------------------------------------------------------------------------------
    def export(self):
        if self._thread is not None:
            return
        sids = self.ticked()
        t0, t1 = self.window()
        outdir = self.outdir.text().strip()
        if not sids:
            self._say("Tick at least one channel to export.", "danger")
            return
        if t1 <= t0:
            self._say("The end of the window must be after its start.", "danger")
            return
        if t1 - t0 > MAX_DAYS * 86400:
            self._say(f"The window is longer than {MAX_DAYS} days - export it in parts.", "danger")
            return
        if not outdir:
            self._say("Choose a folder for the exported files.", "danger")
            return
        try:
            from ..core.export import export_window
        except ImportError as e:
            self._say(f"Export is not available in this build ({e}).", "danger")
            return
        kwargs = {"sids": sids, "t0": t0, "t1": t1, "outdir": outdir,
                  "mode": "combined" if self.combined.isChecked() else "separate",
                  "folders": self.recording_folders(), "hub": self.engine.hub}
        self.text.clear()
        self.text.appendPlainText(f"Exporting {len(sids)} channel(s) ...")
        self._say("Exporting ...")
        self.export_button.setEnabled(False)
        t = ExportThread(export_window, kwargs)
        t.line.connect(self.text.appendPlainText)
        t.done.connect(self._done)
        t.failed.connect(self._failed)
        t.finished.connect(self._finished)
        self._thread = t
        for old in [x for x in _RUNNING if x.isFinished()]:
            _RUNNING.discard(old)
        _RUNNING.add(t)
        log.info("export of %d channel(s) to %s", len(sids), outdir)
        t.start()

    def _done(self, written, report):
        self.written, self.report = written, report
        self.text.appendPlainText("")
        self.text.appendPlainText(report)
        files = [p for p in written if not os.path.basename(p).startswith("export_info")]
        self._say(f"Done: {len(files)} miniSEED file(s) written to {self.outdir.text().strip()}", "ok")

    def _failed(self, message):
        self.text.appendPlainText(f"Export failed: {message}")
        self._say(f"Export failed: {message}", "danger")

    def _finished(self):
        if self._thread is not None and self._thread.isFinished():
            _RUNNING.discard(self._thread)
            self._thread = None
        self.export_button.setEnabled(True)

    def is_running(self):
        return self._thread is not None

    def open_output(self):
        from .main_window import open_folder
        open_folder(self.outdir.text().strip() or paths.exports_dir())

    def done(self, result):
        """Closing during an export: the thread finishes the files on its own (it is kept
        alive in _RUNNING); only its messages are no longer shown."""
        t = self._thread
        if t is not None:
            for sig in (t.line, t.done, t.failed, t.finished):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            self._thread = None
        super().done(result)
