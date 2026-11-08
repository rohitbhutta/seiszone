"""AddSourceDialog: the user types only "IP address : port" and gets a working instrument.

Detect runs core.probe.probe() in a QThread (it may take up to a few minutes over a slow VPN:
SeedLink INFO, a GCF data sample, the instrument's web page and its StationXML). Its progress
lines stream into the dialog; then a result card shows what was found, the channels to read
(ticked by default: every data channel), an editable display name and the notes. Add turns the
choice into a config source with Instrument.to_source(), saves the StationXML and hands the
source to the engine.

When nothing answers, the notes say why and "Add anyway as SeedLink" adds the address as a plain
SeedLink source (every channel) - the engine keeps trying to connect in the background.
"""

import html
import logging
import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from . import theme

log = logging.getLogger("seiszone")

DEFAULT_SEEDLINK_PORT = 18000
NOT_DATA = ("SOH", "LOG", "ACE", "OCF")   # state-of-health / log streams: not ticked by default
EXAMPLES = [("10.8.0.182:18000", "Guralp SeedLink"), ("10.8.0.181:1567", "Guralp GCF"),
            ("rtserve.iris.washington.edu:18000", "SeedLink server")]
CH_ROLE = Qt.UserRole + 1


class ProbeThread(QThread):
    """Runs the probe off the user-interface thread; its log lines arrive as signals."""
    line = Signal(str)
    found = Signal(object)
    failed = Signal(str)

    def __init__(self, probe_fn, text, need_xml, parent=None):
        super().__init__(parent)
        self.probe_fn = probe_fn
        self.text = text
        self.need_xml = need_xml

    def run(self):
        try:
            inst = self.probe_fn(self.text, log=self.line.emit, prefer_direct=True, need_xml=self.need_xml)
        except Exception as e:  # the probe never raises for network trouble; this is a bug or bad text
            log.exception("instrument detection failed for %s", self.text)
            self.failed.emit(str(e) or e.__class__.__name__)
        else:
            self.found.emit(inst)


# Every probe thread is held here until it has ended: a dialog closed during a detection must not
# destroy a thread that is still running (finished ones are dropped when the next one starts).
_RUNNING = set()


def default_ticked(inst, st, ch):
    """Ticked at first: data channels; not state-of-health / log streams, and for GCF only
    streams whose rate is known."""
    if ch.get("cha") in NOT_DATA:
        return False
    return bool(ch.get("rate")) or inst.method == "seedlink"


