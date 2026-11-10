"""Offscreen smoke tests of the application shell: MainWindow with FakeEngine data, its timers,
toolbar dialogs, layout saving, the log panel, single-instance helpers and start with Windows.

Nothing here touches the network: FakeEngine never connects, the single-instance test uses its
own random mutex / local socket names, and the start-with-Windows test writes to a test-only
registry key that is deleted again.
"""

import copy
import logging
import os
import subprocess
import sys
import threading
import time
import types
import uuid

import pytest

# the offscreen platform has no font list of its own: without this the screenshots show boxes
if sys.platform == "win32" and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from seiszone import VERSION, logsetup, paths
from seiszone.config import Config, State
from seiszone.ui import app as app_mod
from seiszone.ui import theme
from seiszone.ui.log_panel import LogPanel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENS = os.path.join(ROOT, "build", "screens")
DATA_SECONDS = 330   # synthetic data: just over the default 5-minute window (keeps the tests fast)
log = logging.getLogger("seiszone")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    theme.apply(app, "dark")
    if not logsetup.LOG_LINES and not log.handlers:
        logsetup.setup(folder=paths.log_dir())
    return app


def pump(app, seconds):
    """Run the Qt event loop for a while (timers fire, widgets repaint)."""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def close_modals_soon(app, every_ms=100, times=30):
    """Reject any modal dialog / message box that a slot opens with exec(), so a test never
    hangs waiting for a click."""
    def tick(n=times):
        w = app.activeModalWidget()
        if isinstance(w, QDialog):
            w.reject()
        if n > 0:
            QTimer.singleShot(every_ms, lambda: tick(n - 1))
    QTimer.singleShot(every_ms, tick)


_BASE = []


def fake_engine(config, state=None):
    """A FakeEngine for this config. The synthetic data (slow to encode) is made once and its
    hub shared: the user interface only reads it."""
    from fake_engine import FakeEngine, FakeRecorder
    if not _BASE:
        _BASE.append(FakeEngine(Config(os.path.join(paths.config_dir(), "shell_base.json")),
                                seconds=DATA_SECONDS))
    base = _BASE[0]
    engine = copy.copy(base)
    engine.config, engine.recorder, engine.calls = config, FakeRecorder(), []
    engine.state = state or State(config.path + ".state.json")
    if not config.sources():
        config.add_source(base.config.sources()[0])
    return engine


@pytest.fixture
def window(qapp, tmp_path):
    from seiszone.ui.main_window import MainWindow
    config = Config(str(tmp_path / "config.json"))
    config.set("recording", "folder", str(tmp_path / "rec"))
    engine = fake_engine(config)
    w = MainWindow(engine, config)
    yield w
    w.prepare_quit()
    w.close()
    w.deleteLater()
    pump(qapp, 0.1)


# --------------------------------------------------------------------------------------------
# main window
# --------------------------------------------------------------------------------------------

def test_window_builds_and_timers_tick(qapp, window):
    assert window.windowTitle() == f"SeisZone {VERSION}"
    texts = [a.text() for a in window.toolbar.actions() if a.text()]
    assert texts == ["Add source", "Remove", "Reconnect", "Record", "Export", "Spectrum", "Settings", "About"]
    window.show()
    pump(qapp, 2.0)
    # first start: the first channels are shown as soon as data is there, and remembered
    shown = window.view.channels()
    assert shown and set(shown) <= set(window.engine.hub.stream_ids())
    assert window.config.get("view", "channels") == list(shown)
    assert "1/1 sources receiving" in window.st["sources"].text()
    assert "Gecko links: 1" in window.st["gecko"].text()
    assert ":18000" in window.st["server"].text() and "1 client" in window.st["server"].text()
    assert window.st["recording"].text() == "Not recording"
    assert window.st["disk"].text().startswith("Free 100.0 GB")


def test_toolbar_dialogs_open_and_close(qapp, window):
    window.show()
    pump(qapp, 0.5)
    for name, opener in (("about", window.open_about), ("record", window.open_record_dialog),
                         ("export", window.open_export_dialog), ("spectrum", window.open_spectrum),
                         ("settings", window.open_settings)):
        dlg = opener()
        assert dlg is not None, name
        pump(qapp, 0.3)
        assert dlg.isVisible(), name
        assert window.dialogs.get(name) is dlg
        dlg.reject()
        pump(qapp, 0.2)
        assert name not in window.dialogs, name
    # add source may open its dialog modally: it is rejected programmatically
    close_modals_soon(qapp)
    window.act_add.trigger()
    pump(qapp, 0.5)
    assert qapp.activeModalWidget() is None


