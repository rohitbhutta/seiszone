"""Small windows of a viewer: ScaleDialog (a channel's scale factor), DetailsWindow (mean, range
and RMS of the visible data, and the channel's offset), CalibrationDialog (counts per physical
unit for a channel without StationXML) and ChannelPickerDialog (which channels a viewer shows).
"""

import logging
import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QRadioButton, QVBoxLayout)

from . import theme
from .waveform_view import fmt_value

log = logging.getLogger("seiszone")

ID_ROLE = Qt.UserRole + 1
CAL_UNITS = [("M/S", "m/s (velocity)"), ("M/S**2", "m/s² (acceleration)"), ("M", "m (displacement)"),
             ("PA", "Pa (pressure)"), ("V", "V (volts)")]


def parse_scale(text, current):
    """The scale factor typed: "2", "x2" / "*2" (times the current one), "/2" (divided), "1".
    None when it is not a positive number."""
    t = text.strip().lower().replace(",", ".")
    if t == "auto":       # as in the earlier viewer: back to the plain trace
        return 1.0
    try:
        if t[:1] in ("*", "x", "×"):
            v = current * float(t[1:])
        elif t[:1] == "/":
            v = current / float(t[1:])
        else:
            v = float(t)
    except (ValueError, ZeroDivisionError, IndexError):
        return None
    return v if math.isfinite(v) and v > 0 else None


class ScaleDialog(QDialog):
    """Scale factor of a channel - also for every channel of its station or of the viewer."""

    def __init__(self, view, sid, parent=None):
        super().__init__(parent)
        self.view, self.sid = view, sid
        self.setWindowTitle(f"Scale - {sid}")
        current = float(view.trace_settings(sid)["scale"])
        self.edit = QLineEdit(f"{current:g}")
        self.edit.selectAll()
        self.this = QRadioButton("This channel")
        self.station = QRadioButton(f"All channels of {'.'.join(sid.split('.')[:2])} in this viewer")
        self.all = QRadioButton("All channels in this viewer")
        group = QButtonGroup(self)
        for b in (self.this, self.station, self.all):
            group.addButton(b)
        self.this.setChecked(True)
        hint = QLabel("A factor of 2 shows the trace twice as large. Type *2 or /2 to change the current factor, "
                      "1 or auto to go back, or another channel of this viewer (e.g. "
                      f"{next((s for s in view.channels() if s != sid), sid)}) to show this one as high as "
                      "that one. The amplitude zoom of the window (Up / Down) multiplies it.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.COLORS['muted']};")
        self.message = QLabel()
        self.message.setStyleSheet(f"color: {theme.COLORS['danger']};")
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.apply)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Scale factor", self.edit)
        lay.addLayout(form)
        for b in (self.this, self.station, self.all):
            lay.addWidget(b)
        lay.addWidget(hint)
        lay.addWidget(self.message)
        lay.addWidget(box)

    def targets(self):
        sids = self.view.channels()
        if self.all.isChecked():
            return sids
        if self.station.isChecked():
            sta = ".".join(self.sid.split(".")[:2])
            return [s for s in sids if ".".join(s.split(".")[:2]) == sta]
        return [self.sid]

    def apply(self):
        text = self.edit.text().strip()
        ref = next((s for s in self.view.channels() if s.lower() == text.lower()), None)
        if ref is not None:   # another channel's name: each target as high as that channel
            done = False
            for s in self.targets():
                f = self.view.match_factor(s, ref) if s != ref else None
                if f is not None and math.isfinite(f) and f > 0:
                    self.view.set_trace_scale(s, f)
                    done = True
            if not done:
                self.message.setText(f"{ref} has no data on screen to match.")
                return False
            self.accept()
            return True
        current = float(self.view.trace_settings(self.sid)["scale"])
        value = parse_scale(text, current)
        if value is None:
            self.message.setText("Type a number above 0 (such as 2, *2 or /2), auto, or a channel of this viewer.")
            return False
        for s in self.targets():
            self.view.set_trace_scale(s, value)
        self.accept()
        return True


