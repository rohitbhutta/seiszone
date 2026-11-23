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

## Status (2026-11-22, version 1.7.0 installed 19:48)

* **2026-11-23 14:40 - 2.2.0 published** (merged #6 a6903b5; release v2.2.0 of
  rohitbhutta/seismoverse-releases, signed; checked end to end with the real GitHub). 2.2.0
  installed and running on this PC since 14:41; its daily update check works. Next update: raise the version, build,
  `python installer/publish.py --notes-file notes.md` (dry run first).

* **2026-11-23 (late) - Linux / Mac / iPhone plan, nothing built:** the user wants SeismoVerse on
  Linux and "iOS". Plan: docs/reports/2026-11-23-linux-macos-ios-plan.md (Linux AppImage + Mac
  .app via GitHub Actions builds, signed on this PC; iPhone/iPad not possible with PySide6 +
  numpy today -> live web page instead). Waiting for the user's answers (Mac or iPhone, Linux
  machine, WSL2, Apple account, Actions OK). The user parked it ("we will work on it later") and
  asked to work on updates first. **Found: the source repo rohitbhutta/seiszone is PUBLIC** (not
  private as noted below and in README); told the user, visibility is theirs to change.

* **2026-11-23 14:30 - signed updates (branch fix/signed-updates):** #5 merged (16b08c8). Releases
  must carry SeismoVerseSetup.exe.sig (Ed25519, core/ed25519.py) by the key in
  %USERPROFILE%\.seismoverse-release\signing.key (public d812e2fb...f19d in TRUSTED_KEYS). NEVER
  commit, upload or print that key; the user keeps an offline copy. Nothing published yet.

* **2026-11-23 14:00 - 2.2.0 on branch fix/windows-fit-screen (pull request, with 2.1.1):** updates
  through GitHub Releases of the PUBLIC repo rohitbhutta/seismoverse-releases (created; README
  only). Publish: build.py then installer/publish.py --notes "...". Commits to that repo through
  the API must use the noreply author (a Gmail slip happened). Nothing published yet.

* **2026-11-23 13:25 - 2.1.1 on branch fix/windows-fit-screen (pull request):** 2.1.0 is installed
  and running. Every window the main window opens is kept inside the screen (ui/screen_fit.py;
  this PC has 1536 x 824 points at 125 %); the Data server tabs scroll. See docs/history.md.
  Same branch: new PCs use SeismoVerse folders (paths.data_folder; a PC with SeisZone folders keeps
  them - this PC does), log file seismoverse.log; manual and guide have no SeisZone name. Update
  plan (GitHub Releases, public releases-only repo; the source repo is private):
  docs/reports/2026-11-23-update-plan.md - not built, waiting for the user's choice.

* **2026-11-23 13:13 - 2.1.0 merged (pull request #4, 4b0324d) and built (SeismoVerseSetup.exe
  68.9 MB; not installed yet):** Data server
  > GCF over UDP > "Send to a computer": computers added by hand (config gcf_server.clients) get
  all data without asking, until removed. SeedLink tab has no Add (protocol). Untested with the
  earlier viewer as receiver. See docs/history.md.

