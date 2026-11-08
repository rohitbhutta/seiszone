"""SourcesPanel: every data source SeisZone has, in one tree.

  * one row per configured source (instrument or SeedLink server SeisZone connects to): name,
    address, kind, state with a colour dot, packets, age of the last packet; child rows per
    station with the packets the hub holds for it
  * "Gecko receiver :58772": the Kelunji Gecko recorders that connected IN to this PC
  * "SeedLink server :18010": the programs reading data FROM SeisZone

refresh() (every second) only updates texts in place - rows are added or removed only when
sources, stations or links come and go - so the selection, the expanded rows and the scroll
position survive. All changes go through the engine (plan.md 5.14).
"""

import logging
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
                               QMenu, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .channels_panel import fmt_latency

log = logging.getLogger("seiszone")

COLUMNS = ["Name", "Address", "Kind", "State", "Packets", "Last packet"]
C_NAME, C_ADDR, C_KIND, C_STATE, C_PACK, C_LAST = range(len(COLUMNS))
KIND_TEXT = {"seedlink": "SeedLink", "gcf-udp": "GCF UDP", "gcf-tcp": "GCF TCP"}
KEY_ROLE = Qt.UserRole + 1
GECKO_KEY = ("gecko",)
SERVER_KEY = ("server",)
ROW_ICONS = {"source": "source", "gecko": "gecko", "server": "server", "station": "channel"}


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


def fmt_age(t, now):
    """'1.2 s ago' / '4 min 12 s ago' for an epoch time, '' when there was none."""
    if not t:
        return ""
    return fmt_latency(max(0.0, now - t)) + " ago"


def source_kind_text(src):
    return KIND_TEXT.get(src.get("kind"), src.get("kind", ""))


def source_stations(src):
    """[(net, sta, desc)] of a source dict (SeedLink stations or the GCF unit's station)."""
    if src.get("kind") == "seedlink":
        return [(s.get("net", ""), s.get("sta", ""), s.get("desc", "")) for s in src.get("stations") or []]
    g = src.get("gcf") or {}
    return [(g.get("net", ""), g.get("sta", ""), g.get("desc", ""))] if g else []


class PropertiesDialog(QDialog):
    """Read-only key / value view of a source (its settings, what the probe found, link status)."""

    def __init__(self, title, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 520)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Property", "Value"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        for key, value in data.items():
            self._add(self.tree.invisibleRootItem(), key, value)
        self.tree.expandToDepth(0)
        self.tree.resizeColumnToContents(0)
        self.tree.setColumnWidth(0, min(240, self.tree.columnWidth(0) + 20))
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tree, 1)
        lay.addWidget(box)

    def _add(self, parent, key, value):
        item = QTreeWidgetItem(parent, [str(key), ""])
        if isinstance(value, dict):
            for k, v in value.items():
                self._add(item, k, v)
        elif isinstance(value, (list, tuple)) and any(isinstance(v, (dict, list, tuple)) for v in value):
            for i, v in enumerate(value):
                self._add(item, i, v)
        else:
            text = ", ".join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)
            item.setText(1, text)
            item.setToolTip(1, text)


