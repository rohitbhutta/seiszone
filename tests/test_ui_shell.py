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
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton

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
    assert window.windowTitle() == f"SeismoVerse {VERSION}"
    texts = [a.text() for a in window.toolbar.actions() if a.text()]
    assert texts == ["Add source", "Remove", "Reconnect", "Open viewer", "Record", "Export", "Data server", "Settings", "About"]
    assert window.centralWidget() is window.channels           # the waveforms are in viewer windows
    window.show()
    pump(qapp, 2.0)
    assert window.channels.model.rows and window.viewers == []
    assert "1/1 sources receiving" in window.st["sources"].text()
    assert "Gecko links: 1" in window.st["gecko"].text()
    assert ":18000" in window.st["server"].text() and "1 client" in window.st["server"].text()
    assert window.st["recording"].text() == "Not recording"
    assert window.st["disk"].text().startswith("Free 100.0 GB")


def test_toolbar_dialogs_open_and_close(qapp, window):
    window.show()
    pump(qapp, 0.5)
    for name, opener in (("about", window.open_about), ("record", window.open_record_dialog),
                         ("export", window.open_export_dialog), ("settings", window.open_settings)):
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

    def info_color():
        doc = panel.text.document()
        for i in range(doc.blockCount()):
            block = doc.findBlockByNumber(i)
            if f"{marker} info" in block.text():
                return block.begin().fragment().charFormat().foreground().color().name()
    assert info_color() == theme.DARK["text"]
    try:
        theme.apply(qapp, "light")          # the old lines take the light theme's text colour too
        pump(qapp, 0.05)
        assert info_color() == theme.LIGHT["text"]
    finally:
        theme.apply(qapp, "dark")
        pump(qapp, 0.05)
    assert info_color() == theme.DARK["text"]


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
        if wins:
            v = wins[0].open_viewer(["WB.KUM.0J.HNZ", "WB.KUM.0J.HNN"])
            seen["channels"] = v.view.channels() if v else []
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
    assert [v["sids"] for v in Config().get("viewers")] == [["WB.KUM.0J.HNZ", "WB.KUM.0J.HNN"]]


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
    """A WM_CLOSE the filter sees once was SENT (the user closed a window: only that window
    closes); one seen twice in a row came through the message queue (taskkill, the installer)
    and asks SeisZone to end - handled once, after the event."""
    called = []
    f = app_mod.ExternalCloseFilter(lambda: called.append(1))
    assert not f.on_message(app_mod.WM_CLOSE, 11)      # the user closes window 11 (sent: seen once)
    assert not f.on_message(0x0200, 11)                 # ... then life goes on
    assert not f.on_message(app_mod.WM_CLOSE, 11)       # and closes it again later: still the user's
    assert not f.on_message(0x000F, 11)
    assert not f.on_message(app_mod.WM_CLOSE, 12)       # another window, once
    assert not f.on_message(app_mod.WM_CLOSE, 13)
    pump(qapp, 0.05)
    assert called == []
    assert not f.on_message(app_mod.WM_CLOSE, 14)       # posted: from the queue ...
    assert f.on_message(app_mod.WM_CLOSE, 14)           # ... and again in the window: another program
    pump(qapp, 0.05)
    assert called == [1]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows messages")
def test_external_close_filter_with_real_windows_messages(qapp):
    """The real thing: a close sent the way Windows does for the title-bar X / Alt+F4 closes only
    that window; a posted WM_CLOSE (what taskkill does) asks SeisZone to end."""
    import ctypes
    from ctypes import wintypes
    from PySide6.QtWidgets import QDialog
    u32 = ctypes.WinDLL("user32")
    u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    called = []
    f = app_mod.ExternalCloseFilter(lambda: called.append(1))
    qapp.installNativeEventFilter(f)
    try:
        if qapp.platformName() != "windows":
            pytest.skip("needs real windows (QT_QPA_PLATFORM=windows)")
        user, other = QDialog(), QDialog()
        user.show()
        other.show()
        pump(qapp, 0.2)
        u32.SendMessageW(int(user.winId()), 0x0112, 0xF060, 0)      # WM_SYSCOMMAND / SC_CLOSE
        pump(qapp, 0.2)
        assert not user.isVisible() and called == []                # only that window closed
        u32.PostMessageW(int(other.winId()), app_mod.WM_CLOSE, 0, 0)
        pump(qapp, 0.2)
        assert called == [1] and other.isVisible()                  # swallowed; SeisZone would end
    finally:
        qapp.removeNativeEventFilter(f)


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
# viewer windows: opened from the ticked channels, saved at quit, opened again at the next start
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