* **2026-11-23 12:42 - 2.0.0 "SeismoVerse" installed and running** (merged in pull request #3,
  65df26a; 38/39 sources as before; installed in place in C:\Program Files\SeisZone):
  the program is now named **SeismoVerse** (the user's request); the folders keep the old name
  SeisZone (DATA_FOLDER) and so do the package, mutex, Run value and installer AppId - see
  docs/history.md before renaming anything else. About shows only name, version, Author: Arnab
  Kole, Supported: Rohit Santra. Add source has no example line / Gecko note. Viewer label box:
  site name on top. User manual: docs/manual (Word + PDF), rebuilt with tools/manual. Word on
  this PC is unlicensed (cannot save/export via COM - do not use it for builds).

* **2026-11-23 - 1.9.0 merged (pull request #2, 15e1f18) and built** (build/dist/SeisZoneSetup.exe
  68.9 MB; not installed yet): SeedLink tab in the Data server window; SeedLink server disk
  buffer (core/packet_store.py, 2 days in %LOCALAPPDATA%\SeisZone\ServerBuffer): clients of this
  PC backfill after hours away and across restarts, viewers show older data. GCF gap recovery not
  built: findings in docs/reports/2026-11-23-gcf-gap-recovery-findings.md (do not guess the bytes).
  IIT R / IIT Roorkee 3 do not answer SeedLink on 18000 (2026-11-23). Add source has a Port box
  (empty = 18000; a typed port is used as given) and accepts any SeedLink server's banner. See
  docs/history.md.
* **2026-11-23 00:26 - 1.8.0 installed and running** (38/38 sources, missed data fetched; the
  first installer run closed SeisZone and ended without installing - check the version after an
  install). Data server off; not yet tried from a second computer.
* **2026-11-23 - 1.8.0 merged into main** (pull request #1, merge commit e7cdd87).
* **2026-11-23 00:10 - 1.8.0 built** (build/dist/SeisZoneSetup.exe 69.0 MB, from branch
  feature/viewer-recording-server c28970e; pull request #1 still open on GitHub then; not
  installed yet).
* **2026-11-22 night - 1.8.0 in a pull request (branch feature/viewer-recording-server):** site name in the label box, eye button (I), plain drag
  selects, recording names with start/end time, Data server (GCF UDP 1567, off by default) - see
  "Version 1.8.0" and docs/history.md.
* **2026-11-22 evening - 1.7.0:** viewer mode like the earlier viewer's WaveView (Per pixel 1:N,
  factors in the label, Scale others to match, Clean display, overlap) - see "Version 1.7.0" and
  docs/history.md. Its pull list now has all 36 GCF units (C:\scream\scream.ini, backup kept).

* **2026-11-22 afternoon - multi-instrument check (report only, nothing changed yet):** see
  docs/reports/2026-11-22-multi-instrument-check.md and docs/history.md (the running work log -
  add an entry there after every piece of work). The "band-pass only on the last instrument"
  complaint is the Common amplitude scale (one range for all instruments), not the filter. 97
  sheet IPs surveyed read-only: 34 SeedLink units, 1 Centaur, 4 GCF-only, 10.8.0.137 = this PC.
  Selector bug: location "1" gives refused selector "1HH?". Waiting for the user's OK to fix.
  Side-by-side check (report section 7): same data sample for sample (GCF vs SeedLink, Tuljapur),
  same filtering; only the Common amplitude scale differs - confirmed as the cause.
* **2026-11-22 evening:** Common scale per instrument + selector fix for location "1" are in the
  code (tests pass), NOT built/released yet. 33 sources from the IP sheet wait in
  tools/add_from_survey.py output (run `write` with SeisZone quit). Selection-menu plan in
  docs/reports/2026-11-22-selection-menu-plan.md: OK'd by the user, being implemented. See docs/history.md.
* **2026-11-22 16:11:** the 33 sources are added (38 sources, 149 channels, all receiving).
* Project at `C:\D Drive\Only Coding\seiszone` (git repository). Since 2026-11-21 it is pushed to
  the PRIVATE GitHub repository https://github.com/rohitbhutta/seiszone (remote `origin`, branch
  `main`; gh CLI logged in as rohitbhutta and set as git's GitHub credential helper). From
  2026-11-21 this repo's own git config commits as rohitbhutta's GitHub no-reply address, so new
  commits show as rohitbhutta. Commits up to d38392c and the commit that recorded the repository
  used rohitsantra8@... (GitHub account DevlpRohit); they were left as they are, not rewritten.
* **Version 1.2.0** (see "Version 1.2.0" below): closing any window no longer quits SeisZone;
  history of the last day in the viewers; Scream-like time scale, label panel, colours, filter
  design by mouse; protocol by port; log colours in light mode.
* **Version 1.1.0** (see "Version 1.1.0" below): the live waveforms are in viewer windows of
  their own with the full set of display tools of the user's earlier viewer (filters per window
  and per channel, filter design, offsets, spectrograms, cursors, ...); the Recordings window can
  be minimized / maximized and has a divider. `build/dist/SeisZoneSetup.exe` (1.1.0) upgrades
  any earlier version in place (same AppId). 1.0.2 is commit 4092142, 1.0.1 5c60396.
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

