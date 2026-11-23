r"""Publish an update: the built installer as a GitHub release of the public releases-only
repository (core/updater.py RELEASES_REPO), so SeismoVerse on any computer finds it with
Help > Check for updates (and the daily check). The source repository stays private.

    python installer/build.py                                   build first (as always)
    python installer/publish.py --notes "What's new ..."       publish it
    python installer/publish.py --notes-file notes.md          What's new from a file (Markdown)
    python installer/publish.py --notes "..." --dry-run        only show what would be done

What it does:
  1. checks that build/dist/SeismoVerseSetup.exe is the build of this version (the build's own
     "--version" answer in build/work/version_check.txt, and the installer newer than it);
  2. writes SeismoVerseSetup.exe.sha256 next to it (the SHA-256 SeismoVerse checks before it
     installs anything) and SeismoVerseSetup.exe.sig: the Ed25519 signature of the version and
     that SHA-256 with the publisher's secret key (KEY_FILE, default
     %USERPROFILE%\.seismoverse-release\signing.key - never in the repository, never on
     GitHub; keep a copy offline: without it no update can be published that SeismoVerse
     accepts). Its public key must be in core/updater.py TRUSTED_KEYS.
     python installer/publish.py --new-key  makes a key once (never overwrites one);
  3. with the GitHub command line tool (gh, signed in on this PC): makes sure the repository
     exists and that release v<version> does not exist yet (each version is published once;
     raise the version for the next update), then creates release v<version> with the installer,
     the checksum file and the notes. It becomes "latest", which is what SeismoVerse asks for.

Nothing is published with --dry-run. The release can be edited or deleted on GitHub afterwards.
"""

import argparse
import base64
import hashlib
import os
import secrets
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from seiszone import APP_NAME, VERSION  # noqa: E402
from seiszone.core import ed25519  # noqa: E402
from seiszone.core.updater import (RELEASES_REPO, SETUP_ASSET, SHA_ASSET, SIG_ASSET, TRUSTED_KEYS,  # noqa: E402
                                   signature_ok, signed_message)

DIST = os.path.join(ROOT, "build", "dist")
SETUP = os.path.join(DIST, SETUP_ASSET)
SHA_FILE = os.path.join(DIST, SHA_ASSET)
SIG_FILE = os.path.join(DIST, SIG_ASSET)
VERSION_CHECK = os.path.join(ROOT, "build", "work", "version_check.txt")
KEY_FILE = os.environ.get("SEISMOVERSE_SIGNING_KEY") or os.path.join(
    os.path.expanduser("~"), ".seismoverse-release", "signing.key")


class PublishError(Exception):
    pass


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_sha_file(setup=None, out=None):
    """The checksum file in the usual "<hex>  <name>" form; returns the hex digest."""
    setup, out = setup or SETUP, out or SHA_FILE
    digest = sha256_of(setup)
    with open(out, "w", encoding="ascii", newline="\n") as fh:
        fh.write(f"{digest}  {os.path.basename(setup)}\n")
    return digest


def check_build(setup=None, version_check=None, version=None):
    """PublishError unless the installer exists and is the build of this version."""
    setup, version_check, version = setup or SETUP, version_check or VERSION_CHECK, version or VERSION
    if not os.path.isfile(setup):
        raise PublishError(f"{setup} is missing: run python installer/build.py first.")
    try:
        with open(version_check, encoding="utf-8") as fh:
            answer = fh.read()
    except OSError:
        raise PublishError("The build's version check is missing: run python installer/build.py first.") from None
    if f"{APP_NAME} {version}" not in answer:
        raise PublishError(f"The last build is not version {version} ({answer.strip()!r}): build again first.")
    if os.path.getmtime(setup) + 1 < os.path.getmtime(version_check):
        raise PublishError(f"{os.path.basename(setup)} is older than the last build: build again first.")


def new_key(path=None):
    """Make the publisher's secret key once (32 random bytes, hex). Returns its public key (hex)."""
    path = path or KEY_FILE
    if os.path.exists(path):
        raise PublishError(f"{path} exists already; it is never overwritten.")
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)          # (icacls below sets the rights: this user only)
        owner_only(folder, folder=True)
    secret = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(secret.hex() + "\n")
    owner_only(path)
    return ed25519.public_key(secret).hex()