class DetailsWindow(QDialog):
    """Mean, minimum, maximum, their difference and RMS of a channel's visible data (as shown:
    filtered, offset, in its units), updated every second; the plot shows the mean (dotted) and
    the minimum and maximum (solid) while it is open. The offset can be typed in (counts) or
    nulled / zeroed / handed back to the mean removal."""

    def __init__(self, view, sid, parent=None):
        super().__init__(parent)
        self.view, self.sid = view, sid
        self.setWindowTitle(f"Details - {sid}")
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.values = {}
        form = QFormLayout()
        for key, text in (("filter", "Filter"), ("rate", "Sample rate"), ("n", "Samples"), ("mean", "Mean"),
                          ("min", "Minimum"), ("max", "Maximum"), ("diff", "Diff (max - min)"), ("rms", "RMS")):
            lab = QLabel("-")
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.values[key] = lab
            form.addRow(text, lab)
        self.offset = QLineEdit()
        self.offset.setPlaceholderText("mean removed (window setting)")
        self.offset.setToolTip("The offset in counts subtracted from this channel; Enter applies it")
        self.offset.returnPressed.connect(self._offset_typed)
        form.addRow("Offset (counts)", self.offset)
        null = QPushButton("Null")
        null.setToolTip("Fix the offset at the mean now (the trace is centred)")
        null.clicked.connect(lambda: self.view.null_offsets(self.sid))
        zero = QPushButton("Zero")
        zero.setToolTip("Offset 0: absolute values (the trace may leave its row)")
        zero.clicked.connect(lambda: self.view.zero_offset(self.sid))
        auto = QPushButton("Mean removal")
        auto.setToolTip("No fixed offset: follow the window's Remove mean setting")
        auto.clicked.connect(lambda: self.view.set_offset(self.sid, None))
        row = QHBoxLayout()
        for b in (null, zero, auto):
            b.setAutoDefault(False)
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addLayout(row)
        view.show_details_lines(sid, True)
        self.refresh()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def refresh(self):
        if self.sid not in self.view.channels():
            self.close()
            return
        st = self.view.trace_stats(self.sid)
        if st is None:
            for lab in self.values.values():
                lab.setText("-")
            return
        physical = st["units"] != "counts"
        u = st["units"]
        self.values["filter"].setText(st["filter"])
        self.values["rate"].setText(f"{st['rate']:g} sps" if st["rate"] else "-")
        self.values["n"].setText(str(st["n"]))
        for key in ("mean", "min", "max", "rms"):
            self.values[key].setText(f"{fmt_value(st[key], physical)} {u}")
        self.values["diff"].setText(f"{fmt_value(st['max'] - st['min'], physical)} {u}")
        if not self.offset.hasFocus():
            off = st["offset"]
            self.offset.setText("" if off is None else f"{off:.6g}")

    def _offset_typed(self):
        text = self.offset.text().strip()
        if not text:
            self.view.set_offset(self.sid, None)
            return
        try:
            self.view.set_offset(self.sid, float(text.replace(",", ".")))
        except ValueError:
            self.offset.selectAll()

    def _finish(self):
        """Every way of closing (X, Esc, the viewer closing) ends here: no more lines or RMS."""
        if getattr(self, "_finished", False):
            return
        self._finished = True
        self.timer.stop()
        try:
            self.view.show_details_lines(self.sid, False)
        except RuntimeError:   # the viewer is already gone
            pass

    def done(self, result):
        self._finish()
        super().done(result)

    def closeEvent(self, ev):
        self._finish()
        super().closeEvent(ev)


