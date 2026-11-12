"""LogPanel: the program log inside the main window.

Lines come from logsetup's in-memory list (the same lines that go to the rotating log file), so
the panel never reads files and can be refreshed every second at no cost. Only lines added
since the last refresh are appended; the filter rebuilds the view from the panel's own copy.
"""

import collections
import logging
import os
import time

from PySide6.QtCore import QEvent, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .. import logsetup, paths
from . import theme

log = logging.getLogger("seiszone")

MAX_LINES = 5000


def _level_color(level):
    if level in ("ERROR", "CRITICAL"):
        return theme.COLORS["danger"]
    if level == "WARNING":
        return theme.COLORS["warn"]
    if level == "DEBUG":
        return theme.COLORS["muted"]
    return theme.COLORS["text"]


def format_line(created, level, msg):
    """'2026-11-07 17:25:01  WARNING  message' (local time, like the log file)."""
    return f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(created))}  {level:<7}  {msg}"


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._seen = 0                       # logsetup counter already shown
        self._lines = collections.deque(maxlen=MAX_LINES)  # (level, text) for re-filtering
        self._colors = dict(theme.COLORS)    # the theme the lines were coloured with

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter log (text or level, e.g. WARNING)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._rebuild)
        self.count = QLabel()
        self.count.setStyleSheet(f"color: {theme.COLORS['muted']};")
        open_btn = QPushButton("Open log folder")
        open_btn.setToolTip(paths.log_dir())
        open_btn.clicked.connect(self.open_folder)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(MAX_LINES)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.text.setFont(mono)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.filter, 1)
        top.addWidget(self.count)
        top.addWidget(open_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.text, 1)
        self.refresh()

    # ------------------------------------------------------------------------------------
    def _matches(self, level, text):
        f = self.filter.text().strip().lower()
        return not f or f in text.lower()

    def _append(self, rows):
        """Append (level, text) rows at the end; keeps the view at the bottom only if it was
        there before, so the user can scroll back and read while new lines arrive."""
        if not rows:
            return
        bar = self.text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        cur = QTextCursor(self.text.document())
        cur.movePosition(QTextCursor.End)
        cur.beginEditBlock()
        fmt = QTextCharFormat()
        empty = self.text.document().isEmpty()
        for level, text in rows:
            if not empty:
                cur.insertBlock()
            empty = False
            fmt.setForeground(QColor(_level_color(level)))
            cur.insertText(text, fmt)
        cur.endEditBlock()
        if at_bottom:
            bar.setValue(bar.maximum())

    def _rebuild(self):
        self.text.clear()
        self._append([r for r in self._lines if self._matches(*r)])
        self._update_count()

    def _update_count(self):
        self.count.setText(f"{len(self._lines)} lines")

    # ------------------------------------------------------------------------------------
    def refresh(self):
        """Append the log lines written since the last call (MainWindow calls this every second)."""
        new, self._seen = logsetup.lines_since(self._seen)
        if not new:
            return
        rows = [(level, format_line(created, level, msg)) for created, level, msg in new]
        self._lines.extend(rows)
        self._append([r for r in rows if self._matches(*r)])
        self._update_count()

    def changeEvent(self, ev):
        """Light / dark switched: every line is coloured again (the colours are part of each
        line, so the old lines kept the other theme's colour - pale grey on white)."""
        super().changeEvent(ev)
        if (ev.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange)
                and getattr(self, "_colors", None) is not None and theme.COLORS != self._colors):
            self._colors = dict(theme.COLORS)
            self.count.setStyleSheet(f"color: {theme.COLORS['muted']};")
            self._rebuild()

    def lines(self):
        """Texts currently held (all levels, filter not applied)."""
        return [t for _, t in self._lines]

    def open_folder(self):
        folder = paths.ensure(paths.log_dir())
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(folder))):
            log.warning("could not open the log folder %s", folder)
