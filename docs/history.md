# SeisZone - work history

One dated entry per piece of work, newest first, so nothing is lost if a session or the
assistant breaks. memory.md holds the current state and the rules; this file holds what was done,
what was found and what is waiting. Reports with the details are in docs/reports/.

## 2026-11-23 midday - 2.0.0: renamed SeismoVerse; About, Add source, label box; user manual

Branch feature/seismoverse-name. The user asked (with four screenshots):
* "Change the application name as SeismoVerse": APP_NAME, window titles, tray, messages, the
  SeedLink server's software string ("SeismoVerse v2.0.0"), GCF status text, probe user agent,
  export report, installer (SeismoVerseSetup.exe, SeismoVerse.exe, Start menu, firewall rule),
  version_info, README and user guide. Version 2.0.0.
  * Kept on purpose: the per-user folders (DATA_FOLDER = "SeisZone": %APPDATA%\SeisZone,
    %LOCALAPPDATA%\SeisZone, Documents\SeisZone), so the 38 sources, settings, recordings, logs
    and the server buffer stay; the Python package "seiszone"; the single-instance mutex and
    activate names; the Run value name "SeisZone" (an entry that starts the old SeisZone.exe is
    taken over by SeismoVerse.exe); the installer's AppId (an update stays in
    C:\Program Files\SeisZone and removes SeisZone.exe and its shortcuts; both exes are closed
    before installing; the old "SeisZone" firewall rule is deleted).
  * The saved SeedLink organization "SeisZone" (the old default) becomes "SeismoVerse"; a typed
    one stays. Another computer's status blocks are recognised under both names.
* About: the "Open standards" list and the Folders list removed; it shows the name, version,
  "Author: Arnab Kole" and "Supported: Rohit Santra" (AUTHOR / SUPPORTED_BY in __init__.py).
* Add source: the example addresses line and the Gecko note under it removed.
* Viewer label box: the site name first (bold), then station, location.channel, units.
* Bug found by the rename: probe.seedlink_info dropped trailing spaces of every 456-byte INFO
  piece; when a piece ended on a space between two XML attributes the station list came back
  empty (the 3 characters longer software name moved a boundary onto a space). Only NUL padding
  is dropped now; regression test splits right after a space (fails on the old code).
* User manual (the user: "Make a user manual of the application", format "Word and PDF file"):
  docs/manual/SeismoVerse_User_Manual.docx and .pdf, 25 pages, 14 chapters + keyboard appendix,
  8 screenshots made offscreen from the test data (tools/manual/shots.py). Built by
  tools/manual (docx-js for Word, reportlab for the PDF from the same content). Word on this PC
  is an unlicensed product (opens read-only, cannot save or export), so the PDF is not made by
  Word and the Word file's contents is a clickable chapter list instead of a TOC field. The Word
  file was checked for unique bookmark ids; it could not be opened in Word here.

## 2026-11-23 - 1.9.0 merged into main and built

* At the user's OK ("Merge and build"): pull request #2 merged (merge commit 15e1f18), local
  main pulled. Full suite on main: 357 passed, 1 skipped.
* `python installer/build.py`: build/dist/SeisZoneSetup.exe 68.9 MB, SeisZone.exe reports 1.9.0.
  Not installed (the user runs the installer).

## 2026-11-23 03:00 - 1.9.0: Port box in Add source; any SeedLink server (same pull request)

* Asked: SeedLink sources by IP and a port typed by hand; no port = 18000 (default); a typed port
  is used as given; detect any type of SeedLink server.
* Add source: a **Port** box beside the address (placeholder 18000). Empty = the probe's default
  (SeedLink 18000, then GCF 1567); a typed port is used exactly (SeedLink first, then GCF).
  "IP:port" in the address box still works; two different ports are refused; "Add anyway as
  SeedLink" uses the Port box (empty = 18000). Picture docs/reports/2026-11-23-add-source-port-box.png.
* probe: one banner reader (_read_banner) for HELLO and INFO: lines ending CR LF, LF or CR, and
  servers that send only one banner line (after BANNER_SECOND_LINE_WAIT 1.5 s) are accepted; the
  first line must name SeedLink or SLPROTO (another service on the port is not taken for it).
