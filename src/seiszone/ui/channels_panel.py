"""ChannelsPanel: one row per channel the hub holds - rate, newest sample, latency, packets,
gaps, source - with a "Show" tick that decides which channels the waveform view plots.

The table is a model (ChannelModel) behind a sort/filter proxy, so sorting and the filter box
cost nothing extra. refresh() (every second) only updates the values in place; the model is
reset only when channels appear or disappear, and the selection is kept across that.

Status colour of a channel, from its latency (now - end of the newest sample):
  green  < 2x the expected packet interval, or < 30 s
  amber  < 5 minutes
  red    older
"""

import logging
import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton,
                               QTableView, QVBoxLayout, QWidget)

from . import theme

log = logging.getLogger("seiszone")

COLUMNS = ["Show", "Channel", "Description", "Rate (sps)", "Latest sample (UTC)", "Latency", "Packets", "Gaps",
           "Source"]
C_SHOW, C_SID, C_DESC, C_RATE, C_LAST, C_LAT, C_PACK, C_GAPS, C_SRC = range(len(COLUMNS))
NUMERIC = {C_RATE, C_LAT, C_PACK, C_GAPS}
SORT_ROLE = Qt.UserRole + 1
OK_SECONDS = 30.0
LATE_SECONDS = 300.0
LEVEL_TEXT = {"ok": "receiving", "late": "late", "old": "no recent data"}
LEVEL_COLOR = {"ok": "ok", "late": "warn", "old": "danger"}


def packet_interval(st):
    """Mean time a record of this channel covers (what one packet adds), in seconds."""
    n = st.get("packets") or 0
    if n < 2:
        return 0.0
    return max(0.0, min(3600.0, (st["last_end"] - st["first"]) / n))


def latency_level(latency, interval=0.0):
    """'ok' (green), 'late' (amber) or 'old' (red) for a latency in seconds."""
    ok = max(OK_SECONDS, 2.0 * interval)
    if latency < ok:
        return "ok"
    if latency < max(LATE_SECONDS, ok):
        return "late"
    return "old"


def fmt_latency(seconds):
    """'0.8 s', '4 min 12 s', '3 h 05 min', '2 d 04 h'."""
    s = float(seconds)
    if abs(s) < 100:
        return f"{s:.1f} s"
    s = int(round(s))
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60:02d} min"
    return f"{s // 86400} d {s % 86400 // 3600:02d} h"


def fmt_utc(t):
    if not t:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 10)}"


def _dot(color):
    pm = QPixmap(12, 12)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(2, 2, 8, 8)
    p.end()
    return QIcon(pm)


class ChannelModel(QAbstractTableModel):
    """Rows built from hub.stats_snapshot(); `shown` is the ordered list of ticked channels
    (it may name channels that have not arrived yet - they appear ticked when they do)."""

    toggled = Signal()      # the user ticked / unticked a row

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.shown = []
        self._dots = {}      # colour -> status dot icon (keyed by colour, so a theme change just works)

    # --- Qt model -------------------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return COLUMNS[section]
            if role == Qt.ToolTipRole and section == C_LAT:
                return "Time since the end of the newest sample received"
        return None

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == C_SHOW:
            f |= Qt.ItemIsUserCheckable
        return f

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            return r["text"][col]
        if role == SORT_ROLE:
            return r["sort"][col] if col != C_SHOW else int(r["sid"] in self.shown)
        if role == Qt.CheckStateRole and col == C_SHOW:
            return Qt.Checked if r["sid"] in self.shown else Qt.Unchecked
        if role == Qt.DecorationRole and col == C_SID:
            color = theme.COLORS[LEVEL_COLOR[r["level"]]]
            if color not in self._dots:
                self._dots[color] = _dot(color)
            return self._dots[color]
        if role == Qt.ForegroundRole and col == C_LAT:
            return QColor(theme.COLORS[LEVEL_COLOR[r["level"]]])
        if role == Qt.TextAlignmentRole and col in NUMERIC:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            return r["tip"]
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.CheckStateRole or index.column() != C_SHOW:
            return False
        sid = self.rows[index.row()]["sid"]
        on = Qt.CheckState(value) == Qt.Checked if not isinstance(value, bool) else value
        if on and sid not in self.shown:
            self.shown.append(sid)
        elif not on and sid in self.shown:
            self.shown.remove(sid)
        else:
            return True
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        self.toggled.emit()
        return True

    # --- ours -----------------------------------------------------------------------------
    def set_rows(self, rows):
        """New values; returns True when the model had to be reset (channels came or went)."""
        if [r["sid"] for r in rows] == [r["sid"] for r in self.rows]:
            self.rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, C_SID), self.index(len(rows) - 1, len(COLUMNS) - 1))
            return False
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()
        return True

    def set_shown(self, sids):
        self.shown = list(dict.fromkeys(sids))
        if self.rows:
            self.dataChanged.emit(self.index(0, C_SHOW), self.index(len(self.rows) - 1, C_SHOW),
                                  [Qt.CheckStateRole])


