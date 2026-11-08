"""SeisZone desktop application: main(argv) -> exit code (plan.md 5.15).

One SeisZone per Windows session: a named mutex says whether one already runs; a second start
only asks the running one (over a local socket) to show its window and exits. The window can
hide to the tray so live data, the SeedLink server and recordings keep running.

"Start with Windows" is a value in HKCU\\...\\Run (per user, no administrator rights).
"""

import argparse
import ctypes
import logging
import os
import sys
import threading
import time

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .. import APP_NAME, ORGANIZATION, VERSION, logsetup
from . import theme

log = logging.getLogger("seiszone")

MUTEX_NAME = "Local\\SeisZone.SingleInstance"
ACTIVATE_NAME = "SeisZone.Activate"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = APP_NAME
ERROR_ALREADY_EXISTS = 183


# --------------------------------------------------------------------------------------------
# single instance
# --------------------------------------------------------------------------------------------

def acquire_single_instance(name=MUTEX_NAME):
    """Handle of the named mutex when this is the first instance, None when another instance
    already holds it. Keep the handle for the life of the process: Windows frees the mutex
    when the process ends, even after a crash. Outside Windows always 'first'."""
    if sys.platform != "win32":
        return True
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    handle = k32.CreateMutexW(None, False, name)
    if not handle:  # cannot tell - rather run twice than refuse to start
        log.warning("single-instance mutex unavailable (error %d)", ctypes.get_last_error())
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        release_single_instance(handle)
        return None
    return handle


def release_single_instance(handle):
    if sys.platform == "win32" and handle and handle is not True:
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        k32.CloseHandle(handle)


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
    """Command line written to the Run key: the installed exe, or pythonw -m seiszone in
    development (no console window either way)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return f'"{pyw if os.path.exists(pyw) else exe}" -m seiszone --minimized'


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
WM_SYSCOMMAND = 0x0112
SC_CLOSE = 0xF060


class ExternalCloseFilter(QAbstractNativeEventFilter):
    """Tells a close by the user from a close request of another program.

    The user's close (title-bar X, Alt+F4, taskbar "Close window") always arrives as
    WM_SYSCOMMAND/SC_CLOSE first and only hides SeisZone to the tray. `taskkill /PID` (and so the
    installer before it replaces the program) posts a bare WM_CLOSE: that means "end now", and
    SeisZone then stops its engine cleanly instead of being killed a few seconds later."""

    def __init__(self, on_external_close):
        super().__init__()
        self.on_external_close = on_external_close
        self._user_close = False

    def on_message(self, message, wparam):
        """True when the message is an external close request (it is then swallowed)."""
        if message == WM_SYSCOMMAND and (wparam & 0xFFF0) == SC_CLOSE:
            self._user_close = True
        elif message == WM_CLOSE:
            if self._user_close:
                self._user_close = False
                return False
            QTimer.singleShot(0, self.on_external_close)
            return True
        return False

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        return self.on_message(msg.message, msg.wParam or 0), 0


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
            menu.addAction("Show SeisZone", self.show_window)
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
            self.tray.showMessage(APP_NAME, "SeisZone keeps running in the tray", theme.app_icon(), 5000)
            self.config.set("ui", "tray_hint_shown", True)

    def quit(self):
        if self.window.isHidden() and self.window.active_recordings():
            self.show_window()
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
    app.setQuitOnLastWindowClosed(False)  # the tray keeps SeisZone alive; Controller.quit ends it
    log.info("%s %s starting (Python %s)", APP_NAME, VERSION, sys.version.split()[0])

    engine = Engine(config, state)
    try:
        engine.start()
    except Exception as e:
        log.exception("engine start failed")
        QMessageBox.critical(None, APP_NAME, f"The data engine could not start:\n\n{e}")
    if config.get("ui", "start_with_windows", False):
        set_autostart(True)  # keeps the Run entry pointing at this program after an update

    window = MainWindow(engine, config)
    ctl = Controller(app, config, state, engine, window)
    if (args.minimized or config.get("ui", "start_minimized", False)) and ctl.tray is not None:
        log.info("started in the tray")
    elif args.minimized:
        window.showMinimized()
    else:
        window.show()
    QTimer.singleShot(0, lambda: log.info("%s ready", APP_NAME))
    try:
        return app.exec()
    finally:
        ctl.shutdown()
        release_single_instance(mutex)
