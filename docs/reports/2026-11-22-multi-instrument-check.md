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
