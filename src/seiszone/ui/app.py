"""SeisZone desktop application: main(argv) -> exit code (plan.md 5.15).

One SeisZone per Windows session: a named mutex says whether one already runs; a second start
only asks the running one (over a local socket) to show its window and exits. The window can
hide to the tray so live data, the SeedLink server and recordings keep running.

"Start with Windows" is a value in HKCU\\...\\Run (per user, no administrator rights). At start it
is only written when it is missing or names a program that no longer exists (a run from the
source folder or a build folder never takes over the installed SeisZone's entry).

Quit waits for an export that is still writing its files (after asking) and briefly for an
instrument detection; a detection that is still running then cannot stop the exit either.
"""

import argparse
import ctypes
import logging
import os
import shlex
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .. import APP_NAME, ORGANIZATION, VERSION, logsetup
from ..single_instance import MUTEX_NAME, acquire_single_instance, release_single_instance  # noqa: F401
from . import theme

log = logging.getLogger("seiszone")

ACTIVATE_NAME = "SeisZone.Activate"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "SeisZone"     # the old program name: the entry of 1.9.0 and older is taken over
OLD_EXE = "seiszone.exe"   # the program file of 1.9.0 and older (now SeismoVerse.exe)
SOURCE_SCRIPT = Path(__file__).resolve().parents[3] / "run_seiszone.py"   # the source folder's starter
PROBE_QUIT_WAIT = 5.0      # s Quit waits for an instrument detection that is still running
UPDATE_CHECK_DELAY_MS = 20000   # the daily update check waits until the sources are connecting


# --------------------------------------------------------------------------------------------
# single instance (seiszone.single_instance: also used by the headless CLI)
# --------------------------------------------------------------------------------------------


def notify_running_instance(name=ACTIVATE_NAME, timeout_ms=2000):
    """Ask the running SeisZone to show its window. True when it confirmed ("ok").

    Waits for the answer instead of disconnecting at once: a pipe closed right after the write
    can lose the message before the running instance has read it."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(b"show\n")
    sock.flush()
    reply = b""
    end = time.monotonic() + timeout_ms / 1000
    while b"ok" not in reply and time.monotonic() < end:
        if sock.bytesAvailable() or sock.waitForReadyRead(100):
            reply += bytes(sock.readAll())
        elif sock.state() != QLocalSocket.ConnectedState:
            break
    sock.disconnectFromServer()
    return b"ok" in reply


class ActivationServer(QObject):
    """Local socket of the first instance: a second start sends "show" here, gets "ok" back."""

    def __init__(self, on_show, name=ACTIVATE_NAME, parent=None):
        super().__init__(parent)
        self.on_show = on_show
        self.server = QLocalServer(self)
        QLocalServer.removeServer(name)  # a stale socket file after a crash (non-Windows)
        if not self.server.listen(name):
            log.warning("activation socket %s not available: %s", name, self.server.errorString())
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()
            sock.readyRead.connect(lambda s=sock, b=buf: self._read(s, b))
            sock.disconnected.connect(sock.deleteLater)
            # the second start writes at once: its bytes may already be here (no readyRead then)
            self._read(sock, buf)

    def _read(self, sock, buf):
        buf += bytes(sock.readAll())
        if b"show" in buf:
            buf.clear()
            sock.write(b"ok\n")
            sock.flush()
            self.on_show()

    def close(self):
        self.server.close()


# --------------------------------------------------------------------------------------------
# start with Windows
# --------------------------------------------------------------------------------------------

def autostart_command():
    """Command line written to the Run key: the installed exe, or pythonw with the source
    folder's run_seiszone.py in development (no console window either way; the seiszone
    package is not installed, so "-m seiszone" would not start at logon)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    pyw = pyw if os.path.exists(pyw) else exe
    if SOURCE_SCRIPT.exists():
        return f'"{pyw}" "{SOURCE_SCRIPT}" --minimized'
    return f'"{pyw}" -m seiszone --minimized'


