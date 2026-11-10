"""SourcesPanel: every data source SeisZone has, in one tree.

  * one row per configured source (instrument or SeedLink server SeisZone connects to): name,
    address, kind, state with a colour dot, packets, age of the last packet; child rows per
    station with the packets the hub holds for it
  * "Gecko receiver :58772": the Kelunji Gecko recorders that connected IN to this PC
  * "SeedLink server :18000": the programs reading data FROM SeisZone

refresh() (every second) only updates texts in place - rows are added or removed only when
sources, stations or links come and go - so the selection, the expanded rows and the scroll
position survive. All changes go through the engine (plan.md 5.14).

When the old Gecko SeedLink service (an older program, see core/ports.py) holds the Gecko or
SeedLink port, the rows say so and a bar above the tree (and the rows' context menu) offers to
free the port: Windows asks for administrator permission once, the service is stopped and
disabled, and the rows show "Waiting for the port…" until the engine listens. The first time
in a run that the Gecko port is held by it, SeisZone asks once by itself.
"""

import logging
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
                               QMenu, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import ports
from . import theme
from .channels_panel import fmt_latency

log = logging.getLogger("seiszone")

OLD_SERVICE = "old-service"         # engine.status() "owner" of a port held by the old service
FREE_ACTION_TEXT = "Free the port – stop the old Gecko SeedLink service"
WAITING_TEXT = "Waiting for the port…"
FREE_WAIT = 60.0                    # s "Waiting for the port…" is shown before the error comes back
OFFER_TITLE = "Gecko recorder"
OFFER_TEXT = ("Your Gecko recorder is connected to the old Gecko SeedLink service, not SeisZone. "
              "Stop and disable the old service now so the Gecko connects to SeisZone? "
              "(Windows asks for administrator permission; you can re-enable it in Windows Services.)")
