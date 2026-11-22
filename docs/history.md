# SeisZone - work history

One dated entry per piece of work, newest first, so nothing is lost if a session or the
assistant breaks. memory.md holds the current state and the rules; this file holds what was done,
what was found and what is waiting. Reports with the details are in docs/reports/.

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
