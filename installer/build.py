"""One-command build of SeisZone: icon -> PyInstaller (build/dist/SeisZone) -> SeisZoneSetup.exe.

    python installer/build.py                  everything
    python installer/build.py --no-installer   only the program folder build/dist/SeisZone
    python installer/build.py --clean          start from an empty PyInstaller work folder

Steps:
  1. tools/make_icon.py draws assets/seiszone.ico and .png (so exe and installer match the app).
  2. PyInstaller, one folder, windowed, name SeisZone, entry installer/seiszone_launcher.py.
     All SeisZone modules are listed as hidden imports (some are imported only when a window or
     option is used); pyqtgraph's PySide6 UI templates and data files are collected explicitly
     because pyqtgraph imports them by name at run time. ObsPy and other large packages that
     are only used by the tests are excluded.
  3. Inno Setup 6 (ISCC.exe) compiles installer/SeisZone.iss -> build/dist/SeisZoneSetup.exe.

Nothing outside build/ is changed except the two icon files; build/screens and older setup
files stay where they are.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
BUILD = os.path.join(ROOT, "build")
WORK = os.path.join(BUILD, "work")
DIST = os.path.join(BUILD, "dist")
APP_DIR = os.path.join(DIST, "SeisZone")
EXE = os.path.join(APP_DIR, "SeisZone.exe")
ISS = os.path.join(ROOT, "installer", "SeisZone.iss")
VERSION_FILE = os.path.join(ROOT, "installer", "version_info.txt")
LAUNCHER = os.path.join(ROOT, "installer", "seiszone_launcher.py")
ICON = os.path.join(ROOT, "assets", "seiszone.ico")
SETUP = os.path.join(DIST, "SeisZoneSetup.exe")

EXCLUDES = ("obspy", "matplotlib", "tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "pytest",
            "pyqtgraph.examples")

sys.path.insert(0, SRC)
from seiszone import APP_NAME, VERSION  # noqa: E402


class BuildError(Exception):
    pass


def run(cmd, **kw):
    print(">", " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    rc = subprocess.call(cmd, cwd=ROOT, **kw)
    if rc != 0:
        raise BuildError(f"{os.path.basename(cmd[0])} failed with exit code {rc}")


def check_version():
    """The exe's file version and the installer must show seiszone.VERSION."""
    with open(VERSION_FILE, encoding="utf-8") as fh:
        text = fh.read()
    nums = [int(x) for x in VERSION.split(".")] + [0] * 4
    tup = "(%d, %d, %d, %d)" % tuple(nums[:4])
    if f"'FileVersion', '{VERSION}'" not in text or f"filevers={tup}" not in text:
        raise BuildError(f"{VERSION_FILE} does not carry version {VERSION} - update it first")


def seiszone_modules():
    """Every module of the seiszone package, e.g. seiszone.core.recorder, seiszone.ui.app."""
    mods = []
    for pkg in ("seiszone", "seiszone.core", "seiszone.ui"):
        folder = os.path.join(SRC, *pkg.split("."))
        mods.append(pkg)
        for path in sorted(glob.glob(os.path.join(folder, "*.py"))):
            name = os.path.splitext(os.path.basename(path))[0]
            if not name.startswith("__"):
                mods.append(f"{pkg}.{name}")
    return mods


def pyqtgraph_templates():
    """pyqtgraph's UI templates (plot / axis context menus, export dialog). pyqtgraph imports them
    by name at run time, so PyInstaller's analysis cannot see them. Newer versions have one
    `*Template_generic` module, older ones one per Qt binding: take generic + PySide6."""
    import pyqtgraph
    base = os.path.dirname(pyqtgraph.__file__)
    other_bindings = ("_pyqt5", "_pyqt6", "_pyside2")
    mods = []
    for path in glob.glob(os.path.join(base, "**", "*emplate*.py"), recursive=True):
        rel = os.path.relpath(os.path.splitext(path)[0], os.path.dirname(base))
        if "examples" not in rel.split(os.sep) and not rel.endswith(other_bindings):
            mods.append(rel.replace(os.sep, "."))
    return sorted(mods)


def pyinstaller(clean):
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise BuildError("PyInstaller is missing: python -m pip install -r requirements-dev.txt") from None
    # --specpath build: PyInstaller reads relative paths from the spec folder, so all are absolute
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
           "--name", APP_NAME, "--icon", ICON, "--version-file", VERSION_FILE,
           "--paths", SRC, "--workpath", WORK, "--distpath", DIST, "--specpath", BUILD, "--noupx",
           "--add-data", f"{os.path.join(ROOT, 'assets')}{os.pathsep}assets",
           "--add-data", f"{os.path.join(ROOT, 'docs', 'user_guide.md')}{os.pathsep}docs",
           "--collect-data", "pyqtgraph"]
    if clean:
        cmd.append("--clean")
    for mod in seiszone_modules() + pyqtgraph_templates():
        cmd += ["--hidden-import", mod]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(LAUNCHER)
    run(cmd)
    if not os.path.isfile(EXE):
        raise BuildError(f"PyInstaller finished but {EXE} is missing")
    # the exe must at least start and run its command line (catches a missing hidden import);
    # it has no console, so the answer comes back through --output
    out = os.path.join(WORK, "version_check.txt")
    run([EXE, "--version", "--output", out], timeout=120)
    with open(out, encoding="utf-8") as fh:
        answer = fh.read()
    if f"{APP_NAME} {VERSION}" not in answer:
        raise BuildError(f"{EXE} --version answered {answer!r}")


def find_iscc():
    candidates = [os.environ.get("ISCC", "")]
    for env in ("LOCALAPPDATA", "ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            sub = ("Programs", "Inno Setup 6") if env == "LOCALAPPDATA" else ("Inno Setup 6",)
            candidates.append(os.path.join(base, *sub, "ISCC.exe"))
    candidates.append(shutil.which("ISCC") or "")
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise BuildError("Inno Setup 6 (ISCC.exe) not found - install it from jrsoftware.org, "
                     "set ISCC=<path to ISCC.exe>, or use --no-installer")


def installer():
    started = time.time()
    run([find_iscc(), "/Q", f"/DAppVer={VERSION}", ISS])
    if not os.path.isfile(SETUP) or os.path.getmtime(SETUP) < started - 2:
        raise BuildError(f"Inno Setup finished but {SETUP} was not written")
    return SETUP


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--no-installer", action="store_true", help="build only build/dist/SeisZone")
    p.add_argument("--clean", action="store_true",
                   help="empty the PyInstaller work folder and cache before building")
    args = p.parse_args(argv)
    t0 = time.time()
    try:
        check_version()
        run([sys.executable, os.path.join(ROOT, "tools", "make_icon.py")])
        if args.clean and os.path.isdir(WORK):
            shutil.rmtree(WORK)
        pyinstaller(args.clean)
        print(f"program folder: {APP_DIR}")
        if not args.no_installer:
            setup = installer()
            print(f"installer: {setup} ({os.path.getsize(setup) / 1e6:.1f} MB)")
    except (BuildError, subprocess.TimeoutExpired, OSError) as e:
        print(f"BUILD FAILED: {e}", file=sys.stderr)
        return 1
    print(f"{APP_NAME} {VERSION} built in {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