def test_viewers_open_from_the_ticked_channels_and_come_back(qapp, tmp_path):
    from seiszone.ui.channels_panel import C_SHOW, C_SID
    from seiszone.ui.viewer_window import ViewerWindow
    config = Config(str(tmp_path / "config.json"))
    w = _window_with_hub(config)
    _add_channels(w.engine.hub, "KA", "KAND", [("0J", "HNZ"), ("0J", "HNN"), ("0J", "HNE")])
    gecko = _add_channels(w.engine.hub, "SS", "SMR03", [("00", "HNZ")])
    try:
        w.show()
        pump(qapp, 0.2)
        w.refresh()
        assert w.viewers == []                                  # no viewer by itself
        assert w.open_viewer() is None and "Tick channels" in w.statusBar().currentMessage()
        proxy = w.channels.proxy
        rows = [proxy.data(proxy.index(r, C_SID)) for r in range(proxy.rowCount())]
        for sid in ("KA.KAND.0J.HNZ", "KA.KAND.0J.HNE"):
            assert proxy.setData(proxy.index(rows.index(sid), C_SHOW), Qt.Checked.value, Qt.CheckStateRole)
        assert Config(config.path).get("view", "channels") == ["KA.KAND.0J.HNZ", "KA.KAND.0J.HNE"]
        assert "2 ticked" in w.channels.count.text()
        assert not hasattr(w.channels, "view_button")           # the panel has no Open viewer button
        w.act_view.trigger()                                    # toolbar Open viewer: the ticked channels
        assert len(w.viewers) == 1
        v1 = w.viewers[0]
        assert isinstance(v1, ViewerWindow) and v1.parent() is None and v1.isVisible()
        assert v1.view.channels() == ["KA.KAND.0J.HNE", "KA.KAND.0J.HNZ"]     # table order
        assert "KA.KAND" in v1.windowTitle()
        # a double-click opens a second viewer with that channel alone
        w.channels._double_clicked(proxy.index(rows.index(gecko[0]), C_SID))
        assert len(w.viewers) == 2 and w.viewers[1].view.channels() == gecko
        # "Add to viewer" from the right-click menu
        assert w.channels.viewer_titles() == [v1.title(), w.viewers[1].title()]
        w.add_to_viewer(["KA.KAND.0J.HNN"], 1)
        assert w.viewers[1].view.channels() == gecko + ["KA.KAND.0J.HNN"]
        menu = w.channels.build_menu(["KA.KAND.0J.HNN"])
        add = [a for a in menu.actions() if a.text() == "Add to viewer"][0]
        assert add.menu().isEnabled() and len(add.menu().actions()) == 2
        # the main window's timer drives the viewers
        v1.view._version += 1
        w._refresh_viewers()
        assert all(tr.version == v1.view._version for tr in v1.view._traces)
        # the Window menu lists them
        w._fill_window_menu()
        assert [a.text() for a in w.window_menu.actions() if a.text()][1:3] == [v1.title(), w.viewers[1].title()]
        # settings of a viewer are saved; closing one by the user forgets it
        v1.view.set_filter("bp1-10")
        v1.view.set_trace_color("KA.KAND.0J.HNZ", "#ff0000")
        second = w.viewers[1]
        second.close()
        pump(qapp, 0.1)
        assert w.viewers == [v1]
        w._save_timer.timeout.emit()
        saved = Config(config.path).get("viewers")
        assert [x["sids"] for x in saved] == [["KA.KAND.0J.HNE", "KA.KAND.0J.HNZ"]]
    finally:
        _close(qapp, w)
    # quitting keeps the open viewer; the next start opens it again with its settings
    saved = Config(config.path).get("viewers")
    assert len(saved) == 1 and saved[0]["filter"] == "bp1-10" and saved[0]["geometry"]
    # by default the next start shows the main window alone; what is open at the quit is saved
    w = _window_with_hub(config)
    try:
        w.show()
        pump(qapp, 0.3)
        assert w.viewers == []
        w.open_viewer(state=saved[0])
    finally:
        _close(qapp, w)
    assert len(Config(config.path).get("viewers")) == 1
    config.set("ui", "reopen_viewers", True)            # Settings: open them again
    w = _window_with_hub(config)
    try:
        assert w.viewers == []
        w.show()
        pump(qapp, 0.3)
        assert len(w.viewers) == 1
        v = w.viewers[0].view
        assert v.channels() == ["KA.KAND.0J.HNE", "KA.KAND.0J.HNZ"] and v.filter_on() and v.filter_key() == "bp1-10"
        assert v.trace_settings("KA.KAND.0J.HNZ")["color"] == "#ff0000"
        assert w.channels.shown() == ["KA.KAND.0J.HNZ", "KA.KAND.0J.HNE"]      # the ticks are kept too
        w.close_viewers()
        pump(qapp, 0.1)
        assert w.viewers == []
    finally:
        _close(qapp, w)
    assert Config(config.path).get("viewers") == []