class ChannelsPanel(QWidget):
    shownChanged = Signal(list)

    def __init__(self, engine, config, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.model = ChannelModel(self)
        self.model.toggled.connect(self._emit_shown)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(SORT_ROLE)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setDynamicSortFilter(True)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter channels (code, site or source)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._filter_changed)
        self.count = QLabel()
        self.count.setStyleSheet(f"color: {theme.COLORS['muted']};")
        show_all = QPushButton("Show all")
        show_all.setToolTip("Tick every channel listed (only those matching the filter)")
        show_all.clicked.connect(self.show_listed)
        hide_all = QPushButton("Hide all")
        hide_all.setToolTip("Untick every channel listed (only those matching the filter)")
        hide_all.clicked.connect(self.hide_listed)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(C_SID, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._double_clicked)
        self._sized = False

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.filter, 1)
        top.addWidget(self.count)
        top.addWidget(show_all)
        top.addWidget(hide_all)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        self.refresh()

    # ------------------------------------------------------------------------------------
    # public contract
    # ------------------------------------------------------------------------------------
    def refresh(self):
        """Read the hub statistics and update the table (MainWindow calls this every second)."""
        now = time.time()
        stats = self.engine.hub.stats_snapshot()
        names = self._source_names()
        rows = [self._row(stats[sid], names, now) for sid in sorted(stats)]
        selected = self.selected()
        reset = self.model.set_rows(rows)
        if reset:
            self._select(selected)
        if rows and not self._sized:
            self._sized = True
            self.table.resizeColumnsToContents()
            self.table.setColumnWidth(C_SHOW, max(48, self.table.columnWidth(C_SHOW)))
        elif rows and reset:
            self._widen_columns()
        self._update_count()

    def _widen_columns(self):
        """Channels came: make room for longer codes / descriptions (a Gecko joining later), but
        never narrow a column the user has widened."""
        header = self.table.horizontalHeader()
        for col in range(len(COLUMNS) - 1):   # the last column stretches anyway
            need = max(self.table.sizeHintForColumn(col), header.sectionSizeHint(col))
            if need > self.table.columnWidth(col):
                self.table.setColumnWidth(col, need)

    def shown(self):
        return list(self.model.shown)

    def set_shown(self, sids):
        """Tick exactly these channels (does not emit shownChanged: the caller already knows)."""
        self.model.set_shown([s for s in sids if isinstance(s, str) and s])
        self._update_count()

    def selected(self):
        """Channels of the selected rows, in table order."""
        rows = sorted(self.table.selectionModel().selectedRows(), key=lambda i: i.row())
        return [self.model.rows[self.proxy.mapToSource(i).row()]["sid"] for i in rows]

    # ------------------------------------------------------------------------------------
    def _emit_shown(self):
        self._update_count()
        self.shownChanged.emit(list(self.model.shown))

    def _change_shown(self, sids):
        sids = list(dict.fromkeys(sids))
        if sids != self.model.shown:
            self.model.set_shown(sids)
            self._emit_shown()

    def _listed(self):
        """Channels of the rows visible through the filter, in table order."""
        return [self.model.rows[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]["sid"]
                for r in range(self.proxy.rowCount())]

    def show_listed(self):
        """'Show all': tick every row visible through the filter (appended in table order)."""
        self._change_shown(self.model.shown + [s for s in self._listed() if s not in self.model.shown])

    def hide_listed(self):
        """'Hide all': untick every row visible through the filter."""
        listed = set(self._listed())
        self._change_shown([s for s in self.model.shown if s not in listed])

    def _double_clicked(self, index):
        if index.isValid() and index.column() != C_SHOW:
            self._change_shown([self.model.rows[self.proxy.mapToSource(index).row()]["sid"]])

    def _context_menu(self, pos):
        sel = self.selected()
        if not sel:
            return
        m = QMenu(self)
        only, show, hide = m.addAction("Show only these"), m.addAction("Show"), m.addAction("Hide")
        m.addSeparator()
        copy = m.addAction("Copy channel codes")
        chosen = m.exec(self.table.viewport().mapToGlobal(pos))
        m.deleteLater()
        if chosen is only:
            self._change_shown(sel)
        elif chosen is show:
            self._change_shown(self.model.shown + [s for s in sel if s not in self.model.shown])
        elif chosen is hide:
            self._change_shown([s for s in self.model.shown if s not in sel])
        elif chosen is copy:
            QGuiApplication.clipboard().setText("\n".join(sel))

    def _filter_changed(self, text):
        self.proxy.setFilterFixedString(text.strip())
        self._update_count()

    def _select(self, sids):
        sel = self.table.selectionModel()
        sel.clearSelection()
        want = set(sids)
        for r in range(self.proxy.rowCount()):
            idx = self.proxy.index(r, 0)
            if self.model.rows[self.proxy.mapToSource(idx).row()]["sid"] in want:
                sel.select(idx, sel.SelectionFlag.Select | sel.SelectionFlag.Rows)

    def _update_count(self):
        total = len(self.model.rows)
        listed = self.proxy.rowCount()
        shown = len(self.model.shown)
        text = f"{total} channel{'s' if total != 1 else ''}, {shown} shown"
        if listed != total:
            text = f"{listed} of {text}"
        self.count.setText(text)

    def _source_names(self):
        names = {}
        for s in self.config.sources():
            names[s.get("id")] = f"{s.get('name') or s.get('host')} ({s.get('host')}:{s.get('port')})"
        return names

    def _row(self, st, names, now):
        sid = st["sid"]
        station = f"{st['net']}.{st['sta']}"
        desc = self.engine.hub.descriptions.get(station, "")
        src = st.get("source") or ""
        if src in names:
            src_text = names[src]
        elif src.startswith("gecko:"):
            src_text = f"Gecko {src[6:]}"
        else:
            src_text = src
        last = st.get("last_end") or 0.0
        latency = now - last if last else float("inf")
        interval = packet_interval(st)
        level = latency_level(latency, interval)
        rate = st.get("rate") or 0.0
        lat_text = fmt_latency(latency) if last else ""
        tip = [f"{sid}  {desc}".strip(), f"Status: {LEVEL_TEXT[level]}",
               f"Newest sample: {fmt_utc(last)} UTC", f"Latency: {lat_text}",
               f"Packets: {st.get('packets', 0)} (about {interval:.1f} s each)" if interval else
               f"Packets: {st.get('packets', 0)}",
               f"Gaps: {st.get('gaps', 0)} ({st.get('gap_seconds', 0.0):.1f} s missing)"]
        if st.get("last_gap"):
            g0, g1 = st["last_gap"]
            tip.append(f"Last gap: {fmt_utc(g0)} to {fmt_utc(g1)} UTC")
        if st.get("backfilled"):
            tip.append(f"Backfilled records: {st['backfilled']}")
        tip.append(f"Source: {src_text}")
        return {"sid": sid, "level": level, "tip": "\n".join(tip),
                "text": ["", sid, desc, f"{rate:g}", fmt_utc(last), lat_text, str(st.get("packets", 0)),
                         str(st.get("gaps", 0)), src_text],
                "sort": [0, sid, desc.lower(), rate, last, latency, st.get("packets", 0), st.get("gaps", 0),
                         src_text.lower()]}
