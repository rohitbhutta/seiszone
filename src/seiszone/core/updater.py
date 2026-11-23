"""Updates without a server of our own: GitHub Releases of a small public repository that holds
only the installers (RELEASES_REPO; the source repository stays private).

Publishing (installer/publish.py, on the build PC): a release "v<version>" with two files,
SeismoVerseSetup.exe and SeismoVerseSetup.exe.sha256 ("<sha256 hex>  SeismoVerseSetup.exe"),
and the "What's new" text as the release notes.

Checking (ui/update_dialog.py): one HTTPS request to the GitHub API for the newest release
(latest_release). Nothing about the PC or its data is sent - only the request itself, with
"SeismoVerse/<version> update check" as the program name. A newer version can be downloaded
(download: into the temp folder, at most MAX_SETUP bytes, refused unless its SHA-256 matches
the published one) and its installer started (start_installer: Windows asks for permission;
setup closes SeismoVerse, updates it in place and starts it again).

SEISZONE_UPDATE_CHECK=off (set by the tests) or config "updates": {"check": false} stops the
automatic check; the tests use a local fake release server only.
"""

import hashlib
import json
import logging
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .. import APP_NAME, VERSION

log = logging.getLogger("seiszone")

RELEASES_REPO = "rohitbhutta/seismoverse-releases"
API = "https://api.github.com"
SETUP_ASSET = f"{APP_NAME}Setup.exe"
SHA_ASSET = SETUP_ASSET + ".sha256"
ENV = "SEISZONE_UPDATE_CHECK"
CHECK_EVERY = 20 * 3600          # s between automatic checks (about once a day)
TIMEOUT = 20.0                   # s for one request
MAX_SETUP = 300 * 1024 * 1024    # bytes; a larger "installer" is refused
CHUNK = 256 * 1024
USER_AGENT = f"{APP_NAME}/{VERSION} update check"


class UpdateError(Exception):
    """A check or download that did not work; the text is for the user."""


@dataclass
class Release:
    version: str          # "2.2.0"
    tag: str              # "v2.2.0"
    title: str
    notes: str            # What's new (Markdown, as written on GitHub)
    setup_url: str
    setup_size: int
    sha256: str           # 64 hex digits
    page_url: str         # the release page on GitHub


