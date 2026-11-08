"""SeisZone look: our own palette, stylesheet and icons (drawn in code - no image files needed).

    theme.apply(app, mode="dark")      Fusion style + palette + stylesheet ("dark" or "light")
    theme.COLORS                        current colours (dict of hex strings)
    theme.trace_color(i)                colour for waveform trace i
    theme.state_color(state)            colour for a link / channel state
    theme.icon(name)                    QIcon: add remove reconnect record stop export spectrum
                                        settings live pause app source channel gecko server zoom
    theme.app_icon()                    the SeisZone application icon
"""

import math
import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath,
                           QPalette, QPen, QPixmap)

DARK = {
    "bg": "#0f1720", "panel": "#16212c", "panel2": "#1c2a37", "border": "#2a3b4c",
    "text": "#d8e3ec", "muted": "#8597a8", "accent": "#2bb3a3", "accent2": "#f2a541",
    "danger": "#e5534b", "ok": "#3fb950", "warn": "#d29922", "plot_bg": "#0b1219",
    "grid": "#22313f", "select": "#21495a",
}
LIGHT = {
    "bg": "#f3f6f9", "panel": "#ffffff", "panel2": "#eaf0f5", "border": "#c9d4de",
    "text": "#1b2834", "muted": "#5d6f80", "accent": "#138a7e", "accent2": "#c77a12",
    "danger": "#c93c36", "ok": "#2a8a3e", "warn": "#9a6c00", "plot_bg": "#ffffff",
    "grid": "#e1e8ee", "select": "#cde9e5",
}
COLORS = dict(DARK)
MODE = "dark"

TRACES_DARK = ["#4fd1c5", "#f6ad55", "#90cdf4", "#f687b3", "#9ae6b4", "#fbd38d", "#b794f4", "#feb2b2"]
TRACES_LIGHT = ["#0f766e", "#b45309", "#1d4ed8", "#be185d", "#15803d", "#a16207", "#6d28d9", "#b91c1c"]


def trace_color(i):
    lst = TRACES_DARK if MODE == "dark" else TRACES_LIGHT
    return lst[i % len(lst)]


def state_color(state):
    s = (state or "").lower()
    if s.startswith("receiving") or s in ("ok", "recording", "listening"):
        return COLORS["ok"]
    if s.startswith(("error", "server refused", "busy")):
        return COLORS["danger"]
    if s.startswith(("stopped", "disabled")):
        return COLORS["muted"]
    return COLORS["warn"]  # connecting, waiting, connected without data yet, late


def _stylesheet(c):
    return f"""
QWidget {{ color: {c['text']}; font-size: 9pt; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QDockWidget::title {{ background: {c['panel2']}; padding: 4px 8px; font-weight: 600; }}
QToolBar {{ background: {c['panel']}; border-bottom: 1px solid {c['border']}; spacing: 4px; padding: 3px; }}
QToolButton {{ padding: 4px 8px; border-radius: 4px; }}
QToolButton:hover {{ background: {c['panel2']}; }}
QToolButton:checked {{ background: {c['select']}; }}
QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['border']}; }}
QTreeView, QTableView, QListView, QPlainTextEdit, QTextEdit {{
    background: {c['panel']}; alternate-background-color: {c['panel2']};
    border: 1px solid {c['border']}; selection-background-color: {c['select']};
    selection-color: {c['text']}; }}
QHeaderView::section {{ background: {c['panel2']}; padding: 4px; border: 0;
    border-right: 1px solid {c['border']}; border-bottom: 1px solid {c['border']}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit {{
    background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 4px; padding: 3px 6px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus {{ border-color: {c['accent']}; }}
QPushButton {{ background: {c['panel2']}; border: 1px solid {c['border']}; border-radius: 4px;
    padding: 5px 14px; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:default {{ background: {c['accent']}; color: #ffffff; border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted']}; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 6px; margin-top: 10px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['muted']}; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; }}
QTabBar::tab {{ background: {c['panel2']}; padding: 5px 12px; }}
QTabBar::tab:selected {{ background: {c['panel']}; border-bottom: 2px solid {c['accent']}; }}
QProgressBar {{ border: 1px solid {c['border']}; border-radius: 4px; text-align: center; }}
QProgressBar::chunk {{ background: {c['accent']}; }}
QToolTip {{ background: {c['panel2']}; color: {c['text']}; border: 1px solid {c['border']}; }}
QTableView::indicator, QTreeView::indicator, QListView::indicator, QCheckBox::indicator {{
    width: 12px; height: 12px; border: 1px solid {c['muted']}; border-radius: 2px;
    background: {c['plot_bg']}; }}
QTableView::indicator:checked, QTreeView::indicator:checked, QListView::indicator:checked,
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QTreeView::indicator:indeterminate, QCheckBox::indicator:indeterminate {{ border-color: {c['accent']};
    background: qlineargradient(x1:0, y1:1, x2:1, y2:0, stop:0 {c['accent']}, stop:0.5 {c['accent']},
    stop:0.51 {c['plot_bg']}, stop:1 {c['plot_bg']}); }}
QCheckBox::indicator:disabled {{ border-color: {c['border']}; }}
"""


