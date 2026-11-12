# SeisZone

SeisZone is a Windows desktop program for **live seismic data**. You type an instrument's
IP address and port, and SeisZone finds its stations and channels, shows every channel live,
records continuously to miniSEED, exports any time window, and shares all data again over
SeedLink so other programs can read it.

It is built only on open standards: SeedLink 3, Guralp GCF / Scream protocol, Kelunji Gecko
telemetry framing, miniSEED 2 and FDSN StationXML.

Version 1.2.0. Python 3.14, PySide6 (Qt 6), pyqtgraph, numpy, scipy.

## Features

* **Add an instrument with only IP:port.** SeisZone detects SeedLink servers, Guralp GCF
  (Scream UDP and TCP) and the Guralp web page, and reads the station, site, channels and
  StationXML by itself. Examples: Guralp Fortimus `10.8.0.182:18000` (SeedLink) or
  `10.8.0.182:1567` (GCF), or any public SeedLink server.
* **Kelunji Gecko SMR03** over a USR-W610 serial-to-WiFi/LAN converter: the Gecko connects
  *in* to this PC on TCP port 58772. No other software is needed.
* **Viewer windows**: tick channels and open them in a viewer window of its own (any number;
  reopened at the next start if wanted). Many channels stacked or overlaid with a label box per
  channel (station, channel, units; colours by component), a time scale in pixels per second up
  to one day with the **history of the last day** (per-second summaries kept and saved by
  SeisZone, the user's recordings read in the background, a new SeedLink station's last day
  fetched at its first connection), amplitude zoom, the window filter switched on and off (F) and every channel's own
  filter (default band-pass, high-pass, low-pass, band-pass, own designs in a filter design
  window, saved filters), counts or physical units (StationXML or a calibration you enter,
  acceleration also in g), auto / common / fixed scaling and per-channel scale factors, offsets
  (null, zero, locked), spectrograms, time and amplitude cursors, details (mean, min, max, RMS),
  colours, sorting, Shift+drag selection (zoom, spectrum, export), print and picture, keys for
  everything, Live / Pause.
* **Channel table**: sample rate, newest sample, latency, packets, gaps, source, with a
  green / amber / red status colour.
* **Continuous recording** to miniSEED (lossless: the received records are written unchanged),
  files of 15, 30 or 60 minutes aligned to the clock, one file per channel or per station.
  Only the user starts and stops a recording. **SeisZone never deletes recorded data**; low
  disk space only gives a warning. Recordings continue by themselves when SeisZone is started again (after a
  restart of the PC, for example), and
  data that the instrument sends late (backfill after a VPN drop) goes into the right file.
* **Export** a time window of chosen channels (one file per channel or one combined file),
  with a report of samples and gaps.
* **SeedLink server** (port 18000, the standard SeedLink port) that offers every channel SeisZone receives to other
  programs, on this PC (`127.0.0.1:18000`) or on the network.
* **Recordings window** of its own (minimize, maximize, a divider to size the recordings list).
* **Spectrum** of the visible data, tray icon, start with Windows, dark and light colours,
  program log file.
* Runs as a normal Windows user; only the installer needs administrator rights.

## Folder layout

```
seiszone/
  README.md  plan.md  memory.md  requirements.txt  requirements-dev.txt  run_seiszone.py
  src/seiszone/          the program
    core/                engine: SeedLink client and server, Gecko receiver, GCF link, probe,
                         data hub, recorder, export, StationXML, filters
    ui/                  windows, panels and dialogs (PySide6 + pyqtgraph)
  tests/                 pytest tests (local fake servers and real captured data in tests/data)
  tools/make_icon.py     draws assets/seiszone.ico and assets/seiszone.png
  assets/                application icon
  docs/user_guide.md     user guide for the station operator
  installer/             build.py, SeisZone.iss (Inno Setup 6), version_info.txt, launcher
  build/                 build output (PyInstaller work folder, dist/SeisZone, SeisZoneSetup.exe)
```

plan.md describes the architecture and the contract of every module; memory.md is the
project handover (status, decisions, real equipment).

Files SeisZone writes on a user's PC:

