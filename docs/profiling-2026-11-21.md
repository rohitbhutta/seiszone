# SeisZone 1.4.0 — speed profile (2026-11-21)

Measured with `tools/profile_seiszone.py`, which uses synthetic data only: no network, no
instrument, and a temporary SEISZONE_HOME. The PC has 4 CPUs and runs Python 3.14.7 with
numpy 2.5.3. The viewer is 1600 x 900 on Qt's offscreen platform. Each live tick (every
250 ms) brings new 200 sps samples and then calls `refresh()` and Qt drawing, as on screen.
Re-run the script after a change and compare.

## Baseline test run

`python -m pytest tests -q`: 274 passed, 1 skipped, **1 failed**.
`test_seedlink.py::test_live_first_then_the_missed_data_in_the_background` fails every time.
`backfilled` is 78 where the test expects 72. The background fetch reaches the 6 live
records before the live connection has noted its first record time, so it counts them as
backfill. No data is lost or doubled, because the hub drops duplicate records. Only the
counter is wrong, and the fetch does not stop exactly where the live data begins. This was
already failing before any of today's changes.

## Ingest (engine thread)

| | per record |
|---|---|
| parse + Steim decode (word by word, Python) | ~680 us (75 % of ingest) |
| DataHub.add_record (buffer + day summary + stats) | ~330 us |
| Steim decode, numpy one record | ~530 us (no gain) |
| Steim decode, numpy batch (`mseed.decode_many`) | **50–75 us** |

* Live traffic: 3 Fortimus units (9 x 200 sps + 9 x 5 sps) are about 6 records/s, which keeps
  the engine thread busy **0.6 %** of the time. This is not a problem.
* Backfill: 6 h of one 200 sps channel takes 9.7 s, so a day of 9 channels takes about
  **350 s of engine-thread CPU**. During that time the engine thread holds the Python GIL
  for long stretches, so the windows can stutter.

## Live viewer (one viewer window)

| case | refresh avg / max ms | draw avg ms | traces recomputed per tick | main thread busy |
|---|---|---|---|---|
| 3 ch, 1 min | 6.5 / 14 | 25 | 3.0 of 3 | 12.5 % |
| 9 ch, 1 min | 17.6 / 29 | 35 | 8.8 of 9 | 21 % |
| 9 ch, 1 min, band-pass | 29.3 / 38 | 32 | 8.9 of 9 | 24 % |
| 9 ch, 10 min, band-pass | 35.5 / 43 | 38 | 4.0 of 9 | 29 % |
| 9 ch, 60 min | 16.9 / 45 | 40 | 1.0 of 9 | 23 % |
| 9 ch, 60 min, band-pass | 44.7 / 64 | 36 | 0.9 of 9 | 32 % |
| 12 ch, 60 min, band-pass | 46.0 / 75 | 40 | 0.9 of 12 | 34 % |
| 9 ch, 24 h | 22.3 / 49 | 54 | 1.0 of 9 | 31 % |

The pyinstrument call tree for 12 ch / 60 min / band-pass covered 15 s, of which 5.75 s was
CPU:

* `_compute`: 2.45 s. Of that, `dsp.apply_spec` (sosfiltfilt over the **whole** 60-minute
  window + 60 s pre-roll) took 1.81 s, and `dsp.envelope` over the whole window took 0.55 s.
  One trace's filter (~45 ms) already exceeds the 30 ms BUDGET.
* Qt drawing: 2.76 s. Of that, curve `drawPath` took 1.33 s and the **12 time axes**
  (`DateAxisItem.paint` / `generateDrawSpecs`, one per row, each regenerated at every range
  change) took 0.99 s.
* `_set_range` (linked X ranges of 12 ViewBoxes): 0.31 s.

## Where the time goes, and the fixes it points to (by gain)

