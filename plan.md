# SeisZone — Plan and Architecture

SeisZone is **our own** Windows application for live seismic data: connect instruments by
**IP address + port only**, see every channel live, record continuously to miniSEED, export
time windows, and share all data again over SeedLink. It replaces the old three-part setup
(a third-party viewer + our earlier Gecko add-on service + its Record bar) with one program.

It is written from scratch on open standards only — SeedLink 3, Güralp GCF / Scream UDP
protocol, Kelunji Gecko telemetry framing, miniSEED 2, FDSN StationXML. No code, name, look or
file of any third-party viewer is used. The tested engine parts of our own earlier projects
(the earlier Gecko add-on project, which already contains `gecko-seedlink-bridge` and the old record add-on)
are carried over and cleaned up.

## 1. Goals (what the user asked for)

| # | Goal | Where |
|---|------|-------|
| G1 | One application named **SeisZone**, no third-party names in it | everything |
| G2 | Add an instrument with only **IP + port**; SeisZone finds station, site, channels, StationXML itself | `core/probe.py`, `ui/add_source_dialog.py` |
| G3 | Güralp Fortimus over SeedLink (18000) and GCF (1567 UDP/TCP) | `core/seedlink_client.py`, `core/gcf_link.py` |
| G4 | Kelunji Gecko SMR03 via USR-W610 connecting in on TCP 58772 (no SRC Streams) | `core/gecko.py` |
| G5 | Any other SeedLink server (e.g. IN.DDI, CI.TEDT) | `core/seedlink_client.py` |
| G6 | Live multi-channel waveform view: time window, zoom, scaling, filters, units | `ui/waveform_view.py`, `core/dsp.py` |
| G7 | Channel table: rate, latest sample, latency, gaps, packets, status colour | `ui/channels_panel.py` |
| G8 | Continuous recording to miniSEED, user start/stop only, **never deletes data**, survives restarts | `core/recorder.py` |
| G9 | Export any time window (per-channel or combined file + report) | `core/export.py`, `ui/export_dialog.py` |
| G10 | Own SeedLink server (default port 18010) so other programs can read all data | `core/seedlink_server.py` |
| G11 | StationXML per station; show counts or physical units | `core/stationxml.py` |
| G12 | Logs, tray icon, start with Windows, one installer | `ui/app.py`, `installer/` |

Standing rules: never use the name of the user's separate business or of the third-party viewer; recording is user-controlled and data is never
deleted (low disk space only warns); the program runs as the normal user (no admin) — only the
installer needs admin (firewall rules); the user runs installers themselves.

## 2. Stages

| Stage | Content | Status |
|-------|---------|--------|
| 1 | Engine: hub, SeedLink client/server, Gecko receiver, GCF link, probe, config | in progress |
| 2 | Main window: sources, channels, live waveforms, log, tray | in progress |
| 3 | Recording, export, StationXML units, filters, spectrum | in progress |
| 4 | Icon, PyInstaller build, Inno Setup installer `SeisZoneSetup.exe` | in progress |
| 5 | Field test on the real units (10.8.0.180/181/182, SMR03) and polishing | after first build |

## 3. Technology

* Python 3.14, **PySide6** (Qt 6) user interface, **pyqtgraph** for fast plots, **numpy**,
  **scipy.signal** (filters). No ObsPy in the program (only in tests, to cross-check files).
* `asyncio` engine in one background thread; the Qt UI reads hub snapshots on timers.
* PyInstaller one-folder build, Inno Setup 6 installer.

## 4. Folder layout