* Tests: test_any_seedlink_server_banner (5 server answers), test_add_dialog_port_box.

## 2026-11-23 02:00-03:00 - 1.9.0: SeedLink server disk buffer (same pull request)

* Asked: "fix the SeedLink backfill in any kind of option", plan first. Measured before planning
  (SeisZone's own SeedLink server read on this PC with ObsPy and raw sessions):
  * 36 SeedLink stations: no gaps in the last hour, also across the disconnects 00:37-00:50
    (resume + TIME fetch work). (ObsPy's helper fails on 1-character location codes with "*":
    an ObsPy limit, "??"/"???" works - not a SeisZone fault.)
  * XX.PRAT / XX.SIRA (the GCF-UDP IIT instruments): real gaps (10 s, 60-83 s): no backfill.
  * SeisZone's SeedLink server reached back only ~1 h 40 min (200,000 packets in memory) and
    nothing across a SeisZone restart: clients of this PC could not backfill longer outages.
* Plan approved ("Yes, do all of it"):
  1. Read-only probe of 10.8.0.121:18000 and 10.8.0.163:18000: nothing answered - they stay on GCF
     (SeedLink may be switchable in their web pages; then add them with port 18000).
  2. core/packet_store.py PacketStore: every packet also on disk (packets/<YYYYMMDD>_<HH>.slp:
     seq + length + raw, per arrival hour; and <NET.STA>/<day>/<NET.STA>_<date>_<HHMM>_<HHMM>.mseed
     per station and data hour). Default 2 days (config seedlink_server.buffer_days, 0 = off),
     folder %LOCALAPPDATA%\SeisZone\ServerBuffer (not synced; another config: beside it). Kept
     below MAX_SPAN 0x700000 sequence numbers (24-bit half range). Purge every 10 min in a worker
     thread (old files only; recordings never touched); pauses below 2 GB free.
     hub.first_seq / has_seq / packets_from / seq_for_time reach into it; the server starts TIME
     requests at the hour of the begin time and warns "fell behind" only for packets really gone.
     Engine: numbers continue after the newest packet on disk (+ SEQ_SAFETY_GAP); status
     server.buffer; the buffer folder is one of recording_folders() (viewers) and Export's folders.
  3. Viewers show data older than the live buffer from it (RecordedData reads it like a recording).
  4. Data server window, SeedLink tab: "Keep on disk" days (0 = off) and a line with size, folder,
     oldest time. Picture docs/reports/2026-11-23-data-server-seedlink-buffer.png.
* GCF gap recovery findings (v45 trailer: version 0x2d, 3-byte block number, source name;
  "Request oldest v45" / "Request block N" over TCP on the same port; bytes unknown):
  docs/reports/2026-11-23-gcf-gap-recovery-findings.md. The user then chose the SeedLink work.
* New tests: tests/test_packet_store.py (8: round trip + restart, skipped numbers, purge, hub,
  a client away for hours across a SeisZone restart in both resume styles, TIME start, viewers);
  engine (buffer default, status, folders, numbers after a crash, off); Data server window.
* Version 1.9.0.

## 2026-11-23 night - 1.8.1 on branch feature/backfill-seedlink-server (pull request)

* Asked (2 screenshots): "live data viewing backfill" like the recording backfill, with the
  earlier viewer's manual and its protocol document as reference; and the SeedLink server
  (port 18000) in the Data server window next to the GCF server.
* Reference read: the user's manual (Downloads, MAN-SWA-0001 (1).pdf; sections 4.3.2 and 7.4) and
  BdsImportStreamGcf.pdf (a third party's notes). GCF gap recovery: each block carries the
  server's block number; a client that sees a jump opens TCP on the same port and asks for the
  missing blocks one by one, newest first. The command format is only in SWA-RFC-SCRM.pdf, which is
  NOT on this PC (searched Downloads, Desktop, Documents, the user folder, D Drive and the earlier
  viewer's own help files; not found on the web either). Not guessed: waiting for the document.
* Found: SeisZone's live viewers already backfill. The hub inserts late records into the live
  buffer (StreamBuffer._insert) and an open viewer re-reads the hub every PAUSED_RELOAD (2 s).
  Shown with a 20 s gap that closed within one refresh. New test
  test_open_viewer_fills_a_gap_when_the_missed_data_arrive. User guide: "Live backfill".
  Limits: data older than the live buffer come from the recordings; GCF UDP sources (2) cannot
  backfill until the gap recovery above is built.
* Data server window (ui/server_window.py) now has two tabs: "GCF over UDP (earlier viewer)" and
  "SeedLink over TCP" (enable, TCP port, state, this PC's addresses, Clients: Client / Stations /
  Connected since / Packets sent, Control). Apply saves both sections. The SeedLink port is
  checked like Settings (number, not a Gecko port). Bind and organization are kept, still edited in
  Settings. SeedLinkServer.control (connect / streaming / disconnect lines) goes into
  status()["server"]["control"]. Picture: docs/reports/2026-11-23-data-server-seedlink-tab.png.
* Version 1.8.1. Full suite 343 passed, 1 skipped. Pull request rohitbhutta/seiszone#2.
* The user then put SWA-RFC-GCFR.pdf (GCF block format, Issue F 2021) in Downloads and pointed
  at the manual again. Neither has the network wire format: the manual's chapters 4.3 and 7 only
  describe GCFPING / GCFSEND:B / GCFSTOP and that backfill goes over TCP on the same port, and
  GCFR says the network protocol is in "SWA-RFC-SCRM". GCF gap recovery is still waiting for that
  document.

## 2026-11-23 00:26 - 1.8.0 installed and running

* First run of the installer (00:15): SeisZone was closed ("SeisZone stopping" 00:15:38), then
  the installer ended without installing (1.7.0 still in Program Files and in Windows' list, no
  error event). The user ran it again; 1.8.0 installed and started 00:26:21 (pid 6104).
* Log: "engine started: 38 source(s), Gecko on 58772, SeedLink server on 18000, GCF server off";
  36 SeedLink sources connected and fetched the data missed since 18:45 UTC (33 of 36 done
  within 30 s); both GCF sources answered GCFPING; Gecko SS.SMR03 sending. No errors or warnings.
* No recording is active (the last one stopped 2026-11-15), so no file with the new names yet.
* Next: a test with a second computer pulling from this PC's data server.

## 2026-11-23 - 1.8.0 merged into main

* At the user's OK ("Yes, merge it"): branch pushed with the build note, pull request #1 merged
  (merge commit e7cdd87); local main pulled. The branch is kept. Installer unchanged (same code).

## 2026-11-23 00:10 - 1.8.0 built

* The user wrote "Merged, build the installer". GitHub still showed pull request #1 open and
  main at 338dffa, so the installer was built from the branch head c28970e (the same code).
* `python installer/build.py`: build/dist/SeisZoneSetup.exe 69.0 MB (00:09), SeisZone.exe
  reports 1.8.0; the frozen archive holds seiszone.core.gcf_server and seiszone.ui.server_window.
* Next: the user merges the pull request and runs the installer (UAC); then a test with the earlier
  viewer or SeisZone on a second computer pulling from this PC's data server.

## 2026-11-22 night - 1.8.0 on branch feature/viewer-recording-server (pull request)

* Asked (with 5 screenshots): site name into the label box, an eye button to hide the details
  above the traces (between Remove mean and Time cursors), the selection menu of the earlier
  viewer, recording file names with start and end time like the earlier viewer's, backfill of
  recordings after a PC restart, and the earlier viewer's "My Server" (UDP 1567) in SeisZone; a
  pull request the user reviews and merges.
* Viewer: label box = station / location.channel / **site** / units (site no longer repeated
  in the caption); **eye** button + key I (state "captions"); a **plain drag selects** a time
  range and opens its menu, as in the earlier viewer (Shift+drag pans; View > "Drag selects a
  time range" switches back, state "drag_selects"). The selection menu already had Save, Zoom
  In, Save on selection, PSD acc/vel, the three calibrations, Relative Orientation and external
  tools (where the earlier viewer has its add-on); before this a Shift+drag was needed.
* Recording names: `<code>_<YYYYMMDD>_<HHMM>_<HHMM>.mseed` (UTC, last of a day ends 2400), e.g.
  KA.KAND.9J.MNE_20261110_1600_1700.mseed. Old `__<stamp>Z` names are still read (viewers,
  export) and never renamed; a period begun under the old name goes on in that file.
* Backfill after a restart: already there for SeedLink sources (resume + TIME fetch from the
  last record); now covered by test_computer_off_while_recording_is_backfilled_into_the_same_files
  (power cut 12:02-12:40: every record once, in its hour file). GCF-UDP sources (2: IIT Roorkee)
  cannot backfill - written in the user guide.
* **Data server** (core/gcf_server.py, ui/server_window.py): GCF over UDP, port 1567, **off by
  default** (the earlier viewer on this PC uses 1567: a busy port is reported, retried every 3 s,
  never fatal). GCFPING/GCFSEND:B/GCFSTOP, clients removed after 180 s without a request, max
  32, "Channels up to" 1000 sps. Records re-cut into GCF blocks on whole seconds (<= 2 s, 8/16/32
  bit, checked with ObsPy's GCF reader); live and backfilled pieces kept apart. Names kept in
  State "gcf_server_ids" (KA.KAND.0J.HNZ -> KAND HN0JZ0; GCF sources keep their own). Status
  block per station names SeisZone + its SeedLink port + the channel table: Add source
  "<IP>:1567" on another SeisZone then uses that SeedLink server (exact names, backfill), else
  GCF with the exact names. Status bar "Data server :1567 - n clients".
* Version 1.8.0. Full suite 342 passed, 1 skipped. Pull request rohitbhutta/seiszone#1 (branch
  feature/viewer-recording-server), waiting for the user's review and merge. Not built, not installed.

## 2026-11-22 19:55 - 1.7.0 pushed to GitHub

* At the user's OK: main pushed to origin (90f0834..f02bd41): the checks against the earlier
  viewer (reports sections 8-11, pictures, GCF comparison list), the viewer plan and 1.7.0.

## 2026-11-22 19:50 - 1.7.0 installed

* The user quit SeisZone from the tray and chose "start the installer"; Claude started
  build/dist/SeisZoneSetup.exe, the user clicked through. SeisZone did not start by itself after
  the install; Claude started it from the desktop shortcut at 19:48. Log: "SeisZone 1.7.0
  starting", "sources 38/38 receiving, 153 channels" (it grows to ~170 as slow channels arrive,
  as before). One "THA packet is no valid miniSEED record; skipped" at connect - seen at every
  THA connect since 16:15, not new. Off 19:46:15-19:48:41. Not pushed.

## 2026-11-22 20:20 - 1.7.0 built

* Full suite 328 passed, 1 skipped. Commit ee9c838. build/dist/SeisZoneSetup.exe (68.8 MB, exe
  reports 1.7.0). Not installed yet (the user quits SeisZone from the tray, then runs it). Not pushed.

## 2026-11-22 20:10 - 1.7.0: a viewer mode like the earlier viewer's WaveView

* At the user's OK ("Yes, build it"): Scale "Per pixel (1:N)" (cpp, power of two, Up/Down/wheel
  x2, Home fits), factor shown in the label box, Scale others to match like that viewer (factors
  + fitted zoom), Scale dialog "auto" / a channel's name, Clean display (K), Traces may overlap
  (Shift+O). Tests: test_per_pixel_zoom_like_the_earlier_viewer, test_clean_display_and_overlap,
  scale-to-match test rewritten. Version 1.7.0.
* Side by side with its 12-stream window in the same settings: median trace height ratio 1.00
  (0.84-1.06 per row). Report section 11; picture 2026-11-22-side-by-side-per-pixel-mode.png.
* Not built: its tick rhythm (minute ticks, longer every 10 min).

## 2026-11-22 19:25 - multi-instrument side by side; viewer plan written

* Its ~130-channel window: one zoom 1:536 870 912, all flat but IS.PAZ.0J.BHZ raw (glitching
  sensor) - the same flat effect the user reported, in that viewer.
* Its 12-stream window vs SeisZone (Fixed +- with per-trace gain = its factors): same events,
  same relative sizes; each of its traces = zoom / factor (within 4-17 %).
* Filter: IIR and FIR designs all give 0.90-0.92 of its drawn amplitude - no filter change needed.
* Plan for a viewer mode like that WaveView: docs/reports/2026-11-22-viewer-like-plan.md
  (counts-per-pixel zoom, factor in the label + match others, pixels per second, clean rows,
  overlap, minute ticks). Waiting for the user's OK.

## 2026-11-22 19:10 - all instruments added to the third-party viewer; data identical

* At the user's request (user closed it first): 32 UDP pull servers added to C:\scream\scream.ini
  (36 in all; backup scream.ini.backup-2026-11-22-before-adding), restarted from the desktop
  shortcut, 57 streams after 20 s. Not added: THA .7, Centaur .119, Gecko.
* GCF (what it receives) vs SeisZone SeedLink, every instrument: 135 channels, 415 059 of 415 059
  samples identical. Report section 9; list 2026-11-22-gcf-vs-seedlink-all.txt.
* Waiting: its WaveViews refill only from its start (~20 min); then compare the pictures of the
  multi-instrument window and plan the viewer changes the user asked for ("like that viewer").

## 2026-11-22 18:55 - same instrument, same minutes: SeisZone vs the third-party viewer

* Tuljapur (7465) over 12:57-13:21 UTC: same band (0.1-0.9 x Nyquist), same events at the same
  time (within 1 s), peaks within 5-11 %, envelope correlation 0.79-0.91. Only intended
  difference: its scale is one fixed counts-per-pixel zoom (1:16, DefAmpScale 2048); SeisZone's
  default fits each sensor. Scale "Fixed +-" 2048 + Units "Counts" looks the same. Details:
  docs/reports/2026-11-22-multi-instrument-check.md section 8; picture
  docs/reports/2026-11-22-side-by-side-tuljapur.png. Its settings were only read
  (C:\scream\scream.ini, calvals.txt); no code read.

## 2026-11-22 18:40 - check: band-pass with every instrument in one viewer (1.6.0)

* The user asked whether the viewer now filters correctly with many instruments, like the
  third-party viewer. Checked offscreen with the 1.6.0 code and the last 10 minutes of every
  station read from SeisZone's own server (127.0.0.1:18000; no instrument contacted): 118
  waveform channels from 42 sensors, Default band-pass, Scale "Common per instrument".
* All 118 rows filtered. With the app's 250 ms refresh: 89 rows at once, all 118 within 1.3 s
  (the refresh budget works round-robin, about 2.5 ms per row). Picture of 6 sensors:
  docs/reports/2026-11-22-many-instruments-bandpass.png.
* Rows that look flat are real, not the filter: KS.ALM.0J.HHE is dead (raw value constant, range
  0); IS.PAZ.0J.BHZ has huge glitches (raw range 16.6 million counts against ~5000 on E/N), so
  its E and N look flat on the shared per-instrument scale (use Scale "Auto" or a per-trace
  gain to see them). Quieter Z components (PATSI, BHD, SRG, HID, KAND) are simply smaller.
* Not seen on the user's screen: no SeisZone viewer was open, and the third-party viewer showed
  one instrument (7465), so there was no many-instrument side-by-side.

## 2026-11-22 16:50 - pushed to GitHub

* At the user's OK: main pushed to origin (f4d3c0c..6799cdb), all 1.6.0 work.

## 2026-11-22 16:46 - 1.6.0 installed

* The user quit SeisZone from the tray and chose "start the installer"; Claude started
  build/dist/SeisZoneSetup.exe, the user clicked through. Log: "SeisZone 1.6.0 starting",
  "sources 38/38 receiving, 153 channels", no refused selector, no error. No viewer was open, so
  the new scale / selection menu were not seen on screen yet (they are covered by the tests).

## 2026-11-22 (evening, 2) - selection menu implemented; version 1.6.0

* Shift+drag over several rows / Ctrl+drag for two channels; tools on the selected channels;
  Save on selection; External tools (Set up external tools...). Tests added (viewer, analysis).
* Version bumped to 1.6.0 (with the Common-per-instrument and selector fixes). Full suite
  326 passed, 1 skipped. Built 16:31: build/dist/SeisZoneSetup.exe (68.8 MB, exe reports 1.6.0);
  not installed yet (the user installs it). See memory.md
  "Version 1.6.0".

## 2026-11-22 16:11 - 33 instruments added

* The user quit SeisZone from the tray; tools/add_from_survey.py write added 33 sources (38 in
  all) and fixed Tuljapur's selector ("1HH?" -> "1?HH?"). SeisZone 1.5.0 started again: "sources
  38/38 receiving, 149 channels", no refused selector. PC clock now 18.4 s fast.
* Next: the selection menu (user OK'd the plan), then build 1.6.0 (Common per instrument needs it).

## 2026-11-22 (evening) - fixes for many instruments; sources prepared; selection plan

* Fixed (not built yet): Common scale per instrument (`waveform_view.instrument`; old behaviour
  as "Common, all channels"); one-character location selectors ("1HH?" -> "1?HH?",
  `seedlink_client.wire_selector`, `probe.selectors_for`; "1?HH?" checked read-only on
  Tuljapur). Tests added; suite 321+ passed (2 failures from negated selectors fixed after).
* tools/add_from_survey.py: probed the answering units read-only; 33 sources ready (skipped
  10.8.0.137 = this PC, and 10.8.0.21 / .48 = GCF relays of Ralej and Kavdej, which are added
  directly). Writing them needs SeisZone quit (config would be overwritten) - quitting it from
  here was not allowed, the user is asked to quit it from the tray. Config backup:
  %APPDATA%\SeisZone\config.backup-2026-11-22-before-adding.json.
* Selection menu plan (Shift+drag over several channels): docs/reports/2026-11-22-selection-menu-plan.md
  - waiting for the user's OK.

## 2026-11-22 (afternoon, 2) - side-by-side behaviour check (report only)

* Behaviour only (no code of the third-party viewer read): its windows, its User's Guide and its
  settings. It pulls GCF/UDP 1567 from Tuljapur, Nagdhaniba, Panchavada, PATSIO.
* Data: 30 s of Tuljapur GCF vs SeisZone's SeedLink data: 9 400 / 9 400 samples identical.
* Filter: both apply it to every channel, same default band. Amplitude: the viewer uses a
  user zoom + per-stream / per-instrument factors; SeisZone's Common scale (largest of all
  channels) is the cause of the "only one instrument filtered" look.
* Report section 7. Still waiting for the user's OK to fix.

## 2026-11-22 (afternoon) - multi-instrument check (report only, nothing changed)

* Asked: add all IPs of `Downloads\IP Distrubation.xlsx`, check SeedLink 18000 vs GCF 1567,
  fix "band-pass only on the last instrument's channels", make a plan, then (second message)
  "check everything first, then tell me".
* Found: the filter does reach every channel; the "Common" amplitude scale makes all but the
  largest instrument flat lines. Proven with 60 min of the 27 real live channels.
* Survey of 97 IPs (read-only): 34 Guralp SeedLink units, 1 Nanometrics Centaur (INFO timed
  out), 4 GCF-only sites, 10.8.0.137 = this PC, 57 without a service / no answer.
* Bugs found: selector `1HH?` refused for one-character location "1" (17 units); local
  server TIME with end time sent nothing.
* Report: docs/reports/2026-11-22-multi-instrument-check.md (+ survey table/json, image).
* Waiting for the user's OK on the fix plan (report section 1 and 4) and on which instruments to
  add.

## 2026-11-22 (night) - version 1.5.0

* True UTC for latency (time servers), Open viewer button removed from Channels, speed fixes
  1-5; pushed to main, installer built, installed by the user (see memory.md "Version 1.5.0").
