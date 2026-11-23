"""Updates in the user interface (core/updater.py does the work).

  * UpdateCheck: asks GitHub for the newest release in a worker thread and reports back in the
    Qt thread (finished(release or None, error text)).
  * UpdateDialog: "SeismoVerse X is available (you have Y)" with What's new and the buttons
    Download and install / Later / Skip this version. Download and install downloads the
    installer (progress, Cancel), checks its SHA-256 and starts it; Windows asks for permission,
    setup closes SeismoVerse, updates it and opens it again.
  * check_for_updates(): Help > Check for updates (manual: always answers) and the automatic
    check once a day at start (only when a newer, not skipped version exists does a window open).

config["updates"]: {"check": bool, "last_check": time, "skip": "version not to offer again"}.
"""

import logging
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QTextBrowser,
                               QVBoxLayout)

from .. import APP_NAME, VERSION
from ..core import updater
from . import theme

log = logging.getLogger("seiszone")


class UpdateCheck(QObject):
    """One check for the newest release, in a worker thread."""

    finished = Signal(object, str)          # Release or None, error text ("" when it worked)

    def start(self):
        threading.Thread(target=self._run, name="update-check", daemon=True).start()

    def _run(self):
        try:
            rel = updater.latest_release()
        except updater.UpdateError as e:
            self.finished.emit(None, str(e))
        except Exception as e:  # noqa: BLE001 - never break the program over an update check
            log.exception("update check failed")
            self.finished.emit(None, f"The update check did not work ({e}).")
        else:
            self.finished.emit(rel, "")


class _Download(QObject):
    progress = Signal(int, int)
    finished = Signal(str, str)             # path, error text

    def __init__(self, release):
        super().__init__()
        self.release = release
        self.stop = False

    def start(self):
        threading.Thread(target=self._run, name="update-download", daemon=True).start()

    def _run(self):
        try:
            path = updater.download(self.release, progress=lambda d, t: self.progress.emit(int(d), int(t)),
                                    cancelled=lambda: self.stop)
        except updater.UpdateError as e:
            self.finished.emit("", str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("update download failed")
            self.finished.emit("", f"The download did not work ({e}).")
        else:
            self.finished.emit(path, "")


class UpdateDialog(QDialog):
    def __init__(self, release, config, parent=None):
        super().__init__(parent)
        self.release = release
        self.config = config
        self.job = None
        self.setWindowTitle(f"{APP_NAME} update")
        self.setWindowIcon(theme.app_icon())
        self.resize(560, 440)
        c = theme.COLORS
        title = QLabel(f"<span style='font-size:13pt; font-weight:600'>{APP_NAME} {release.version} is available"
                       f"</span><br><span style='color:{c['muted']}'>You have version {VERSION}.</span>")
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        if release.notes.strip():
            self.notes.setMarkdown(release.notes)
        else:
            self.notes.setPlainText("No notes were published for this version.")
        size = f" ({release.setup_size / 1e6:.0f} MB)" if release.setup_size else ""
        self.info = QLabel(f"Download and install{size}: Windows asks for permission, {APP_NAME} closes, is "
                           "updated and opens again by itself. Your instruments, settings and recordings are "
                           "kept; running recordings continue.")
        self.info.setWordWrap(True)
        self.info.setStyleSheet(f"color: {c['muted']};")
        self.bar = QProgressBar()
        self.bar.hide()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        self.install_button = QPushButton(theme.icon("export"), "Download and install")
        self.install_button.setDefault(True)
        self.install_button.clicked.connect(self.install)
        self.later_button = QPushButton("Later")
        self.later_button.clicked.connect(self.reject)
        self.skip_button = QPushButton("Skip this version")
        self.skip_button.setToolTip("Do not offer this version again when SeismoVerse starts "
                                    "(Help > Check for updates still shows it)")
        self.skip_button.clicked.connect(self.skip)
        row = QHBoxLayout()
        row.addWidget(self.skip_button)
        row.addStretch(1)
        row.addWidget(self.later_button)
        row.addWidget(self.install_button)
        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(QLabel("What's new"))
        lay.addWidget(self.notes, 1)
        lay.addWidget(self.info)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        lay.addLayout(row)

    def skip(self):
        self.config.update_section("updates", {"skip": self.release.version})
        log.info("update %s skipped by the user", self.release.version)
        self.reject()

    def _say(self, text, color=None):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color or theme.COLORS['text']};")
        self.status.show()

    def install(self):
        """Download (in a worker thread), then start the installer."""
        if self.job is not None:                       # a second click while downloading: cancel
            self.job.stop = True
            return
        self.job = _Download(self.release)
        self.job.progress.connect(self._progress, Qt.QueuedConnection)
        self.job.finished.connect(self._downloaded, Qt.QueuedConnection)
        self.bar.setRange(0, 0)
        self.bar.show()
        self.install_button.setText("Cancel download")
        self.skip_button.setEnabled(False)
        self._say("Downloading the installer from GitHub...")
        self.job.start()

    def _progress(self, done, total):
        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * min(done, total) / total))
            self._say(f"Downloading the installer from GitHub... {done / 1e6:.1f} of {total / 1e6:.1f} MB")

    def _downloaded(self, path, error):
        self.job = None
        self.bar.hide()
        self.install_button.setText("Download and install")
        self.skip_button.setEnabled(True)
        if error:
            self._say(error, theme.COLORS["danger"])
            return
        self._say("Checked. Starting the installer: allow it when Windows asks. "
                  f"{APP_NAME} closes now and opens again when the update is done.", theme.COLORS["ok"])
        try:
            updater.start_installer(path)
        except updater.UpdateError as e:
            self._say(f"{e} The installer is kept in {path}; you can also run it yourself.",
                      theme.COLORS["danger"])
            return
        self.install_button.setEnabled(False)

    def done(self, result):
        if self.job is not None:
            self.job.stop = True
        super().done(result)


def check_for_updates(window, config, manual):
    """Ask GitHub for the newest release. manual=True (Help > Check for updates): always say
    what was found; manual=False (at start): a window only for a newer version not skipped."""
    check = UpdateCheck(window)
    window._update_check = check                       # kept alive until it reports

    def finished(release, error):
        window._update_check = None
        if error:
            log.info("update check: %s", error)
            if manual:
                QMessageBox.information(window, f"{APP_NAME} update", error)
            return
        config.update_section("updates", {"last_check": time.time()})
        if not updater.is_newer(release.version):
            log.info("update check: %s %s is the newest version", APP_NAME, VERSION)
            if manual:
                QMessageBox.information(window, f"{APP_NAME} update",
                                        f"{APP_NAME} {VERSION} is the newest version.")
            return
        if not manual and (config.get("updates") or {}).get("skip") == release.version:
            log.info("update check: %s is available but skipped by the user", release.version)
            return
        log.info("update check: %s %s is available", APP_NAME, release.version)
        window.open_update(release)

    check.finished.connect(finished, Qt.QueuedConnection)
    check.start()
    return check