def test_record_button_and_quit_confirmation(qapp, window, monkeypatch):
    window.engine.recorder.start("test", str(window.config.get("recording", "folder")))
    window.refresh()
    assert window.act_record.text() == "Recording (1)"
    assert "Recording 1" in window.st["recording"].text()
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.No))
    assert window.confirm_quit() is False and asked
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    assert window.confirm_quit() is True


def test_close_without_tray_closes(qapp, window):
    window.show()
    pump(qapp, 0.2)
    window.close()
    pump(qapp, 0.2)
    assert not window.isVisible()
    assert not window.fast.isActive() and not window.slow.isActive()
    assert window.config.get("ui", "geometry")


def test_close_with_tray_hides(qapp, window):
    hidden = []
    window.tray_available = True
    window.hiddenToTray.connect(lambda: hidden.append(1))
    window.show()
    pump(qapp, 0.2)
    window.close()
    pump(qapp, 0.1)
    assert hidden and not window.isVisible()
    assert window.fast.isActive()  # still running in the tray


def test_layout_saved_and_restored(qapp, window):
    from seiszone.ui.main_window import MainWindow
    window.resize(700, 520)  # within the offscreen screen (restoreGeometry clamps to the screen)
    window.show()
    pump(qapp, 0.3)
    size = (window.width(), window.height())  # the panels' minimum sizes may enlarge it
    window.save_layout()
    geo = window.config.get("ui", "geometry")
    assert geo and window.config.get("ui", "state")
    w2 = MainWindow(window.engine, window.config)
    try:
        w2.show()
        pump(qapp, 0.3)
        # restored, not the 1600x950 first-start size (minimum widths follow the live labels)
        assert abs(w2.width() - size[0]) <= 30 and abs(w2.height() - size[1]) <= 30
    finally:
        w2.prepare_quit()
        w2.close()
        w2.deleteLater()


def test_damaged_layout_ignored(qapp, window):
    from seiszone.ui.main_window import MainWindow
    window.config.update_section("ui", {"geometry": "not base64 !!", "state": "%%%"})
    w2 = MainWindow(window.engine, window.config)
    try:
        assert w2.width() >= 800
    finally:
        w2.prepare_quit()
        w2.close()
        w2.deleteLater()


def test_screenshot(qapp, window):
    window.resize(1600, 950)
    window.show()
    pump(qapp, 2.0)
    os.makedirs(SCREENS, exist_ok=True)
    path = os.path.join(SCREENS, "main_window.png")
    assert window.grab().save(path)
    assert os.path.getsize(path) > 10_000
    window.open_about()
    pump(qapp, 0.3)
    about = window.dialogs["about"]
    assert about.grab().save(os.path.join(SCREENS, "about.png"))
    about.reject()


# --------------------------------------------------------------------------------------------
# log panel
# --------------------------------------------------------------------------------------------

def test_log_panel_shows_new_lines(qapp):
    logsetup.setup(folder=paths.log_dir())
    panel = LogPanel()
    marker = f"shell-test {uuid.uuid4().hex}"
    log.warning("%s warning", marker)
    log.info("%s info", marker)
    panel.refresh()
    held = [t for t in panel.lines() if marker in t]
    assert len(held) == 2 and "WARNING" in held[0]
    assert marker in panel.text.toPlainText()
    panel.refresh()  # nothing new: nothing appended twice
    assert sum(marker in t for t in panel.lines()) == 2
    panel.filter.setText("WARNING")
    assert f"{marker} warning" in panel.text.toPlainText()
    assert f"{marker} info" not in panel.text.toPlainText()
    panel.filter.setText("")
    assert f"{marker} info" in panel.text.toPlainText()


def test_log_panel_keeps_at_most_5000_lines(qapp):
    logsetup.setup(folder=paths.log_dir())
    panel = LogPanel()
    for i in range(5200):
        log.info("flood %d", i)
    panel.refresh()
    assert len(panel.lines()) <= 5000
    assert panel.text.document().blockCount() <= 5000
    assert panel.lines()[-1].endswith("flood 5199")


# --------------------------------------------------------------------------------------------
# single instance, start with Windows, arguments
# --------------------------------------------------------------------------------------------

