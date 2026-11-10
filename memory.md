# SeisZone — memory.md (handover)

Read this first in any new session (local or cloud). It is complete on purpose: a reader who has
seen nothing else should be able to continue the project from here. plan.md holds the detailed
architecture and module contracts.

## What SeisZone is

The user's own Windows application for live seismic data, named **SeisZone** (the user once also
wrote "Size Zone"; SeisZone is the name). It replaces the earlier three-part setup:

* a third-party seismic viewer (proprietary, signed by its vendor — **never** rename, patch,
  re-skin or copy it; never write its name in SeisZone),
* our earlier Gecko add-on project (the `gecko-...-addon` folder in `C:\D Drive\Only Coding`;
  Windows service: Gecko receiver + SeedLink server 18010 + GCF gateway; Record bar; recording
  engine; its own installer, version 1.5.4),
* the older folders `gecko-seedlink-bridge` and the old record add-on (earlier versions of code
  that is already inside the add-on project's src folder).

SeisZone is written from scratch on open standards (SeedLink 3, Güralp GCF / Scream UDP, Kelunji
Gecko telemetry framing, miniSEED 2, FDSN StationXML). The proven protocol logic of our own
Gecko add-on code was ported into it.

## Standing rules from the user

* Never use the name of the user's separate business. Never write the third-party viewer's or its
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

## Status (2026-11-08)

* Project at `C:\D Drive\Only Coding\seiszone` (git repository, local only - not pushed).
* All modules written and integrated: spine, SeedLink client/server, Gecko receiver, GCF link,
  probe, StationXML, recorder, export, DSP, engine + CLI, every UI panel/dialog, icon, installer,
  README, user guide. `python -m pytest tests -q`: 188 passed (about 2 min; 144 before the review fixes below).
* `tests/test_integration.py` runs the real Engine + MainWindow offscreen against a local fake
  instrument (200 sps in real time, history backfilled by SeedLink resume), replays the captured
  Gecko link into the engine's Gecko port, records, exports 30 s and checks it sample by sample;
  screenshot `build/screens/integration.png`.
* Build OK: `build/dist/SeisZone/SeisZone.exe` (one folder) and `build/dist/SeisZoneSetup.exe`
  (about 68 MB). The frozen exe was checked with `--version`, `--engine --seconds 8 --config ...
  --output FILE` (windowed exe: `--output` writes everything into a file) and with its window
  for 12 s; a plain `taskkill /PID` (and the installer) now ends SeisZone cleanly, while the
  user's own close (X, Alt+F4) still hides it to the tray.
* Committed as 8c7f6d9 ("SeisZone 1.0.0 ...").
* Field test 2026-11-08 (read-only, scratch SEISZONE_HOME, frozen exe `--engine --seconds 120`):
  `--probe`/`--add` found all three Fortimus units (FMUS-5469 KUMARI etc., serial, firmware, GNSS
  position, 901 KB StationXML each). All three streamed 0J.HN? 200 sps + 9J.MN? 5 sps with 0 gaps.
  Shown latency ~22 s = PC clock 18.8 s fast (w32tm vs time.google.com) + ~3 s real; the PC clock
  should be synced (Windows Settings > Time > Sync now).
* The OLD `GeckoSeedLink` service (the old Gecko add-on's exe) was found running again on 2026-11-08 and
  holds 58772 + 18010 with the SMR03 connected. SeisZone reports "port in use by another program"
  and carries on. The Gecko part of the field test waits until the user stops/uninstalls that old
  service (the user's decision; we did not touch it).
* Field test 2026-11-10 after the review fixes (rebuilt frozen exe, same scratch home, 90 s): the
  new resume command `DATA n <time>` is accepted by all three Fortimus servers; each backfilled
  ~22-24 h of history (~5,850 packets per source) with 0 gaps and no errors.
* Not yet done: Gecko field test; user installs and accepts the installer.

### Review fixes (2026-11-08, after an independent review of 35 confirmed defects)

All 35 were fixed, each with a regression test that fails without the fix (checked by running
the new tests against the unfixed code). `python -m pytest tests -q`: 188 passed (about 2 min).
New test files: test_config.py, test_hub.py, test_installer.py. plan.md section 5 was updated
where a contract changed. What changed, by area:

* SeedLink server: commands may end in a bare CR (libslink, slarchive, ObsPy: ObsPy's own client
  is now a test); `DATA n` for a number that is not held starts at the oldest held packet (live
  only for n >= the next packet), a begin time counts only when n is not held; packet numbers
  continue across app restarts (State "server_seq" + 100000 at start); idle negotiating clients
  closed after 300 s, TCP keep-alive on; a record that cannot be re-encoded is skipped
  (struct.error caught; `mseed.seed_rate` now always gives an int16 pair, e.g. 40.5 -> 405/-10).
* SeedLink client: resumes with `DATA n <end time of the last record>` (State "seedlink_time"),
  falling back to `DATA n`, then `DATA`.
* miniSEED: Steim differences summed with int32 wraparound; an Xn mismatch is only counted
  (`mseed.xn_mismatches`), like libmseed; OverflowError caught in parse_record.
* GCF: a bare block cut by a TCP read waits for its rest (gcf.SHORT); bare status blocks only
  when printable; GcfLink uses only the selected streams (non-empty map) and one digitiser
  (`src["gcf"]["system_id"]`, set by the probe; the probe notes other units on the same port).
* Probe: a SeedLink server whose banner is not a Guralp unit's no longer waits ~64 s for a web
  page that does not answer.
* Gecko: serials unpacked unsigned (0x8xxxxxxx works with renames); a stray 0x55 no longer
  holds back the frames behind it.
* Hub: a record far in the future (bad time stamp) is recorded/served but kept out of the live
  buffer and stats; the packet ring keeps raw bytes only (saves ~300-500 MB when full).
* config.json / state.json: a locked file is read again for ~1 s; an unreadable one is never
  overwritten (copied to `<name>.<stamp>.bad` first, or Config.read_only); the window warns.
* Recorder: write backlog is a deque (O(1) per record while the disk is full); an LRU eviction
  can no longer close the new handle; records already in a file after a crash are not appended
  again; records still unwritten at close are logged as LOST with their time span and become a
  GAP at the next start.
* Export: low-rate channels look back far enough (period + one record); the export dialog
  offers channels found in the recording folders ("recorded only") and uses whole seconds.
* Engine: a busy-port retry no longer swallows a settings change; a disabled server shows no
  stale error. CLI: `--engine`/`--add` with the user's config refuse to run beside SeisZone
  (exit code 3, mutex in `seiszone/single_instance.py`); `--engine` with a missing `--config`:
  exit code 2; a failed engine: exit code 1.
* App: the Run value is only rewritten when missing or stale (source runs use
  `pythonw run_seiszone.py`); Quit waits for a running export (after asking) and ends cleanly
  while a detection is still running. Waveform view: no jumping live range, low-pass with
  fixed scale stays on screen, many channels scroll; Settings' default window applies at once.
* Installer: SeisZone is closed in PrepareToInstall / at usUninstall (after the user
  committed), and a silent update starts it again.
* Rebuilt after the fixes (`python installer/build.py`: SeisZone.exe + SeisZoneSetup.exe 68.4 MB);
  the frozen exe passed `--version`, `--engine --seconds 8 --config <temp, no sources, ports 0>
  --output FILE` (exit code 0) and a missing `--config` (exit code 2), with SEISZONE_HOME in a
  temp folder. Not committed by this step (the coordinator commits).

## How to run / test / build

```
cd "C:\D Drive\Only Coding\seiszone"
python -m pip install -r requirements.txt -r requirements-dev.txt
python run_seiszone.py                          # the application, from source
python run_seiszone.py --probe 10.8.0.182:18000 # read-only detection, prints what was found
python run_seiszone.py --engine --seconds 60    # headless, user's config (quit SeisZone first)
python run_seiszone.py --engine --seconds 8 --output out.txt   # output into a file (windowed exe)
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

1. User installs build/dist/SeisZoneSetup.exe (UAC) and checks the window on the real desktop.
2. Fortimus field test done (above). SMR03 field test: the user first stops/uninstalls the old
   GeckoSeedLink service (it holds 58772); then run SeisZone and check "Gecko receiver" links.
3. User acceptance; then decide whether the old Gecko add-on folder is retired.
