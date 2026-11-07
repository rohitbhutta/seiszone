"""Logging for SeisZone: logger "seiszone" -> rotating file in paths.log_dir() and an in-memory
list for the log panel.

    logsetup.setup(verbose=False, console=False)
    logsetup.LOG_LINES   collections.deque of (epoch, levelname, message), newest last
    logsetup.counter()   number of lines ever added (the log panel shows only new ones)
"""

import collections
import logging
import logging.handlers
import os
import sys
import threading

from . import paths

LOG_LINES = collections.deque(maxlen=5000)
_lock = threading.Lock()
_count = 0
FORMAT = "%(asctime)s %(levelname)s %(message)s"


class MemoryHandler(logging.Handler):
    def emit(self, record):
        global _count
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
        except Exception:
            msg = str(record.msg)
        with _lock:
            LOG_LINES.append((record.created, record.levelname, msg))
            _count += 1


def counter():
    with _lock:
        return _count


def lines_since(n):
    """(lines added after counter value n, new counter value)."""
    with _lock:
        new = _count - n
        if new <= 0:
            return [], _count
        return list(LOG_LINES)[-min(new, len(LOG_LINES)):], _count


def setup(verbose=False, console=False, folder=None):
    log = logging.getLogger("seiszone")
    log.handlers.clear()
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False
    folder = folder or paths.log_dir()
    try:
        os.makedirs(folder, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(os.path.join(folder, "seiszone.log"),
                                                  maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(logging.Formatter(FORMAT))
        log.addHandler(fh)
    except OSError:
        pass
    log.addHandler(MemoryHandler())
    if console and sys.stdout:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(FORMAT))
        log.addHandler(sh)
    return log