1. **Live scrolling below one pixel.** The live edge moves every 250 ms. At 60 min one pixel
   is about 2.5 s; at 24 h it is about 60 s. Most ticks move the view by a fraction of a
   pixel, yet they still re-range 12 linked ViewBoxes and redraw every curve and axis.
   Moving the range only when it shifts by at least one pixel should cut drawing on long
   windows by roughly 10x (an estimate, to measure). Short windows scroll as now.
2. **Incremental filtering and decimation.** Today every pixel step refilters and
   re-decimates the whole window. The fix is to keep each trace's filtered data and envelope
   bins and work only on the new data plus an overlap of many filter decay lengths, splicing
   it in. The result must match whole-window filtering to within a tested tolerance. It turns
   an O(window) cost into O(new data): about 45 ms down to a few ms per trace.
3. **Batch Steim decode in the backfill.** The background fetch reads up to 64 KB (~126
   records) at a time. Decoding each batch with `decode_many` (7–10x faster) and adding it to
   the hub in one go (one summary merge per batch) should cut the day-long backfill from about
   6 minutes of CPU to under 1 minute, and the GIL stutter with it.
4. **One set of time ticks for all rows.** Compute the tick positions once per range and
   share them with the grid lines of the other rows (or draw the lane grid lines directly).
   This saves about 16 ms per tick with 12 rows.
5. **Fix the SeedLink test race** (see above). This is about correctness, not speed. The live
   start time should be known before the background fetch decides where to stop.

## After fixes 1 and 2 (same day, same PC, same script)

* Fix 1: `WaveformView.refresh` moves a live view only when its range is at least
  `LIVE_STEP_PX` (1) pixel behind (`_moves_a_pixel`).
* Fix 2: `ui/trace_cache.py` keeps each channel's filtered samples and decimation bins and
  redoes only the part new data can change. The refiltered stretch is `dsp.settle_samples`,
  where the slowest filter pole has decayed to 1e-10. The bins are on a fixed grid.

| case | busy before | busy after | refresh avg before → after (ms) | draw avg before → after (ms) |
|---|---|---|---|---|
| 9 ch, 10 min, band-pass | 29 % | 23 % | 35.5 → 20.0 | 38 → 37 |
| 9 ch, 60 min | 23 % | 7.8 % | 16.9 → 5.5 | 40 → 14 |
| 9 ch, 60 min, band-pass | 32 % | 8.7 % | 44.7 → 7.0 | 36 → 15 |
| 12 ch, 60 min, band-pass | 34 % (4 ticks > 100 ms) | 9 % (0) | 46.0 → 7–9 | 40 → 14.5 |
| 9 ch, 24 h | 31 % | 8.5 % | 22.3 → 9.1 | 54 → 12 |
| 1 min windows | 12–24 % | the same | A/B with the cache off: equal within noise | |

1-minute windows move several pixels every refresh, so the cost there is Qt work per trace
(`setText` of the label, `setData`, drawing), not filtering. That is a separate possible fix.

Precision (`tests/test_trace_cache.py`, 28 tests):

* For every ready-made filter, the default band-pass and a gain + low-pass spec, the incremental
  filter differs from one sosfiltfilt over all the data by **< 1e-7 of the signal**, across
  200+ uneven records.
* A filter settling more slowly than the window (e.g. high-pass 0.05 Hz order 8) falls back to
  the full computation, exactly as before.
* The viewer's curve equals the from-scratch curve: x identical, y within 1e-6.
* Every point of a reused bin is a real sample, and no extreme is lost.

Finding about the old method (still true for windows filtered in full): the visible left
edge carries the filter's start transient, because the pre-roll is 3 periods of the lowest
corner and at most 60 s. For high-pass 0.05 Hz order 8 that is up to **0.66 %** of the
signal. The incremental path keeps a longer history, so it does not have this error. A
pre-roll based on `settle_samples` would remove it everywhere.

Full suite after the change: 303 passed, 1 skipped. The SeedLink race test passed in this
run; it is timing-dependent.

Precision safeguard for all of the above: tests compare the new incremental and batched
results sample by sample with the current whole-window ones (`steim_decode_py` stays the
reference decoder).
