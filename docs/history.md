# SeisZone - work history

One dated entry per piece of work, newest first, so nothing is lost if a session or the
assistant breaks. memory.md holds the current state and the rules; this file holds what was done,
what was found and what is waiting. Reports with the details are in docs/reports/.

## 2026-11-22 (evening, 2) - selection menu implemented; version 1.6.0

* Shift+drag over several rows / Ctrl+drag for two channels; tools on the selected channels;
  Save on selection; External tools (Set up external tools...). Tests added (viewer, analysis).
* Version bumped to 1.6.0 (with the Common-per-instrument and selector fixes). See memory.md
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
