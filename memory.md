# SeisZone — memory.md (handover)

Read this first in any new session (local or cloud). It is complete on purpose: a reader who has
seen nothing else should be able to continue the project from here. plan.md holds the detailed
architecture and module contracts.

## What SeisZone is

The user's own Windows application for live seismic data, named **SeisZone** (the user once also
wrote "Size Zone"; SeisZone is the name). It replaces the earlier three-part setup:

* a third-party seismic viewer (proprietary, signed by its vendor — **never** rename, patch,
  re-skin or copy it; never write its name in SeisZone),
* our add-on `C:\D Drive\Only Coding\gecko-omega-addon` (Windows service: Gecko receiver +
  SeedLink server 18010 + GCF gateway; Record bar; recording engine; installer GeckoOmegaSetup 1.5.4),
* the older folders `gecko-seedlink-bridge` and `omega-record-addon` (earlier versions of code that
  is already inside gecko-omega-addon/src).

SeisZone is written from scratch on open standards (SeedLink 3, Güralp GCF / Scream UDP, Kelunji
Gecko telemetry framing, miniSEED 2, FDSN StationXML). The proven protocol logic of our own
gecko-omega-addon code was ported into it.

## Standing rules from the user

* Never use the name "Kellith" (separate business). Never write the third-party viewer's or its
  vendor's names in SeisZone.
* Recording is user-controlled (start/stop by the user); data is never deleted by the program;
  low disk space only warns.
* The program runs as the normal user (no admin). Only the installer needs admin (Program Files +
  one firewall rule). The user installs installers themselves (UAC) — build them, do not install.
* The user wants: type only **IP + port** and the instrument connects.
* Keep this memory.md complete and up to date at the end of every piece of work.

## The real equipment (for field tests — read-only access only)

| Unit | Address | Codes | Notes |
|------|---------|-------|-------|
| Güralp Fortimus | 10.8.0.180 | WB.KDT Kangsabati | over VPN |
| Güralp Fortimus | 10.8.0.181 | KA.KAND KANGSABATI DOWN | over VPN |
| Güralp Fortimus | 10.8.0.182 | WB.KUM KUMARI | over VPN |
| Kelunji Gecko SMR03 | connects IN via USR-W610 to this PC, TCP 58772 | SS.SMR03 | network code SS |

Fortimus: SeedLink on 18000 (channels 0J.HN? 200 sps, 9J.MN? 5 sps), GCF Scream UDP 1567, web page
port 80 (status, data streams, StationXML ~0.9 MB — about a minute over the slow VPN).
Tests must never contact these or listen on 58772/18010 (the real Gecko would connect).

## Status (2026-11-07)

* Project created at `C:\D Drive\Only Coding\seiszone` (git repository, local only — not pushed;
  a cloud session cannot see the code unless it is pushed somewhere).
* Written and checked by the lead: plan.md, memory.md, core spine (`config.py`, `paths.py`,
  `logsetup.py`, `core/mseed.py` lossless Steim2 codec cross-checked with ObsPy, `core/hub.py`
  DataHub with gap/backfill handling, `core/links.py`, `core/gcf.py`), `ui/theme.py` (own palette
  and drawn icons), `tests/fake_engine.py`, `tests/conftest.py`.
* In progress (parallel build workflow): SeedLink client/server, Gecko receiver, GCF link, probe,
  StationXML, recorder, export, DSP, engine + CLI, all UI panels/dialogs, icon, installer, docs.
* Not yet done: field test on the real units; user acceptance.

## How to run / test / build

```
cd "C:\D Drive\Only Coding\seiszone"
python -m pip install -r requirements.txt -r requirements-dev.txt
python run_seiszone.py                          # the application, from source
python run_seiszone.py --probe 10.8.0.182:18000 # read-only detection, prints what was found
python run_seiszone.py --engine --seconds 60    # headless engine with the user's config
python -m pytest tests -q                       # all tests (offscreen Qt, temp SEISZONE_HOME)
python installer/build.py                       # PyInstaller -> build/dist/SeisZone, Inno -> SeisZoneSetup.exe
```

Python 3.14.7, PySide6 6.12.0, pyqtgraph 0.14.0, numpy, scipy, PyInstaller; Inno Setup 6 ISCC at
`C:\Users\rohit\AppData\Local\Programs\Inno Setup 6\ISCC.exe`.

User files: `%APPDATA%\SeisZone` (config.json, state.json, stationxml\), `%LOCALAPPDATA%\SeisZone\logs`,
`Documents\SeisZone\Recordings` and `\Exports`. `SEISZONE_HOME` moves all of them (tests).

## Decisions and why

* Own app instead of renaming the third-party viewer: that program is the vendor's copyrighted,
  signed software; renaming or patching it is not allowed.
* PySide6 + pyqtgraph: smooth live plots of many 200 sps channels; Qt is LGPL (dynamic linking via
  PySide6 is fine for a closed application).
* No Windows service: the app itself receives data and records; "start with Windows" (HKCU Run)
  plus tray keeps it running without admin rights. A service could be added later if recording
  must continue with nobody logged in.
* Recording appends the received miniSEED records unchanged (lossless, fast) to clock-aligned
  period files; export copies whole records and re-encodes only the boundary records.
* SeedLink client resumes with the saved sequence number, so the instruments' own buffers
  backfill gaps after VPN or PC outages.

## Open items / next steps

1. Finish and verify the parallel build (all tests green, offscreen screenshots, installer).
2. Field test read-only against 10.8.0.180/181/182 and the SMR03 (Gecko connects only when
   SeisZone listens on 58772 — the old GeckoSeedLink service is uninstalled since 2026-11-06).
3. User acceptance; then decide whether the old gecko-omega-addon folder is retired.
