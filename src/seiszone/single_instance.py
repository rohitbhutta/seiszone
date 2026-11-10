"""One SeisZone per Windows session: a named mutex (no Qt here, so the headless --engine / --add
of core.cli can use it too). The desktop application and the CLI commands that use the user's
config.json and state.json hold the same mutex, so they never run side by side."""

import ctypes
import logging
import sys

log = logging.getLogger("seiszone")

MUTEX_NAME = "Local\\SeisZone.SingleInstance"
ERROR_ALREADY_EXISTS = 183


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
