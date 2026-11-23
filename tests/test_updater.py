"""Updates (core/updater.py, installer/publish.py): the newest release asked from a local fake
GitHub (127.0.0.1, random port - the tests never contact GitHub), versions compared, the
installer downloaded and refused unless its SHA-256 matches, the automatic check's rules, the
start of the installer (Windows' ShellExecute replaced), and the publish command's checks."""

import ctypes
import hashlib
import http.server
import json
import os
import sys
import threading
import time

import pytest

from seiszone import VERSION
from seiszone.core import updater

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_BYTES = os.urandom(300_000)
SETUP_SHA = hashlib.sha256(SETUP_BYTES).hexdigest()


class FakeGitHub:
    """GET /repos/o/r/releases/latest and the two download files, like GitHub serves them."""

    def __init__(self):
        self.mode = "ok"
        self.sha_text = f"{SETUP_SHA}  SeismoVerseSetup.exe\n"
        self.setup = SETUP_BYTES
        self.agents = []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.agents.append(self.headers.get("User-Agent"))
                base = f"http://127.0.0.1:{fake.port}"
                if self.path == "/repos/o/r/releases/latest":
                    if fake.mode in ("404", "403"):
                        return self._send(int(fake.mode), b'{"message": "x"}')
                    assets = [{"name": "SeismoVerseSetup.exe", "size": len(fake.setup),
                               "browser_download_url": base + "/dl/SeismoVerseSetup.exe"}]
                    if fake.mode != "no-sha":
                        assets.append({"name": "SeismoVerseSetup.exe.sha256", "size": 90,
                                       "browser_download_url": base + "/dl/SeismoVerseSetup.exe.sha256"})
                    body = {"tag_name": "v9.1.0", "name": "SeismoVerse 9.1.0", "body": "## New\n* faster",
                            "html_url": "https://github.com/o/r/releases/tag/v9.1.0", "assets": assets}
                    return self._send(200, json.dumps(body).encode())
                if self.path == "/dl/SeismoVerseSetup.exe":
                    return self._send(200, fake.setup, "application/octet-stream")
                if self.path == "/dl/SeismoVerseSetup.exe.sha256":
                    return self._send(200, fake.sha_text.encode(), "application/octet-stream")
                self._send(404, b"{}")

            def _send(self, code, body, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.api = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def gh():
    fake = FakeGitHub()
    yield fake
    fake.close()


def test_versions():
    assert updater.parse_version("v2.1.0") == (2, 1, 0)
    assert updater.parse_version("SeismoVerse 2.2") == (2, 2, 0)
    assert updater.parse_version("no version") is None
    assert updater.is_newer("v2.1.1", "2.1.0") and updater.is_newer("2.10.0", "2.9.9")
    assert not updater.is_newer("2.1.0", "2.1.0") and not updater.is_newer("2.0.9", "2.1.0")
    assert not updater.is_newer("nightly", "2.1.0")
    assert updater.is_newer("v99.0.0") and not updater.is_newer(VERSION)


def test_latest_release_and_what_is_sent(gh):
    rel = updater.latest_release(repo="o/r", api=gh.api)
    assert (rel.version, rel.tag, rel.title) == ("9.1.0", "v9.1.0", "SeismoVerse 9.1.0")
    assert rel.notes.startswith("## New") and rel.sha256 == SETUP_SHA and rel.setup_size == len(SETUP_BYTES)
    assert rel.setup_url.endswith("/dl/SeismoVerseSetup.exe") and rel.page_url.endswith("/v9.1.0")
    # only the program name and version go out with a request
    assert gh.agents and set(gh.agents) == {f"SeismoVerse/{VERSION} update check"}


@pytest.mark.parametrize("mode, says", [("404", "No update has been published yet"),
                                        ("403", "GitHub is busy"),
                                        ("no-sha", "no checksum file")])
def test_release_problems_say_why(gh, mode, says):
    gh.mode = mode
    with pytest.raises(updater.UpdateError, match=says):
        updater.latest_release(repo="o/r", api=gh.api)


def test_bad_checksum_file_and_no_connection(gh):
    gh.sha_text = "not a checksum"
    with pytest.raises(updater.UpdateError, match="checksum file of the update is not valid"):
        updater.latest_release(repo="o/r", api=gh.api)
    port = gh.port
    gh.close()
    gh.close = lambda: None
    with pytest.raises(updater.UpdateError, match="no connection to GitHub"):
        updater.latest_release(repo="o/r", api=f"http://127.0.0.1:{port}", timeout=3)


def test_download_checks_the_sha256(gh, tmp_path):
    rel = updater.latest_release(repo="o/r", api=gh.api)
    seen = []
    path = updater.download(rel, folder=str(tmp_path), progress=lambda d, t: seen.append((d, t)))
    assert open(path, "rb").read() == SETUP_BYTES and path.endswith("SeismoVerseSetup-9.1.0.exe")
    assert seen and seen[-1] == (len(SETUP_BYTES), len(SETUP_BYTES))
    assert os.listdir(tmp_path) == [os.path.basename(path)]
    # a changed file on the server: refused, nothing kept
    gh.setup = SETUP_BYTES[:-1] + b"X"
    other = tmp_path / "other"
    with pytest.raises(updater.UpdateError, match="does not match its published checksum"):
        updater.download(rel, folder=str(other))
    assert os.listdir(other) == []
    # cancelled: nothing kept
    with pytest.raises(updater.UpdateError, match="cancelled"):
        updater.download(rel, folder=str(other), cancelled=lambda: True)
    assert os.listdir(other) == []


def test_automatic_check_rules(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setenv(updater.ENV, "off")                     # the tests: never
    assert not updater.automatic_check_allowed({}, now=now, frozen=True)
    monkeypatch.delenv(updater.ENV)
    assert updater.automatic_check_allowed({}, now=now, frozen=True)
    assert not updater.automatic_check_allowed({}, now=now, frozen=False)          # a source run
    assert not updater.automatic_check_allowed({"check": False}, now=now, frozen=True)
    assert not updater.automatic_check_allowed({"last_check": now - 3600}, now=now, frozen=True)
    assert updater.automatic_check_allowed({"last_check": now - 21 * 3600}, now=now, frozen=True)


def test_start_installer_asks_windows(monkeypatch, tmp_path):
    calls = []

    class Shell:
        rc = 42

        def ShellExecuteW(self, hwnd, verb, path, args, folder, show):
            calls.append((verb, path, args))
            return Shell.rc

    class Win:
        shell32 = Shell()

    monkeypatch.setattr(ctypes, "windll", Win(), raising=False)
    setup = str(tmp_path / "SeismoVerseSetup-9.1.0.exe")
    updater.start_installer(setup)
    assert calls == [("open", setup, "/SILENT /SUPPRESSMSGBOXES /SHOWAPP=1")]
    Shell.rc = 5
    with pytest.raises(updater.UpdateError, match="did not get permission"):
        updater.start_installer(setup)


# ------------------------------------------------------------------------------------------
# installer/publish.py
# ------------------------------------------------------------------------------------------
def _publish():
    sys.path.insert(0, os.path.join(ROOT, "installer"))
    import publish
    return publish


def test_publish_checks_the_build_and_writes_the_checksum(tmp_path, monkeypatch):
    publish = _publish()
    setup = tmp_path / "SeismoVerseSetup.exe"
    check = tmp_path / "version_check.txt"
    sha = tmp_path / "SeismoVerseSetup.exe.sha256"
    monkeypatch.setattr(publish, "SETUP", str(setup))
    monkeypatch.setattr(publish, "VERSION_CHECK", str(check))
    monkeypatch.setattr(publish, "SHA_FILE", str(sha))
    with pytest.raises(publish.PublishError, match="missing"):
        publish.check_build()
    check.write_text("SeismoVerse 0.0.1\nexit code 0\n")
    setup.write_bytes(SETUP_BYTES)
    with pytest.raises(publish.PublishError, match="not version"):
        publish.check_build()
    check.write_text(f"SeismoVerse {VERSION}\nexit code 0\n")
    os.utime(setup, (time.time() - 3600, time.time() - 3600))     # an installer older than the build
    with pytest.raises(publish.PublishError, match="older than the last build"):
        publish.check_build()
    os.utime(setup, None)
    publish.check_build()
    assert publish.write_sha_file() == SETUP_SHA
    assert sha.read_text() == f"{SETUP_SHA}  SeismoVerseSetup.exe\n"
    cmd = publish.release_command("notes.md", repo="o/r")
    assert cmd[:4] == ["gh", "release", "create", f"v{VERSION}"] and cmd[4:6] == [str(setup), str(sha)]
    assert cmd[cmd.index("--repo") + 1] == "o/r" and "--latest" in cmd
    # a dry run publishes nothing (gh is never called)
    monkeypatch.setattr(publish, "_gh", lambda *a, **k: pytest.fail("gh called in a dry run"))
    monkeypatch.setattr(publish.subprocess, "run", lambda *a, **k: pytest.fail("command run in a dry run"))
    assert publish.main(["--notes", "Faster viewers", "--dry-run"]) == 0


def test_installer_reopens_seismoverse_after_an_update():
    with open(os.path.join(ROOT, "installer", "SeisZone.iss"), encoding="utf-8-sig") as fh:
        iss = fh.read()
    assert "ExpandConstant('{param:showapp|0}') = '1'" in iss
    post = iss[iss.index("procedure CurStepChanged"):]
    post = post[:post.index("\nend;")]
    assert "ExecAsOriginalUser(ExpandConstant('{app}\\{#Exe}'), ''," in post        # in its window
    assert "'--minimized'" in post                                                 # a silent update otherwise
