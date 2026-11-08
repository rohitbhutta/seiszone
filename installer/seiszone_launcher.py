"""Entry point of the built SeisZone.exe (PyInstaller).

Same choice as `python -m seiszone`: the command-line options of the headless engine
(--engine, --probe, --add, --list, --version) go to seiszone.core.cli.main, everything else
starts the desktop application (seiszone.ui.app.main).

The exe is built "windowed" (no console window flashes up for the operator), which leaves
sys.stdout and sys.stderr as None. For the command-line options SeisZone therefore attaches to
the console it was started from, so `SeisZone.exe --probe 10.8.0.182:18000` prints in cmd /
PowerShell; without a console (and for the desktop application) output goes to os.devnull so a
stray print can never crash the program. The real log is the file log (seiszone.logsetup).
"""

import os
import sys

if not getattr(sys, "frozen", False):  # run from source: python installer/seiszone_launcher.py
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

CLI_FLAGS = ("--engine", "--probe", "--add", "--list", "--version")


def wants_cli(argv):
    """True when argv holds one of the headless options (also in the --probe=HOST:PORT form)."""
    return any(a.split("=", 1)[0] in CLI_FLAGS for a in argv)


def _attach_console():
    """Point stdout/stderr at the parent console if there is one (Windows windowed exe)."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            con = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
            sys.stdout = sys.stdout or con
            sys.stderr = sys.stderr or con
    except (AttributeError, OSError):  # not Windows, or no console to attach to
        pass


def _null_streams():
    """Give a windowed program harmless stdout/stderr instead of None."""
    if sys.stdout is None or sys.stderr is None:
        null = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or null
        sys.stderr = sys.stderr or null


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if wants_cli(argv):
        _attach_console()
        _null_streams()
        from seiszone.core import cli
        return cli.main(argv)
    _null_streams()
    from seiszone.ui import app
    return app.main(argv)


if __name__ == "__main__":
    sys.exit(main())
