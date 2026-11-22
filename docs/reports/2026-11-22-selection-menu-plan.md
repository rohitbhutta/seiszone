# Plan: the selection menu (Shift+drag over several channels), 2026-11-22

The user showed the third-party viewer's WaveView: Shift+drag from one channel down to another
selects a time range on those channels only (the selected part drawn inverted, white, with the
sample count at its top-left), and the right-click menu offers: Save..., Zoom In, Save on
selection, ART 3, PSD in Acceleration Units, PSD in Velocity Units, Sinewave Calibration, Step
Calibration, Broadband Noise Calibration, Relative Orientation.

How this was worked out: from that screenshot, the viewer's public User's Guide (sections 6.1.4
"Selecting data", 6.3, the [custom] SelectionMenu add-on mechanism) and its settings file
(`screamglobal.ini`: the last seven items are add-on programs it starts with the selected data).
Its program code was NOT read, decompiled or copied (project rule and its licence) - the REA
tools were not used.

## What SeisZone already has (1.5.0) and what is missing

| Item in the viewer | SeisZone 1.5.0 | To do |
|---|---|---|
| Selecting: Shift+drag from channel A to channel B = only A..B (adjacent rows); Ctrl+drag = exactly two channels, in the order clicked | Shift+drag selects a time range on **all** channels; every tool works on all channels of the viewer | **New**: the drag's vertical extent picks the rows; Ctrl+drag picks two channels in click order |
| Selected part drawn inverted (white) with the sample count; hatched when a stream has a gap or overlap; stays while the menu is open | translucent band on every row, sample count shown; removed at once | Draw it only on the chosen rows, keep it until the menu closes, hatched when there is a gap |
| Save... | "Save... (miniSEED)" (all channels) | Only the selected channels |
| Zoom In | yes | - |
| Save on selection | no (not in the guide - most likely: every new selection is saved at once, no dialog) | **New**: a tick item; each selection is written to the Exports folder (miniSEED) and the status bar says where. To be confirmed by trying it in the viewer |
| ART 3 | no (it is the vendor's separate analysis program, started as an add-on) | **New, generic**: "External tools" - programs the user lists in Settings get the selection as miniSEED files (paths on the command line), the same way the viewer's add-ons get theirs. Any tool can be used, ART 3 included if it reads those files |
| PSD in Acceleration / Velocity Units | yes (all channels) | Only the selected channels |
| Sinewave / Step / Broadband Noise Calibration | yes (the channels guessed) | The Ctrl+drag pair: first = calibration signal, second = sensor output (as the guide describes) |
| Relative Orientation | yes (all horizontal pairs) | The first channel clicked is the reference |
| (SeisZone only) Spectrum, Peak values, Strong-motion analysis, use as noise / signal in the filter design | yes | Only the selected channels |

## Steps

1. Selection model in `ui/waveform_view.py`: the rows under the drag (from the row where it
   starts to the row where it ends) or the two Ctrl-clicked rows; `selectionMade` carries the
   list of channels; the highlight only on them, inverted colours, kept while the menu is open,
   hatched over a gap.
2. `ui/viewer_window.build_selection_menu` and every tool (`analysis_dialogs`, `spectrum_view`,
   export) take that list instead of all channels of the viewer.
3. "Save on selection" (tick item, remembered per viewer) using `core/export.export_window`.
4. "External tools": Settings list (name + program + arguments with {files} / {t0} / {t1}); the
   selection written to a temporary folder; the program started without waiting; its output
   folder opened when it ends. No admin rights, nothing installed.
5. Tests: selection of 1, 2, 3 adjacent rows and of a Ctrl pair; every menu item gets exactly
   those channels; save on selection writes the files; an external tool gets the right paths.
6. User guide, plan.md, memory.md, docs/history.md.

Waiting for the user's OK before starting.
