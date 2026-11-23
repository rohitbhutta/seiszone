"""DataServerWindow: SeisZone's two data servers in one window, one tab each (like the "My Server"
page of the user's earlier viewer: switch, port, Clients, Control).

  * GCF over UDP (core/gcf_server.py, port 1567, off by default): for the earlier viewer -
    Network Control > right-click > Add UDP Server > <this PC's IP>:1567.
  * SeedLink over TCP (core/seedlink_server.py, port 18000, on by default): for SeisZone,
    SeisComP, Swarm, ObsPy ... - Add source / server <this PC's IP>:18000. A SeedLink client asks
    again for what it missed, so its data are backfilled after an outage - from the disk buffer
    (core/packet_store.py, "Keep on disk" days) also after hours away and across restarts.

Settings are saved in config["gcf_server"] and config["seedlink_server"] (its "Listen on" and
"Organization" stay in Settings > SeedLink server) and applied at once (Apply).

GCF tab, "Send to a computer": the user types another computer's IP address (and port, empty =
1567) and clicks Add: it is saved in config["gcf_server"]["clients"] at once and gets all data
without asking first, until the user selects it in Clients and clicks Remove. SeedLink cannot
send to a computer that did not ask, so the SeedLink tab has no Add: its programs connect by
themselves.
"""

import logging
import socket
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..core.gcf_server import DEFAULT_PORT, client_list, parse_client
from . import theme
from .settings_dialog import parse_port

log = logging.getLogger("seiszone")

COLUMNS = ("Client", "Params", "Connected since", "Last request", "Blocks sent")
SL_COLUMNS = ("Client", "Stations", "Connected since", "Packets sent")
TAB_GCF, TAB_SEEDLINK = range(2)
WHEN = "%d-%m-%Y %H:%M:%S"


def local_addresses():
    """IPv4 addresses of this PC (other computers type one of them), loopback left out."""
    out = []
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except OSError:
        pass
    return out