```
seiszone/
  plan.md  memory.md  README.md  requirements.txt  run_seiszone.py  .gitignore
  src/seiszone/
    __init__.py        APP_NAME, VERSION
    __main__.py        python -m seiszone [--engine] [--minimized]
    paths.py           config / state / logs / documents folders
    config.py          Config (config.json), State (state.json), validate_source, source_label
    logsetup.py        rotating file log + in-memory queue for the log panel
    core/
      mseed.py         Record, parse_record, iter_records, read_file, encode_segment, encode_records
      gcf.py           GCF block decoder, BTP stream parser, default SEED names
      hub.py           DataHub, StreamBuffer
      links.py         Link base class (state, feed(), pause())
      seedlink_client.py  SeedLinkClient(Link)
      seedlink_server.py  SeedLinkServer
      gecko.py         GeckoReceiver (Kelunji Gecko telemetry, listens on 58772)
      gcf_link.py      GcfLink(Link) (Scream UDP pull, TCP/BTP)
      probe.py         probe("IP[:port]") -> Instrument; Instrument.to_source()
      stationxml.py    ResponseStore
      recorder.py      RecordingManager
      export.py        export_window()
      dsp.py           filters, demean, envelope (min/max decimation), spectrum
      engine.py        Engine (owns loop thread, links, server, gecko, recorder)
      cli.py           headless engine (python -m seiszone --engine)
    ui/
      app.py           main(): QApplication, single instance, tray, start-with-Windows
      theme.py         palette, stylesheet, icons
      main_window.py   MainWindow
      sources_panel.py SourcesPanel
      channels_panel.py ChannelsPanel
      waveform_view.py WaveformView
      add_source_dialog.py  AddSourceDialog
      record_dialog.py      RecordDialog (start/stop recordings, list)
      export_dialog.py      ExportDialog
      settings_dialog.py    SettingsDialog
      log_panel.py     LogPanel
      spectrum_view.py SpectrumDialog
  tests/               pytest; data/ holds real captures (Fortimus GCF UDP, Gecko native link, web pages, StationXML)
  tools/make_icon.py   draws assets/seiszone.ico + .png
  installer/           SeisZone.iss, version_info.txt, build.py
  assets/              seiszone.ico, seiszone.png
  docs/                user_guide.md
```

## 5. Contracts (every module must follow these exactly)

### 5.1 Records — `core/mseed.py` (written)
`Record` fields: `raw` (bytes), `net sta loc cha`, `start` (epoch s of first sample), `rate`,
`rfac rmul`, `nsamp`, `encoding`, `reclen`, `quality`; properties `sid` ("NET.STA.LOC.CHA"),
`station` ("NET.STA"), `end` (time after last sample), `samples` (numpy, decoded once).
`parse_record(buf, off=0, check=True) -> (Record, reclen) | None | "short"`.
`iter_records(buf, check=False)`, `read_file(path)`, `encode_segment(net, sta, loc, cha, rfac,
rmul, start, samples, seq_start=1, reclen=512) -> (bytes, next_seq)`, `encode_records(net, sta,
loc, cha, rate, start, samples, reclen=512) -> [Record]`, `seed_rate(rate)` (an int16 pair, exact for
integer, 1/n and decimal rates), `fmt_time(t)`. Steim differences are summed with int32
wraparound (like the C decoders); an Xn that does not match the last sample is only counted
(`xn_mismatches`, `steim_decode(..., strict_xn=False)`), like libmseed.

### 5.2 DataHub — `core/hub.py` (written)
Engine thread only: `add_record(rec, source_id) -> bool` (False = duplicate),
`add_listener(fn(rec, source_id))`, `remove_listener(fn)`, `new_waiter() -> asyncio.Event`,
`drop_waiter(ev)`, `packets_from(next_seq, limit) -> ([(seq, Record)], next_seq_after)`,
`next_seq()`, `has_seq(seq)`, `first_seq()`.
Any thread: `stream_ids()`, `stats_snapshot() -> {sid: {sid, net, sta, loc, cha, rate, source,
packets, first, last_end, last_arrival, latency, gaps, gap_seconds, last_gap, backfilled,
last_value, future_rejected}}`, `window(sid, t0, t1) -> [(start, rate, ndarray)]`, `latest_time()`,
`descriptions` dict "NET.STA" -> text, `forget_stream(sid)`, `set_buffer_seconds(s)`.
A record starting more than 600 s ahead of the PC clock and more than the buffer length past
the channel's newest data goes to the ring and the listeners only (not to the live buffer and
the statistics). The ring keeps raw bytes only (decoded samples are dropped after use).
`seq` (newest packet number) is set by the Engine at start, see 5.14.

### 5.3 Links — `core/links.py` (written)
`Link(src, hub, state)`; attributes `state detail packets duplicates bytes last_packet since
failures kick`; `feed(rec)`, `set_state(state, detail)`, `status() -> dict`,
`await pause(stop) -> stopped?`, `async run(stop)` (reconnects forever, returns when stop set,
drops the connection and reconnects at once when `kick` is set).

