"""Screenshots of SeismoVerse for the user manual: offscreen Qt, the test suite's synthetic data
(FakeEngine), a temporary SEISZONE_HOME. No instrument, no network, no real settings."""
import os
import sys
import tempfile
import time

REPO = sys.argv[1]
OUT = sys.argv[2]
os.environ["SEISZONE_HOME"] = tempfile.mkdtemp(prefix="seismoverse_manual_")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"
os.environ["SEISZONE_TIME_CHECK"] = "off"
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.join(REPO, "tests"))
os.makedirs(OUT, exist_ok=True)

from PySide6.QtWidgets import QApplication  # noqa: E402

from seiszone.config import Config  # noqa: E402
from seiszone.ui import theme  # noqa: E402

app = QApplication([])
theme.apply(app, "dark")

from fake_engine import FakeEngine  # noqa: E402
from seiszone.ui import server_window  # noqa: E402
server_window.local_addresses = lambda: ["192.168.1.50"]
from seiszone.ui.main_window import MainWindow  # noqa: E402


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def tidy_paths(widget):
    """Temporary folders in the pictures read like a normal PC's."""
    from PySide6.QtWidgets import QLineEdit
    for e in widget.findChildren(QLineEdit):
        txt = e.text()
        if "seismoverse_manual" in txt:
            last = txt.replace("/", "\\").rstrip("\\").split("\\")[-1]
            e.setText(r"C:\Users\Station\Documents\SeisZone" + "\\" + last)
            e.setCursorPosition(0)


def shot(widget, name):
    tidy_paths(widget)
    app.processEvents()
    path = os.path.join(OUT, name)
    assert widget.grab().save(path), path
    print("saved", path)


home = os.environ["SEISZONE_HOME"]
config = Config(os.path.join(home, "config.json"))
config.set("recording", "folder", os.path.join(home, "Recordings"))
engine = FakeEngine(config, seconds=900)
engine.hub.descriptions["WB.KUM"] = "KUMARI"
engine.hub.descriptions["SS.SMR03"] = "NAGDHANIBA"

win = MainWindow(engine, config)
win.resize(1500, 900)
win.show()
pump(2.0)
shot(win, "01_main_window.png")

dlg = win.open_about()
pump(0.4)
shot(dlg, "02_about.png")
dlg.reject()

from seiszone.ui.add_source_dialog import AddSourceDialog  # noqa: E402
add = AddSourceDialog(engine, config)
add.edit.setText("10.8.0.182")
add.show()
pump(0.4)
shot(add, "03_add_source.png")
add.reject()

sids = sorted(engine.hub.stream_ids())
viewer = win.open_viewer(sids)
viewer.resize(1400, 820)
pump(1.0)
viewer.view._window = 840
viewer.view.redraw()
pump(3.0)
shot(viewer, "04_viewer.png")

rec = win.open_record_dialog()
rec.resize(1000, 760)
pump(0.6)
shot(rec, "05_record.png")

exp = win.open_export_dialog()
pump(0.6)
shot(exp, "06_export.png")

srv = win.open_data_server()
pump(0.6)
tabs = srv.findChildren(__import__("PySide6.QtWidgets", fromlist=["QTabWidget"]).QTabWidget)
if tabs:
    tabs[0].setCurrentIndex(1)
    pump(0.4)
    shot(srv, "07_data_server_seedlink.png")
    tabs[0].setCurrentIndex(0)
    pump(0.4)
    shot(srv, "08_data_server_gcf.png")

st = win.open_settings()
pump(0.6)
shot(st, "09_settings.png")
os._exit(0)