### Speed work after 1.4.0 (2026-11-21, released in 1.5.0)

* Profiled with `tools/profile_seiszone.py` (synthetic data only). The report, with before and
  after numbers, is `docs/profiling-2026-11-21.md`.
* Done:
  - (1) a live view moves only by whole pixels (`WaveformView._moves_a_pixel`, `LIVE_STEP_PX`);
  - (2) `ui/trace_cache.py` provides incremental filtering and min/max decimation, and
    `dsp.settle_samples` gives the refilter length (slowest pole down to 1e-10).
  - Result: a 60-minute or 24-hour view uses about 9 % of the UI thread instead of about 32 %.
* Precision is proven in `tests/test_trace_cache.py`: < 1e-7 against one sosfiltfilt over all
  the data, and the viewer curve matches the from-scratch curve to 1e-6.
* `python -m pytest tests -q`: 303 passed, 1 skipped.
* Git:
  - Fixes 1 + 2 are the 2 commits on the local branch `perf/live-viewer` (3ed0437, 775f776),
    NOT pushed: the user asked for a PR but "don't push it now".
  - PR text is in `C:\D Drive\Rohit Claude App\seiszone-PR-perf-live-viewer.md`.
  - Fixes 3–5 are on the stacked local branch `perf/backfill-axes-race` as 3 commits (fix 3
    4abcc7c, fix 5 b99aaa4, then fix 4 + these notes), also NOT pushed (2026-11-21).
* Fix 3: the SeedLink fetch of missed data parses each network read without decoding and
  decodes its records together (`mseed.decode_many`). `history.bin_reduce` has a quick path for
  records spanning at most 8 bins (`_bin_firsts`, bit-identical to the full path), and
  `Summary._merge` has a one-block path.
  - `add_record`: ~330 -> 86 us.
  - A day of 9 channels of backfill: ~221–348 s -> ~74 s of engine CPU.
  - Tests: `test_quick_bin_reduce_is_identical` and `test_quick_summary_merge_is_identical`.
* Fix 4: `_TimeAxis` in waveform_view works out the tick times once per range and shares them
  across all rows. It is correct and tested, but the measured gain is only ~1 % (within noise):
  the remaining cost of short windows is Qt curve painting, per-row axis geometry and the label
  setText.
* Fix 5: the fetch waits for the live connection's first record of its stations (`LIVE_WAIT`,
  20 s; a silent station: it starts anyway), so it knows where to stop and never takes live
  records.
  - Test: `test_the_fetch_waits_for_the_first_live_record`. It fails 3/3 with the wait
    disabled.
* `python -m pytest tests -q`: 307 passed, 1 skipped.
* Still open:
  - 1-minute windows are bound by Qt work per trace;
  - the old full-window filtering has a start transient of up to 0.66 % at the left edge for
    very slow filters (pre-roll capped at 60 s; `settle_samples` could set it);
  - no version bump or build yet.
* The user asked to check the viewer against the earlier third-party viewer "using REA".
  The REA/Ghidra work files are in `C:\D Drive\Rohit Claude App\rea-work`. Decompiling that
  viewer was declined: it breaks the standing rule above and probably its licence. A
  side-by-side comparison of the same data in both programs (behaviour only) was offered instead.

### Version 1.8.0 (2026-11-22; built 2026-11-23 00:09, installed 00:26; 38/38 sources)

* Viewer: `panel_lines(sid, units, label, scale, site)` -> 4 lines (site from
  hub.descriptions; the caption no longer has it); `set_captions` / eye button (`eye_button`,
  theme icons "eye" / "eye-off", key I, state "captions"); `set_drag_selects` (state
  "drag_selects", default True: a plain drag selects, Shift+drag pans).
* Recorder: `file_name` / `legacy_name` / `file_path(..., legacy=)`, `FILE_RE` matches both,
  `name_time(m)`; `_Session.path_for` keeps a period in its old-name file.