| What | Where |
|------|-------|
| Settings and sources | `%APPDATA%\SeisZone\config.json` |
| Resume positions, running recordings | `%APPDATA%\SeisZone\state.json` |
| StationXML per station | `%APPDATA%\SeisZone\stationxml\` |
| Program log | `%LOCALAPPDATA%\SeisZone\logs\seiszone.log` |
| Recordings (default) | `Documents\SeisZone\Recordings\` |
| Exports (default) | `Documents\SeisZone\Exports\` |

The environment variable `SEISZONE_HOME` moves all of these below one folder (the tests use it).

## Run from source

```
cd "C:\D Drive\Only Coding\seiszone"
python -m pip install -r requirements.txt
python run_seiszone.py                           # the application
python run_seiszone.py --minimized               # start in the tray
python run_seiszone.py --probe 10.8.0.182:18000  # detect an instrument (read-only) and print it
python run_seiszone.py --engine                  # engine without a window, with the saved sources (*)
python run_seiszone.py --engine --seconds 60     # ... for 60 s only
python run_seiszone.py --add 10.8.0.182:18000    # detect and add a source (*)
python run_seiszone.py --list                    # the configured sources
python run_seiszone.py --version
```

(*) Only while SeisZone itself is not running: quit it from the tray icon (Quit) first -
closing the window only hides it. Otherwise these commands stop with exit code 3.

`python -m seiszone` (with `src` on `PYTHONPATH`) does the same as `run_seiszone.py`. The
installed `SeisZone.exe` takes the same options; started from cmd or PowerShell it prints into
that console, and `--output PATH` writes everything into a file instead (for example
`SeisZone.exe --engine --seconds 10 --output C:\Temp\seiszone.txt`).

## Tests

```
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

The tests use only local fake servers on `127.0.0.1` with random ports, real captured data in
`tests/data`, a temporary `SEISZONE_HOME`, and Qt in offscreen mode. They never contact real
instruments and never listen on the real ports 58772, 18000 or 18010. ObsPy is used only by the tests,
to cross-check the miniSEED files SeisZone writes.

## Build the installer

Needs PyInstaller, Pillow (both in requirements-dev.txt) and Inno Setup 6.

```
python installer/build.py                 # icon -> build/dist/SeisZone -> build/dist/SeisZoneSetup.exe
python installer/build.py --no-installer  # only the program folder build/dist/SeisZone
python installer/build.py --clean         # rebuild from an empty PyInstaller work folder
```

`build.py` runs `tools/make_icon.py`, then PyInstaller (one folder, windowed, `SeisZone.exe`
with icon and version information), checks that the new exe starts (`SeisZone.exe --version --output ...`),
and compiles `installer/SeisZone.iss` with ISCC.exe (found in `%LOCALAPPDATA%\Programs\Inno
Setup 6`, `Program Files (x86)\Inno Setup 6`, or the `ISCC` environment variable).

The installer (`SeisZoneSetup.exe`, 64-bit Windows 10/11):

* installs to `C:\Program Files\SeisZone`, adds a Start menu icon and, if chosen, a desktop icon;
* adds one inbound Windows Firewall rule "SeisZone" for `SeisZone.exe` (covers the Gecko port
  58772 and the SeedLink server port);
* closes a running SeisZone first (it tells the user);
* task `stopoldservice` (ticked by default, shown only when the Windows service
  `GeckoSeedLink` exists): stops and disables the old Gecko SeedLink service (`sc stop`,
  `sc config ... start= disabled`), which holds ports 58772 and 18010 (SeisZone's own SeedLink server uses 18000); it is not removed;
* starts SeisZone as the normal user at the end.

Uninstalling removes the program and the firewall rule. **Recordings, exports, settings and
logs are never deleted.** "Start with Windows" is switched on and off in SeisZone's Settings.

Silent install: `SeisZoneSetup.exe /VERYSILENT /SUPPRESSMSGBOXES [/MERGETASKS="desktopicon"]`
(`/MERGETASKS` keeps the default tasks; `/TASKS=` would switch off `stopoldservice`).

## Gecko recorder does not appear / port in use

The Gecko connects to whatever listens on port 58772 of this PC. While the old Gecko SeedLink
service (Windows service `GeckoSeedLink` of the earlier Gecko setup) runs, it holds 58772 (and its
own SeedLink port 18010): the Gecko stays connected to it and SeisZone shows `port 58772 is used
by the old Gecko SeedLink service` on the Gecko receiver row (`engine.status()` gives
`"owner": "old-service"`; `core/ports.py` finds the listening process with
`GetExtendedTcpTable` and compares it with the service's process id, read-only). SeisZone asks
once per run, and offers **Free the port – stop the old Gecko SeedLink service** above the
sources list and in the rows' context menu: one Windows administrator prompt runs
`sc stop GeckoSeedLink & sc config GeckoSeedLink start= disabled`, the rows show "Waiting for
the port…", and the engine, which tries a busy port again every 3 s, takes the ports. The
service is only disabled (Windows Services can switch it on again). A port held by any other
program is shown as `in use by another program` (the log gives its process id).

## Version

The version is `seiszone.VERSION` in `src/seiszone/__init__.py`. When it changes, also change
`installer/version_info.txt` (build.py checks this and passes the version to Inno Setup).