def test_sources_panel_and_viewer_open_the_other_windows(qapp, window, monkeypatch):
    window.show()
    pump(qapp, 0.3)
    window.sources.channelsRequested.emit(["WB.KUM.0J.HNZ", "WB.KUM.0J.HNN"])     # "Open in a viewer"
    assert len(window.viewers) == 1
    v = window.viewers[0]
    assert v.view.channels() == ["WB.KUM.0J.HNZ", "WB.KUM.0J.HNN"]
    rec = v.open_record()                         # Record these channels: the Recordings window, ticked
    assert rec is window.dialogs["record"] and rec.chosen_radio.isChecked()
    assert rec.ticked() == ["WB.KUM.0J.HNN", "WB.KUM.0J.HNZ"] and rec.parent() is None
    exp = v.open_export()                         # Export with the viewer's channels and time range
    assert exp is window.dialogs["export"] and exp.view_range == pytest.approx(v.view.visible_range())
    exp.reject()
    rec.reject()
    dup = v.duplicate()                           # Ctrl+D: another viewer with the same settings
    assert len(window.viewers) == 2 and dup.view.channels() == v.view.channels()
    pump(qapp, 0.1)
    # an Export window already open is replaced by one with the new viewer's channels and range
    first = window.open_export_dialog()
    second = dup.open_export()
    pump(qapp, 0.1)
    assert second is not first and window.dialogs["export"] is second
    assert second.view_range == pytest.approx(dup.view.visible_range())
    second.reject()
    # a filter saved or deleted in one viewer: every other viewer looks it up again
    seen = []
    monkeypatch.setattr(dup.view, "filters_changed", lambda: seen.append("dup"))
    monkeypatch.setattr(v.view, "filters_changed", lambda: seen.append("v"))
    v.view.filtersSaved.emit()
    assert seen == ["dup"]


def test_settings_default_window_is_for_new_viewers(qapp, window, monkeypatch):
    monkeypatch.setattr(app_mod, "autostart_enabled", lambda: False)   # never the real registry
    monkeypatch.setattr(app_mod, "set_autostart", lambda on: True)
    window.config.update_section("view", {"window_seconds": 3600})    # what Settings > OK saves
    window._settings_applied()
    v = window.open_viewer(["WB.KUM.0J.HNZ"])
    assert v.view.pps() == 0.5            # the time scale nearest to an hour on the plot


def test_data_server_window(qapp, window, tmp_path):
    """The Data server window: off by default, switched on (saved, applied), clients and control."""
    from seiszone.ui.server_window import DataServerWindow
    assert window.act_server in window.toolbar.actions()
    dlg = window.open_data_server()
    try:
        assert isinstance(dlg, DataServerWindow) and not dlg.isModal()
        assert not dlg.enabled.isChecked() and dlg.port.text() == "1567"
        assert "off" in dlg.state.text()
        dlg.port.setText("99999")
        assert not dlg.apply() and "1 to 65535" in dlg.error.text()
        dlg.port.setText("1567")
        dlg.enabled.setChecked(True)
        dlg.max_rate.setValue(500)
        assert dlg.apply()
        assert window.config.get("gcf_server") == {"enabled": True, "bind": "0.0.0.0", "port": 1567, "max_rate": 500}
        assert ("apply_settings",) in window.engine.calls
        dlg.refresh()
        assert "Server active on UDP port 1567 - 1 client" in dlg.state.text()
        assert dlg.clients.rowCount() == 1 and dlg.clients.item(0, 0).text() == "192.168.1.100:1567"
        assert dlg.clients.item(0, 1).text() == "B"
        assert "Added client 192.168.1.100:1567" in dlg.control.toPlainText()
        assert ":1567" in dlg.how.text() and "Add UDP Server" in dlg.how.text()
        window._update_status(window.engine.status(), [])
        assert "Data server :1567 - 1 client" in window.st["gcf_server"].text()
        # the SeedLink tab: on by default (port 18000), its clients and what they asked for
        assert dlg.tabs.count() == 2 and "SeedLink" in dlg.tabs.tabText(1)
        assert dlg.sl_enabled.isChecked() and dlg.sl_port.text() == "18000"
        assert "Server active on TCP port 18000 - 1 client" in dlg.sl_state.text()
        assert dlg.sl_clients.item(0, 0).text() == "127.0.0.1:50555" and dlg.sl_clients.item(0, 3).text() == "99"
        assert "streaming *" in dlg.sl_control.toPlainText()
        assert ":18000" in dlg.sl_how.text()
        # the disk buffer: 2 days by default, its size, folder and how far back it reaches
        assert dlg.sl_days.value() == 2
        text = dlg.sl_buffer.text()
        assert "Disk buffer: 2.6 GB" in text and "ServerBuffer" in text and "Kept 2 days" in text
        assert "recordings are never touched" in text
        # a wrong SeedLink port shows its tab and says why; nothing is saved
        dlg.tabs.setCurrentIndex(0)
        dlg.sl_port.setText("58772")                      # the Gecko receiver's port
        assert not dlg.apply() and dlg.tabs.currentIndex() == 1 and "Gecko" in dlg.error.text()
        dlg.sl_port.setText("abc")
        assert not dlg.apply() and "not a number" in dlg.error.text()
        assert window.config.get("seedlink_server")["port"] == 18000
        # switched off and on another port: saved (Listen on / Organization kept) and applied
        org = window.config.get("seedlink_server").get("organization")
        dlg.sl_port.setText("18001")
        dlg.sl_enabled.setChecked(False)
        assert dlg.apply()
        s = window.config.get("seedlink_server")
        assert s["enabled"] is False and s["port"] == 18001 and s["organization"] == org and s["bind"]
        dlg.refresh()
        assert "SeedLink server is off" in dlg.sl_state.text() and dlg.sl_clients.rowCount() == 0
        dlg.sl_enabled.setChecked(True)
        dlg.sl_port.setText("18000")
        assert dlg.apply() and window.config.get("seedlink_server")["enabled"] is True
        dlg.sl_days.setValue(5)
        assert dlg.apply() and window.config.get("seedlink_server")["buffer_days"] == 5
        dlg.refresh()
        assert "Kept 5 days" in dlg.sl_buffer.text()
        dlg.sl_days.setValue(0)
        assert dlg.sl_days.text() == "off (memory only)"
        assert dlg.apply() and window.config.get("seedlink_server")["buffer_days"] == 0
        dlg.refresh()
        assert dlg.sl_buffer.text().startswith("Disk buffer off")
        dlg.sl_days.setValue(2)
        assert dlg.apply()
    finally:
        dlg.close()


