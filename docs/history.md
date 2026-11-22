# SeisZone - work history

One dated entry per piece of work, newest first, so nothing is lost if a session or the
assistant breaks. memory.md holds the current state and the rules; this file holds what was done,
what was found and what is waiting. Reports with the details are in docs/reports/.

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
