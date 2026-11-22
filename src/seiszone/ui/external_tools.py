"""External tools: programs the user lists (config "tools") that get a selection of the viewer.

Like the add-on programs of other seismic viewers (a calibration script, an analysis package):
the selected channels and time range are written as miniSEED files (one per channel, plus the
export report) into a new folder under the local SeisZone folder, then the program is started
with them and SeisZone does not wait for it.

A tool is {"name": "...", "program": "C:\\...\\tool.exe", "args": "..."}. In the arguments:
    {files}   every written file, each as its own argument (also the default when args is empty)
    {folder}  the folder holding them
    {t0} {t1} start and end of the selection (UTC, 2026-11-22T10:07:30.000)
    {sids}    the channel codes, comma-separated
Arguments are split like a command line (quotes keep spaces together) BEFORE the values are put
in, so paths with spaces stay one argument each. Nothing runs as administrator.
"""

import datetime as dt
import logging
import os

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import paths
from ..core import export

log = logging.getLogger("seiszone")

CONFIG_KEY = "tools"


def tools(config):
    """The configured tools (only complete entries)."""
    out = []
    for t in config.get(CONFIG_KEY) or []:
        if isinstance(t, dict) and str(t.get("name") or "").strip() and str(t.get("program") or "").strip():
            out.append({"name": str(t["name"]).strip(), "program": str(t["program"]).strip(),
                        "args": str(t.get("args") or "").strip()})
    return out


def _iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def arguments(template, files, folder, t0, t1, sids):
    """The argument list for a tool: see the module text."""
    parts = QProcess.splitCommand(template) if template.strip() else ["{files}"]
    out = []
    for p in parts:
        if p == "{files}":
            out.extend(files)
            continue
        out.append(p.replace("{folder}", folder).replace("{t0}", _iso(t0)).replace("{t1}", _iso(t1))
                   .replace("{sids}", ",".join(sids)).replace("{files}", " ".join(files)))
    return out


def selection_folder(t0):
    """A new folder for one selection: <local SeisZone folder>/selections/<time>."""
    stamp = dt.datetime.fromtimestamp(t0, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(paths.local_dir(), "selections", f"{stamp}_{dt.datetime.now():%H%M%S%f}")
    os.makedirs(base, exist_ok=True)
    return base


def write_selection(engine, sids, t0, t1, folder):
    """The selection as miniSEED files (one per channel) in folder; returns the .mseed paths."""
    written, _report = export.export_window(sids, t0, t1, folder, mode="separate",
                                            hub=getattr(engine, "hub", None), log=lambda _m: None)
    return [p for p in written if p.lower().endswith(".mseed")]


def run(tool, engine, sids, t0, t1, start=QProcess.startDetached):
    """Write the selection and start the tool. Returns (ok, message, arguments)."""
    folder = selection_folder(t0)
    files = write_selection(engine, sids, t0, t1, folder)
    if not files:
        return False, "No data of the selected channels in that time range", []
    args = arguments(tool.get("args", ""), files, folder, t0, t1, sids)
    program = tool["program"]
    if not os.path.isfile(program):
        return False, f"{tool['name']}: the program {program} is not there", args
    result = start(program, args, folder)
    ok = result[0] if isinstance(result, tuple) else bool(result)
    if not ok:
        return False, f"{tool['name']} could not be started", args
    log.info("External tool %s started with %d file(s) in %s", tool["name"], len(files), folder)
    return True, f"{tool['name']} started with {len(files)} file(s) ({folder})", args


class ExternalToolsDialog(QDialog):
    """The list of external tools: name, program, arguments."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("External tools")
        self.resize(820, 340)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Programs that get a selection of a viewer (Shift+drag, then the selection menu). The "
            "selected channels are written as miniSEED files and the program is started with them.\n"
            "Arguments: {files} = the files (the default), {folder}, {t0}, {t1} (UTC), {sids}."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Program", "Arguments"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("Add...")
        add.clicked.connect(self.add_tool)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_tool)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        for t in tools(config):
            self._append(t["name"], t["program"], t["args"])

    def _append(self, name, program, args=""):
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, v in enumerate((name, program, args)):
            self.table.setItem(r, c, QTableWidgetItem(v))

    def add_tool(self, program=None):
        if not program:
            program, _f = QFileDialog.getOpenFileName(self, "Program", "", "Programs (*.exe *.bat *.cmd);;All files (*)")
        if program:
            self._append(os.path.splitext(os.path.basename(program))[0], program, "{files}")

    def remove_tool(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def entries(self):
        out = []
        for r in range(self.table.rowCount()):
            vals = [(self.table.item(r, c).text().strip() if self.table.item(r, c) else "") for c in range(3)]
            if vals[0] and vals[1]:
                out.append({"name": vals[0], "program": vals[1], "args": vals[2]})
        return out

    def save(self):
        self.config.set_value(CONFIG_KEY, self.entries())
        self.accept()