* `core/gcf_server.py` GcfServer (DatagramProtocol, hub listener), `gcf.encode_block` /
  `encode_status` / `encode_id` / `rate_code`; engine `_open_gcf_server` etc., status
  "gcf_server"; config "gcf_server" {enabled False, bind, port 1567, max_rate 1000};
  `probe.seiszone_status` (another SeisZone typed as IP:1567 -> its SeedLink server);
  `ui/server_window.py` DataServerWindow (main tool bar "Data server").

### Version 1.7.0 (2026-11-22, installed 19:48; 38/38 sources)

* **Per pixel (1:N) scale** like the earlier viewer's WaveView zoom: `WaveformView._cpp` (state
  "cpp"), range = cpp x row height (`_row_px`, from `_fit_rows`) / the trace's factor; Up/Down/wheel
  x2, Home = `fit_cpp` (median trace fills 0.9 of its row). The factor is shown in the label box
  (`panel_lines(..., scale)`). `scale_others_to_match` = that viewer's: per-channel factors
  (`match_factor`, 4 significant digits) + fitted zoom; Scale dialog takes "auto" and a channel id.
* **Clean display** (`clean`, key K: no row axes/captions/frames/gaps) and **Traces may overlap**
  (`overlap`, Shift+O: rows do not clip; lower rows draw on top).
* Checked against its 12-stream window: median trace height ratio 1.00 (report section 11).

### Version 1.6.0 (2026-11-22, installed 16:46; 38/38 sources, 153 channels)

* **Common scale per instrument.** The field complaint "with 5-6 instruments the band-pass only
  works on the last instrument" was the Common amplitude scale (one range = the largest trace of
  ALL channels; the others became flat lines). The filter always reached every channel. Common
  is now shared per sensor (`waveform_view.instrument`: NET.STA.LOC + band/instrument code);
  "Common, all channels" (`common_all`) keeps the old way. Proven with 60 min of 27 real channels.
  Checked again 2026-11-22 18:40 with all 118 waveform channels / 42 sensors: all filtered
  within 1.3 s; flat rows are faults (KS.ALM.0J.HHE dead, IS.PAZ.0J.BHZ glitches) - docs/history.md.
  Same instrument / minutes as the third-party viewer (Tuljapur, 18:55): same waveform, events, time
  (1 s) and amplitudes (5-11 %); its look = Scale "Fixed +-" 2048 counts (report section 8).
  19:10: all 36 GCF units added to its pull list (C:\scream\scream.ini, backup kept); GCF vs SeisZone
  135 channels, 415 059/415 059 samples identical (section 9). Next: picture compare + viewer plan.
  19:25: pictures match (section 10); filter needs no change; plan
  docs/reports/2026-11-22-viewer-like-plan.md - built in 1.7.0 (below).
* **Selectors for a one-character location** (Minimus Lite / older Minimus use "1"): "1HH?" was
  refused at every reconnect; now "1?HH?" (`seedlink_client.wire_selector` at sending, also for
  old configs; `probe.selectors_for` makes it so). Checked read-only on Tuljapur.
* **Selection menu like the third-party viewer's WaveView** (worked out from its User's Guide and
  screenshots only, no code): Shift+drag selects the rows from the start row to the row under
  the mouse; Ctrl+drag exactly two rows in the order picked (calibration: signal, then output);
  the selection stays drawn (light band behind the traces, hatched over a gap) while the menu is
  open; every tool works on the selected channels (`ViewerWindow._pick`; orientation and
  calibration add the rest of the stations from the viewer, `_with_station`). New items: "Save
  on selection" (tick, per viewer, state key `save_on_selection`; miniSEED into Exports) and
  "External tools" (`ui/external_tools.py`, config "tools": name / program / args with {files}
  {folder} {t0} {t1} {sids}; the selection written under %LOCALAPPDATA%\SeisZone\selections\).
* **33 instruments added** from the IP sheet (tools/add_from_survey.py; docs/reports/). 38
  sources, 149 channels. 10.8.0.137 is this PC - never add it.
* Report, survey and plans: docs/reports/2026-11-22-*.md; running log: docs/history.md.

### Version 1.5.0 (2026-11-22)