class AddSourceDialog(QDialog):
    def __init__(self, engine, config, parent=None, initial_text=""):
        super().__init__(parent)
        self.engine = engine
        self.config = config
        self.inst = None
        self.added_source = None
        self.state = "idle"          # idle -> detecting -> found | nothing
        self._thread = None
        self._started = 0.0
        self.setWindowTitle("Add source")
        self.setWindowIcon(theme.icon("add"))
        self.setMinimumWidth(640)
        c = theme.COLORS

        title = QLabel("Add an instrument or SeedLink server")
        title.setStyleSheet("font-size: 13pt; font-weight: 600;")
        intro = QLabel("Type its IP address and, if you know it, the port. SeisZone finds the stations, "
                       "channels, sample rates and StationXML by itself.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {c['muted']};")

        self.edit = QLineEdit(initial_text)
        self.edit.setPlaceholderText("IP address : port     e.g. 10.8.0.182:18000")
        self.edit.setClearButtonEnabled(True)
        self.edit.setMinimumHeight(30)
        self.edit.setStyleSheet("font-size: 11pt;")
        self.edit.textEdited.connect(self._text_edited)
        self.detect_button = QPushButton(theme.icon("zoom"), "Detect")
        self.detect_button.setMinimumHeight(30)
        self.detect_button.clicked.connect(self.detect)
        row = QHBoxLayout()
        row.addWidget(self.edit, 1)
        row.addWidget(self.detect_button)

        links = "&nbsp;&nbsp;&middot;&nbsp;&nbsp;".join(
            f"<a href='{t}' style='color:{c['accent']}; text-decoration:none'>{t}</a> {d}" for t, d in EXAMPLES)
        examples = QLabel(f"Examples: {links}")
        examples.setWordWrap(True)
        examples.setToolTip("Click an example to put it in the field")
        examples.linkActivated.connect(self._use_example)
        gecko = QLabel("Kelunji Gecko recorders are not added here: they connect by themselves to this PC "
                       "on port 58772 and appear under \"Gecko receiver\" in the Sources list.")
        gecko.setWordWrap(True)
        gecko.setStyleSheet(f"color: {c['muted']};")

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {c['danger']}; font-weight: 600;")
        self.error.hide()

        # progress: busy bar + the probe's own lines
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumHeight(14)
        self.busy.setTextVisible(False)
        self.busy.hide()
        self.progress_label = QLabel()
        self.progress_label.setStyleSheet(f"color: {c['muted']};")
        self.progress = QPlainTextEdit()
        self.progress.setReadOnly(True)
        self.progress.setMaximumBlockCount(500)
        self.progress.setFixedHeight(96)
        self.progress.setPlaceholderText("Progress of the detection appears here.")
        self.progress.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 8.5pt;")
        self.tick = QTimer(self)
        self.tick.setInterval(500)
        self.tick.timeout.connect(self._show_elapsed)

        self.card = self._build_card()
        self.card.hide()

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.manual_button = QPushButton("Add anyway as SeedLink")
        self.manual_button.setToolTip("Add the address as a SeedLink source with every channel; SeisZone "
                                      "keeps trying to connect in the background")
        self.manual_button.clicked.connect(self.add_manual)
        self.manual_button.hide()
        self.add_button = QPushButton(theme.icon("add"), "Add")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self.add)
        for b in (self.cancel_button, self.manual_button, self.add_button):
            b.setAutoDefault(False)
        self.detect_button.setAutoDefault(False)
        self.detect_button.setDefault(True)   # Enter in the address field starts Detect
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.manual_button)
        buttons.addWidget(self.add_button)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(title)
        lay.addWidget(intro)
        lay.addSpacing(4)
        lay.addLayout(row)
        lay.addWidget(examples)
        lay.addWidget(gecko)
        lay.addWidget(self.error)
        prog = QHBoxLayout()
        prog.addWidget(self.busy, 1)
        prog.addWidget(self.progress_label)
        lay.addLayout(prog)
        lay.addWidget(self.progress)
        lay.addWidget(self.card, 1)
        lay.addLayout(buttons)
        self.edit.setFocus()

    def _build_card(self):
        c = theme.COLORS
        card = QFrame()
        card.setObjectName("resultCard")
        card.setStyleSheet(f"QFrame#resultCard {{ background: {c['panel']}; border: 1px solid {c['border']}; "
                           "border-radius: 6px; }")
        self.card_title = QLabel()
        self.card_title.setStyleSheet("font-size: 11pt; font-weight: 600; border: 0;")
        self.card_summary = QLabel()
        self.card_summary.setWordWrap(True)
        self.card_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.card_summary.setStyleSheet("border: 0;")
        self.card_notes = QLabel()
        self.card_notes.setWordWrap(True)
        self.card_notes.setStyleSheet(f"color: {c['warn']}; border: 0;")

        self.name = QLineEdit()
        self.name.setPlaceholderText("Name shown in SeisZone, e.g. KUMARI")
        name_label = QLabel("Display name")
        name_label.setStyleSheet("border: 0;")

        self.channels = QTreeWidget()
        self.channels.setHeaderLabels(["Channel", "Rate (sps)", "Units", "Note"])
        self.channels.setRootIsDecorated(True)
        self.channels.setUniformRowHeights(True)
        self.channels.setMinimumHeight(150)
        self.channels.itemChanged.connect(lambda *_: self._update_add_button())
        self.channels_label = QLabel("Channels to read")
        self.channels_label.setStyleSheet("border: 0;")
        self.all_button = QPushButton("All")
        self.all_button.clicked.connect(lambda: self._tick_all(True))
        self.none_button = QPushButton("None")
        self.none_button.clicked.connect(lambda: self._tick_all(False))
        for b in (self.all_button, self.none_button):
            b.setAutoDefault(False)

        grid = QGridLayout(card)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.addWidget(self.card_title, 0, 0, 1, 4)
        grid.addWidget(self.card_summary, 1, 0, 1, 4)
        grid.addWidget(self.card_notes, 2, 0, 1, 4)
        grid.addWidget(name_label, 3, 0)
        grid.addWidget(self.name, 3, 1, 1, 3)
        grid.addWidget(self.channels_label, 4, 0)
        grid.addWidget(self.all_button, 4, 2)
        grid.addWidget(self.none_button, 4, 3)
        grid.addWidget(self.channels, 5, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(5, 1)
        return card

    # ------------------------------------------------------------------------------------
    # detection
    # ------------------------------------------------------------------------------------
    def _use_example(self, text):
        if self.state == "detecting":
            return
        self.edit.setText(text)
        self._text_edited(text)
        self.edit.setFocus()

    def _text_edited(self, _text):
        """A changed address makes the old result meaningless: back to Detect."""
        self.error.hide()
        if self.state in ("found", "nothing"):
            self._set_state("idle")

    def _set_state(self, state):
        self.state = state
        detecting = state == "detecting"
        self.edit.setEnabled(not detecting)
        self.detect_button.setEnabled(not detecting)
        self.busy.setVisible(detecting)
        self.card.setVisible(state in ("found", "nothing"))
        self.manual_button.setVisible(state == "nothing")
        self.channels.setVisible(state == "found")
        self.channels_label.setVisible(state == "found")
        self.all_button.setVisible(state == "found")
        self.none_button.setVisible(state == "found")
        if detecting:
            self.tick.start()
        else:
            self.tick.stop()
        self._update_add_button()
        target = self.add_button if state == "found" else self.manual_button if state == "nothing" \
            else self.detect_button
        for b in (self.detect_button, self.add_button, self.manual_button):
            b.setDefault(b is target)

    def show_error(self, text):
        self.error.setText(text)
        self.error.show()

    def detect(self):
        if self.state == "detecting":
            return
        text = self.edit.text().strip()
        if not text:
            self.show_error("Type the IP address of the instrument first, for example 10.8.0.182:18000.")
            return
        try:
            from ..core import probe as probe_mod
        except ImportError as e:
            self.show_error(f"Instrument detection is not available in this build ({e}).")
            return
        try:
            probe_mod.parse_target(text)
        except ValueError as e:
            self.show_error(f"{str(e).rstrip('.')}. Type it like 10.8.0.182:18000 or just 10.8.0.182.")
            return
        self.error.hide()
        self.inst = None
        self.progress.clear()
        self.progress.appendPlainText(f"Detecting {text} ...")
        responses = getattr(self.engine, "responses", None)
        need_xml = (lambda net, sta: not responses.has(net, sta)) if responses else None
        self._started = time.time()
        self._set_state("detecting")
        self._show_elapsed()
        t = ProbeThread(probe_mod.probe, text, need_xml)
        t.line.connect(self._probe_line)
        t.found.connect(self._probe_done)
        t.failed.connect(self._probe_failed)
        t.finished.connect(self._thread_finished)
        self._thread = t
        for old in [x for x in _RUNNING if x.isFinished()]:
            _RUNNING.discard(old)
        _RUNNING.add(t)
        log.info("detecting the instrument at %s", text)
        t.start()

    def _show_elapsed(self):
        if self.state == "detecting":
            self.progress_label.setText(f"Detecting ... {time.time() - self._started:.0f} s")

    def _probe_line(self, text):
        self.progress.appendPlainText(text)

    def _thread_finished(self):
        if self._thread is not None and self._thread.isFinished():
            _RUNNING.discard(self._thread)
            self._thread = None

    def _probe_failed(self, message):
        self.progress.appendPlainText(f"Detection failed: {message}")
        self.inst = None
        self._show_nothing([f"The detection stopped with an error: {message}"])

    def _probe_done(self, inst):
        self.inst = inst
        self.progress_label.setText(f"Finished in {time.time() - self._started:.0f} s")
        if not inst.found:
            self.progress.appendPlainText("Nothing usable was found.")
            self._show_nothing(list(inst.summary_lines()) + list(inst.notes))
            return
        self.progress.appendPlainText("Done.")
        c = theme.COLORS
        self.card_title.setText(f"<span style='color:{c['ok']}'>&#9679;</span> Found")
        self.card_summary.setText("<br>".join(html.escape(x) for x in inst.summary_lines()))
        self.card_notes.setText("<br>".join(html.escape(x) for x in inst.notes))
        self.card_notes.setVisible(bool(inst.notes))
        try:
            suggested = inst.to_source().get("name", "")
        except ValueError:
            suggested = ""
        self.name.setText(suggested or inst.host)
        self._fill_channels(inst)
        self._set_state("found")
        self.name.setFocus()

    def _show_nothing(self, lines):
        c = theme.COLORS
        self.progress_label.setText(f"Finished in {time.time() - self._started:.0f} s")
        self.card_title.setText(f"<span style='color:{c['warn']}'>&#9679;</span> Nothing found")
        self.card_summary.setText("<br>".join(html.escape(x) for x in lines))
        self.card_notes.setText("Check the address and the port, and that this PC can reach the instrument "
                                "(VPN connected?). If it is a SeedLink server that is slow to answer, you can "
                                "still add it: SeisZone keeps trying to connect in the background.")
        self.card_notes.show()
        try:
            from ..core.probe import parse_target
            host = parse_target(self.edit.text())[0]
        except (ImportError, ValueError):
            host = ""
        self.name.setText(host)
        self._set_state("nothing")

    def _fill_channels(self, inst):
        self.channels.blockSignals(True)
        self.channels.clear()
        for st in inst.stations:
            top = QTreeWidgetItem(self.channels, [f"{st['net']}.{st['sta']}  {st.get('desc') or ''}".rstrip()])
            top.setData(0, CH_ROLE, ("station", st["net"], st["sta"]))
            if st["channels"]:
                top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                for ch in st["channels"]:
                    rate = ch.get("rate") or 0
                    note = "state of health" if ch.get("cha") in NOT_DATA else \
                        ("" if rate or inst.method == "seedlink" else "rate unknown")
                    if ch.get("gcf"):
                        note = (note + "  " if note else "") + f"GCF {ch['gcf']}"
                    item = QTreeWidgetItem(top, [f"{ch.get('loc') or '--'}.{ch['cha']}",
                                                 f"{rate:g}" if rate else "", ch.get("units") or "", note])
                    item.setData(0, CH_ROLE, ("channel", st["net"], st["sta"], ch.get("loc", ""), ch["cha"]))
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.Checked if default_ticked(inst, st, ch) else Qt.Unchecked)
            else:
                top.setFlags(top.flags() | Qt.ItemIsUserCheckable)
                top.setCheckState(0, Qt.Checked)
                top.setText(3, "channel list not available: every channel is read")
            top.setExpanded(True)
        self.channels.blockSignals(False)
        for col in range(4):
            self.channels.resizeColumnToContents(col)
        self.channels.setColumnWidth(0, max(170, self.channels.columnWidth(0) + 20))

    def _tick_all(self, on):
        root = self.channels.invisibleRootItem()
        for i in range(root.childCount()):
            top = root.child(i)
            if top.childCount():
                for k in range(top.childCount()):
                    top.child(k).setCheckState(0, Qt.Checked if on else Qt.Unchecked)
            else:
                top.setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    def chosen(self):
        """(ticked channels [(net, sta, loc, cha)], ticked stations without a channel list [(net, sta)])."""
        chans, bare = [], []
        root = self.channels.invisibleRootItem()
        for i in range(root.childCount()):
            top = root.child(i)
            if top.childCount() == 0:
                if top.checkState(0) == Qt.Checked:
                    _kind, net, sta = top.data(0, CH_ROLE)
                    bare.append((net, sta))
                continue
            for k in range(top.childCount()):
                item = top.child(k)
                if item.checkState(0) == Qt.Checked:
                    chans.append(tuple(item.data(0, CH_ROLE)[1:]))
        return chans, bare

    def _update_add_button(self):
        if self.state != "found":
            self.add_button.setEnabled(False)
            return
        chans, bare = self.chosen()
        self.add_button.setEnabled(bool(chans or bare))

    # ------------------------------------------------------------------------------------
    # adding
    # ------------------------------------------------------------------------------------
    def build_source(self):
        """The config source for the ticked channels (ValueError with a message for the user)."""
        inst = self.inst
        chans, bare = self.chosen()
        if not chans and not bare:
            raise ValueError("Tick at least one channel to read.")
        if chans:
            src = inst.to_source(selected=chans)
            if bare and src.get("kind") == "seedlink":
                have = {(s["net"], s["sta"]) for s in src["stations"]}
                for st in inst.stations:
                    if (st["net"], st["sta"]) in bare and (st["net"], st["sta"]) not in have:
                        src["stations"].append({"net": st["net"], "sta": st["sta"], "desc": st.get("desc", ""),
                                                "selectors": []})
        else:  # only stations whose channel list the server did not give: all their channels
            src = inst.to_source()
            src["stations"] = [s for s in src.get("stations", []) if (s["net"], s["sta"]) in bare]
        src["name"] = self.name.text().strip() or src.get("name") or inst.host
        return src

    def add(self):
        if self.state != "found" or self.inst is None:
            return
        try:
            src = self.build_source()
        except ValueError as e:
            self.show_error(str(e))
            return
        self._save_stationxml(src)
        self._finish(src)

    def add_manual(self):
        """Nothing was detected: add the address as a SeedLink source with every channel."""
        try:
            from ..core.probe import parse_target
            host, port = parse_target(self.edit.text())
        except ImportError:
            text = self.edit.text().strip()
            host, _, p = text.partition(":")
            port = int(p) if p.isdigit() else None
        except ValueError as e:
            self.show_error(str(e))
            return
        src = {"kind": "seedlink", "host": host, "port": port or DEFAULT_SEEDLINK_PORT,
               "name": self.name.text().strip() or host, "stations": [],
               "details": {"added_without_detection": True}}
        self._finish(src)

    def _save_stationxml(self, src):
        xml = getattr(self.inst, "stationxml", None)
        responses = getattr(self.engine, "responses", None)
        if not xml or responses is None:
            return
        stations = [(s["net"], s["sta"]) for s in src.get("stations", [])]
        if src.get("gcf"):
            stations.append((src["gcf"]["net"], src["gcf"]["sta"]))
        for net, sta in dict.fromkeys(stations):
            try:
                path = responses.save(net, sta, xml)
                log.info("StationXML of %s.%s saved: %s", net, sta, path)
            except (OSError, ValueError) as e:
                log.warning("StationXML of %s.%s could not be saved: %s", net, sta, e)

    def _finish(self, src):
        """Hand the source to the engine; an existing source for the same address is replaced
        (keeping its id, so SeedLink resumes where it stopped) only if the user agrees."""
        existing = self.config.find_source(src["host"], src["port"])
        try:
            if existing:
                ans = QMessageBox.question(
                    self, "Source already added",
                    f"{src['host']}:{src['port']} is already in SeisZone as "
                    f"'{existing.get('name') or existing['host']}'.\n\nReplace it with what was found now?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if ans != QMessageBox.Yes:
                    return
                src = dict(src, id=existing["id"], enabled=True, added=existing.get("added", time.time()))
                result = self.engine.update_source(src)
                log.info("source replaced: %s:%s (%s)", src["host"], src["port"], src["name"])
            else:
                result = self.engine.add_source(src)
                log.info("source added: %s:%s (%s)", src["host"], src["port"], src["name"])
        except ValueError as e:
            self.show_error(f"The source could not be added: {e}")
            return
        self.added_source = result or src
        self.accept()

    def done(self, result):
        """Closing while the probe still runs: the thread finishes on its own (it cannot be
        interrupted); its results are simply not used any more."""
        t = self._thread
        if t is not None:
            for sig in (t.line, t.found, t.failed, t.finished):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            self._thread = None
        self.tick.stop()
        super().done(result)