def test_single_instance_helpers(qapp):
    name = f"Local\\SeisZone.Test.{uuid.uuid4().hex}"
    first = app_mod.acquire_single_instance(name)
    assert first
    try:
        if sys.platform == "win32":
            assert app_mod.acquire_single_instance(name) is None
    finally:
        app_mod.release_single_instance(first)
    again = app_mod.acquire_single_instance(name)
    assert again
    app_mod.release_single_instance(again)

    shown = []
    sock_name = f"SeisZone.Test.{uuid.uuid4().hex}"
    server = app_mod.ActivationServer(lambda: shown.append(1), name=sock_name)
    # the second start is another process, like in real use (a QLocalSocket in a short-lived
    # Python thread of this process leaves Windows pipe notifications behind for a dead thread)
    code = ("import sys; sys.path.insert(0, sys.argv[1]);"
            "from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]);"
            "from seiszone.ui import app as a; sys.exit(0 if a.notify_running_instance(sys.argv[2], 5000) else 3)")
    child = subprocess.Popen([sys.executable, "-c", code, os.path.join(ROOT, "src"), sock_name])
    try:
        end = time.time() + 30
        while child.poll() is None and time.time() < end:
            qapp.processEvents()
            time.sleep(0.01)
        assert child.poll() == 0 and shown == [1]
    finally:
        if child.poll() is None:
            child.kill()
        server.close()
    assert not app_mod.notify_running_instance(f"SeisZone.Test.{uuid.uuid4().hex}", timeout_ms=200)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_autostart_registry(monkeypatch):
    import winreg
    parent = rf"Software\SeisZoneTest_{uuid.uuid4().hex[:8]}"
    key = parent + r"\Run"
    monkeypatch.setattr(app_mod, "RUN_KEY", key)
    try:
        assert app_mod.autostart_enabled() is False
        assert app_mod.set_autostart(True)
        assert app_mod.autostart_enabled() is True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, kind = winreg.QueryValueEx(k, app_mod.RUN_VALUE)
        # from source: pythonw with the source folder's starter ("-m seiszone" cannot start at logon)
        assert kind == winreg.REG_SZ and value.endswith("--minimized") and "run_seiszone.py" in value
        prog = app_mod.command_program(value)
        assert os.path.exists(prog) and os.path.basename(prog).lower().startswith("python")
        assert app_mod.set_autostart(False)
        assert app_mod.autostart_enabled() is False
        assert app_mod.set_autostart(False)  # removing twice is fine
    finally:
        for k in (key, parent):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
            except OSError:
                pass


def _fake_engine_module(monkeypatch, made):
    """seiszone.core.engine replaced by FakeEngine (no sockets, no listeners)."""
    def engine(config, state=None):
        made.append(fake_engine(config, state))
        return made[-1]
    mod = types.ModuleType("seiszone.core.engine")
    mod.Engine = engine
    monkeypatch.setitem(sys.modules, "seiszone.core.engine", mod)


def _isolate_main(monkeypatch):
    """Own single-instance names and restore the global hooks main() installs."""
    monkeypatch.setattr(app_mod, "MUTEX_NAME", f"Local\\SeisZone.Test.{uuid.uuid4().hex}")
    monkeypatch.setattr(app_mod, "ACTIVATE_NAME", f"SeisZone.Test.{uuid.uuid4().hex}")
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)


def test_main_starts_and_quits(qapp, monkeypatch):
    from seiszone.ui.main_window import MainWindow
    made, seen = [], {}
    _fake_engine_module(monkeypatch, made)
    _isolate_main(monkeypatch)

    def fake_exec(app):
        """Instead of the real event loop: let it run 1.5 s, then File > Quit."""
        pump(app, 1.5)
        wins = [w for w in app.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible()]
        seen["windows"] = len(wins)
        seen["channels"] = wins[0].view.channels() if wins else []
        if wins:
            wins[0].act_quit.trigger()
        pump(app, 0.2)
        seen["visible_after"] = any(w.isVisible() for w in wins)
        return 0
    monkeypatch.setattr(QApplication, "exec", fake_exec)
    try:
        assert app_mod.main(["--theme", "dark"]) == 0
    finally:
        qapp.setQuitOnLastWindowClosed(True)
    assert seen["windows"] == 1 and seen["channels"] and not seen["visible_after"]
    calls = [c[0] for c in made[0].calls]
    assert calls.count("start") == 1 and calls.count("stop") == 1
    assert Config().get("ui", "geometry")  # layout saved on quit