# --------------------------------------------------------------------------------------------
# 2.0.0: the program is named SeismoVerse
# --------------------------------------------------------------------------------------------

def test_about_shows_the_name_author_and_supporter_only(window, qapp):
    from seiszone import APP_NAME, AUTHOR, SUPPORTED_BY
    window.open_about()
    about = window.dialogs["about"]
    text = about.title.text()
    assert APP_NAME == "SeismoVerse" and about.windowTitle() == "About SeismoVerse"
    assert f"version {VERSION}" in text and "SeismoVerse" in text
    assert f"<b>Author:</b> {AUTHOR}" in text and AUTHOR == "Arnab Kole"
    assert f"<b>Supported:</b> {SUPPORTED_BY}" in text and SUPPORTED_BY == "Rohit Santra"
    everything = " ".join(w.text() for w in about.findChildren(QLabel))
    for gone in ("Open standards", "Folders", "Settings and state", "StationXML", "Guralp", "Kelunji"):
        assert gone not in everything, gone
    assert not [b for b in about.findChildren(QPushButton) if b.text() == "Open"]
    about.reject()


def test_the_folders_keep_the_old_name(monkeypatch, tmp_path):
    from seiszone import DATA_FOLDER, paths
    monkeypatch.delenv("SEISZONE_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert DATA_FOLDER == "SeisZone"
    assert paths.config_dir() == str(tmp_path / "Roaming" / "SeisZone")
    assert paths.log_dir() == str(tmp_path / "Local" / "SeisZone" / "logs")
    assert paths.server_buffer_dir() == str(tmp_path / "Local" / "SeisZone" / "ServerBuffer")
    assert os.path.basename(paths.documents_dir()) == "SeisZone"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_autostart_entry_of_the_old_program_is_taken_over(monkeypatch, tmp_path):
    """The installed SeismoVerse.exe replaces the "SeisZone" Run value that starts the old
    SeisZone.exe, also while that file still exists."""
    import winreg
    parent = rf"Software\SeisZoneTest_{uuid.uuid4().hex[:8]}"
    key = parent + r"\Run"
    monkeypatch.setattr(app_mod, "RUN_KEY", key)
    old = tmp_path / "SeisZone.exe"
    old.write_bytes(b"")
    new = tmp_path / "SeismoVerse.exe"
    new.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(new))
    assert app_mod.RUN_VALUE == "SeisZone"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, app_mod.RUN_VALUE, 0, winreg.REG_SZ, f'"{old}" --minimized')
        assert app_mod.refresh_autostart()
        assert app_mod.current_autostart_command() == f'"{new}" --minimized'
    finally:
        for k in (key, parent):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
            except OSError:
                pass