### 5.4 Config — `config.py` (written)
`Config(path)`: `get(section, key=None, default=None)`, `set(section, key, value)`,
`update_section(section, dict)`, `sources()`, `source(id)`, `add_source(src)`,
`update_source(src)`, `remove_source(id)`, `find_source(host, port)`, `save()`.
`State(path)`: `get/set`, `get_seq(source_id, "NET.STA")`, `get_seq_time(...)`,
`set_seq(source_id, station, seq, end=None)` (end = end time of that record), `drop_source(id)`,
`flush()`. Source dict schema: see the docstring of `config.py`.
A file locked for a moment is read again (about 1 s); a damaged one is kept as
`<name>.<stamp>.bad`. A config.json that stays unreadable is never overwritten (`read_only`,
`save()` writes nothing); a state.json is replaced only after it was copied to .bad.
`load_error` says what happened (the user interface warns at start).

### 5.5 SeedLink client — `core/seedlink_client.py`
`SeedLinkClient(Link)`, `kind = "seedlink"`. SeedLink 3 protocol: `HELLO` (2 lines), then for each
configured station `STATION sta net`, one `SELECT x` per selector, `DATA [seq [begin]]`
(multi-station; resume with **last received seq + 1** in hex from `State.get_seq` plus the end
time of that record from `State.get_seq_time`, as libslink does, so the server backfills what
was missed while SeisZone was closed or the VPN was down - also when its numbers no longer
match; a server that refuses it gets `DATA seq`, then plain `DATA`), `END`; then
read 520-byte packets `SL` + 6 hex digits + 512-byte record (also handle `SLINFO` packets and
servers that answer `ERROR`: retry that station without the sequence number). If `stations` is
empty use uni-station mode (`SELECT`s then `DATA`). Keep-alive: send `INFO ID` after 60 s
without data; reconnect after 180 s without any byte. Save the last seq per station with
`State.set_seq` (throttled by State). Every record goes through `Link.feed`. Never raises.

### 5.6 SeedLink server — `core/seedlink_server.py`
`SeedLinkServer(hub, organization="SeisZone")`, `async handle(reader, writer)`, `.clients`
(count), `.peers` (list of dict peer/since/stations/packets). Commands: HELLO, CAT, BATCH,
STATION, SELECT (with `!` negation, `?`/`*` wildcards, LLCCC / CCC / LL forms, `.D` type),
DATA, FETCH, TIME, END, INFO ID|CAPABILITIES|STATIONS|STREAMS|CONNECTIONS, BYE. Data packets
`b"SL%06X" % seq + rec.raw` (512-byte records only; records of another length are re-encoded
to 512 with `encode_records`; a record that cannot be is skipped). `DATA n` starts at packet n
(the standard meaning) when held (a begin time then does not count); n = the next packet or
later: live; any other n (older than the ring, or a number of an earlier run) from the oldest
held packet (ending after the begin time, if given) - resending is harmless, live would lose
data. `TIME` from the oldest held packet matching the time. Commands end in CR, LF or CR LF
(libslink / ObsPy send a bare CR). A client silent for 300 s while negotiating is closed; TCP
keep-alive is on.
INFO XML like a standard server (`<seedlink software="SeisZone v1.0.0" organization=...>`,
`<station name network description begin_seq end_seq>` with `<stream location seedname type
begin_time end_time>`), packed into `SLINFO *`/`SLINFO  ` 520-byte packets. Station
descriptions come from `hub.descriptions`.

### 5.7 Gecko receiver — `core/gecko.py`
`GeckoReceiver(hub, rename=None)`, `async handle(reader, writer)`, `.links` {peer: status dict
peer/serial/station/sitename/records/bytes/since}. Native Gecko telemetry framing exactly as
the old `gecko_seedlink_bridge.GeckoReceiver` (frame `0x55 type len src dst time seq payload
xor32`, hub hello 0xFB, hello reply 0xFD, ack 0xFF, text 0x8A `sampling 1` / `? "sitename"`,
miniSEED 0x88; ack at start then every 3 packets or 1 s; close after 120 s silence). Records go
to `hub.add_record(rec, "gecko:<serial>")`. Optional rename table {serial: {net, sta, loc}}.
`hub.descriptions["NET.STA"] = "<sitename> - Kelunji Gecko <serial>"`.