class SourcesPanel(QWidget):
    sourceSelected = Signal(str)        # id of the selected source ("" when none)
    channelsRequested = Signal(list)    # SEED ids to show in the waveform view

    def __init__(self, engine, config, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self._dots = {}
        self._dialogs = []              # open non-blocking dialogs (kept alive until closed)
        self._last_selected = None

        add = QPushButton(theme.icon("add"), "Add source")
        add.setToolTip("Add an instrument or SeedLink server by its IP address and port")
        add.clicked.connect(lambda: self.add_source())
        self.summary = QLabel()
        self.summary.setStyleSheet(f"color: {theme.COLORS['muted']};")

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(lambda item, _col: self._request_channels(item))
        self.tree.currentItemChanged.connect(lambda *_: self._selection_changed())
        head = self.tree.header()
        head.setStretchLastSection(True)
        head.setSectionResizeMode(QHeaderView.Interactive)
        # what matters most (is it receiving?) first, so it is visible in a narrow dock; the
        # logical column numbers stay, only the order on screen changes
        for pos, col in enumerate((C_NAME, C_STATE, C_PACK, C_LAST, C_ADDR, C_KIND)):
            head.moveSection(head.visualIndex(col), pos)
        for col, width in ((C_NAME, 188), (C_STATE, 86), (C_PACK, 56), (C_LAST, 78), (C_ADDR, 120)):
            self.tree.setColumnWidth(col, width)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(add)
        top.addStretch(1)
        top.addWidget(self.summary)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.tree, 1)
        self.refresh()

    # ------------------------------------------------------------------------------------
    # public contract
    # ------------------------------------------------------------------------------------
    def refresh(self):
        """Update the tree from config.sources(), engine.status() and the hub statistics."""
        now = time.time()
        status = self.engine.status() or {}
        stats = self.engine.hub.stats_snapshot()
        rows = [self._source_row(src, status.get("sources") or {}, stats, now) for src in self.config.sources()]
        rows.append(self._gecko_row(status.get("gecko") or {}, stats, now))
        rows.append(self._server_row(status.get("server") or {}, now))
        self._sync(self.tree.invisibleRootItem(), rows)
        n = len(rows) - 2
        receiving = sum(1 for r in rows[:n] if str(r["texts"][C_STATE]).startswith("receiving"))
        self.summary.setText(f"{receiving} of {n} source{'s' if n != 1 else ''} receiving")

    def selected_source_id(self):
        """Id of the selected configured source (also when one of its station rows is selected)."""
        key = self._key(self.tree.currentItem())
        return key[1] if key and key[0] in ("source", "station") else ""

    def add_source(self, initial_text=""):
        """Open the Add source dialog (non-blocking: live data keeps flowing behind it)."""
        from .add_source_dialog import AddSourceDialog
        dlg = AddSourceDialog(self.engine, self.config, self, initial_text=initial_text)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        self._dialogs.append(dlg)

        def finished(result, d=dlg):
            if d in self._dialogs:
                self._dialogs.remove(d)
            if result == QDialog.Accepted and d.added_source:
                self.refresh()
                self._select_source(d.added_source["id"])
        dlg.finished.connect(finished)
        dlg.open()
        return dlg

    def remove_selected(self):
        sid = self.selected_source_id()
        src = self.config.source(sid) if sid else None
        if not src:
            return False
        ans = QMessageBox.question(
            self, "Remove source",
            f"Remove {self._label(src)}?\n\nSeisZone stops reading from it. Recordings already "
            "written stay on disk.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return False
        self.engine.remove_source(sid)
        log.info("source removed: %s", self._label(src))
        self.refresh()
        return True

    def reconnect_selected(self):
        sid = self.selected_source_id()
        if sid:
            self.engine.reconnect(sid)
        return bool(sid)

    def set_enabled_selected(self, enabled):
        sid = self.selected_source_id()
        if sid:
            self.engine.set_enabled(sid, bool(enabled))
            self.refresh()
        return bool(sid)

    # ------------------------------------------------------------------------------------
    # rows
    # ------------------------------------------------------------------------------------
    def _label(self, src):
        return f"'{src.get('name') or src.get('host')}' ({src.get('host')}:{src.get('port')})"

    def _source_row(self, src, link_status, stats, now):
        st = link_status.get(src["id"]) or {}
        enabled = src.get("enabled", True)
        state = st.get("state") or ("stopped" if not enabled else "waiting")
        if not enabled:
            state = "disabled"
        children = []
        for net, sta, desc in source_stations(src):
            mine = [s for s in stats.values() if s.get("source") == src["id"] and s["net"] == net and s["sta"] == sta]
            last = max((s.get("last_arrival") or 0 for s in mine), default=0)
            children.append({"key": ("station", src["id"], f"{net}.{sta}"),
                             "texts": [f"{net}.{sta}", desc or self.engine.hub.descriptions.get(f"{net}.{sta}", ""),
                                       f"{len(mine)} ch", "", str(sum(s.get("packets", 0) for s in mine)),
                                       fmt_age(last, now)],
                             "tip": f"{net}.{sta} {desc}".strip() + f"\n{len(mine)} channel(s) received"})
        tip = [self._label(src), f"Kind: {source_kind_text(src)}", f"State: {state}"]
        if st.get("detail"):
            tip.append(st["detail"])
        if st.get("since"):
            tip.append(f"Connected since {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(st['since']))} UTC")
        if st.get("duplicates"):
            tip.append(f"Duplicate packets dropped: {st['duplicates']}")
        state_text = f"{state} - {st['detail']}" if enabled and st.get("detail") else state
        return {"key": ("source", src["id"]), "state": state, "tip": "\n".join(tip),
                "texts": [src.get("name") or src.get("host", ""), f"{src.get('host')}:{src.get('port')}",
                          source_kind_text(src), state_text, str(st.get("packets", 0)),
                          fmt_age(st.get("last_packet"), now)],
                "children": children}

    def _gecko_row(self, g, stats, now):
        ports = ",".join(str(p) for p in g.get("ports") or []) or "-"
        links = g.get("links") or []
        if g.get("error"):
            state = f"error - {g['error']}"
        elif g.get("listening"):
            state = "listening"
        else:
            state = "off"
        children = []
        for link in links:
            serial = link.get("serial") or "?"
            mine = [s for s in stats.values() if s.get("source") == f"gecko:{serial}"]
            last = max((s.get("last_arrival") or 0 for s in mine), default=0)
            children.append({"key": ("gecko-link", serial, link.get("peer", "")), "state": "receiving",
                             "texts": [f"{serial}  {link.get('station', '')}", link.get("peer", ""),
                                       "Kelunji Gecko", "receiving", str(link.get("records", 0)),
                                       fmt_age(last, now)],
                             "tip": "\n".join(x for x in (
                                 f"Gecko {serial} - {link.get('sitename', '')}".rstrip(" -"),
                                 f"Station {link.get('station', '')}", f"From {link.get('peer', '')}",
                                 f"Connected {fmt_age(link.get('since'), now)}" if link.get("since") else "") if x)})
        tip = (f"Kelunji Gecko recorders connect by themselves to this PC on TCP port {ports}.\n"
               f"Connected now: {len(links)}")
        return {"key": GECKO_KEY, "state": state, "fixed": True, "tip": tip,
                "texts": [f"Gecko receiver :{ports}", "this PC", "Gecko in", state,
                          str(sum(x.get("records", 0) for x in links)), f"{len(links)} linked"],
                "children": children}

    def _server_row(self, s, now):
        if s.get("error"):
            state = f"error - {s['error']}"
        elif s.get("listening"):
            state = "listening"
        else:
            state = "off"
        peers = s.get("peers") or []
        children = [{"key": ("server-peer", p.get("peer", str(i))), "state": "receiving",
                     "texts": [str(p.get("peer", "")), str(p.get("stations", "")), "client", "connected",
                               str(p.get("packets", 0)), fmt_age(p.get("since"), now).replace(" ago", "")],
                     "tip": f"{p.get('peer', '')} reads {p.get('stations', '')}"}
                    for i, p in enumerate(peers)]
        k = int(s.get("clients") or len(peers))
        return {"key": SERVER_KEY, "state": state, "fixed": True,
                "tip": f"Other programs read every channel from SeisZone here (SeedLink, port {s.get('port')}).",
                "texts": [f"SeedLink server :{s.get('port', '-')}", s.get("bind", "this PC") or "this PC",
                          "SeedLink out", state, str(sum(p.get("packets", 0) for p in peers)),
                          f"{k} client{'s' if k != 1 else ''}"],
                "children": children}

    # ------------------------------------------------------------------------------------
    # tree upkeep
    # ------------------------------------------------------------------------------------
    @staticmethod
    def _key(item):
        return item.data(0, KEY_ROLE) if item is not None else None

    def _icon(self, state):
        color = theme.state_color(state)
        if color not in self._dots:
            self._dots[color] = _dot(color)
        return self._dots[color]

    def _sync(self, parent, rows):
        """Make the children of `parent` exactly `rows` (in order), re-using items by key."""
        existing = {self._key(parent.child(i)): parent.child(i) for i in range(parent.childCount())}
        wanted = [r["key"] for r in rows]
        for key, item in existing.items():
            if key not in wanted:
                parent.removeChild(item)
        for pos, row in enumerate(rows):
            item = existing.get(row["key"])
            new = item is None
            if new:
                item = QTreeWidgetItem()
                item.setData(0, KEY_ROLE, row["key"])
                item.setTextAlignment(C_PACK, Qt.AlignRight | Qt.AlignVCenter)
                if row.get("fixed"):
                    f = QFont(item.font(0))
                    f.setBold(True)
                    item.setFont(0, f)
                parent.insertChild(pos, item)
            elif parent.indexOfChild(item) != pos:
                expanded = item.isExpanded()
                parent.takeChild(parent.indexOfChild(item))
                parent.insertChild(pos, item)
                item.setExpanded(expanded)
            for col, text in enumerate(row["texts"]):
                if item.text(col) != text:
                    item.setText(col, text)
            item.setToolTip(C_NAME, row.get("tip", ""))
            item.setToolTip(C_STATE, row["texts"][C_STATE])
            if "state" in row:
                item.setIcon(C_STATE, self._icon(row["state"]))
            icon = ROW_ICONS.get(row["key"][0])
            if icon:
                item.setIcon(C_NAME, theme.icon(icon))
            self._sync(item, row.get("children", []))
            if new and parent is self.tree.invisibleRootItem():
                item.setExpanded(True)  # top-level rows start open; the user's choice is kept after

    def _select_source(self, sid):
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            if self._key(root.child(i)) == ("source", sid):
                self.tree.setCurrentItem(root.child(i))
                return

    def _selection_changed(self):
        sid = self.selected_source_id()
        if sid != self._last_selected:
            self._last_selected = sid
            self.sourceSelected.emit(sid)

    # ------------------------------------------------------------------------------------
    # context menu and actions
    # ------------------------------------------------------------------------------------
    def channels_of(self, item):
        """SEED ids the hub holds for a row: a source, one of its stations, a Gecko link or the
        whole Gecko receiver."""
        key = self._key(item)
        if not key:
            return []
        stats = self.engine.hub.stats_snapshot()
        if key[0] == "source":
            match = [s for s in stats.values() if s.get("source") == key[1]]
        elif key[0] == "station":
            match = [s for s in stats.values()
                     if s.get("source") == key[1] and f"{s['net']}.{s['sta']}" == key[2]]
        elif key[0] == "gecko-link":
            match = [s for s in stats.values() if s.get("source") == f"gecko:{key[1]}"]
        elif key == GECKO_KEY:
            match = [s for s in stats.values() if str(s.get("source", "")).startswith("gecko:")]
        else:
            match = []
        return sorted(s["sid"] for s in match)

    def _request_channels(self, item):
        sids = self.channels_of(item)
        if sids:
            self.channelsRequested.emit(sids)

    def build_menu(self, item):
        """The context menu for a row (separate from showing it, so it can be tested)."""
        m = QMenu(self)
        key = self._key(item)
        sids = self.channels_of(item)
        act = m.addAction(theme.icon("channel"), f"Show channels ({len(sids)})", lambda: self._request_channels(item))
        act.setEnabled(bool(sids))
        if key and key[0] in ("source", "station"):
            self.tree.setCurrentItem(item)
            src = self.config.source(key[1]) or {}
            enabled = src.get("enabled", True)
            m.addSeparator()
            m.addAction(theme.icon("reconnect"), "Reconnect", self.reconnect_selected).setEnabled(enabled)
            m.addAction("Disable" if enabled else "Enable", lambda: self.set_enabled_selected(not enabled))
            m.addAction(theme.icon("remove"), "Remove...", self.remove_selected)
            m.addSeparator()
            m.addAction("Properties...", lambda: self.show_properties(key[1]))
        elif key in (GECKO_KEY, SERVER_KEY) or (key and key[0] in ("gecko-link", "server-peer")):
            m.addSeparator()
            m.addAction("Properties...", lambda: self.show_properties(None, key))
        return m

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            m = QMenu(self)
            m.addAction(theme.icon("add"), "Add source...", lambda: self.add_source())
        else:
            m = self.build_menu(item)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def properties(self, sid=None, key=None):
        """The details shown by Properties: the source's settings and what the probe found,
        plus the live link status; or the receiver / server status for the fixed rows."""
        status = self.engine.status() or {}
        if sid:
            src = self.config.source(sid) or {}
            data = {k: v for k, v in src.items() if k != "details"}
            data["details"] = src.get("details") or {}
            data["link"] = (status.get("sources") or {}).get(sid, {})
            return f"Source {src.get('name') or src.get('host', '')}", data
        if key and key[0] in ("gecko", "gecko-link"):
            return "Gecko receiver", status.get("gecko") or {}
        return "SeedLink server", status.get("server") or {}

    def show_properties(self, sid=None, key=None):
        title, data = self.properties(sid, key)
        dlg = PropertiesDialog(title, data, self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.open()
        return dlg