@pytest.mark.skipif(sys.platform != "win32", reason="named mutex")
def test_main_second_instance_only_signals(qapp, monkeypatch):
    made = []
    _fake_engine_module(monkeypatch, made)
    _isolate_main(monkeypatch)
    held = app_mod.acquire_single_instance(app_mod.MUTEX_NAME)  # "the first SeisZone"
    try:
        t = time.time()
        assert app_mod.main([]) == 0
        assert time.time() - t < 5
        assert made == []  # no engine, no window: the running instance keeps everything
    finally:
        app_mod.release_single_instance(held)


def test_external_close_filter(qapp):
    """The title-bar X (SC_CLOSE, then WM_CLOSE) is the user's; a bare WM_CLOSE (taskkill, the
    installer) asks SeisZone to end and is handled once, after the event."""
    called = []
    f = app_mod.ExternalCloseFilter(lambda: called.append(1))
    assert not f.on_message(app_mod.WM_SYSCOMMAND, app_mod.SC_CLOSE | 0x3)   # low bits: how it was asked
    assert not f.on_message(app_mod.WM_CLOSE, 0)
    pump(qapp, 0.05)
    assert called == []
    assert f.on_message(app_mod.WM_CLOSE, 0)
    assert not f.on_message(0x0200, 0)   # other messages pass
    pump(qapp, 0.05)
    assert called == [1]


def test_parse_args():
    args, rest = app_mod.parse_args(["--minimized", "--theme", "light", "-platform", "offscreen"])
    assert args.minimized and args.theme == "light" and not args.verbose
    assert rest == ["-platform", "offscreen"]


# --------------------------------------------------------------------------------------------
# review fixes
# --------------------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_autostart_refresh_never_takes_over_a_working_entry(monkeypatch):
    import winreg
    parent = rf"Software\SeisZoneTest_{uuid.uuid4().hex[:8]}"
    key = parent + r"\Run"
    monkeypatch.setattr(app_mod, "RUN_KEY", key)
    installed = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "notepad.exe")  # "SeisZone.exe"

    def put(value):
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, app_mod.RUN_VALUE, 0, winreg.REG_SZ, value)
    try:
        put(f'"{installed}" --minimized')          # the installed SeisZone's entry
        assert app_mod.refresh_autostart()       # a run from the source folder ...
        assert app_mod.current_autostart_command() == f'"{installed}" --minimized'   # ... keeps it
        put(r'"C:\gone\SeisZone.exe" --minimized')  # moved / uninstalled: repaired
        assert app_mod.refresh_autostart()
        assert app_mod.current_autostart_command() == app_mod.autostart_command()
        assert app_mod.command_program(app_mod.autostart_command()) and \
            os.path.exists(app_mod.command_program(app_mod.autostart_command()))
    finally:
        for k in (key, parent):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
            except OSError:
                pass


def test_quit_lets_a_running_export_finish(qapp, tmp_path):
    from seiszone.ui import export_dialog
    marker = tmp_path / "done.txt"

    def slow_export(log=print, **kw):
        log("writing ...")
        time.sleep(1.0)
        marker.write_text("complete")
        return [str(marker)], "ok"
    t = export_dialog.ExportThread(slow_export, {})
    export_dialog._RUNNING.add(t)
    t.start()
    try:
        assert app_mod.running_threads()[0] == [t]
        left = app_mod.finish_threads(probe_wait=0.1)
        assert left == [] and t.isFinished() and marker.read_text() == "complete"
    finally:
        t.wait()
        export_dialog._RUNNING.discard(t)


# --------------------------------------------------------------------------------------------
# channels shown by themselves as they arrive; the ones the user hid stay hidden
# --------------------------------------------------------------------------------------------

def _add_channels(hub, net, sta, chans, rate=5.0, seconds=20):
    from seiszone.core import mseed
    now = time.time()
    for loc, cha in chans:
        for rec in mseed.encode_records(net, sta, loc, cha, rate, now - seconds, list(range(int(rate * seconds)))):
            hub.add_record(rec, "test")
    return [f"{net}.{sta}.{loc}.{cha}" for loc, cha in chans]


def _window_with_hub(config):
    """A MainWindow on FakeEngine with an own, empty DataHub (the shared one stays untouched)."""
    from seiszone.core.hub import DataHub
    from seiszone.ui.main_window import MainWindow
    engine = fake_engine(config)
    engine.hub = DataHub(buffer_seconds=600)
    return MainWindow(engine, config)


def _close(qapp, w):
    w.prepare_quit()
    w.close()
    w.deleteLater()
    pump(qapp, 0.05)


