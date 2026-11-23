"""Updates (core/updater.py, core/ed25519.py, installer/publish.py): the newest release asked
from a local fake GitHub (127.0.0.1, random port - the tests never contact GitHub) and signed
with a test key, versions compared, signatures checked (RFC 8032 test vectors; a missing, foreign
or replayed signature refused), only GitHub https addresses used, the installer downloaded and
refused unless its SHA-256 matches, the automatic check's rules, the start of the installer
(Windows' ShellExecute replaced), and the publish command's checks."""

import base64
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
from seiszone.core import ed25519, updater

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_BYTES = os.urandom(300_000)
SETUP_SHA = hashlib.sha256(SETUP_BYTES).hexdigest()
TEST_SECRET = bytes(range(32))                         # the tests' own publisher key
TEST_PUBLIC = ed25519.public_key(TEST_SECRET).hex()


def signature(version="9.1.0", sha=SETUP_SHA, secret=TEST_SECRET):
    return base64.b64encode(ed25519.sign(secret, updater.signed_message(version, sha))).decode() + "\n"


@pytest.fixture(autouse=True)
def local_github(monkeypatch):
    """The fake GitHub runs on 127.0.0.1 over http and signs with the tests' key."""
    monkeypatch.setattr(updater, "ALLOWED_HOSTS", updater.ALLOWED_HOSTS + ("127.0.0.1",))
    monkeypatch.setattr(updater, "REQUIRE_HTTPS", False)
    monkeypatch.setattr(updater, "TRUSTED_KEYS", (TEST_PUBLIC,))


class FakeGitHub:
    """GET /repos/o/r/releases/latest and the two download files, like GitHub serves them."""

    def __init__(self):
        self.mode = "ok"
        self.sha_text = f"{SETUP_SHA}  SeismoVerseSetup.exe\n"
        self.sig_text = signature()
        self.redirect = None
        self.paths = []
        self.setup = SETUP_BYTES
        self.agents = []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.agents.append(self.headers.get("User-Agent"))
                fake.paths.append(self.path)
                base = f"http://127.0.0.1:{fake.port}"
                if self.path == "/repos/o/r/releases/latest":
                    if fake.mode in ("404", "403"):
                        return self._send(int(fake.mode), b'{"message": "x"}')
                    assets = [{"name": "SeismoVerseSetup.exe", "size": len(fake.setup),
                               "browser_download_url": base + "/dl/SeismoVerseSetup.exe"}]
                    if fake.mode != "no-sha":
                        assets.append({"name": "SeismoVerseSetup.exe.sha256", "size": 90,
                                       "browser_download_url": base + "/dl/SeismoVerseSetup.exe.sha256"})
                    if fake.mode != "no-sig":
                        assets.append({"name": "SeismoVerseSetup.exe.sig", "size": 90,
                                       "browser_download_url": base + "/dl/SeismoVerseSetup.exe.sig"})
                    body = {"tag_name": "v9.1.0", "name": "SeismoVerse 9.1.0", "body": "## New\n* faster",
                            "html_url": "https://github.com/o/r/releases/tag/v9.1.0", "assets": assets}
                    return self._send(200, json.dumps(body).encode())
                if self.path == "/dl/SeismoVerseSetup.exe":
                    if fake.redirect:
                        self.send_response(302)
                        self.send_header("Location", fake.redirect)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return None
                    return self._send(200, fake.setup, "application/octet-stream")
                if self.path == "/dl/elsewhere.exe":
                    return self._send(200, fake.setup, "application/octet-stream")
                if self.path == "/dl/SeismoVerseSetup.exe.sig":
                    return self._send(200, fake.sig_text.encode(), "application/octet-stream")
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
                                        ("no-sha", "no checksum file"),
                                        ("no-sig", "not signed by the SeismoVerse publisher")])
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