### 5.8 GCF link — `core/gcf_link.py`
`GcfLink(Link)`, `kind = "gcf-udp" | "gcf-tcp"` from `src["kind"]`. UDP: GCFPING, GCFSEND:B,
refresh GCFSEND:B every 20 s, restart after 60 s without blocks, GCFSTOP (twice) when stopping.
TCP: BTP/raw stream with acknowledgements. Each data block -> 512-byte Steim2 records with
`src["gcf"]["net"/"sta"]` and `src["gcf"]["map"]` {gcf stream id: [loc, cha]} (fallback
`gcf.default_seed_name` only when the map is empty: a non-empty map is the user's selection and
other streams are not used), duplicates dropped (key: system id, stream id, start), then
`Link.feed`. One digitiser per source: `src["gcf"]["system_id"]` (older configs: the probe's,
else the first seen); blocks of other system ids are dropped. Status blocks logged; in a bare
TCP stream only plausible (printable) status blocks are believed, and a block cut by a read
boundary waits for its rest.

### 5.9 Probe — `core/probe.py`
Port of `instrument_probe.py` of the earlier Gecko add-on project (read-only detection: SeedLink HELLO/INFO,
GCF UDP/TCP sample, Güralp web page status/data-stream/StationXML), without any third-party
viewer names. Adds `Instrument.to_source(selected=None) -> dict` (a valid config source:
seedlink -> `stations` with selectors from `selectors_for`; gcf -> `gcf` net/sta/desc/map/system_id;
several digitisers on one port: the one the web page names, else the one with most blocks),
`Instrument.summary_lines()` for the dialog. `probe(text, log, prefer_direct=True, need_xml=None)`.

### 5.10 StationXML — `core/stationxml.py`
`ResponseStore(folder=paths.stationxml_dir())`: `save(net, sta, data: bytes) -> path`,
`path(net, sta)`, `has(net, sta)`, `load(net, sta) -> summary dict | None`,
`sensitivity(sid, t=None) -> (value, input_units) | None` (InstrumentSensitivity of the channel
epoch covering t), `channels(net, sta)`. ElementTree only (no ObsPy).

### 5.11 Recorder — `core/recorder.py`
`RecordingManager(hub, state, folder_default)`, registered as hub listener (`on_record`).
`start(name, folder, sids=None (all channels, also new ones) | [sid...], file_minutes=60,
layout="channel"|"station") -> id`, `stop(id)`, `sessions() -> [dict id name folder sids
file_minutes layout started stopped state files bytes last_write last_error]`,
`flush()`, `close()`, `disk_status(folder) -> (free_bytes, warning_text|None)`.
Files: `<folder>/<NET.STA>/<YYYY-MM-DD>/<NET.STA.LOC.CHA>__<YYYYMMDDTHHMMSSZ>.mseed` (layout
channel) or `<NET.STA>__<...>.mseed` (layout station); the period (15/30/60 min, aligned to the
clock, UTC) is chosen by each record's start time; the raw record bytes are appended unchanged
(lossless, backfilled records go into the right older file). Each recording writes
`<folder>/recording_log.txt` (start, stop, new files, gaps per channel, links lost/back).
Active recordings are kept in `State["recordings"]` and continue after a restart of SeisZone.
A file that existed before this run first appends to it is read once: records already in it
are not appended again (the SeedLink resume after a crash). Records that cannot be written wait
in memory (64 MB, O(1) per record); those still unwritten at close are reported (log and
recording_log.txt, with their time span) and the channel end is set back, so the hole is logged
as a GAP. Nothing is ever deleted. Disk-space warning below 5 GB or 5 %.

### 5.12 Export — `core/export.py`
`export_window(sids, t0, t1, outdir, mode="separate"|"combined", folders=[recording folders],
hub=None, log=print) -> (written_paths, report_text)`: collects records of each channel in
[t0, t1) from recording folders (files found by name/date) and, for what is missing, from the
hub's live buffer (re-encoded), merges, trims exactly, writes Steim2 512-byte miniSEED plus
`export_info__<stamp>.txt` (samples, expected, gaps per channel). Files are found by their
period stamp up to 2 h before t0, further back for low sample rates (period + one record).
`recorded_sids(folders, t0, t1)`: channel ids the recording folders hold for a window (the
export dialog offers them, so an offline station can be exported). The dialog's window is
whole seconds.