def test_new_channels_are_shown_as_they_arrive(qapp, tmp_path):
    config = Config(str(tmp_path / "config.json"))
    config.set("view", "channels", ["KA.KAND.0J.HNZ"])     # what the user's first start had saved
    w = _window_with_hub(config)
    try:
        assert w.view.is_live() and w.act_live.isChecked()   # always starts live
        hub = w.engine.hub
        first = _add_channels(hub, "KA", "KAND", [("0J", "HNZ")])
        w.refresh()
        assert w.view.channels() == first
        # the other channels of the source arrive a moment later: shown too (it used to keep
        # only the first channel that had arrived)
        more = _add_channels(hub, "KA", "KAND", [("0J", "HNN"), ("0J", "HNE"), ("9J", "MNZ"), ("9J", "MNN"),
                                                 ("9J", "MNE")])
        w.refresh()
        assert w.view.channels() == first + sorted(more)
        assert w.channels.shown() == w.view.channels()
        assert Config(config.path).get("view", "channels") == w.view.channels()     # persisted
        # a second instrument with eight channels: filled up to twelve, the rest only listed
        other = _add_channels(hub, "WB", "KUM", [("0J", f"HN{c}") for c in "ZNE"] + [("9J", f"MN{c}") for c in "ZNE"]
                              + [("0J", "HN1"), ("0J", "HN2")])
        w.refresh()
        assert len(w.view.channels()) == 12
        assert w.view.channels()[6:] == sorted(other)[:6]
        w.refresh()
        assert len(w.view.channels()) == 12
    finally:
        _close(qapp, w)


def test_channels_the_user_hid_stay_hidden(qapp, tmp_path):
    from seiszone.ui.channels_panel import C_SHOW, C_SID
    config = Config(str(tmp_path / "config.json"))
    w = _window_with_hub(config)
    sids = _add_channels(w.engine.hub, "KA", "KAND", [("0J", "HNZ"), ("0J", "HNN"), ("0J", "HNE")])
    try:
        w.refresh()
        assert w.view.channels() == sorted(sids)
        proxy = w.channels.proxy
        row = [proxy.data(proxy.index(r, C_SID)) for r in range(proxy.rowCount())].index("KA.KAND.0J.HNN")
        assert proxy.setData(proxy.index(row, C_SHOW), Qt.Unchecked.value, Qt.CheckStateRole)   # the user unticks
        assert "KA.KAND.0J.HNN" not in w.view.channels()
        assert config.get("view", "hidden") == ["KA.KAND.0J.HNN"]
        w.refresh()
        assert "KA.KAND.0J.HNN" not in w.view.channels()
    finally:
        _close(qapp, w)
    # next start: the channels arrive again, the hidden one is not shown
    w = _window_with_hub(config)
    try:
        _add_channels(w.engine.hub, "KA", "KAND", [("0J", "HNZ"), ("0J", "HNN"), ("0J", "HNE")])
        w.refresh()
        assert w.view.channels() == ["KA.KAND.0J.HNE", "KA.KAND.0J.HNZ"]
        # ticking it again shows it and forgets the hiding
        proxy = w.channels.proxy
        row = [proxy.data(proxy.index(r, C_SID)) for r in range(proxy.rowCount())].index("KA.KAND.0J.HNN")
        assert proxy.setData(proxy.index(row, C_SHOW), Qt.Checked.value, Qt.CheckStateRole)
        assert w.view.channels()[-1] == "KA.KAND.0J.HNN" and config.get("view", "hidden") == []
        # "show these channels" from the Sources panel shows a hidden channel again, but does not
        # hide the others for good (they were not unticked)
        w.channels.proxy.setData(proxy.index(row, C_SHOW), Qt.Unchecked.value, Qt.CheckStateRole)
        assert config.get("view", "hidden") == ["KA.KAND.0J.HNN"]
        w.show_channels(["KA.KAND.0J.HNN"])
        assert w.view.channels() == ["KA.KAND.0J.HNN"] and config.get("view", "hidden") == []
    finally:
        _close(qapp, w)


def test_settings_default_window_applied_at_once(qapp, window, monkeypatch):
    monkeypatch.setattr(app_mod, "autostart_enabled", lambda: False)   # never the real registry
    monkeypatch.setattr(app_mod, "set_autostart", lambda on: True)
    assert window.view._window != 3600
    window.config.update_section("view", {"window_seconds": 3600})    # what Settings > OK saves
    window._settings_applied()
    assert window.view._window == 3600 and window.view.window_box.currentData() == 3600
    window.view.filter_box.setCurrentIndex(window.view.filter_box.findData("hp1"))   # saves the view
    assert Config(window.config.path).get("view", "window_seconds") == 3600