* Speed fixes 1-5 (above) + true UTC for latency + no Open viewer button in Channels (below).
* Built 2026-11-22: `build/dist/SeisZoneSetup.exe` (68.8 MB), exe reports 1.5.0 and contains
  `core/utc_clock` and `ui/trace_cache`. A headless run of the built exe was not possible:
  the user's SeisZone 1.4.0 was running (single instance). The user runs the installer.
* Installed 2026-11-22 00:25 (the user chose "you start it for me": no recording was running;
  `taskkill /PID` ended 1.4.0 cleanly, Claude started SeisZoneSetup.exe, the user clicked through).
  The log shows "PC clock checked against time.google.com: PC clock 19.0 s fast". Newest samples
  read from SeisZone's own SeedLink server (127.0.0.1:18000, INFO STREAMS): 200 sps channels of
  KA.KAND and DG.PATSI 2.3-4.2 s behind UTC (21.3-23.2 s by the PC clock).
* The version bump is the commit "SeisZone 1.5.0" on main, pushed 2026-11-22 at the user's OK.

### True UTC for latency, Channels panel without Open viewer (2026-11-21, in 1.5.0)

Why: the user asked what the ~21 s Latency was and to take the clock "from UTC time, which is
already working on the record data option". Recording is right because file names and periods
come from the data's own GNSS time stamps (`recorder.period_start(rec.start)`); Latency is
"now - newest sample", and "now" was the PC clock, measured 19.0 s fast on 2026-11-21 against
time.google.com, time.windows.com and pool.ntp.org (all three agreed). Real delay: ~2.5 s.

* `core/utc_clock.py`: an SNTP client (RFC 4330, UDP 123, 48-byte request, no user data). The
  process-wide `clock` asks the servers when the engine starts and every 15 min (2 min after a
  failure), keeps the shortest of 4 round trips (answers > 2 s are not trusted), and checks
  again within a second when the PC clock is stepped (Windows "Sync now"). `utc_clock.now()` is
  the PC clock + that offset; with no answer it is the PC clock, as before.
* Uses UTC now: Latency column, `hub` stats latency and the 600 s future-record check,
  `cli.status_text`. Arrival times (`last_arrival`, Sources "x s ago", recorder silence) stay
  on the PC clock because they are compared with the PC clock. The live viewer's right edge
  already followed the data (1.2.0), unchanged.
* Status bar field: "PC clock 19.0 s fast - corrected" (amber, tooltip: server, round trip, how
  to sync), "Clock: UTC ok" within 1 s, "PC clock not checked" with no internet.
* Settings: `"time": {"check": true, "servers": []}` in config.json (no dialog field yet).
  Tests set `SEISZONE_TIME_CHECK=off` (conftest) and use a fake SNTP server on 127.0.0.1.
* The user asked to remove the viewer option from the Channels panel and chose "only the Open
  viewer button": the panel's button is gone; ticks, Tick all / Untick all and the count stay,
  and the toolbar / Window menu Open viewer (Ctrl+Shift+N) still opens the ticked channels.
* Git: branch `feature/utc-clock` on top of `perf/backfill-axes-race` (321 passed, 1 skipped).
  On 2026-11-22 the user said push everything: `perf/live-viewer`, `perf/backfill-axes-race`
  and `feature/utc-clock` are on GitHub. Then "push everything to main": main was fast-forwarded
  to a6f3812 (no merge commit, no PR) and pushed. Then built as 1.5.0 (see Version 1.5.0).

### Version 1.4.0 (2026-11-16)

Why: the user asked for the four selection-menu entries of their earlier viewer that 1.3.0 left
out ("Do this also"). That viewer ships them as separate compiled programs with no description
of their maths; nothing was decompiled - they are the standard calculations, written new:
* `core/analysis.py`: `measures` (PGA, PGV, PGD, RMS, Arias, CAV, 5-95 % duration, predominant
  period), `integrate`, `response_spectrum` (frequency-domain oscillator; SD / PSV / PSA),
  `sine_fit`, `sine_calibration`, `transfer_function` (Welch cross spectra, coherence),
  `find_steps` / `step_response` / `step_gain` / `fit_velocity_step`, `equivalent_acceleration`
  (counts x V/count / (resistor x coil constant)). tests/test_analysis.py checks each against a
  case with a known answer.
