import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
DATA = os.path.join(ROOT, "tests", "data")

# never touch the real %APPDATA% / Documents while testing
os.environ.setdefault("SEISZONE_HOME", tempfile.mkdtemp(prefix="seiszone_test_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


_STATUS = []


@pytest.fixture(autouse=True)
def _delete_closed_widgets():
    """After each test: run Qt's pending deleteLater()s, so the windows (and the data their
    engines hold) of finished tests are freed instead of piling up for the whole run."""
    yield
    if "PySide6.QtWidgets" in sys.modules:
        from PySide6.QtCore import QCoreApplication, QEvent
        if QCoreApplication.instance() is not None:
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def pytest_sessionfinish(session, exitstatus):
    _STATUS.append(int(exitstatus))


def pytest_unconfigure(config):
    """End the test process here, after pytest has written its report: with Qt windows, pyqtgraph
    items and worker threads of many tests still around, the interpreter's own teardown now and
    then crashed the process after every test had passed (an access violation on exit, seen also
    before version 1.1.0). The test result is the exit code, as usual."""
    if "PySide6.QtWidgets" not in sys.modules or not _STATUS:
        return
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":   # os._exit still unloads the DLLs, where Qt's leftovers crash
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess(k32.GetCurrentProcess(), _STATUS[-1])
    os._exit(_STATUS[-1])
