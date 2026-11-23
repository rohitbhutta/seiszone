"""Publish an update: the built installer as a GitHub release of the public releases-only
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
     installs anything);
  3. with the GitHub command line tool (gh, signed in on this PC): makes sure the repository
     exists and that release v<version> does not exist yet (each version is published once;
     raise the version for the next update), then creates release v<version> with the installer,
     the checksum file and the notes. It becomes "latest", which is what SeismoVerse asks for.

Nothing is published with --dry-run. The release can be edited or deleted on GitHub afterwards.
"""

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from seiszone import APP_NAME, VERSION  # noqa: E402
from seiszone.core.updater import RELEASES_REPO, SETUP_ASSET, SHA_ASSET  # noqa: E402

DIST = os.path.join(ROOT, "build", "dist")
SETUP = os.path.join(DIST, SETUP_ASSET)
SHA_FILE = os.path.join(DIST, SHA_ASSET)
VERSION_CHECK = os.path.join(ROOT, "build", "work", "version_check.txt")


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


def release_command(notes_file, repo=RELEASES_REPO, version=None, setup=None, sha_file=None):
    version, setup, sha_file = version or VERSION, setup or SETUP, sha_file or SHA_FILE
    return ["gh", "release", "create", f"v{version}", setup, sha_file, "--repo", repo,
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
    p.add_argument("--repo", default=RELEASES_REPO, help=f"the public releases repository (default {RELEASES_REPO})")
    p.add_argument("--dry-run", action="store_true", help="check and show the command, publish nothing")
    a = p.parse_args(argv)
    try:
        check_build()
        digest = write_sha_file()
        print(f"{SETUP_ASSET}: {os.path.getsize(SETUP) / 1e6:.1f} MB, SHA-256 {digest}")
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