def apply(app, mode="dark"):
    global MODE
    MODE = "light" if mode == "light" else "dark"
    COLORS.clear()
    COLORS.update(LIGHT if MODE == "light" else DARK)
    c = COLORS
    app.setStyle("Fusion")
    if sys.platform == "win32":  # the Windows UI font by name: platforms without a font list
        app.setFont(QFont("Segoe UI", 9))  # (offscreen screenshots) would pick any font otherwise
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "panel"), (QPalette.AlternateBase, "panel2"),
                      (QPalette.Button, "panel2"), (QPalette.WindowText, "text"), (QPalette.Text, "text"),
                      (QPalette.ButtonText, "text"), (QPalette.ToolTipBase, "panel2"),
                      (QPalette.ToolTipText, "text"), (QPalette.Highlight, "select"),
                      (QPalette.HighlightedText, "text"), (QPalette.PlaceholderText, "muted")):
        pal.setColor(role, QColor(c[key]))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(c["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(_stylesheet(c))
    app.setWindowIcon(app_icon())


# --------------------------------------------------------------------------------------------
# icons
# --------------------------------------------------------------------------------------------

def _pen(color, w=2.0):
    p = QPen(QColor(color), w)
    p.setCapStyle(Qt.RoundCap)
    p.setJoinStyle(Qt.RoundJoin)
    return p


def _wave_path(r, amp_scale=1.0):
    """A seismogram-like trace across rect r: calm, a burst, decaying coda."""
    path = QPainterPath()
    n = 26
    for i in range(n + 1):
        x = r.left() + r.width() * i / n
        u = i / n
        env = 0.07 + (0.93 * math.exp(-((u - 0.38) * 4.2) ** 2) if u > 0.25 else 0.0)
        y = r.center().y() - r.height() / 2 * env * amp_scale * (1 if i % 2 else -1) * (0 if i in (0, n) else 1)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    return path


def _draw(name, p, s):
    c = COLORS
    fg = c["text"]
    acc = c["accent"]
    m = s * 0.18
    r = QRectF(m, m, s - 2 * m, s - 2 * m)
    p.setBrush(Qt.NoBrush)
    if name == "add":
        p.setPen(_pen(acc, s * 0.11))
        p.drawLine(QPointF(s / 2, m), QPointF(s / 2, s - m))
        p.drawLine(QPointF(m, s / 2), QPointF(s - m, s / 2))
    elif name == "remove":
        p.setPen(_pen(c["danger"], s * 0.11))
        p.drawLine(QPointF(m, s / 2), QPointF(s - m, s / 2))
    elif name == "reconnect":
        p.setPen(_pen(fg, s * 0.09))
        p.drawArc(r, 40 * 16, 280 * 16)
        tip = QPointF(r.right() - s * 0.02, r.top() + r.height() * 0.28)
        p.drawLine(tip, tip + QPointF(-s * 0.2, -s * 0.02))
        p.drawLine(tip, tip + QPointF(s * 0.02, -s * 0.2))
    elif name == "record":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["danger"]))
        p.drawEllipse(r.adjusted(s * 0.05, s * 0.05, -s * 0.05, -s * 0.05))
    elif name == "stop":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(fg))
        p.drawRoundedRect(r.adjusted(s * 0.07, s * 0.07, -s * 0.07, -s * 0.07), s * 0.06, s * 0.06)
    elif name == "export":
        p.setPen(_pen(fg, s * 0.08))
        p.drawRect(QRectF(m, s * 0.45, s - 2 * m, s * 0.37))
        p.setPen(_pen(acc, s * 0.09))
        p.drawLine(QPointF(s / 2, s * 0.62), QPointF(s / 2, m * 0.6))
        p.drawLine(QPointF(s / 2, m * 0.6), QPointF(s * 0.34, s * 0.27))
        p.drawLine(QPointF(s / 2, m * 0.6), QPointF(s * 0.66, s * 0.27))
    elif name == "spectrum":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(acc))
        for i, h in enumerate((0.35, 0.7, 0.5, 0.85, 0.3)):
            w = r.width() / 5
            p.drawRect(QRectF(r.left() + i * w + w * 0.15, r.bottom() - r.height() * h, w * 0.7, r.height() * h))
    elif name == "settings":
        p.setPen(_pen(fg, s * 0.09))
        p.drawEllipse(QPointF(s / 2, s / 2), s * 0.16, s * 0.16)
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(s / 2 + math.cos(a) * s * 0.26, s / 2 + math.sin(a) * s * 0.26),
                       QPointF(s / 2 + math.cos(a) * s * 0.38, s / 2 + math.sin(a) * s * 0.38))
    elif name == "live":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["ok"]))
        path = QPainterPath()
        path.moveTo(r.left() + s * 0.08, r.top())
        path.lineTo(r.right(), s / 2)
        path.lineTo(r.left() + s * 0.08, r.bottom())
        path.closeSubpath()
        p.drawPath(path)
    elif name == "pause":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(fg))
        p.drawRect(QRectF(r.left() + s * 0.05, r.top(), s * 0.18, r.height()))
        p.drawRect(QRectF(r.right() - s * 0.23, r.top(), s * 0.18, r.height()))
    elif name == "zoom":
        p.setPen(_pen(fg, s * 0.09))
        p.drawEllipse(QRectF(m, m, s * 0.48, s * 0.48))
        p.drawLine(QPointF(s * 0.58, s * 0.58), QPointF(s - m, s - m))
    elif name == "gecko":
        p.setPen(_pen(c["accent2"], s * 0.08))
        p.drawRoundedRect(r, s * 0.1, s * 0.1)
        p.setPen(_pen(fg, s * 0.07))
        p.drawPath(_wave_path(r.adjusted(s * 0.08, s * 0.12, -s * 0.08, -s * 0.12)))
    elif name == "server":
        p.setPen(_pen(fg, s * 0.08))
        for k in range(3):
            p.drawRoundedRect(QRectF(m, m + k * s * 0.22, s - 2 * m, s * 0.17), s * 0.04, s * 0.04)
        p.setPen(_pen(acc, s * 0.1))
        p.drawPoint(QPointF(s - m * 1.6, m + s * 0.085))
    elif name == "channel":
        p.setPen(_pen(acc, s * 0.08))
        p.drawPath(_wave_path(r))
    elif name == "source":
        p.setPen(_pen(fg, s * 0.08))
        p.drawLine(QPointF(s / 2, s * 0.45), QPointF(s / 2, s - m))
        p.drawLine(QPointF(s * 0.3, s - m), QPointF(s * 0.7, s - m))
        p.setPen(_pen(acc, s * 0.07))
        for k, rad in enumerate((0.14, 0.26)):
            p.drawArc(QRectF(s / 2 - s * rad, s * 0.33 - s * rad, 2 * s * rad, 2 * s * rad), 30 * 16, 120 * 16)
    else:  # "app" and unknown names
        _draw_app(p, s)