def parse_version(text):
    """(major, minor, patch) from "v2.1.0", "2.1", "SeismoVerse 2.1.0"; None without a version."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text or ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def is_newer(latest, current=VERSION):
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def automatic_check_allowed(cfg_updates, now=None, frozen=None):
    """True when the automatic check should run now: switched on, not turned off for the tests,
    the installed program (not a source run), and the last one more than CHECK_EVERY ago."""
    u = cfg_updates or {}
    if os.environ.get(ENV, "").lower() in ("off", "0", "no", "false"):
        return False
    if not u.get("check", True):
        return False
    if frozen is None:
        import sys
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return False
    now = time.time() if now is None else now
    try:
        last = float(u.get("last_check") or 0)
    except (TypeError, ValueError):
        last = 0.0
    return not (0 < now - last < CHECK_EVERY)


def _get(url, timeout, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    return urllib.request.urlopen(req, timeout=timeout)   # noqa: S310 - https (or the tests' local server)


def _reason(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"GitHub answered {e.code}"
    r = getattr(e, "reason", None) or e
    return str(r)


def latest_release(repo=RELEASES_REPO, api=API, timeout=TIMEOUT):
    """The newest published release (Release), or UpdateError with a message for the user."""
    url = f"{api.rstrip('/')}/repos/{repo}/releases/latest"
    try:
        with _get(url, timeout) as r:
            data = json.loads(r.read(2_000_000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("No update has been published yet.") from None
        if e.code in (403, 429):
            raise UpdateError("GitHub is busy (too many checks from this network); try again later.") from None
        raise UpdateError(f"The update check did not work ({_reason(e)}).") from None
    except (OSError, ValueError) as e:
        raise UpdateError(f"The update check did not work: no connection to GitHub ({_reason(e)}).") from None
    tag = str(data.get("tag_name") or "")
    version = parse_version(tag) or parse_version(data.get("name"))
    if version is None:
        raise UpdateError(f"The newest release ({tag or 'no name'}) has no version number.")
    assets = {a.get("name"): a for a in data.get("assets") or [] if isinstance(a, dict)}
    setup, sha = assets.get(SETUP_ASSET), assets.get(SHA_ASSET)
    if setup is None:
        raise UpdateError(f"The newest release has no {SETUP_ASSET}.")
    if sha is None:
        raise UpdateError(f"The newest release has no checksum file ({SHA_ASSET}); it is not installed.")
    try:
        with _get(sha["browser_download_url"], timeout, accept="application/octet-stream") as r:
            text = r.read(4096).decode("ascii", "replace")
    except (OSError, KeyError) as e:
        raise UpdateError(f"The checksum of the update could not be read ({_reason(e)}).") from None
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    if not m:
        raise UpdateError("The checksum file of the update is not valid; it is not installed.")
    return Release(version=".".join(str(x) for x in version), tag=tag, title=str(data.get("name") or tag),
                   notes=str(data.get("body") or ""), setup_url=str(setup.get("browser_download_url") or ""),
                   setup_size=int(setup.get("size") or 0), sha256=m.group(1).lower(),
                   page_url=str(data.get("html_url") or ""))


def download(release, folder=None, progress=None, cancelled=None, timeout=TIMEOUT):
    """Download the installer of a Release into folder (default: the temp folder) and check its
    SHA-256. progress(done, total) is called now and then; cancelled() -> True stops it. Returns
    the file's path; UpdateError (the partial file is removed) when it fails or does not match."""
    folder = folder or os.path.join(tempfile.gettempdir(), f"{APP_NAME}Update")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{APP_NAME}Setup-{release.version}.exe")
    part = path + ".part"
    digest, done = hashlib.sha256(), 0
    try:
        with _get(release.setup_url, timeout, accept="application/octet-stream") as r, open(part, "wb") as fh:
            total = int(r.headers.get("Content-Length") or release.setup_size or 0)
            if total > MAX_SETUP:
                raise UpdateError("The update is larger than an installer can be; it is not downloaded.")
            while True:
                if cancelled is not None and cancelled():
                    raise UpdateError("The download was cancelled.")
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if done > MAX_SETUP:
                    raise UpdateError("The update is larger than an installer can be; it is not downloaded.")
                digest.update(chunk)
                fh.write(chunk)
                if progress is not None:
                    progress(done, total)
    except UpdateError:
        _remove(part)
        raise
    except (OSError, ValueError) as e:
        _remove(part)
        raise UpdateError(f"The download did not work ({_reason(e)}).") from None
    if digest.hexdigest() != release.sha256:
        _remove(part)
        raise UpdateError("The downloaded installer does not match its published checksum; it was not kept "
                          "and is not installed.")
    _remove(path)
    os.replace(part, path)
    log.info("update %s downloaded to %s (%d bytes, SHA-256 checked)", release.version, path, done)
    return path


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def start_installer(path):
    """Start the downloaded installer (Windows asks for permission). /SILENT: only a progress
    window; /SUPPRESSMSGBOXES: setup closes SeismoVerse without asking; /SHOWAPP=1: setup opens
    SeismoVerse again in its window when it is done. UpdateError when Windows did not start it."""
    import ctypes
    args = "/SILENT /SUPPRESSMSGBOXES /SHOWAPP=1"
    rc = ctypes.windll.shell32.ShellExecuteW(None, "open", path, args, os.path.dirname(path), 1)
    if rc <= 32:
        if rc == 5:
            raise UpdateError("The installer was not started: Windows did not get permission.")
        raise UpdateError(f"Windows could not start the installer (error {rc}).")
    log.info("update installer started: %s %s", path, args)