def current_autostart_command():
    """The Run value now in the registry, or None."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            return str(winreg.QueryValueEx(key, RUN_VALUE)[0])
    except OSError:
        return None


def command_program(cmd):
    """The program a command line starts ('"C:\\x\\SeisZone.exe" --minimized' -> C:\\x\\SeisZone.exe)."""
    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError:
        return ""
    return parts[0].strip('"') if parts else ""


def refresh_autostart():
    """At start, with "Start with Windows" on: write the Run value only when it is missing or
    starts a program that no longer exists (SeisZone moved or reinstalled elsewhere). An
    installed SeisZone also replaces a development entry (Python). A value that works is never
    taken over by another copy (the source folder, a build folder)."""
    cur = current_autostart_command()
    if cur == autostart_command():
        return True
    program = command_program(cur) if cur else ""
    if not cur or not os.path.exists(program):
        return set_autostart(True)
    if getattr(sys, "frozen", False) and (os.path.basename(program).lower().startswith("python")
                                          or os.path.basename(program).lower() == OLD_EXE):
        return set_autostart(True)
    log.info("start with Windows keeps the existing entry (%s)", cur)
    return True


def set_autostart(enabled):
    """Add or remove SeisZone in HKCU Run. Returns True on success (errors are logged)."""
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        log.info("start with Windows %s", "on" if enabled else "off")
        return True
    except OSError as e:
        log.error("cannot change start with Windows: %s", e)
        return False


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------------------------
# unhandled exceptions
# --------------------------------------------------------------------------------------------

_last_box = [0.0]


def install_excepthook():
    """Unhandled exceptions are logged and shown in a non-fatal message box (at most one every
    10 s), so one bug in a panel never takes down acquisition or recordings."""
    def hook(etype, value, tb):
        if issubclass(etype, KeyboardInterrupt):
            sys.__excepthook__(etype, value, tb)
            return
        log.error("unhandled error", exc_info=(etype, value, tb))
        if QApplication.instance() is None or time.time() - _last_box[0] < 10:
            return
        _last_box[0] = time.time()
        QMessageBox.warning(None, APP_NAME, f"An unexpected error occurred:\n\n{value}\n\n"
                            f"{APP_NAME} keeps running. Details are in the log.")

    def thread_hook(args):
        if args.exc_type is not SystemExit:
            log.error("unhandled error in thread %s", getattr(args.thread, "name", "?"),
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = hook
    threading.excepthook = thread_hook


# --------------------------------------------------------------------------------------------
# a close request from outside (taskkill, the installer)
# --------------------------------------------------------------------------------------------

WM_CLOSE = 0x0010


class ExternalCloseFilter(QAbstractNativeEventFilter):
    """Tells a close by the user from a close request of another program.

    `taskkill /PID` (and so the installer before it replaces the program) POSTS a WM_CLOSE to
    each window: that means "end now", and SeisZone then stops its engine cleanly instead of
    being killed a few seconds later. When the user closes a window (title-bar X, Alt+F4, the
    taskbar's "Close window"), Windows SENDS the WM_CLOSE straight to the window procedure.

    Qt hands a posted message to native event filters twice - first from the message queue,
    then again in the window procedure - and a sent message once. So: a WM_CLOSE seen a second
    time, with no other message between, came through the queue and is another program's
    request (it is swallowed and SeisZone ends once, after the event); one seen once is the
    user's and closes only that window. (Version 1.1.0 waited for the WM_SYSCOMMAND/SC_CLOSE
    of a user's close instead - but Windows SENDS that one too and filters never see it, so
    closing any window, a Details or Label box included, ended SeisZone.)"""

    def __init__(self, on_external_close):
        super().__init__()
        self.on_external_close = on_external_close
        self._pending = None          # (hwnd) of a WM_CLOSE just seen once

    def on_message(self, message, hwnd=0):
        """True when the message is an external close request (it is then swallowed)."""
        if message != WM_CLOSE:
            self._pending = None
            return False
        if self._pending == hwnd:     # the second sighting: it came through the message queue
            self._pending = None
            QTimer.singleShot(0, self.on_external_close)
            return True
        self._pending = hwnd          # the queue's sighting of a posted one, or a user's (sent) close
        return False

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        return self.on_message(msg.message, int(msg.hWnd or 0)), 0


# --------------------------------------------------------------------------------------------
# the running application
# --------------------------------------------------------------------------------------------

class Controller(QObject):
    """Owns engine, window and tray icon; the only place that really quits."""

    def __init__(self, app, config, state, engine, window):
        super().__init__()
        self.app, self.config, self.state, self.engine, self.window = app, config, state, engine, window
        self._down = False
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(theme.app_icon(), self)
            self.tray.setToolTip(f"{APP_NAME} {VERSION}")
            menu = QMenu()
            menu.addAction("Show SeismoVerse", self.show_window)
            menu.addAction("Recording...", self.show_recording)
            menu.addSeparator()
            menu.addAction("Quit", self.quit)
            self.tray.setContextMenu(menu)
            self._menu = menu
            self.tray.activated.connect(self._tray_activated)
            self.tray.show()
        window.tray_available = self.tray is not None
        window.quit_callback = self.quit
        window.hiddenToTray.connect(self.hidden_to_tray)
        self.activation = ActivationServer(self.show_window, ACTIVATE_NAME, parent=self)
        app.aboutToQuit.connect(self.shutdown)
        self.close_filter = None
        if sys.platform == "win32":
            self.close_filter = ExternalCloseFilter(self.quit_external)
            app.installNativeEventFilter(self.close_filter)

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_window()

    def show_window(self):
        w = self.window
        if w.isMinimized():
            w.showNormal()
        else:
            w.show()
        w.setWindowState((w.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        w.raise_()
        w.activateWindow()

    def show_recording(self):
        self.show_window()
        self.window.open_record_dialog()

    def hidden_to_tray(self):
        if self.tray is not None and not self.config.get("ui", "tray_hint_shown", False):
            self.tray.showMessage(APP_NAME, "SeismoVerse keeps running in the tray", theme.app_icon(), 5000)
            self.config.set("ui", "tray_hint_shown", True)

    def quit(self):
        if self.window.isHidden() and self.window.active_recordings():
            self.show_window()
        if running_threads()[0]:
            self.show_window()
            answer = QMessageBox.question(
                self.window, APP_NAME, "An export is still being written.\n\nQuit SeismoVerse when it "
                "is finished?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer != QMessageBox.Yes:
                return
        if not self.window.confirm_quit():
            return
        self.window.prepare_quit()
        self.shutdown()
        self.window.close()
        if self.tray is not None:
            self.tray.hide()
        self.app.quit()

    def quit_external(self):
        """Another program asked SeisZone to end (taskkill, the installer): no question, the
        caller has already decided; active recordings continue at the next start."""
        if self._down:
            return
        log.info("%s was asked to close by another program", APP_NAME)
        self.window.prepare_quit()
        self.shutdown()
        self.window.close()
        if self.tray is not None:
            self.tray.hide()
        self.app.quit()

    def shutdown(self):
        """Stop the engine and save the state once (also on Windows log-off)."""
        if self._down:
            return
        self._down = True
        log.info("%s stopping", APP_NAME)
        if self.close_filter is not None:  # Qt holds a plain pointer to it: unhook before it goes
            self.app.removeNativeEventFilter(self.close_filter)
        try:
            self.engine.stop()
        except Exception:
            log.exception("engine stop failed")
        try:
            self.state.flush()
        except Exception:
            log.exception("state save failed")
        self.activation.close()
        finish_threads()


def running_threads():
    """([export threads], [detection threads]) that are still running."""
    from . import add_source_dialog, export_dialog
    return ([t for t in list(export_dialog._RUNNING) if t.isRunning()],
            [t for t in list(add_source_dialog._RUNNING) if t.isRunning()])


def finish_threads(probe_wait=None):
    """Before the interpreter ends (a QThread destroyed while it runs aborts the process): wait
    until every export has written its files, and up to `probe_wait` s for detections. Returns
    the threads still running (then main() ends the process without tearing Python down)."""
    exports, probes = running_threads()
    for t in exports:
        log.info("waiting for an export to finish its files")
        t.wait()
    wait = PROBE_QUIT_WAIT if probe_wait is None else probe_wait
    for t in probes:
        t.requestInterruption()
        t.wait(max(0, int(wait * 1000)))
    return [t for group in running_threads() for t in group]


def parse_args(argv):
    p = argparse.ArgumentParser(prog="seiszone", description=f"{APP_NAME} {VERSION}")
    p.add_argument("--minimized", action="store_true", help="start in the tray (no window)")
    p.add_argument("--verbose", action="store_true", help="debug messages in the log")
    p.add_argument("--theme", choices=("dark", "light"), help="colour theme for this start")
    return p.parse_known_args(argv)


def _set_app_id():
    """Own taskbar identity, so Windows shows the SeisZone icon (also when run with pythonw)."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{ORGANIZATION}.{APP_NAME}")
        except (AttributeError, OSError):
            pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args, qt_args = parse_args(argv)
    logsetup.setup(verbose=args.verbose)
    install_excepthook()
    _set_app_id()

    app = QApplication.instance() or QApplication([sys.argv[0] if sys.argv else APP_NAME] + qt_args)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANIZATION)
    app.setApplicationVersion(VERSION)

    mutex = acquire_single_instance(MUTEX_NAME)
    if mutex is None:
        reached = notify_running_instance(ACTIVATE_NAME)
        log.info("%s is already running - %s", APP_NAME,
                 "its window was brought forward" if reached else "it did not answer")
        return 0

    from .. import config as cfg_mod
    from .main_window import MainWindow
    from ..core.engine import Engine  # written in parallel; imported only when the app really starts

    config = cfg_mod.Config()
    state = cfg_mod.State()
    theme.apply(app, args.theme or config.get("ui", "theme", "dark") or "dark")
    app.setQuitOnLastWindowClosed(False)  # the tray keeps SeismoVerse alive; Controller.quit ends it
    log.info("%s %s starting (Python %s)", APP_NAME, VERSION, sys.version.split()[0])

    engine = Engine(config, state)
    try:
        engine.start()
    except Exception as e:
        log.exception("engine start failed")
        QMessageBox.critical(None, APP_NAME, f"The data engine could not start:\n\n{e}")
    if config.get("ui", "start_with_windows", False):
        refresh_autostart()  # repairs a missing / stale entry; never takes over a working one

    window = MainWindow(engine, config)
    ctl = Controller(app, config, state, engine, window)
    if (args.minimized or config.get("ui", "start_minimized", False)) and ctl.tray is not None:
        log.info("started in the tray")
    elif args.minimized:
        window.showMinimized()
    else:
        window.show()
    problems = [x for x in (config.load_error, state.load_error) if x]
    if problems:
        QTimer.singleShot(0, lambda: QMessageBox.warning(
            window if window.isVisible() else None, APP_NAME,
            "SeismoVerse could not read its files at start:\n\n" + "\n\n".join(problems)
            + ("\n\nSettings are not saved in this session; start SeismoVerse again later."
               if config.read_only else "")
            + ("\n\nRecordings in progress and SeedLink resume positions were not restored."
               if state.load_error else "")))
    QTimer.singleShot(0, lambda: log.info("%s ready", APP_NAME))
    QTimer.singleShot(UPDATE_CHECK_DELAY_MS, window.check_updates_at_start)   # once a day, if switched on
    try:
        code = app.exec()
    finally:
        ctl.shutdown()
        release_single_instance(mutex)
    if running_threads()[1]:   # a detection still waits for the network: do not tear Python down
        log.info("%s ends while a detection is still running", APP_NAME)
        logging.shutdown()
        os._exit(code)
    return code