OFFER_STOP, OFFER_LATER = "Stop old service", "Not now"

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
        self._held = []                 # rows whose port the old Gecko SeedLink service holds
        self._freeing = None            # time the old service was asked to stop (waiting for the port)
        self._waiting = []              # the rows that wait for their port since then
        self._offered = False           # the one question per run has been asked
        self.offer_box = None           # that question while it is open

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

        # shown only while the old Gecko SeedLink service holds a port SeisZone needs
        self.old_service_bar = QWidget()
        self.old_service_label = QLabel()
        self.old_service_label.setWordWrap(True)
        self.old_service_label.setStyleSheet(f"color: {theme.COLORS['warn']};")
        self.free_button = QPushButton(FREE_ACTION_TEXT)
        self.free_button.setToolTip("Stops the old Gecko SeedLink service and switches it off (Windows asks for "
                                    "administrator permission). It is not uninstalled: you can switch it on "
                                    "again in Windows Services.")
        self.free_button.clicked.connect(lambda: self.free_ports())
        bar = QVBoxLayout(self.old_service_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(2)
        bar.addWidget(self.old_service_label)
        bar.addWidget(self.free_button)
        self.old_service_bar.setVisible(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self.old_service_bar)
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
        gecko, server = status.get("gecko") or {}, status.get("server") or {}
        self._update_old_service(gecko, server, now)
        rows = [self._source_row(src, status.get("sources") or {}, stats, now) for src in self.config.sources()]
        rows.append(self._gecko_row(gecko, stats, now))
        rows.append(self._server_row(server, now))
        self._sync(self.tree.invisibleRootItem(), rows)
        n = len(rows) - 2
        receiving = sum(1 for r in rows[:n] if str(r["texts"][C_STATE]).startswith("receiving"))
        self.summary.setText(f"{receiving} of {n} source{'s' if n != 1 else ''} receiving")
        if GECKO_KEY in self._held and not self._offered and self._freeing is None and self._window_shown():
            self._offered = True        # asked once per run; the bar and the menu stay
            QTimer.singleShot(0, self.offer_stop_old_service)

    def _window_shown(self):
        """The window is on screen (not in the tray, not minimized): the one question waits for it."""
        win = self.window()
        return win.isVisible() and not win.isMinimized()

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
        if GECKO_KEY in self._waiting:
            state = WAITING_TEXT
        elif g.get("error"):
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
        if GECKO_KEY in self._held:
            tip += ("\n\nThe old Gecko SeedLink service holds this port, so the Gecko connects to it and "
                    f"not to SeisZone. Right-click: {FREE_ACTION_TEXT}.")
        return {"key": GECKO_KEY, "state": state, "fixed": True, "tip": tip,
                "texts": [f"Gecko receiver :{ports}", "this PC", "Gecko in", state,
                          str(sum(x.get("records", 0) for x in links)), f"{len(links)} linked"],
                "children": children}

    def _server_row(self, s, now):
        if SERVER_KEY in self._waiting:
            state = WAITING_TEXT
        elif s.get("error"):
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
        tip = f"Other programs read every channel from SeisZone here (SeedLink, port {s.get('port')})."
        if SERVER_KEY in self._held:
            tip += (f"\n\nThe old Gecko SeedLink service holds this port. Right-click: {FREE_ACTION_TEXT}.")
        return {"key": SERVER_KEY, "state": state, "fixed": True, "tip": tip,
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
            if key in self._held:
                m.addSeparator()
                m.addAction(FREE_ACTION_TEXT, self.free_ports).setEnabled(self._freeing is None)
            m.addSeparator()
            m.addAction("Properties...", lambda: self.show_properties(None, key))
        return m

    # ------------------------------------------------------------------------------------
    # the old Gecko SeedLink service
    # ------------------------------------------------------------------------------------
    def _update_old_service(self, gecko, server, now):
        """Which rows the old service holds; the bar above the tree; the end of the wait."""
        held = [key for key, st in ((GECKO_KEY, gecko), (SERVER_KEY, server))
                if st.get("error") and st.get("owner") == OLD_SERVICE]
        self._held = held
        if self._freeing is not None:
            # the wait ends when the engine listens on those ports (no error left), not as soon
            # as the old service lets go: a port can stay busy a moment longer
            waiting = [key for key, st in ((GECKO_KEY, gecko), (SERVER_KEY, server))
                       if key in self._waiting and st.get("error")]
            if not waiting or now - self._freeing > FREE_WAIT:
                if waiting:
                    log.warning("the ports are still not free %.0f s after the old Gecko SeedLink service was "
                                "asked to stop", now - self._freeing)
                self._freeing, waiting = None, []
            self._waiting = waiting
        if not held and self._freeing is None:
            self.old_service_bar.setVisible(False)
            return
        port_text = " and ".join(str(p) for p in self._held_ports(gecko, server, held or self._waiting))
        if self._freeing is not None:
            self.old_service_label.setText(f"{WAITING_TEXT} The old Gecko SeedLink service is being stopped; "
                                           f"SeisZone takes port {port_text} as soon as it is free.")
        elif GECKO_KEY in held:
            self.old_service_label.setText(f"Port {port_text} is used by the old Gecko SeedLink service: your Gecko "
                                           "recorder connects to it, not to SeisZone.")
        else:
            self.old_service_label.setText(f"Port {port_text} is used by the old Gecko SeedLink service, so "
                                           "SeisZone's SeedLink server cannot start.")
        self.free_button.setEnabled(self._freeing is None)
        self.old_service_bar.setVisible(True)

    @staticmethod
    def _held_ports(gecko, server, keys):
        out = []
        if GECKO_KEY in keys:
            listening = set(gecko.get("listening") or [])
            out += [p for p in gecko.get("ports") or [] if p not in listening]
        if SERVER_KEY in keys and server.get("port") is not None:
            out.append(server["port"])
        return out

    def offer_stop_old_service(self):
        """The one question per run: stop the old service so the Gecko connects to SeisZone?
        (non-blocking; "Stop old service" calls free_ports)."""
        if not self._held or self._freeing is not None or self.offer_box is not None:
            return None
        box = QMessageBox(QMessageBox.Question, OFFER_TITLE, OFFER_TEXT, QMessageBox.NoButton, self)
        stop = box.addButton(OFFER_STOP, QMessageBox.AcceptRole)
        box.addButton(OFFER_LATER, QMessageBox.RejectRole)
        box.setDefaultButton(stop)
        box.setAttribute(Qt.WA_DeleteOnClose)

        def answered(button):
            self.offer_box = None
            if box.buttonRole(button) == QMessageBox.AcceptRole:
                self.free_ports()
            else:
                log.info("the old Gecko SeedLink service keeps running (Not now)")
        box.buttonClicked.connect(answered)
        box.rejected.connect(lambda: setattr(self, "offer_box", None))
        self.offer_box = box
        box.open()
        return box

    def free_ports(self):
        """Stop and disable the old Gecko SeedLink service (Windows asks for administrator
        permission). True when that was started: the rows then wait for the port."""
        if self._freeing is not None or not self._held:   # one stop at a time; the port may be free already
            return False
        if not ports.stop_old_service():
            return False
        self._freeing, self._waiting = time.time(), list(self._held)
        self.refresh()
        return True

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