### 5.13 DSP — `core/dsp.py`
`FILTERS` ordered dict name -> label/spec (none, highpass 0.1/1 Hz, lowpass 5/20 Hz, bandpass
0.5-5 / 1-10 / 1-20 Hz); `apply(y, rate, name, demean=True) -> ndarray` (scipy sosfiltfilt,
stable for short arrays: pad / fall back to demean); `envelope(t0, rate, y, max_points) ->
(x, y)` min/max decimation for plotting; `spectrum(y, rate) -> (freq, amplitude)` (Welch);
`stats(y)` min/max/mean/rms.

### 5.14 Engine — `core/engine.py`
`Engine(config, state=None, hub=None)`; `start()` (thread "seiszone-engine" with its own asyncio
loop; opens SeedLink server and Gecko listeners — a port that is busy is reported, not fatal;
starts one task per enabled source), `stop(timeout=5)`; thread-safe:
`add_source(src) -> src` (validated, saved, started), `remove_source(id)`,
`update_source(src)`, `set_enabled(id, bool)`, `reconnect(id)`, `apply_settings()` (restart
listeners after a settings change), `call_soon(fn, *args)`, `status() -> {"sources": {id:
link.status()}, "gecko": {"ports", "listening", "error", "links": [...]}, "server": {"port",
"listening", "error", "clients", "peers"}, "started"}`. Attributes `hub`, `config`, `state`,
`recorder` (RecordingManager), `responses` (ResponseStore). Logging to logger "seiszone".
The SeedLink server's packet numbers continue across runs: `State["server_seq"]` is saved every
second and at stop; at start `hub.seq = saved + 100000` (the packets numbered after the last
save). A busy-port retry uses the settings the listeners were opened with (only
`apply_settings` takes new ones). `cli --engine` / `--add` with the user's own config hold the
single-instance mutex (`seiszone/single_instance.py`) and stop with exit code 3 while SeisZone
runs; `--engine` with a missing `--config` file: exit code 2; a failed engine: exit code 1.

### 5.15 User interface — `ui/`
`ui.app.main(argv)`: QApplication (Fusion style + `theme.apply(app)`), single instance (named
mutex `Local\SeisZone.SingleInstance`; a second start brings the running window forward via a
local socket `SeisZone.Activate`), creates Config, State, Engine, MainWindow, tray icon
(Show / Start-stop recording / Quit); closing the window hides to tray if enabled; Quit stops
the engine cleanly (it waits for an export that is still writing, after asking, and up to 5 s
for a detection). `--minimized` starts in the tray. "Start with Windows" writes
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\SeisZone` (from source: pythonw with
run_seiszone.py); at start the value is only rewritten when missing or its program no longer
exists, so a source or build-folder run never takes over the installed SeisZone's entry.

`MainWindow(engine, config)`: toolbar (Add source +, Remove, Reconnect, Record, Export,
Spectrum, Settings), left dock `SourcesPanel`, centre `WaveformView`, bottom docks
`ChannelsPanel` and `LogPanel`, status bar (sources receiving, Gecko links, SeedLink server
clients, recording state, free disk). Timers: waveforms 250 ms, tables 1 s.
Window geometry saved in `config["ui"]`.

Panels read only `engine.status()`, `engine.hub.stats_snapshot()`, `engine.hub.window()`,
`engine.recorder.sessions()` and change things only through Engine/RecordingManager methods.

## 6. Testing

* `python -m pytest tests -q` — unit tests per module (real captured data, local fake servers).
* End-to-end: hub + SeisZone SeedLink server + SeisZone SeedLink client in one process.
* UI smoke test offscreen (`QT_QPA_PLATFORM=offscreen`): window with synthetic data, screenshot.
* Live, read-only: `python -m seiszone --engine --probe 10.8.0.182:18000` and a 60 s receive.

## 7. Build

```
python tools/make_icon.py
python installer/build.py          # PyInstaller -> build/dist/SeisZone, then ISCC -> SeisZoneSetup.exe
```
