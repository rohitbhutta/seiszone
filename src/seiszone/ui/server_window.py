"""DataServerWindow: SeisZone's own GCF data server (core/gcf_server.py) - switch it on, its
port and rate limit, the computers pulling from it and what happened (like the "My Server" page
of the user's earlier viewer: Clients, Params, Timeout, Control).

Another computer on the same network gets every channel of this one:
  * the earlier viewer: Network Control > right-click > Add UDP Server > <this PC's IP>:1567
  * SeisZone: Add source > <this PC's IP>:1567 (SeisZone then reads this PC's SeedLink server,
    port 18000, with the exact channel names and the data missed during an outage)

Settings are saved in config["gcf_server"] and applied at once (Apply).
"""

import logging
import socket
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from . import theme

log = logging.getLogger("seiszone")

COLUMNS = ("Client", "Params", "Connected since", "Last request", "Blocks sent")


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


class DataServerWindow(QDialog):
    def __init__(self, engine, config, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.setWindowTitle("Data server")
        self.setWindowIcon(theme.icon("server"))
        self.resize(720, 560)
        s = config.get("gcf_server") or {}
        c = theme.COLORS

        self.enabled = QCheckBox("Send all data to other computers on the network (GCF over UDP)")
        self.enabled.setChecked(bool(s.get("enabled", False)))
        self.port = QLineEdit(str(s.get("port") or 1567))
        self.port.setFixedWidth(90)
        self.max_rate = QSpinBox()
        self.max_rate.setRange(1, 5000)
        self.max_rate.setSuffix(" sps")
        self.max_rate.setValue(int(float(s.get("max_rate") or 1000)))
        self.max_rate.setToolTip("Channels with a higher sample rate are not sent (transmitted block limit)")
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply)
        form = QFormLayout()
        form.addRow("", self.enabled)
        row = QHBoxLayout()
        row.addWidget(self.port)
        row.addStretch(1)
        form.addRow("Port used for all network communications", row)
        form.addRow("Channels up to", self.max_rate)

        self.state = QLabel()
        self.state.setWordWrap(True)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {c['danger']}; font-weight: 600;")
        self.error.hide()
        ips = local_addresses()
        self.how = QLabel(
            "On the other computer (same network) add this PC as a UDP server: "
            + (" or ".join(f"<b>{ip}:{{port}}</b>" for ip in ips) if ips else "<b>this PC's IP:{port}</b>")
            + ".<br>Earlier viewer: Network Control &gt; right-click &gt; Add UDP Server. "
              "SeisZone: Add source (it then reads this PC's SeedLink server too, with the exact "
              "channel names and the data missed during an outage).<br>"
              "Only one program on a PC can use a port: if the earlier viewer runs on this PC with "
              "port 1567, choose another port here (for example 1568) and type that on the other computer.")
        self.how.setWordWrap(True)
        self.how.setTextFormat(Qt.RichText)
        self._how_text = self.how.text()

        self.clients = QTableWidget(0, len(COLUMNS))
        self.clients.setHorizontalHeaderLabels(COLUMNS)
        self.clients.verticalHeader().hide()
        self.clients.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.clients.setSelectionMode(QAbstractItemView.NoSelection)
        self.clients.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.control = QPlainTextEdit()
        self.control.setReadOnly(True)
        self.control.setMaximumBlockCount(500)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.addButton(self.apply_button, QDialogButtonBox.ApplyRole)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.error)
        lay.addWidget(self.state)
        lay.addWidget(self.how)
        lay.addWidget(QLabel("Clients"))
        lay.addWidget(self.clients, 2)
        lay.addWidget(QLabel("Control"))
        lay.addWidget(self.control, 1)
        lay.addWidget(box)
        for w in (self.port,):
            w.textEdited.connect(self.error.hide)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def values(self):
        """config["gcf_server"] from the fields (ValueError with a message for the user)."""
        try:
            port = int(self.port.text().strip())
        except ValueError:
            raise ValueError("The port must be a number from 1 to 65535.") from None
        if not 1 <= port <= 65535:
            raise ValueError("The port must be a number from 1 to 65535.")
        old = self.config.get("gcf_server") or {}
        return {"enabled": self.enabled.isChecked(), "bind": old.get("bind") or "0.0.0.0", "port": port,
                "max_rate": self.max_rate.value()}

    def apply(self):
        try:
            vals = self.values()
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()
            return False
        self.error.hide()
        self.config.update_section("gcf_server", vals)
        log.info("data server settings saved: %s", vals)
        try:
            self.engine.apply_settings()
        except Exception:
            log.exception("applying the data server settings failed")
        QTimer.singleShot(300, self.refresh)
        return True

    def refresh(self):
        st = (self.engine.status() or {}).get("gcf_server") or {}
        c = theme.COLORS
        port = st.get("port") or self.port.text()
        self.how.setText(self._how_text.replace("{port}", str(port)))
        if st.get("error"):
            text, color = f"Not sending: {st['error']}", c["danger"]
        elif st.get("listening"):
            k = int(st.get("clients") or 0)
            text, color = (f"Server active on UDP port {port} - {k} client{'s' if k != 1 else ''}, "
                           f"{int(st.get('blocks') or 0)} blocks sent"), c["ok"]
        else:
            text, color = "The data server is off.", c["muted"]
        self.state.setText(text)
        self.state.setStyleSheet(f"color: {color}; font-weight: 600;")
        peers = st.get("peers") or []
        self.clients.setRowCount(len(peers))
        for r, p in enumerate(peers):
            idle = float(p.get("idle") or 0)
            cells = (p.get("address", ""), p.get("params", ""),
                     time.strftime("%d-%m-%Y %H:%M:%S", time.localtime(p.get("since") or 0)),
                     f"{idle:.0f} s ago", str(p.get("blocks", 0)))
            for k, v in enumerate(cells):
                self.clients.setItem(r, k, QTableWidgetItem(v))
        lines = st.get("control") or []
        if lines != getattr(self, "_lines", None):
            self._lines = list(lines)
            self.control.setPlainText("\n".join(lines))
            self.control.verticalScrollBar().setValue(self.control.verticalScrollBar().maximum())

    def done(self, result):
        self.timer.stop()
        super().done(result)