def test_ed25519_rfc8032_vectors():
    vectors = [  # RFC 8032 section 7.1, tests 1-3 (2 and 3 also checked against OpenSSL)
        ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
         "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
         "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
        ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
         "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
         "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
        ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
         "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
         "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    ]
    for sk, pk, msg, sig in vectors:
        sk, pk, msg, sig = (bytes.fromhex(x) for x in (sk, pk, msg, sig))
        assert ed25519.public_key(sk) == pk and ed25519.sign(sk, msg) == sig
        assert ed25519.verify(pk, msg, sig)
        assert not ed25519.verify(pk, msg + b"x", sig)
        assert not ed25519.verify(pk, msg, sig[:-1] + bytes([sig[-1] ^ 1]))
        assert not ed25519.verify(pk, msg, sig[:32] + (2 ** 253).to_bytes(32, "little"))   # S >= L
    assert not ed25519.verify(b"\x00" * 31, b"", b"\x00" * 64)                          # bad sizes


def test_signature_must_be_the_publishers_and_for_this_version(gh):
    # signed by another key
    gh.sig_text = signature(secret=bytes(32))
    with pytest.raises(updater.UpdateError, match="not signed by the SeismoVerse publisher"):
        updater.latest_release(repo="o/r", api=gh.api)
    # an old installer offered as a newer version: its signature names another version
    gh.sig_text = signature(version="9.0.0")
    with pytest.raises(updater.UpdateError, match="not signed by the SeismoVerse publisher"):
        updater.latest_release(repo="o/r", api=gh.api)
    # a signature of another installer (other SHA-256)
    gh.sig_text = signature(sha="0" * 64)
    with pytest.raises(updater.UpdateError, match="not signed"):
        updater.latest_release(repo="o/r", api=gh.api)
    gh.sig_text = "not base64 at all!"
    with pytest.raises(updater.UpdateError, match="not signed"):
        updater.latest_release(repo="o/r", api=gh.api)
    gh.sig_text = signature()
    assert updater.latest_release(repo="o/r", api=gh.api).version == "9.1.0"
    # the key built into the program is the publisher's real one, and checks like any other
    assert updater.signature_ok("1.0.0", "a" * 64, b"\x00" * 64, keys=None) is False


def test_only_github_https_addresses(gh, monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "REQUIRE_HTTPS", True)
    with pytest.raises(updater.UpdateError, match="not GitHub"):
        updater.latest_release(repo="o/r", api=gh.api)                 # http: refused before asking
    monkeypatch.setattr(updater, "REQUIRE_HTTPS", False)
    monkeypatch.setattr(updater, "ALLOWED_HOSTS", ("api.github.com",))
    with pytest.raises(updater.UpdateError, match="not GitHub"):
        updater.latest_release(repo="o/r", api=gh.api)                 # another host
    assert not gh.agents                                               # nothing was asked
    monkeypatch.setattr(updater, "ALLOWED_HOSTS", ("api.github.com", "127.0.0.1"))
    rel = updater.latest_release(repo="o/r", api=gh.api)
    gh.redirect = "http://localhost:%d/dl/elsewhere.exe" % gh.port   # a redirect away from GitHub
    with pytest.raises(updater.UpdateError, match="not GitHub"):
        updater.download(rel, folder=str(tmp_path))
    assert os.listdir(tmp_path) == [] and "/dl/elsewhere.exe" not in gh.paths     # never asked
    gh.redirect = "http://127.0.0.1:%d/dl/elsewhere.exe" % gh.port   # a redirect within GitHub: followed
    path = updater.download(rel, folder=str(tmp_path))
    assert open(path, "rb").read() == SETUP_BYTES and "/dl/elsewhere.exe" in gh.paths


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


def test_publish_signing_key(tmp_path, monkeypatch):
    publish = _publish()
    key = tmp_path / "k" / "signing.key"
    with pytest.raises(publish.PublishError, match="missing or damaged"):
        publish.load_key(str(key))
    pub = publish.new_key(str(key))
    if os.name == "nt":                     # only this Windows user may read the key and its folder
        import subprocess
        for p in (key, key.parent):
            acl = subprocess.run(["icacls", str(p)], capture_output=True, text=True).stdout
            rights = [ln for ln in acl.splitlines()[:-1] if ":(" in ln]
            me = os.environ.get("USERNAME", "").lower()
            assert len(rights) == 1 and me in rights[0].lower(), acl
    with pytest.raises(publish.PublishError, match="never overwritten"):
        publish.new_key(str(key))
    with pytest.raises(publish.PublishError, match="not in core/updater.py TRUSTED_KEYS"):
        publish.load_key(str(key), trusted=("00" * 32,))
    secret = publish.load_key(str(key), trusted=(pub,))
    out = tmp_path / "x.sig"
    sig = publish.write_sig_file(SETUP_SHA, secret, version="9.1.0", out=str(out))
    assert base64.b64decode(out.read_text()) == sig
    assert updater.signature_ok("9.1.0", SETUP_SHA, sig, keys=(pub,))
    assert not updater.signature_ok("9.1.1", SETUP_SHA, sig, keys=(pub,))
    # the real publisher key is built into the program
    assert len(updater.TRUSTED_KEYS) == 1 and len(bytes.fromhex(updater.TRUSTED_KEYS[0])) == 32


def test_publish_checks_the_build_and_writes_the_checksum(tmp_path, monkeypatch):
    publish = _publish()
    setup = tmp_path / "SeismoVerseSetup.exe"
    check = tmp_path / "version_check.txt"
    sha = tmp_path / "SeismoVerseSetup.exe.sha256"
    sig = tmp_path / "SeismoVerseSetup.exe.sig"
    key = tmp_path / "signing.key"
    key.write_text(TEST_SECRET.hex() + "\n")
    monkeypatch.setattr(publish, "SETUP", str(setup))
    monkeypatch.setattr(publish, "VERSION_CHECK", str(check))
    monkeypatch.setattr(publish, "SHA_FILE", str(sha))
    monkeypatch.setattr(publish, "SIG_FILE", str(sig))
    monkeypatch.setattr(publish, "KEY_FILE", str(key))
    monkeypatch.setattr(publish, "TRUSTED_KEYS", (TEST_PUBLIC,))
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
    assert cmd[:4] == ["gh", "release", "create", f"v{VERSION}"] and cmd[4:7] == [str(setup), str(sha), str(sig)]
    assert cmd[cmd.index("--repo") + 1] == "o/r" and "--latest" in cmd
    # a dry run publishes nothing (gh is never called)
    monkeypatch.setattr(publish, "_gh", lambda *a, **k: pytest.fail("gh called in a dry run"))
    monkeypatch.setattr(publish.subprocess, "run", lambda *a, **k: pytest.fail("command run in a dry run"))
    assert publish.main(["--notes", "Faster viewers", "--dry-run"]) == 0
    assert updater.signature_ok(VERSION, SETUP_SHA, base64.b64decode(sig.read_text()), keys=(TEST_PUBLIC,))


def test_installer_reopens_seismoverse_after_an_update():
    with open(os.path.join(ROOT, "installer", "SeisZone.iss"), encoding="utf-8-sig") as fh:
        iss = fh.read()
    assert "ExpandConstant('{param:showapp|0}') = '1'" in iss
    post = iss[iss.index("procedure CurStepChanged"):]
    post = post[:post.index("\nend;")]
    assert "ExecAsOriginalUser(ExpandConstant('{app}\\{#Exe}'), ''," in post        # in its window
    assert "'--minimized'" in post                                                 # a silent update otherwise