* `ui/analysis_dialogs.py`: `StrongMotionDialog`, `CalibrationCheckDialog` (output channel,
  calibration channel, sensor type, µV/count, resistor, coil constant - kept per station in
  config "caltool"); viewer selection menu: Strong-motion analysis..., Sinewave Calibration...,
  Step Calibration..., Broadband Noise Calibration... (`ViewerWindow.open_analysis`).
* Limits to tell the user: the calibration checks need the instrument to SEND its calibration
  channel and the calibration signal to be switched on at the instrument (SeisZone does not
  command it); not tried on the real instruments.

### Version 1.3.0 (2026-11-16)

Why: the user compared a SeisZone viewer with their earlier viewer and found SeisZone "less
precise" - it turned out they showed different instruments (SeisZone KA.KAND = 10.8.0.181, the
other one 5269 = 10.8.0.180; the user confirmed) with different filters. Kept from it: a new
viewer now uses the Common scale (one amplitude scale for all channels, as that viewer draws),
not Auto. The "time difference" (20:06 vs 20:12) was only where each program's data began.
They also asked for that viewer's selection menu:
* Shift+drag selection shows the sample count per channel; menu: Save... (miniSEED), Zoom In,
  Spectrum..., PSD in Acceleration Units, PSD in Velocity Units (`ui/analysis_dialogs.py`
  PsdDialog, `dsp.psd`), Peak values (`peak_report`), Relative Orientation
  (`orientation_report`: rotation of a second station's N/E pair that best fits the reference N).
* Left out of 1.3.0, built in 1.4.0 (see above): strong-motion analysis and the Sinewave / Step /
  Broadband Noise calibration checks.
* A read-only data fetch from 10.8.0.180/.181 for a comparison was DENIED by the user: ask before
  contacting the instruments again.

### Version 1.2.1 (2026-11-16)

Why: with 1.2.0 the user saw KA.KAND "latency 7 h 28 min" (Gecko fine, VPN up). SeisZone had been
closed for hours; the SeedLink resume (DATA seq time) makes the server send the gap oldest first,
so the newest sample stayed hours old until the gap had come through the slow VPN.
* `seedlink_client`: live first. A resume position older than `LIVE_FIRST` (120 s), or none (new
  station: last day), -> plain DATA (live) and the gap begin noted in State `seedlink_gaps`
  ("<id>|<NET.STA>": time). `_backfill()` opens a second connection with `TIME <begin>`, feeds the
  hub, stops at the live connection's first record time, moves the begin forward every 5 s
  (minus `BACKFILL_MARGIN` 300 s) so a restart continues, clears the gap when done. Link detail:
  "live - fetching missed data, X to go". Short gaps keep the resume by sequence number.
  Uni-station sources (no stations configured) keep the old resume.
* Field check, read-only on 10.8.0.180 (scratch home): 200 sps channels live with ~20 s latency
  within seconds while the day was fetched at about 50x real time. The ~19 s that remain are the
  PC clock (18.8 s fast, still not synced).

### Version 1.2.0 (2026-11-12)

Why (the user's reports on 1.1.0, with screenshots of their earlier viewer):
1. Closing a Details box, the Label box, a viewer or the Recordings window closed ALL of
   SeisZone (log: "SeisZone was asked to close by another program"). Cause: the 1.0.x
   ExternalCloseFilter waited for WM_SYSCOMMAND/SC_CLOSE to recognise a user's close, but Windows
   SENDS that message straight to the window procedure and Qt's native filters never see it, so
   every user close looked like taskkill. Proven with real windows (scratch closetest.py). Now:
   a WM_CLOSE seen twice in a row came through the queue (posted: taskkill / installer); seen once
   is the user's. Real taskkill on a test process still ends it; tests with real Windows messages.
2. Viewers and the Recordings window reopened at every start: reopening viewers is now a Settings
   option, off by default (the Recordings window was never restored by itself).
3. A viewer showed no history ("backfill") - only data since SeisZone started. Now one day:
   hub per-second summaries (saved/loaded, `view_history.npz`), the recordings read in the
   background (`core/history.py`), and SeedLink TIME for a station's first connection (checked
   read-only on 10.8.0.180: TIME is accepted and sends data from 6 h back). The Gecko link sends
   live data only: its history comes from recordings (the user records SS.SMR03).
4. Time window like their viewer: arrows and pixels per second, up to 1440 minutes.
5. Filter design like their viewer: red / green parts, Hz + secs + order, Gain (dB), left / right
   mouse drag of the corners, axis in Hz and seconds.
6. Typing only an IP or IP:18000 seemed to "take GCF": the probe did pick SeedLink (checked
   read-only on 10.8.0.180), but the channel list showed the instrument's GCF stream names.
   Now the GCF names show only for a GCF connection, and the port rules are explicit.
7. Label panel on the left like their viewer (station, channel, units such as g) with colours by
   component (Z green, N red, E sky blue, others blue); "Label..." names a channel (shown there);
   the viewer's own name is "Window title...".
8. Light mode: old log lines kept the dark theme's pale colour - they are coloured again.
* SMR03 has no StationXML: "g" needs a Calibration (counts per m/s²) for its channels.
* The viewer's tool bar is a QToolBar now: the old bar forced the window wider than the screen
  (found by a width check in tests).
* Tests: tests/test_history.py (new), test_ui_viewer (panel, labels, design drag, time scale),
  test_probe (port rules), test_seedlink (first-connection TIME), test_ui_shell (close filter
  with real messages, log colours, reopen option).

### Version 1.1.0 (2026-11-11)

Why: the user (with screenshots of the Recordings window and of their earlier viewer's trace
window and its right-click menu) asked for: (1) a minimize option on the Recordings window and a
way to drag the recordings list bigger / smaller "like the channels section of the main
window"; (2) the viewer NOT inside the application by default: the user selects channels and a
viewer pops up as a new window; (3) every option of the earlier viewer's trace window (they
named the bandpass filter on/off, filters for each channel, parameters and the right-click menu)
researched and built in. Research source: that viewer's own help pages and user guide PDF that
are installed on this PC (read only; its name is not written anywhere in SeisZone).