def _scrolling(page):
    """A tab page in a scroll area: on a small screen the window stays on the screen and the tab
    scrolls instead of the window growing past the screen's edge."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(page)
    return area


def _table(columns):
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.NoSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return t


def _log_box():
    box = QPlainTextEdit()
    box.setReadOnly(True)
    box.setMaximumBlockCount(500)
    return box


def _fill(table, rows):
    table.setRowCount(len(rows))
    for r, cells in enumerate(rows):
        for k, v in enumerate(cells):
            table.setItem(r, k, QTableWidgetItem(v))


def _show_lines(box, lines, last):
    """Put lines into a Control box when they changed (scrolled to the end); returns them."""
    if lines != last:
        box.setPlainText("\n".join(lines))
        box.verticalScrollBar().setValue(box.verticalScrollBar().maximum())
    return list(lines)


def _ip_list(ips, port):
    return " or ".join(f"<b>{ip}:{port}</b>" for ip in ips) if ips else f"<b>this PC's IP:{port}</b>"


def _size(n):
    if n is None:
        return "?"
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} bytes"


def buffer_text(b):
    """One line about the disk buffer for the SeedLink tab."""
    if not b:
        return ""
    if b.get("error"):
        return f"Disk buffer not available: {b['error']}. Packets are kept in memory only (about the last 1-2 hours)."
    if not b.get("enabled"):
        return ("Disk buffer off: only the packets in memory (about the last 1-2 hours) can be sent again, "
                "and none after SeismoVerse restarts.")
    days = float(b.get("days") or 0)
    text = f"Disk buffer: {_size(b.get('bytes'))} in {b.get('folder', '')}"
    if b.get("oldest"):
        text += time.strftime(", from %d-%m-%Y %H:%M UTC", time.gmtime(b["oldest"]))
    text += (f". Kept {days:g} day{'s' if days != 1 else ''}: other computers catch up on that much, "
             "also after SeismoVerse restarts, and the viewers show it. Older files are removed "
             "(recordings are never touched).")
    if b.get("paused"):
        text += " Paused: less than 2 GB free on the disk."
    return text


class DataServerWindow(QDialog):
    def __init__(self, engine, config, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.setWindowTitle("Data server")
        self.setWindowIcon(theme.icon("server"))
        self.resize(760, 600)
        c = theme.COLORS
        self._ips = local_addresses()
        self._lines = None
        self._sl_lines = None

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {c['danger']}; font-weight: 600;")
        self.error.hide()
        self.tabs = QTabWidget()
        self.tabs.addTab(_scrolling(self._gcf_tab()), "GCF over UDP (earlier viewer)")
        self.tabs.addTab(_scrolling(self._seedlink_tab()), "SeedLink over TCP")

        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.addButton(self.apply_button, QDialogButtonBox.ApplyRole)
        lay = QVBoxLayout(self)
        lay.addWidget(self.error)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(box)
        for w in (self.port, self.sl_port):
            w.textEdited.connect(self.error.hide)

        self.setMinimumSize(480, 360)      # the tabs scroll below this; the window fits any screen
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    # ------------------------------------------------------------------ the two tabs
    def _gcf_tab(self):
        s = self.config.get("gcf_server") or {}
        w = QWidget()
        self.enabled = QCheckBox("Send all data to other computers on the network (GCF over UDP)")
        self.enabled.setChecked(bool(s.get("enabled", False)))
        self.port = QLineEdit(str(s.get("port") or 1567))
        self.port.setFixedWidth(90)
        self.max_rate = QSpinBox()
        self.max_rate.setRange(1, 5000)
        self.max_rate.setSuffix(" sps")
        self.max_rate.setValue(int(float(s.get("max_rate") or 1000)))
        self.max_rate.setToolTip("Channels with a higher sample rate are not sent (transmitted block limit)")
        form = QFormLayout()
        form.addRow("", self.enabled)
        row = QHBoxLayout()
        row.addWidget(self.port)
        row.addStretch(1)
        form.addRow("UDP port", row)
        form.addRow("Channels up to", self.max_rate)
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.how = QLabel()
        self.how.setWordWrap(True)
        self.how.setTextFormat(Qt.RichText)
        self.clients = _table(COLUMNS)
        self.clients.setSelectionMode(QAbstractItemView.SingleSelection)
        self.clients.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.control = _log_box()
        # a computer added by hand: it gets all data without asking first
        self.add_ip = QLineEdit()
        self.add_ip.setPlaceholderText("IP address, e.g. 192.168.1.20")
        self.add_ip.setClearButtonEnabled(True)
        self.add_port = QLineEdit()
        self.add_port.setPlaceholderText(str(DEFAULT_PORT))
        self.add_port.setFixedWidth(90)
        self.add_button = QPushButton(theme.icon("add"), "Add")
        self.add_button.setToolTip("Send all data to this computer from now on, without it asking first")
        self.add_button.clicked.connect(self.add_client)
        self.remove_button = QPushButton(theme.icon("remove"), "Remove")
        self.remove_button.setToolTip("Stop sending to the computer selected in Clients (only those added by hand)")
        self.remove_button.clicked.connect(self.remove_client)
        for b in (self.add_button, self.remove_button):
            b.setAutoDefault(False)         # Enter in another field never adds or removes a computer
        self.add_ip.returnPressed.connect(self.add_client)
        self.add_port.returnPressed.connect(self.add_client)
        for e in (self.add_ip, self.add_port):
            e.textEdited.connect(self.error.hide)
        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Send to a computer"))
        add_row.addWidget(self.add_ip, 1)
        add_row.addWidget(QLabel("Port"))
        add_row.addWidget(self.add_port)
        add_row.addWidget(self.add_button)
        add_row.addWidget(self.remove_button)
        self.clients_hint = QLabel(
            "Computers you add here get all data without asking first and stay until you select them and "
            "click Remove. Other computers appear by themselves while they read this PC.")
        self.clients_hint.setWordWrap(True)
        self.clients_hint.setStyleSheet(f"color: {theme.COLORS['muted']};")
        self.add_note = QLabel()
        self.add_note.setWordWrap(True)
        self.add_note.hide()
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self.state)
        lay.addWidget(self.how)
        lay.addLayout(add_row)
        lay.addWidget(self.add_note)
        lay.addWidget(QLabel("Clients"))
        lay.addWidget(self.clients, 2)
        lay.addWidget(self.clients_hint)
        lay.addWidget(QLabel("Control"))
        lay.addWidget(self.control, 1)
        return w

    def _seedlink_tab(self):
        s = self.config.get("seedlink_server") or {}
        w = QWidget()
        self.sl_enabled = QCheckBox("Share all data with other computers and programs (SeedLink over TCP)")
        self.sl_enabled.setChecked(bool(s.get("enabled", True)))
        self.sl_port = QLineEdit(str(s.get("port") or 18000))
        self.sl_port.setFixedWidth(90)
        self.sl_days = QSpinBox()
        self.sl_days.setRange(0, 30)
        self.sl_days.setSuffix(" days")
        self.sl_days.setSpecialValueText("off (memory only)")
        self.sl_days.setFixedWidth(170)
        try:
            self.sl_days.setValue(int(round(float(s.get("buffer_days", 2)))))
        except (TypeError, ValueError):
            self.sl_days.setValue(2)
        self.sl_days.setToolTip("Every packet is also kept on disk this long, so other computers can catch up "
                                "after an outage (also after SeismoVerse restarts) and the viewers show older data. "
                                "About 1.5 GB per day with the current stations.")
        form = QFormLayout()
        form.addRow("", self.sl_enabled)
        row = QHBoxLayout()
        row.addWidget(self.sl_port)
        row.addStretch(1)
        form.addRow("TCP port", row)
        form.addRow("Keep on disk", self.sl_days)
        self.sl_state = QLabel()
        self.sl_state.setWordWrap(True)
        self.sl_how = QLabel()
        self.sl_how.setWordWrap(True)
        self.sl_how.setTextFormat(Qt.RichText)
        self.sl_buffer = QLabel()
        self.sl_buffer.setWordWrap(True)
        self.sl_clients = _table(SL_COLUMNS)
        self.sl_control = _log_box()
        sl_hint = QLabel("SeedLink programs connect by themselves and appear here while they are connected; "
                         "SeedLink cannot send to a computer that did not ask. To send to a computer without "
                         "it asking, use the GCF over UDP tab (Send to a computer).")
        sl_hint.setWordWrap(True)
        sl_hint.setStyleSheet(f"color: {theme.COLORS['muted']};")
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self.sl_state)
        lay.addWidget(self.sl_buffer)
        lay.addWidget(self.sl_how)
        lay.addWidget(QLabel("Clients"))
        lay.addWidget(self.sl_clients, 2)
        lay.addWidget(sl_hint)
        lay.addWidget(QLabel("Control"))
        lay.addWidget(self.sl_control, 1)
        return w

    # ------------------------------------------------------------------ settings
    def values(self):
        """{"gcf_server": ..., "seedlink_server": ...} from the fields; ValueError(tab, message)
        for the user."""
        try:
            port = int(self.port.text().strip())
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            raise ValueError(TAB_GCF, "The port must be a number from 1 to 65535.")
        try:
            sl_port = parse_port(self.sl_port.text(), "The SeedLink port")
        except ValueError as e:
            raise ValueError(TAB_SEEDLINK, str(e)) from None
        g = self.config.get("gecko") or {}
        if (self.sl_enabled.isChecked() and g.get("enabled", True)
                and sl_port in [int(p) for p in g.get("ports") or []]):
            raise ValueError(TAB_SEEDLINK, f"Port {sl_port} is the Gecko receiver's port: choose another one "
                                           "(the usual SeedLink port is 18000).")
        old = self.config.get("gcf_server") or {}
        sl_old = dict(self.config.get("seedlink_server") or {})
        sl_old.update({"enabled": self.sl_enabled.isChecked(), "port": sl_port,
                       "buffer_days": self.sl_days.value()})
        sl_old.setdefault("bind", "0.0.0.0")
        sl_old.setdefault("organization", "SeismoVerse")
        return {"gcf_server": {"enabled": self.enabled.isChecked(), "bind": old.get("bind") or "0.0.0.0",
                               "port": port, "max_rate": self.max_rate.value()},
                "seedlink_server": sl_old}

    def apply(self):
        try:
            vals = self.values()
        except ValueError as e:
            tab, text = e.args if len(e.args) == 2 else (TAB_GCF, str(e))
            self.tabs.setCurrentIndex(tab)
            self.error.setText(text)
            self.error.show()
            return False
        self.error.hide()
        for section, v in vals.items():
            self.config.update_section(section, v)
        log.info("data server settings saved: %s", vals)
        try:
            self.engine.apply_settings()
        except Exception:
            log.exception("applying the data server settings failed")
        QTimer.singleShot(300, self.refresh)
        return True

    # ------------------------------------------------------------------ clients added by hand
    def _fixed(self):
        """["ip:port"] of the computers added by hand (the saved list)."""
        return [f"{ip}:{port}" for ip, port in
                client_list((self.config.get("gcf_server") or {}).get("clients"))]

    def _show_error(self, text):
        self.tabs.setCurrentIndex(TAB_GCF)
        self.error.setText(text)
        self.error.show()

    def _save_fixed(self, fixed):
        self.config.update_section("gcf_server", {"clients": list(fixed)})
        log.info("GCF server clients added by hand: %s", ", ".join(fixed) or "none")
        try:
            self.engine.apply_settings()
        except Exception:
            log.exception("applying the GCF server clients failed")
        QTimer.singleShot(300, self.refresh)

    def add_client(self):
        """Add the typed computer to the computers added by hand (saved at once)."""
        try:
            ip, port = parse_client(self.add_ip.text(), self.add_port.text())
        except ValueError as e:
            self._show_error(str(e))
            return False
        key = f"{ip}:{port}"
        fixed = self._fixed()
        if key in fixed:
            self._show_error(f"{key} is already in the list.")
            return False
        self.error.hide()
        self._save_fixed(fixed + [key])
        self.add_ip.clear()
        self.add_port.clear()
        c = theme.COLORS
        st = (self.engine.status() or {}).get("gcf_server") or {}
        if st.get("listening"):
            self.add_note.setText(f"{key} added: it gets all data from now on.")
            self.add_note.setStyleSheet(f"color: {c['ok']};")
        else:
            self.add_note.setText(f"{key} added. It gets data once the GCF server is on: tick \"Send all data to "
                                  "other computers on the network\" above and click Apply.")
            self.add_note.setStyleSheet(f"color: {c['warn']};")
        self.add_note.show()
        return True

    def remove_client(self):
        """Remove the computer selected in Clients (only one added by hand)."""
        rows = self.clients.selectionModel().selectedRows() if self.clients.selectionModel() else []
        if not rows:
            self._show_error("Select a computer in Clients first, then click Remove.")
            return False
        item = self.clients.item(rows[0].row(), 0)
        key = item.text() if item is not None else ""
        fixed = self._fixed()
        if key not in fixed:
            self._show_error(f"{key} was not added by hand: it stops by itself when it stops asking "
                             "(after 3 minutes at the latest).")
            return False
        self.error.hide()
        fixed.remove(key)
        self._save_fixed(fixed)
        self.add_note.setText(f"{key} removed: nothing more is sent to it.")
        self.add_note.setStyleSheet(f"color: {theme.COLORS['muted']};")
        self.add_note.show()
        return True

    # ------------------------------------------------------------------ live state
    def refresh(self):
        status = self.engine.status() or {}
        self._refresh_gcf(status.get("gcf_server") or {})
        self._refresh_seedlink(status.get("server") or {})

    def _state(self, label, text, color):
        label.setText(text)
        label.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _refresh_gcf(self, st):
        c = theme.COLORS
        port = st.get("port") or self.port.text()
        self.how.setText(
            f"On the other computer (same network) add this PC as a UDP server: {_ip_list(self._ips, port)}."
            "<br>Earlier viewer: Network Control &gt; right-click &gt; Add UDP Server. "
            "SeismoVerse: Add source (it then reads this PC's SeedLink server too, with the exact "
            "channel names and the data missed during an outage).<br>"
            "Only one program on a PC can use a port: if the earlier viewer runs on this PC with "
            "port 1567, choose another port here (for example 1568) and type that on the other computer.")
        if st.get("error"):
            self._state(self.state, f"Not sending: {st['error']}", c["danger"])
        elif st.get("listening"):
            k = int(st.get("clients") or 0)
            self._state(self.state, f"Server active on UDP port {port} - {k} client{'s' if k != 1 else ''}, "
                                    f"{int(st.get('blocks') or 0)} blocks sent", c["ok"])
        else:
            self._state(self.state, "The GCF server is off.", c["muted"])
        rows, seen = [], set()
        for p in st.get("peers") or []:
            addr = p.get("address", "")
            seen.add(addr)
            if p.get("fixed"):
                rows.append((addr, f"{p.get('params', '')} (added by hand)",
                             time.strftime(WHEN, time.localtime(p.get("since") or 0)),
                             "sent without asking", str(p.get("blocks", 0))))
            else:
                rows.append((addr, p.get("params", ""), time.strftime(WHEN, time.localtime(p.get("since") or 0)),
                             f"{float(p.get('idle') or 0):.0f} s ago", str(p.get("blocks", 0))))
        waiting = "waiting: the GCF server is off" if not st.get("listening") else "waiting"
        for addr in st.get("fixed") if "fixed" in st else self._fixed():
            if addr not in seen:
                rows.append((addr, "(added by hand)", "-", waiting, "0"))
        sel = self.clients.selectionModel().selectedRows() if self.clients.selectionModel() else []
        picked = self.clients.item(sel[0].row(), 0).text() if sel and self.clients.item(sel[0].row(), 0) else None
        _fill(self.clients, rows)
        if picked is not None:
            for r, cells in enumerate(rows):
                if cells[0] == picked:
                    self.clients.selectRow(r)
                    break
        self._lines = _show_lines(self.control, st.get("control") or [], self._lines)

    def _refresh_seedlink(self, st):
        c = theme.COLORS
        port = st.get("port") or self.sl_port.text()
        self.sl_how.setText(
            f"On the other computer (same network) add this PC as a SeedLink server: "
            f"{_ip_list(self._ips, port)}.<br>"
            "SeismoVerse: Add source. SeisComP, Swarm, ObsPy and other SeedLink programs: server "
            "address and port as above. Each client asks again for what it missed, so its data are "
            "backfilled after a network outage.<br>Listen on and Organization: Settings &gt; SeedLink server.")
        if st.get("error"):
            self._state(self.sl_state, f"Not serving: {st['error']}", c["danger"])
        elif st.get("listening"):
            k = int(st.get("clients") or 0)
            self._state(self.sl_state, f"Server active on TCP port {port} - {k} client{'s' if k != 1 else ''}",
                        c["ok"])
        else:
            self._state(self.sl_state, "The SeedLink server is off.", c["muted"])
        b = st.get("buffer") or {}
        self.sl_buffer.setText(buffer_text(b))
        self.sl_buffer.setStyleSheet(f"color: {c['danger'] if b.get('error') or b.get('paused') else c['text']};")
        _fill(self.sl_clients, [(p.get("peer", ""), p.get("stations") or "-",
                                 time.strftime(WHEN, time.localtime(p.get("since") or 0)), str(p.get("packets", 0)))
                                for p in st.get("peers") or []])
        self._sl_lines = _show_lines(self.sl_control, st.get("control") or [], self._sl_lines)

    def done(self, result):
        self.timer.stop()
        super().done(result)