def _draw_app(p, s):
    grad = QLinearGradient(0, 0, s, s)
    grad.setColorAt(0, QColor("#0e3b47"))
    grad.setColorAt(1, QColor("#0a1a26"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(QRectF(s * 0.03, s * 0.03, s * 0.94, s * 0.94), s * 0.2, s * 0.2)
    p.setPen(_pen("#1f5f6b", max(1.0, s * 0.02)))
    for k in (0.3, 0.5, 0.7):
        p.drawLine(QPointF(s * 0.12, s * k), QPointF(s * 0.88, s * k))
    p.setPen(_pen("#4fd1c5", max(1.2, s * 0.035)))
    p.drawPath(_wave_path(QRectF(s * 0.12, s * 0.2, s * 0.76, s * 0.6)))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#f6ad55"))
    p.drawEllipse(QPointF(s * 0.8, s * 0.22), s * 0.07, s * 0.07)


_CACHE = {}


def icon(name):
    key = (name, MODE)
    if key in _CACHE:
        return _CACHE[key]
    ic = QIcon()
    for s in (16, 20, 24, 32, 48):
        pm = QPixmap(s, s)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        _draw(name, p, float(s))
        p.end()
        ic.addPixmap(pm)
    _CACHE[key] = ic
    return ic


def _asset(name):
    base = getattr(sys, "_MEIPASS", None)
    roots = [base] if base else []
    here = os.path.dirname(os.path.abspath(__file__))
    roots += [os.path.join(here, "..", "..", ".."), os.path.dirname(sys.executable)]
    for r in roots:
        p = os.path.join(r, "assets", name)
        if os.path.exists(p):
            return p
    return None


def app_icon():
    path = _asset("seiszone.ico") or _asset("seiszone.png")
    if path:
        ic = QIcon(path)
        if not ic.isNull():
            return ic
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(s, s)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        _draw_app(p, float(s))
        p.end()
        ic.addPixmap(pm)
    return ic


def draw_app_pixmap(size):
    """The application icon as a QPixmap (used by tools/make_icon.py)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    _draw_app(p, float(size))
    p.end()
    return pm