* `ui/viewer_window.py` `ViewerWindow` (parentless QMainWindow: own taskbar button, minimize,
  maximize; menus Channels / View / Filter / Tools / Window / Help; right-click menu in two parts
  - channel: Colour C, Scale S, Scale others to match Ctrl+S, Details D, Filter submenu, Offset
  submenu (follow mean removal / Null / Zero Z), Locked offset, Amplitude cursors on this
  channel, Spectrogram P (+/- contrast), Calibration, Remove Del; window: Background colour, Lane
  lines L, Overlay O, Spectrogram size, All spectrograms Ctrl+P, Sort by, Channels, Label, Clear
  Ctrl+Del, No caption F11 (full screen), Duplicate Ctrl+D, Spectrum / Export / Record these
  channels / Save picture; Print Ctrl+Shift+P; Shift+drag selection menu: zoom, spectrum,
  export, use as noise / signal in the filter design; status bar mouse readout).
* `ui/waveform_view.py` rewritten around the old speed design: tool bar Channels, Window -/+,
  Amplitude -/x1/+ (x2 steps), Filter on/off split button (F) with the filter list, Units
  (counts / physical / physical with acceleration in g), Scale (auto / common / fixed), Remove
  mean, Null (N), Time cursors (dt and 1/dt), Ampl cursors, Live. Wheel = amplitude, Shift+wheel
  = window, Ctrl+wheel = scroll (pauses). Per-channel settings in `traces[sid]` (colour, scale,
  own filter, offset, locked, spectrogram, contrast); `state()` saved per viewer.
* `ui/filter_dialog.py`: filter list (Default band-pass = 0.1-0.9 x Nyquist, 10-30 s at 1-2 sps;
  the ready-made filters; saved filters in config "filters"), `FilterDesignDialog` (high-/low-pass
  on/off, corner in Hz or s, order 1-8, response graph, left click = HP corner, right click = LP
  corner, noise / signal spectra, Apply / OK / Auto-apply / Save).