class CalibrationDialog(QDialog):
    """Counts per physical unit of a channel, for physical units without (or instead of) a
    StationXML response. Saved in config "calibration" {channel: {"sensitivity", "units"}}."""

    def __init__(self, config, engine, sid, parent=None):
        super().__init__(parent)
        self.config, self.sid = config, sid
        self.setWindowTitle(f"Calibration - {sid}")
        c = theme.COLORS
        known = None
        responses = getattr(engine, "responses", None)
        if responses is not None:
            try:
                known = responses.sensitivity(sid)
            except Exception:
                log.exception("StationXML sensitivity of %s failed", sid)
        own = (config.get("calibration") or {}).get(sid) or {}
        self.value = QLineEdit(f"{own['sensitivity']:g}" if isinstance(own.get("sensitivity"), (int, float))
                               else "")
        self.value.setPlaceholderText("e.g. 407880 (counts per m/s²)")
        self.units = QComboBox()
        for key, text in CAL_UNITS:
            self.units.addItem(text, key)
        self.units.setCurrentIndex(max(0, self.units.findData(own.get("units") or (known[1] if known else "M/S"))))
        info = QLabel(f"StationXML: {known[0]:g} counts per {known[1]}" if known else
                      "No StationXML response is known for this channel.")
        info.setStyleSheet(f"color: {c['muted']};")
        hint = QLabel("Physical units divide the counts by this value (the digitiser's counts per volt times the "
                      "sensor's volts per unit). It is used instead of StationXML for this channel.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {c['muted']};")
        self.message = QLabel()
        self.message.setStyleSheet(f"color: {c['danger']};")
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.remove_button = box.addButton("Remove", QDialogButtonBox.DestructiveRole)
        self.remove_button.setEnabled(bool(own))
        self.remove_button.clicked.connect(self.remove)
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow("Counts per unit", self.value)
        form.addRow("Unit", self.units)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(form)
        lay.addWidget(hint)
        lay.addWidget(self.message)
        lay.addWidget(box)

    def save(self):
        try:
            v = float(self.value.text().strip().replace(",", "."))
        except ValueError:
            v = math.nan
        if not math.isfinite(v) or v == 0:
            self.message.setText("Type the number of counts per unit (not 0).")
            return False
        cal = dict(self.config.get("calibration") or {})
        cal[self.sid] = {"sensitivity": v, "units": self.units.currentData()}
        self.config.set_value("calibration", cal)
        self.accept()
        return True

    def remove(self):
        cal = dict(self.config.get("calibration") or {})
        cal.pop(self.sid, None)
        self.config.set_value("calibration", cal)
        self.accept()


class ChannelPickerDialog(QDialog):
    """Tick the channels a viewer shows: every channel SeisZone receives, plus those already in
    the viewer. chosen() keeps the viewer's order and appends new ones in list order."""

    def __init__(self, engine, current, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.current = list(current)
        self.setWindowTitle("Channels of this viewer")
        self.resize(520, 560)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter channels (code or site)")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._filter)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        have = set(self.current)
        for sid in sorted(set(engine.hub.stream_ids()) | have):
            desc = engine.hub.descriptions.get(".".join(sid.split(".")[:2]), "")
            item = QListWidgetItem(f"{sid}    {desc}" if desc else sid)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if sid in have else Qt.Unchecked)
            item.setData(ID_ROLE, sid)
            self.list.addItem(item)
        all_b = QPushButton("All")
        all_b.clicked.connect(lambda: self._tick(True))
        none_b = QPushButton("None")
        none_b.clicked.connect(lambda: self._tick(False))
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(all_b)
        row.addWidget(none_b)
        row.addStretch(1)
        row.addWidget(box)
        lay = QVBoxLayout(self)
        lay.addWidget(self.filter)
        lay.addWidget(self.list, 1)
        lay.addLayout(row)

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(bool(t) and t not in item.text().lower())

    def _tick(self, on):
        """All / None: only the channels listed (matching the filter)."""
        for i in range(self.list.count()):
            item = self.list.item(i)
            if not item.isHidden():
                item.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def chosen(self):
        ticked = [self.list.item(i).data(ID_ROLE) for i in range(self.list.count())
                  if self.list.item(i).checkState() == Qt.Checked]
        keep = [s for s in self.current if s in ticked]
        return keep + [s for s in ticked if s not in keep]
