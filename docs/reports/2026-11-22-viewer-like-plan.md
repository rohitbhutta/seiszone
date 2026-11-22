# Plan: a viewer mode that looks and behaves like the third-party WaveView (2026-11-22)

Asked by the user: "our viewer should be a clone of that viewer - amplitude, channel viewing and
filters as precise". Worked out from behaviour only (its windows, its User's Guide, its settings
file C:\scream\scream.ini). No code read or copied; its name does not go into SeisZone.

## What the side-by-side checks proved (report 2026-11-22-multi-instrument-check.md, sections 7-10)

* Data: identical. 36 instruments, 135 channels, 415 059 of 415 059 samples.
* Time: identical within 1 s (1 pixel).
* Filter: same band (0.1-0.9 x Nyquist). Its drawn amplitudes are 0.90-0.92 of the filtered
  values for every design tried (SeisZone's zero-phase IIR, causal IIR, FIR 31-255 taps), with
  the same spread and correlation - the small gap is how it thins samples per pixel when
  drawing, not the filter. Changing SeisZone's filter would not bring it closer.
* Amplitude: each trace is drawn at (window zoom in counts per pixel) / (its own factor),
  measured within 4-17 % on 12 streams of 4 instruments.
* Its one-window-for-everything has the same flat-trace problem the user reported: with ~130
  channels and no filter, IS.PAZ.0J.BHZ (unit 2762, glitches of 16.6 million counts) forced the
  zoom to 1:536 870 912 and every other channel was a flat line.

## Differences left, and what to build

| # | That viewer | SeisZone now | Build |
|---|---|---|---|
| 1 | Vertical zoom for the whole window "1:N" = N counts per pixel, powers of 2, arrows / mouse wheel double or halve it; stays when rows are resized | Scale modes Auto / Common per instrument / Common all / Fixed +- (a range, not per pixel) | New Scale mode "Counts per pixel" showing 1:N with the same steps and keys; range = N x half the row height |
| 2 | Per-stream factor (e.g. *3067) shown in the label instead of the units; "Scale others to match" sets factors once | Per-trace gain exists (shown in the trace header) | Show the gain in the label panel ("x3067"); type it there; "Match others to this channel" (RMS of the visible window) as a one-off |
| 3 | Time scale in pixels per second (1, 2, 5 ...), window length follows the width | Window length in seconds | Option "pixels per second" with the same steps; the window length follows the width |
| 4 | Clean trace area: no per-row axis numbers, no header text, rows packed, black | Axis numbers and a header line per row | "Clean" display option: hide row axes and headers, no row frames |
| 5 | A loud trace may draw over its neighbours | Each trace is clipped to its row | Option "traces may overlap rows" |
| 6 | Time axis: a tick per minute (or finer), longer ticks every 10 minutes, labels every minute | Labels every 30 s / 1 min | Same tick rhythm in the new time-scale mode |
| 7 | Only data since it started; PC clock | History from recordings; corrected UTC | Keep SeisZone's (better) |

Default for new viewers: unchanged ("Common per instrument") unless the user wants the new mode
as default. Every new option is per viewer and saved with it.

## Tests

Offscreen: counts-per-pixel range for row heights; zoom steps and keys; gain in the label;
match-others factors equal RMS ratios; pixels-per-second window length; clean mode hides axes;
overlap mode does not clip. Then the same side-by-side as section 10 with the new mode.