* `ui/viewer_dialogs.py`: ScaleDialog, DetailsWindow (mean/min/max/diff/RMS, offset box,
  lines on the plot), CalibrationDialog (counts per unit -> config "calibration", used before
  StationXML), ChannelPickerDialog.
* `core/dsp.py` filter specs (`apply_spec`, `default_band`, `describe`, `response`,
  `spectrogram`, ...). `config.py`: DEFAULTS `viewers`, `filters`, `calibration`; `set_value()`.
* Main window: Channels table in the centre ("View" ticks + Open viewer, double-click, right-click
  "Open in a new viewer" / "Add to viewer", drag rows into a viewer), Log at the bottom, Window
  menu; no auto-shown channels any more (the "hidden" list is gone). Viewers open at quit are
  saved (`config["viewers"]`) and reopened at the next start (at the first show of the window).
* Recordings window: parentless, minimize / maximize / close buttons, shown non-modal; vertical
  QSplitter between the list and the scrolling "New recording" form; size and divider kept in
  `config["ui"]["record_geometry" / "record_splitter"]`; a viewer's "Record these channels"
  ticks exactly its channels.
* An independent 3-agent review found 18 defects in the new code (ambiguous Ctrl+F, overlay
  leftovers, Shift+drag losing the selection, overlay Y ranges, filter design dialog tracking,
  saved filter caching / broadcast, menu leaks, Details lines left on Esc, L/O keys, export
  window reuse, seconds rounding, untouched default band-pass); all fixed with a regression test
  each (tests/test_ui_viewer.py end).
* Tests: tests/test_ui_viewer.py (new), test_dsp, test_ui_data, test_ui_shell, test_ui_dialogs,
  test_integration updated. `python -m pytest tests -q`: 252 passed.
* Test process exit: the test run (also at 1.0.2) now and then crashed AFTER all tests passed
  (access violation in Qt teardown / DLL unload). tests/conftest.py `pytest_unconfigure` now ends
  the process with TerminateProcess (exit code = pytest's result; os._exit alone still crashes
  in the DLL unload). The 12 x 60 min speed test measures without GC pauses and measures once
  more when a busy PC makes it miss (1.0.2 code missed its 150 ms limit 3 of 4 times alone;
  1.1.0 is faster: 73-135 ms).
* The frozen 1.1.0 build contains the new modules (checked in the PyInstaller TOC). A GUI run of
  the frozen exe was not possible: the user's own SeisZone was running (single-instance mutex);
  the check only brought the user's window forward.
* Screens: build/screens/viewer.png, main_window.png, record_window.png, filter_design.png.

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

1. (done) 1.0.1 = 5c60396, 1.0.2 = 4092142, 1.1.0 = 465a94a, 1.2.0 = af71f90, 1.2.1 = af4da1b, 1.3.0 = 7a7cfe8, 1.4.0 = 6cce137, 1.5.0 = commit "SeisZone 1.5.0" on main. 1.6.0 = 3fc703d "SeisZone 1.6.0" on main, pushed 2026-11-22 at the user's OK. 1.7.0 = ee9c838 (installed 2026-11-22, pushed 2026-11-22 at the user's OK).
2. User installs build/dist/SeisZoneSetup.exe 1.2.0 (UAC); checks closing viewers / dialogs keeps
   SeisZone running, a 1-day window shows the history, the filter design drag, the label panel.
3. Optional: a Calibration for SS.SMR03 channels so they can be shown in g.
3. The user chooses to stop the old GeckoSeedLink service: installer task "stopoldservice" or
   the Sources panel's "Free the port" / "Stop old service". Then check that "Gecko receiver"
   shows the SMR03 link (W610 redial time unknown, usually within a minute) - the SMR03 field test.
4. User syncs the PC clock (Windows Settings > Time > Sync now; 19.0 s fast on 2026-11-21).
   Since the UTC clock work the Latency column is right anyway; the status bar shows the error.
5. User acceptance; then decide whether the old Gecko add-on folder is retired.
6. WAITING for the user's OK (2026-11-22 report): per-instrument Common scale; selector for a
   one-character location; Centaur without INFO; offer only live streams; local server TIME with
   end time; which sheet instruments to add (never 10.8.0.137 = this PC).
