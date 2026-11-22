# Multi-instrument check, 2026-11-22 (SeisZone 1.5.0)

The user's request: add every IP of the sheet `IP Distrubation.xlsx` (Downloads), check the
connections (SeedLink 18000 vs GCF 1567), and fix the viewer, where "with five or six instruments
the band-pass only works on the last instrument's three channels". Then the user said: check
everything first and report before changing anything. Nothing in SeisZone's code or the user's
configuration was changed for this report; no source was added yet.

Files next to this one:
* `2026-11-22-ip-survey.md` / `.json` - the survey of every IP in the sheet (table / raw data)
* `2026-11-22-common-scale-flat-traces.png` - the viewer with 27 real channels, band-pass on,
  "Common" amplitude scale (offscreen render: the fonts show as boxes there)

## 1. Viewer: "the band-pass works only on the last instrument" - cause found

**The filter is applied to every channel. The channels look unfiltered because of the "Common"
amplitude scale** (the viewer's default, saved in the user's config as `view.scale = "common"`).

Common scale gives every row the same amplitude range: that of the largest trace of all channels
(`waveform_view._apply_y_ranges`, `half_common = max(spans) / 2`). Different instruments have very
different counts (gain, sensor type, accelerometer vs broadband, and physical units differ too:
m/s vs m/s^2). After a band-pass the differences grow, because the band-pass removes the large
offsets and drifts that made the raw traces look similar in size.

Proof with real data (60 minutes of all 27 live channels copied from SeisZone's own SeedLink
server on this PC, 127.0.0.1:18000 - not from the instruments), a 60-minute live window, band-pass
1-10 Hz, the real viewer code offscreen:

* every one of the 27 traces was filtered with the current filter (checked trace by trace);
* the largest trace (SS.SMR03.00.HNZ, the Gecko, which had a big spike when its link started at
  14:58) set the scale for all; the other rows used only 0.00 % - 6.5 % of their height, most of
  them under 0.1 % - **flat lines that look as if nothing happened**. Only the instrument with the
  biggest filtered amplitude shows a waveform. Which one that is depends on the data, so to the user
  it looks like "only the last instrument takes the filter".

Earlier checks that ruled out other causes: the filter code per channel (dsp.apply_spec), the
incremental filter cache (trace_cache), the round-robin recompute budget with 15 and 27 channels
at 5/40/50/100/200/250 sps, live data arriving while the filter is switched on: all filtered.