def owner_only(path, folder=False):
    """Only the current Windows user may read the key (and its folder): inherited rights removed,
    full control for this user alone. PublishError when that does not work."""
    if os.name != "nt":
        os.chmod(path, 0o700 if folder else 0o600)
        return
    me = "\\".join(x for x in (os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")) if x)
    grant = f"{me}:(OI)(CI)F" if folder else f"{me}:F"
    r = subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", grant], capture_output=True, text=True)
    if r.returncode:
        raise PublishError(f"Could not limit access to {path} to {me}: {(r.stderr or r.stdout).strip()}")


def load_key(path=None, trusted=None):
    """The secret key; PublishError when it is missing or SeismoVerse would not trust it."""
    path = path or KEY_FILE
    try:
        with open(path, encoding="ascii") as fh:
            secret = bytes.fromhex(fh.read().strip())
    except (OSError, ValueError):
        raise PublishError(f"The signing key {path} is missing or damaged. Restore it from your copy "
                           "(or make a new one with --new-key and put its public key into "
                           "core/updater.py TRUSTED_KEYS - SeismoVerse versions without it will not "
                           "accept updates signed with it).") from None
    if len(secret) != 32:
        raise PublishError(f"The signing key {path} is damaged.")
    pub = ed25519.public_key(secret).hex()
    if pub not in (TRUSTED_KEYS if trusted is None else trusted):
        raise PublishError(f"The signing key's public key {pub} is not in core/updater.py TRUSTED_KEYS: "
                           "SeismoVerse would refuse this update.")
    return secret


def write_sig_file(digest, secret, version=None, out=None):
    """The signature file (base64); checked here with the program's own check before upload."""
    version, out = version or VERSION, out or SIG_FILE
    sig = ed25519.sign(secret, signed_message(version, digest))
    if not signature_ok(version, digest, sig, keys=[ed25519.public_key(secret).hex()]):
        raise PublishError("The signature does not check; nothing is published.")
    with open(out, "w", encoding="ascii", newline="\n") as fh:
        fh.write(base64.b64encode(sig).decode("ascii") + "\n")
    return sig


def release_command(notes_file, repo=RELEASES_REPO, version=None, setup=None, sha_file=None, sig_file=None):
    version, setup, sha_file, sig_file = version or VERSION, setup or SETUP, sha_file or SHA_FILE, sig_file or SIG_FILE
    return ["gh", "release", "create", f"v{version}", setup, sha_file, sig_file, "--repo", repo,
            "--title", f"{APP_NAME} {version}", "--notes-file", notes_file, "--latest"]


def _gh(args, check=True):
    try:
        return subprocess.run(["gh"] + args, capture_output=True, text=True, check=check)
    except FileNotFoundError:
        raise PublishError("The GitHub command line tool (gh) is not installed.") from None
    except subprocess.CalledProcessError as e:
        raise PublishError((e.stderr or e.stdout or str(e)).strip()) from None


def main(argv=None):
    p = argparse.ArgumentParser(description=f"Publish {APP_NAME} {VERSION} as an update on GitHub")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--notes", help="What's new in this version (text)")
    g.add_argument("--notes-file", help="What's new in this version (a Markdown file)")
    g.add_argument("--new-key", action="store_true", help="make the publisher's signing key once")
    p.add_argument("--repo", default=RELEASES_REPO, help=f"the public releases repository (default {RELEASES_REPO})")
    p.add_argument("--dry-run", action="store_true", help="check and show the command, publish nothing")
    a = p.parse_args(argv)
    try:
        if a.new_key:
            pub = new_key()
            print(f"Signing key made: {KEY_FILE}\nKeep a copy offline (USB stick). Public key - put it into "
                  f"src/seiszone/core/updater.py TRUSTED_KEYS:\n  {pub}")
            return 0
        secret = load_key()
        check_build()
        digest = write_sha_file()
        write_sig_file(digest, secret)
        print(f"{SETUP_ASSET}: {os.path.getsize(SETUP) / 1e6:.1f} MB, SHA-256 {digest}, signed")
        notes_file = a.notes_file
        if notes_file is None:
            fd, notes_file = tempfile.mkstemp(suffix=".md", prefix="release_notes_")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(a.notes.strip() + "\n")
        elif not os.path.isfile(notes_file):
            raise PublishError(f"{notes_file} does not exist.")
        cmd = release_command(notes_file, repo=a.repo)
        if a.dry_run:
            print("dry run - would run:\n  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
            return 0
        if _gh(["repo", "view", a.repo, "--json", "visibility", "-q", ".visibility"], check=False).returncode:
            raise PublishError(f"The repository {a.repo} does not exist (or gh is not signed in): create it as a "
                               "public repository first.")
        if _gh(["release", "view", f"v{VERSION}", "--repo", a.repo], check=False).returncode == 0:
            raise PublishError(f"v{VERSION} is already published. Raise the version (src/seiszone/__init__.py, "
                               "installer/SeisZone.iss, installer/version_info.txt), build and publish again.")
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode:
            raise PublishError((out.stderr or out.stdout).strip())
        print(f"Published {APP_NAME} {VERSION}: {out.stdout.strip()}")
        return 0
    except PublishError as e:
        print(f"Not published: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
