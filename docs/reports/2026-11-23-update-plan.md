# Updates for SeismoVerse without a server - plan (2026-11-23, not built yet)

The user asked how to publish updates so that SeismoVerse on any computer, anywhere, can get
them, with no server, only the GitHub account.

## The problem
The repository `rohitbhutta/seiszone` is **private**. Files in a private repository (also its
Releases) can only be downloaded with a GitHub login that has access, so other computers
cannot fetch updates from it.

## Plan: GitHub Releases in a small public repository (recommended)
1. A second, **public** repository only for installers, for example `rohitbhutta/seismoverse-releases`.
   The source code stays private; only `SeismoVerseSetup.exe` and the release notes are public.
   GitHub hosts the files for free and serves them worldwide over HTTPS.
2. **Publishing an update** (on the build PC, one command): `python installer/publish.py`
   builds the installer, works out its SHA-256 checksum and creates a GitHub Release
   `v<version>` in that repository (through the `gh` command line tool, already signed in on this
   PC) with the installer, the checksum and the "What's new" text.
3. **In SeismoVerse** (new): at start (once a day, can be switched off in Settings) and with
   **Help > Check for updates**, it asks GitHub for the newest release of that repository
   (one HTTPS request to api.github.com, nothing about the PC or its data is sent).
   - Newer version: "SeismoVerse 2.2.0 is available" with What's new, **Download and install**,
     Later, Skip this version.
   - Download and install: downloads the installer into the temp folder, checks the SHA-256,
     then starts it; Windows asks for permission (UAC) as for any install; setup closes
     SeismoVerse, updates it in place (settings, sources and recordings kept, running
     recordings continue) and starts it again.
   - No internet: nothing happens; the installer can still be copied and run by hand.
4. Manual: a chapter "Getting updates".

## Notes
- Windows SmartScreen may warn about a downloaded installer that is not code-signed ("Windows
  protected your PC" > More info > Run anyway). A code-signing certificate removes that warning;
  it costs money every year and is optional.
- Alternative: make the main repository public (no second repository), but then the source
  code is public too.
- Tests use a local fake release server only; they never contact GitHub.