Proposed fix (not done yet, waiting for the user's OK):
1. "Common" scale shared **per instrument** (NET.STA.LOC + band code, e.g. the three components of
   one sensor), not across different instruments / units; the old all-channels behaviour stays as
   an extra choice "Common (all channels)".
2. Existing viewers with "common" move to the per-instrument common scale.
3. A test with channels of several instruments of very different amplitudes: every trace must fill
   a useful part of its row with the band-pass on.

## 2. Connection survey of the sheet (97 IPs)

| Result | Count | IPs |
|---|---|---|
| SeedLink on 18000 (a Guralp digitiser's own server) | 34 | see the survey table |
| SeedLink 3.1 from a Nanometrics Centaur (10.8.0.119, IIT Roorkee 1): HELLO answered, INFO STREAMS timed out | 1 | 10.8.0.119 |
| GCF only (1567 open, GCFPING answered, no SeedLink) | 4 | 10.8.0.21 ISR CRS, 10.8.0.48 ISR 40 BBS CRS, 10.8.0.121 IIT Roorkee 3, 10.8.0.163 IIT R |
| **This PC** (SeisZone's own server answers there) | 1 | 10.8.0.137 ("Telengiri Dam" in the sheet) - must NOT be added as a source |
| Connection refused on both ports (host up, no service) | 3 | 10.8.0.120, 10.8.0.206 (and .163 / .121 refuse 18000 but answer GCF) |
| No answer at all (offline, or VPN route down) | 54 | see the survey table |

Five of the SeedLink units are already in SeisZone (KANGSABATI DOWN 10.8.0.181, PATSIO 10.8.0.210,
Tuljapur 10.8.0.78, Panchavada 10.8.0.15, Nagdhaniba 10.8.0.11).

Sheet names that differ from what the units report (the unit's own name is the true one):
10.8.0.65 sheet "ISR Dholavira" -> unit VIGHAKOT (IS.VGO); 10.8.0.118 sheet "ISR Vigakot" -> unit
DHOLAVIRA (IS.DOL) - the two look swapped in the sheet; 10.8.0.112 "ISR Sorthi Dam" -> SR.SRJ
SURAJWADI DAM; 10.8.0.144 "ISR Dholera" -> IS.SRG Sarangpur; 10.8.0.7 "Linganmakkhi" -> unit THA;
10.8.0.19 "Ramesh" -> IS.KLK KALAK; 10.8.0.10 "Jagadeesh" -> IS.KDR KADODARA; 10.8.0.203 "Simcon
Site Gaya" -> CI.TEDT Tetar Dam Top; 10.8.0.210 "DRDO_DGRE" -> DG.PATSI PATSIO.

## 3. 18000 (SeedLink) vs 1567 (GCF) - the difference between the instruments

* The Guralp digitisers in the sheet (Minimus, Minimus Plus, Minimus Lite, Fortimus) answer on
  **both**: SeedLink on 18000 (miniSEED, standard SEED names, backfill of missed data after a
  dropout) and GCF on 1567 (the vendor's own block format, UDP pull, no backfill). SeisZone's rule
  (since 1.2.0): a bare IP uses 18000; 1567 is used only when no SeedLink server answers, or when
  the user types IP:1567. 18000 is the better choice whenever it answers.
* Four sites answer **only GCF on 1567** (the CRS / recording-station machines and two IIT Roorkee
  addresses). They can be added only as GCF sources (IP:1567); the names come from the unit's
  channel map, there is no backfill.
* The **Nanometrics Centaur** (10.8.0.119) speaks SeedLink 3.1 but did not answer INFO STREAMS in
  15 s. SeisZone's Add-source needs that list to name the stations; a fallback (connect with "all
  stations" and learn the names from the data) is needed.
* The **Kelunji Gecko** (SS.SMR03) is the other way round: it pushes its data to SeisZone on 58772.

## 4. Problems found on the way

1. **Selector bug for a one-character location code.** 17 units (all Minimus Lite and older
   Minimus) name their channels with location "1" (`1.BHZ`, `1.HHZ`). SeisZone builds the selector
   `1HH?`, which the unit refuses: the log shows "refused selector 1HH? for MU.TLJP" at every
   reconnect of Tuljapur. It still gets data because a refused selector means "everything", but it
   also pulls the state-of-health streams, and a channel choice is ignored. Fix: send `1?HH?`
   (? matches the padding space) - to be checked against one unit, read-only.
2. **Units that list many streams but send few.** The Minimus Plus units list 2J...8J / K / Q
   streams that are not live (only 0J / 1J are). Add-source should offer only live streams.
3. **SeisZone's own server: TIME with an end time returned nothing** when a client asked for the
   last 15 minutes (FETCH worked). To be checked.
4. PATSIO's CH? channels are 250 sps (memory.md said 200).

## 5. Noisy and active sites (general knowledge + web, to confirm with the data)

* **Kachchh (Kutch)** - Bhachau, Vighakot, Dholavira, Gadhda, Suvai, Kakarva, Khavda, Rapar: the
  most active part of the list (2001 Bhuj earthquake zone; South Wagad - Kachchh Mainland fault
  system; Mw 5.3 Bhachau event of 14 June 2020; frequent small tremors).
* **Saurashtra** - Talala/Junagadh, Una, Amreli, Jambuda (Jamnagar swarm 2019): moderate, with
  swarms.
* **Himalaya** - PATSIO (Lahaul, 3803 m): high seismicity.
* **Marathwada** - SRTM sites Tuljapur, Renapur, Deoni, Gunjoti, Arjapur: near Killari / Latur
  (M6.2 1993); low to moderate, small events.
* **Karnataka dams** (KSNDMC: TG Halli, Hemavathi, Hidkal, Almatti, Raichur, Linganamakki): stable
  craton, low natural seismicity; watched for reservoir-induced events.
* **Noise**: dam sites, cities and sites near roads/industry are noisier; a proper ranking should
  come from the data (noise spectrum of each station against the standard low/high noise models,
  with each unit's StationXML), not from names - proposed as a SeisZone feature.

Sources: ISR annual reports (isr.gujarat.gov.in), Journal of Asian Earth Sciences X 2022 (Jamnagar
swarm, 2020 Bhachau event), KSNDMC "Seismic Monitoring", NICEE reading on reservoir seismicity.

## 6. Not done

* Reading or decompiling the third-party viewer's code with the REA/Ghidra tools: not done - the
  project rule says never to decompile, patch or copy it (and its licence). A behaviour comparison
  (same instrument, same filter, both viewers side by side) can be done instead.
* The user's screen was not looked at (no screen tools in this session); the viewer was checked
  offscreen with the real code and real data.

## 7. Side-by-side behaviour check with the third-party viewer (2026-11-22, later)

Asked by the user instead of decompiling. Done by behaviour only: its windows were captured (only
its own windows), its public User's Guide (in its install folder) was read, its settings file was
read. No program code was read, decompiled or copied. The viewer was pulling GCF over UDP 1567
from four units that SeisZone gets over SeedLink 18000: Tuljapur 10.8.0.78, Nagdhaniba 10.8.0.11,
Panchavada 10.8.0.15, PATSIO 10.8.0.210.

| Point | Third-party viewer | SeisZone 1.5.0 | Verdict |
|---|---|---|---|
| Data values | GCF blocks from the unit (30 s pulled read-only from Tuljapur, as the probe does) | SeedLink records from the same unit | **Identical**: 9 400 of 9 400 samples equal, value and time (HHZ/HHN/HHE vs INSTZ0/N0/E0) |
| Filter | applied to every displayed stream; default band-pass 0.1-0.9 x Nyquist (10-30 s at 1-2 sps) | the same, zero phase | Same: both filter every channel |
| Amplitude | one window zoom set by the user + a scale factor per stream (or per instrument, "all components"); physical units from its calibration file; "Scale others to match" is a one-off | "Common": every row gets the range of the largest trace of ALL channels, recomputed all the time | **Different - this is the bug.** With mixed instruments SeisZone flattens all but the largest |
| Time | shows "PC Time (UTC)" from the PC clock (19 s fast here), so its latency reads ~19 s too long | corrected UTC from time servers (1.5.0) | SeisZone better |
| Latency of low-rate streams | GCF blocks: Tuljapur sent 500-sample blocks (5 s at 100 sps) | SeedLink 512-byte records: 50 sps BH? streams arrive 7-15 s late (a record fills slowly) | GCF on 1567 is faster for low rates; SeedLink keeps backfill. Protocol property, not a bug |

Verdict: SeisZone receives exactly the same data and filters every channel the same way. The one
real difference is the amplitude scale: "Common" must not mix instruments. Fix plan (unchanged,
now confirmed): Common per instrument (all components of one sensor share a range, like the
viewer's "all components" scaling), each instrument its own range; the old behaviour as an extra
choice; plus the selector fix for location "1".

## 8. Same instrument, same minutes: picture and numbers (2026-11-22 18:55)

The user asked whether a SeisZone viewer shows the same as the third-party viewer. Compared on
Tuljapur (MINL-7465 = MU.TLJP.1.HH?, 100 sps), the instrument open in its WaveView. Its window
was captured (picture only) at 13:21 UTC; its settings were read from C:\scream\scream.ini and
its User's Guide (no code). SeisZone's data: the same minutes from its own server (127.0.0.1:18000).
SeisZone 1.6.0 drawn offscreen with Default band-pass.

| Point | Third-party viewer | SeisZone 1.6.0 | Verdict |
|---|---|---|---|
| Filter | Default filter = FIR band-pass 0.1-0.9 x Nyquist (5-45 Hz at 100 sps) | Default band-pass 0.1-0.9 x Nyquist, zero-phase IIR | Same band. Peaks agree within 5-11 %, noise within 7-15 % (measured from its picture at 16 counts per pixel; its 1-2 px line width explains most of it) |
| Events | 13:01:48, 13:09:45, 13:13:03 UTC; E biggest at 13:09:45, N biggest at 13:13:03 | the same | Same. Envelope correlation Z 0.91, N 0.79, E 0.85 |
| Time axis | plot column 0 = 12:57:08 by its axis | 12:57:07 by cross-correlation of the data | Same within 1 s (1 pixel). Only its top-right clock is the PC clock (19 s fast) |
| Amplitude scale | one vertical zoom for the whole window, in counts per pixel (here 1:16; scream.ini DefAmpScale=2048) | Default "Common per instrument" fits each sensor to its largest component | Different by design. Scale "Fixed +-" 2048 counts with Units "Counts" gives the same look (picture) |
| Units label | nm/s (from its calvals.txt: 7465 G=1998-2002 V/(m/s), 2.398 uV/count) | counts or physical units from StationXML | Values on the axis can differ slightly; the waveform does not |
| Horizontal | fixed 1 pixel per second | the window fits the chosen time span | Layout only |

Picture: 2026-11-22-side-by-side-tuljapur.png (its WaveView on top, SeisZone Fixed +-2048 below).

Verdict: for the same instrument and minutes SeisZone shows the same waveform, the same events at
the same time and the same amplitudes (within what a picture can measure). The only intended
difference is the default amplitude scale; to look exactly like it, choose Scale "Fixed +-",
Units "Counts", 2048.

## 9. All instruments in the third-party viewer; data compared channel by channel (2026-11-22 19:10)

At the user's request the instruments SeisZone receives were added to the third-party viewer's
pull list. It was closed by the user; its own settings file C:\scream\scream.ini got 32 more
[PullServers] lines in the form its "Add UDP Server..." writes (UDP;IP:1567=GCFSEND:B), 36 in
all; backup C:\scream\scream.ini.backup-2026-11-22-before-adding. Not added: THA 10.8.0.7 (no
GCF answer), the Centaur 10.8.0.119 (no GCF), the Gecko (it pushes to SeisZone). Restarted from
the desktop shortcut: 57 streams after 20 s. It does not record (no C:\scream\data).

Data check: for every instrument, 30 s of GCF over UDP 1567 (what that viewer receives; GCFPING,
GCFSEND:B, GCFSTOP, as SeisZone's probe) against SeisZone's SeedLink data for the same seconds,
matched on sample time. Result: 36 instruments, 135 channels, **415 059 of 415 059 samples
identical** (2026-11-22-gcf-vs-seedlink-all.txt; the two IIT Roorkee GCF units checked separately,
18 200 of 18 200). SeisZone and that viewer hold exactly the same numbers for every channel.

What its settings file shows about its viewer (saved per WaveView window when it exits):
MagScale = vertical zoom, 1 pixel per N counts for the whole window (16, 128, 256, 8192 seen);
TimeScale = pixels per second (1, 2, 5); Filter_LP / Filter_HP = the window's custom design
(on, corner, order 4); per stream: colour, a vertical offset, a scale "auto" or a factor
(e.g. 3067, 848.6 - shown in the label as "*3067" instead of the units), a gain 1.0.
Its WaveView shows only data received since it started (no history after a restart).
