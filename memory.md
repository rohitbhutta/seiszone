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
Tests must never contact these or listen on 58772, 18000 or 18010 (the real Gecko connects to
58772; the old service holds 18010; SeisZone's own server runs on 18000).

## Status (2026-11-10, version 1.0.2)

* Project at `C:\D Drive\Only Coding\seiszone` (git repository, local only - not pushed).
* **Version 1.0.2** (see "Version 1.0.2" below): SeisZone's SeedLink server moves to port 18000.
  1.0.1 is commit 5c60396. The user installed 1.0.0 and was installing 1.0.1 when asking for
  the port change; `build/dist/SeisZoneSetup.exe` (1.0.2) upgrades either in place (same AppId).
* All modules written and integrated: spine, SeedLink client/server, Gecko receiver, GCF link,
  probe, StationXML, recorder, export, DSP, engine + CLI, every UI panel/dialog, icon, installer,
  README, user guide. `python -m pytest tests -q`: 211 passed (about 2 min; 188 in 1.0.0 after
  the review fixes, 144 before them).
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
* The OLD `GeckoSeedLink` service (the old Gecko add-on's service) was found running again on
  2026-11-08 and holds 58772 + 18010 with the SMR03 connected. 1.0.0 only said "port in use by
  another program"; 1.0.1 names it and offers to stop it (below). We never stopped it ourselves:
  that is the user's choice (button in SeisZone or the installer task).
* Field test 2026-11-10 after the review fixes (rebuilt frozen exe, same scratch home, 90 s): the
  new resume command `DATA n <time>` is accepted by all three Fortimus servers; each backfilled
  ~22-24 h of history (~5,850 packets per source) with 0 gaps and no errors.
* Not yet done: Gecko field test; user installs and accepts 1.0.2.

### Version 1.0.2 (2026-11-10)

Why: on the 1.0.1 installer's task page the user asked that SeisZone's SeedLink server use port
**18000** (the port their earlier viewer setup used), not 18010, and that 18010 be released
(it is the old Gecko SeedLink service's port; stopping that service frees it). 58772 stays.

* Default `seedlink_server.port` = 18000 (`config.py` DEFAULTS, engine fallback, Settings field).
* Config version 2: `Config._migrate()` moves a version-1 file whose server port is the old
  default 18010 to 18000 once and saves it at once; a port the user chose stays (test
  `test_version_1_server_port_moves_to_18000`).
* Installer task text: "... (frees ports 58772 and 18010; the SeisZone SeedLink server uses port
  18000)". The firewall rule is per program, so it needs no port change.
* Docs (README, user guide, plan.md G10) say 18000. `python -m pytest tests -q`: 212 passed.

### Version 1.0.1 (2026-11-10)

Why: after installing 1.0.0 and connecting a Fortimus (IP + port 18000) the user reported
(1) the readings were not live: the view sat on one fixed reading and showed only one channel
(KA.KAND.0J.HNZ), and (2) the Gecko recorder was connected to the PC but not in SeisZone. Causes:
(1) the live right edge followed the PC clock, which is ~18.8 s fast, so the newest data sat ~20 s
left of the edge and moved in record-sized jumps; a mouse wheel paused the view without a clear
sign; only the first channels were ever shown. (2) the old Gecko SeedLink service still holds
58772 (Gecko) and 18010 (SeedLink server), so SeisZone could not listen on them.

* Live view (`ui/waveform_view.py`): right edge = now minus the smallest arrival lag seen in the
  last 60 s (follows the data, not the PC clock; guards against backfill, long silences and
  backwards steps; a new channel set takes its lag at once). Wheel while Live steps the window
  length and stays Live (while Paused it zooms; a drag pauses). Paused is an amber
  "Paused – back to live" button and goes Live by itself after 120 s without mouse use.
* Channels (`ui/main_window.py`, `ui/channels_panel.py`): every new channel is shown as it
  arrives (up to 12) unless the user unticked it; unticked channels go into config view
  `"hidden"` and stay hidden after a restart. On the user's PC all KA.KAND channels appear.
* Old service (`core/ports.py` new, `core/engine.py`, `ui/sources_panel.py`): a busy port's
  owner is found read-only (TCP listener table + service status query; no program names are ever
  looked up or shown); status `owner`/`owner_pid`, error "port N is used by the old Gecko
  SeedLink service". The Sources panel shows a bar + context-menu action "Free the port – stop
  the old Gecko SeedLink service" and asks once per run (only while the window is on screen).
  The action is one UAC request: `cmd /c sc stop GeckoSeedLink & sc config GeckoSeedLink start=
  disabled` (stopped and disabled, never deleted). Rows then show "Waiting for the port…" until
  the engine listens (max 60 s). Busy ports are retried every 3 s (was 30 s).
* Installer: task `stopoldservice` (on by default, offered only when the service's registry key
  exists) does the same stop + disable after install. Silent installs use `/MERGETASKS`.
* Docs: README and user guide ("Gecko recorder does not appear / port in use"), plan.md 5.14,
  5.15 (incl. live view and auto-show) and new 5.16.
* Built 2026-11-10 (`python installer/build.py`, SEISZONE_HOME in a temp folder): frozen exe
  `--version` = "SeisZone 1.0.1"; `--engine --seconds 8 --config <temp: no sources, Gecko off,
  server 127.0.0.1 port 0> --output FILE` exit code 0. Setup not run.
* Known limits: on the first data after a start the edge moves back once by the lag (~21 s on
  the user's PC); a PC clock more than 5 min off gets no compensation; "Show only these" hides
  the others for good; stale saved channels count towards the 12; with many channels the scroll
  bar (not the wheel) scrolls the plots; the UAC request blocks the window until answered; the
  W610's redial time after the port frees up is not measured.

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

1. (done) 1.0.1 committed as 5c60396; 1.0.2 committed after its build.
2. User installs build/dist/SeisZoneSetup.exe 1.0.2 (UAC) and checks that the view is live, shows
   all channels, and "SeedLink server :18000" is listening.
3. The user chooses to stop the old GeckoSeedLink service: installer task "stopoldservice" or
   the Sources panel's "Free the port" / "Stop old service". Then check that "Gecko receiver"
   shows the SMR03 link (W610 redial time unknown, usually within a minute) - the SMR03 field test.
4. User syncs the PC clock (Windows Settings > Time > Sync now; it was 18.8 s fast), so the
   Latency column shows the real ~3 s.
5. User acceptance; then decide whether the old Gecko add-on folder is retired.
