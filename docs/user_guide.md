# SeisZone user guide

SeisZone shows, records and shares live seismic data. This guide is for the person who runs
the station PC. Press **F1** in SeisZone (or Help > User guide) to open it again.

Contents

1. Install
2. First start
3. Add an instrument (only IP address and port)
4. Kelunji Gecko SMR03 with a USR-W610
5. The main window
6. The channels table and its colours
7. The waveform view
8. Recording
9. Export a time window
10. The SeedLink server: data for other programs
11. Settings
12. The tray icon
13. Troubleshooting
14. Where SeisZone keeps its files

---

## 1. Install

1. Run **SeisZoneSetup.exe**. Windows asks for administrator permission: answer **Yes**.
   Setup needs it only to copy the program to `C:\Program Files\SeisZone` and to add one
   Windows Firewall rule. SeisZone itself always runs with your normal user rights.
2. Choose whether you want a desktop icon. A Start menu icon is always made. If the old Gecko
   SeedLink service is installed, leave **"Stop and disable the old Gecko SeedLink service, so
   your Gecko recorder connects to SeisZone"** ticked (see section 13).
3. If SeisZone is already running (for an update), setup tells you **"SeisZone is running and
   will be closed"**. Click OK. Recordings that were running continue when SeisZone starts again.
4. At the end leave **Launch SeisZone** ticked and click Finish.

The firewall rule is called **SeisZone**. It lets a Kelunji Gecko (port 58772) and other
programs (the SeisZone SeedLink server, port 18000) connect to this PC.

**Uninstall** (Windows Settings > Apps > SeisZone) removes the program and the firewall rule.
Your recordings, exports, settings and log files are **not** deleted.

## 2. First start

* SeisZone opens with an empty Sources list. Add your instruments (section 3). A Kelunji Gecko
  appears by itself once its USR-W610 is set up (section 4).
* Everything you add is saved. Next time SeisZone starts it connects to all sources again by
  itself.
* When Windows asks whether SeisZone may use the network (the firewall question), allow it for
  the networks you use. The installer has already added a rule, so usually no question comes.
* Only one SeisZone runs at a time. Starting it again just brings the open window to the front.

## 3. Add an instrument (only IP address and port)

1. Click **Add source** in the toolbar (or press Ctrl+N).
2. Type the IP address and the port, for example `10.8.0.182:18000`.
3. Click **Detect**. SeisZone asks the instrument what it is: station and network code, site
   name, channels, and its StationXML (instrument response). Over a slow VPN this can take
   a minute or two; the progress lines show what it is doing.
4. Check the result: the display name (you may change it, e.g. KUMARI) and the channels to
   read (all data channels are ticked). Click **Add**.

Examples for our Guralp Fortimus units:

| Unit | SeedLink (recommended) | GCF (Guralp's own protocol) |
|------|------------------------|-----------------------------|
| Kangsabati | `10.8.0.180:18000` | `10.8.0.180:1567` |
| Kangsabati Down | `10.8.0.181:18000` | `10.8.0.181:1567` |
| Kumari | `10.8.0.182:18000` | `10.8.0.182:1567` |

What you type decides how SeisZone reads the instrument:

| You type | SeisZone reads |
|----------|----------------|
| `10.8.0.182` (IP only) | **SeedLink** (miniSEED) on port 18000. Only if no SeedLink server answers there does it try GCF on 1567 (and says so). |
| `10.8.0.182:18000` | **SeedLink** only |
| `10.8.0.182:1567` | **GCF** only (Scream protocol) |

* **SeedLink is the best choice:** after a break (VPN down, PC off) the instrument sends the
  missed data again, so recordings have no gaps. SeisZone shows **live data first** and fetches
  the missed time in the background over a second connection; the Sources list then says
  "live - fetching missed data, 5 h 10 min to go". The first time SeisZone reads a station it
  fetches the instrument's **last day** the same way. **Latency** is always that of the live
  data (a few seconds; SeisZone measures it against UTC from internet time servers, so a
  wrong PC clock does not change it).
* **GCF (1567)**: use it if SeedLink is switched off on the instrument.
* You can also add any other SeedLink server on the Internet or your network, for example a
  public data centre server on port 18000.

If nothing answers, the notes say why (wrong address, VPN not connected, port closed). You can
still click **Add anyway as SeedLink**: SeisZone then keeps trying to connect in the
background and starts receiving as soon as the instrument can be reached.

To remove a source, select it in the Sources list and click **Remove**. **Reconnect** drops
the connection and opens it again at once.

## 4. Kelunji Gecko SMR03 with a USR-W610

A Gecko is not added with Add source. Its USR-W610 (serial to WiFi/LAN converter) connects
**to this PC** and SeisZone receives it on TCP port **58772**.

Set up the USR-W610 once (in its own web page, see its manual):

| Setting | Value |
|---------|-------|
| Work mode / socket | **TCP Client** |
| Server address | the IP address of **this PC** (see below) |
| Server port | **58772** |
| Serial port | as the Gecko's telemetry port is set (the W610 just passes the data through) |

To find this PC's IP address: open a Command Prompt and type `ipconfig`; use the "IPv4
Address" of the network the W610 is on. Give the PC a fixed IP address (or a DHCP
reservation in the router), otherwise the W610 cannot find it after the address changes.

Nothing else is needed on the PC: the firewall rule was made by the installer and SeisZone
listens on 58772 whenever it runs. The Gecko then appears in the Sources list under
**Gecko receiver :58772** with its serial number and site name, and its channels (network
SS, station SMR03) appear in the channels table.

If the old Gecko SeedLink service of the earlier Gecko setup still runs on this PC, the Gecko
connects to it instead; see **Gecko recorder does not appear / port in use** in section 13.

## 5. The main window

* **Toolbar**: Add source, Remove, Reconnect, Open viewer, Record, Export, Settings, About.
* **Sources** (left): your instruments and servers with a coloured dot:
  green = receiving data, amber = connecting or waiting for data, red = error,
  grey = stopped or switched off. Also listed: the **Gecko receiver** (Geckos connected to this
  PC) and the **SeedLink server** (programs reading data from SeisZone).
* **Channels** (centre): one row per channel (section 6). Tick channels and click **Open viewer** in
  the toolbar to see them live in a viewer window of their own (section 7).
* **Log** (bottom): what SeisZone is doing, also errors and warnings.
* **Status bar**: sources receiving, Gecko links, SeedLink server clients, recording state,
  free disk space and the PC clock: "PC clock 19.0 s fast - corrected" means the PC clock is
  wrong, but SeisZone uses the correct UTC anyway (to set the PC clock right: Windows Settings >
  Time & language > Date & time > Sync now).
* **Window** menu: open a new viewer, bring an open viewer to the front, close all viewers.

The panels can be moved, closed and opened again from the **View** menu. The window size and
layout are remembered.

## 6. The channels table and its colours

Columns: **View** (tick the channels you want to see together), Channel (NET.STA.LOC.CHA),
Description, Rate (samples per second), Latest sample (UTC), Latency, Packets, Gaps, Source.

**Latency** is how old the newest sample is: UTC now minus the time stamp of the newest sample.
UTC now comes from internet time servers (checked every 15 minutes), so it is right even when the
PC clock is not; without internet SeisZone uses the PC clock. The colour of the row tells you at a glance:

| Colour | Meaning |
|--------|---------|
| Green | receiving: the newest data is fresh (less than about 30 s, or within two packets) |
| Amber | late: no new data for up to 5 minutes |
| Red | no recent data for more than 5 minutes: check the instrument, the VPN or the network |

A slow channel (for example 5 samples per second) sends a packet only every minute or so, so
its latency is naturally larger; SeisZone allows for that.

Use the filter box to find channels, **Tick all** / **Untick all** for the listed channels, and
click a column title to sort. To see channels:

* tick them and click **Open viewer** in the toolbar (or Ctrl+Shift+N): a new viewer window with those
  channels;
* **double-click** a channel: a viewer with that channel alone;
* right-click selected channels: **Open in a new viewer**, or **Add to viewer** (an open one);
* **drag** channels from the table into an open viewer;
* in the Sources panel, right-click a source or station: **Open in a viewer**.

## 7. Viewer windows

A viewer is a window of its own (with its own taskbar button, minimize and maximize). Open as
many as you like, with any channels; the same channel can be in several viewers with different
settings. Closing a viewer (X) closes only that viewer, never SeisZone. SeisZone starts with its
main window alone; tick **Open the viewer windows again that were open when SeisZone was
closed** in Settings > Program to have them back at every start.

On the left of every trace a box shows its **station** (or the label you gave the channel), its
**location.channel**, the **site name** (for example Nagdhaniba) and its **units** (counts, m/s,
m/s² or g). The colours follow the
component: **Z green, N red, E sky blue**, others blue. The box
of the channel under the mouse is drawn in full colour.

**History of the last day.** A viewer can show up to **1440 minutes (one day)**, also of times
before SeisZone was started:

* SeisZone keeps a per-second summary (minimum, maximum) of every channel for the last day and
  saves it when it stops, so a long window shows the whole day also after a restart.
* Older times are read from your **recordings** (in the background; "Reading the recorded
  history..." shows meanwhile): full detail for short windows, and filtered views of long ones.
  Record a station continuously and its whole history can be viewed.
* Data that was received but not recorded can be shown in long windows only, and without a
  filter (the note says so). The live data in memory (Settings: "Live data kept in memory")
  always has full detail.

### The bar above the traces

* **Channels**: tick which channels this viewer shows.
* **Time ◂▸ 2 ▸◂**: the time scale in **pixels per second**.
  ◂▸ (or a left click on the number, the Left arrow, Shift+wheel up) expands the traces in time;
  ▸◂ (a right click on the number, the Right arrow, Shift+wheel down) squashes them, up to
  1440 minutes (one day) in the window. The status bar shows the time range on screen. A wider
  window shows more time at the same scale.
* **Amplitude - x1 +**: makes every trace larger or smaller in steps of 2 (click x1 to reset).
  Keys: Up / Down, or the mouse wheel.
* **Filter**: click to switch the filter of the whole window on or off (key **F**); the arrow
  beside it chooses the filter: the **Default band-pass** (0.1 to 0.9 of each channel's Nyquist
  frequency, e.g. 5-45 Hz at 100 sps; 10-30 s period for 1 or 2 sps data), the ready-made
  high-pass, low-pass and band-pass filters, your saved filters, or **Design...** (Ctrl+F).
  Filters change only the display, never recorded data.
* **Units**: Counts, Physical units (m/s, m/s² from StationXML or your calibration), or Physical
  with acceleration in **g**.
* **Scale**: Common per instrument (the components of one sensor share one amplitude scale, so a
  quiet component looks quiet next to the others; every instrument has its own scale - the choice
  of a new viewer), Common, all channels (one scale for every trace: the largest one sets it, so
  with different instruments the quiet ones look flat), Auto (each trace fills its row), Fixed ± a
  value you type, or **Per pixel (1:N)** - the zoom of the earlier viewer: one value for the whole
  window, N counts per pixel of row height (shown as 1:16, 1:8192 in place of the amplitude
  zoom; Up / Down or the mouse wheel halve / double it, Home fits it to the traces). Each trace is
  divided by its own scale factor, which its label box shows instead of the units (×3067).
* **Remove mean**: centres each trace on zero. **Null** (key **N**): fixes each trace's offset
  at its mean now.
* **The eye**: shows or hides the details line above each trace (channel, min / max, units,
  filter) - the eye is lit while they are shown, crossed out while hidden (key **I**). The label
  boxes on the left stay.
* **Time cursors**: two vertical lines to drag; the button shows the time between them and the
  matching frequency. **Ampl cursors**: two horizontal lines on the channel under the mouse;
  the button shows the difference.
* **Live / Paused**: Live follows the newest data. A paused viewer turns amber and goes live by
  itself after 2 minutes without mouse use.

### Mouse

* **Wheel**: larger / smaller traces (stays live). **Shift+wheel**: shorter / longer window.
  **Ctrl+wheel**: scroll back / forward in time (pauses).
* **Shift+drag**: move in time (pauses). **Double-click**: live again.
* **Drag**: select a time range (also while live), as in the earlier viewer, on the channels
  from the one where you start to the one where you let go - drag down (or up) over several channels to take them all.
  **Ctrl+drag**: exactly two channels - the one where you start and the one where you let go, in
  that order (for a calibration: the calibration signal first, then the sensor output). The
  selected part stays marked while the menu is open (hatched where a channel has a gap), the
  number of samples is shown on each selected channel, and every item works on the selected
  channels only:
  * **Save... (miniSEED)** - export the selection; **Zoom In** - show only the selection;
  * **Save on selection** - when ticked, every new selection of this viewer is saved at once
    (one miniSEED file per channel in Documents\SeisZone\Exports; the status bar says so);
  * **Spectrum...**, **PSD in Acceleration Units**, **PSD in Velocity Units** - the power
    spectral density of every channel (needs StationXML or a Calibration; else counts);
  * **Peak values** - peak, peak-to-peak, RMS, and for acceleration channels the peak in g and
    the peak velocity;
  * **Relative Orientation** - the angle between the horizontal sensors of two stations in the
    viewer (both need N and E channels);
  * **Strong-motion analysis...** - for every acceleration channel: PGA (m/s² and g), PGV, PGD,
    RMS, Arias intensity, CAV, the 5-95 % duration and the predominant period; and for the
    channel you choose a graph of its response spectrum (PSA, PSV or SD at 2, 5 or 10 %
    damping), its acceleration, velocity or displacement, or its Fourier spectrum;
  * **Sinewave / Step / Broadband Noise Calibration...** - see "Calibration checks" below;
  * **External tools** - your own programs (an analysis package, a script): **Set up external
    tools...** lists them (name, program, arguments). The selected channels are written as
    miniSEED files into a new folder and the program is started with them; in the arguments
    {files} stands for the files (the default), {folder}, {t0}, {t1} (UTC) and {sids};
  * use it as "noise" or "signal" in the filter design.
* **Right-click** a channel: the menu below. The status bar shows the time and value under the
  mouse.
* Prefer dragging to move in time? Untick **View > Drag selects a time range**: then a drag
  moves in time and Shift+drag selects (each viewer keeps its choice).

### Right-click menu

For the channel under the mouse:

| Entry | Key | What it does |
|-------|-----|--------------|
| Colour... | C | the trace colour |
| Scale... | S | a scale factor for this channel (2 = twice as large; *2, /2; auto or 1 = back; another channel's name = as high as that one; also for all channels of the station or of the viewer) |
| Scale others to match | Ctrl+S | every other channel gets the scale factor at which it is as high as this one (shown in its label box), and the viewer goes to Per pixel with the zoom at which this one fills its row - as in the earlier viewer |
| Details... | D | mean, minimum, maximum, difference and RMS of the visible data; type an offset; Null / Zero / Mean removal. The plot shows the mean (dotted) and min / max lines |
| Filter | | its own filter: same as the window, none, any filter of the list, or Design for this channel |
| Offset | Z = zero | follow Remove mean, Null now (fix at the mean), Zero (absolute values) |
| Locked offset | | Null (N) leaves this channel alone |
| Spectrogram | P | a colour spectrogram under the trace (red = strong, purple = weak). + / - brighter / darker; the wheel over it zooms the frequency |
| Amplitude cursors on this channel | | the two amplitude cursors on this channel |
| Label... | | a name for the channel, shown in its box on the left (empty: the station) |
| Calibration... | | counts per m/s or m/s² for a channel without StationXML (then **g** works too) |
| Remove from this viewer | Del | |

For the whole viewer: Background colour, Show lane lines (L), Overlay channels (O, all traces
in one row on top of each other), Clean display (K: no amplitude axis, caption, frame or gap per
row - the traces packed like the earlier viewer's), Traces may overlap (Shift+O: a loud trace
draws over its neighbours instead of being cut at its row), Spectrogram size, All spectrograms on / off (Ctrl+P), Sort by
(channel, component Z-N-E, station, sample rate, also reversed), Channels..., Window title... (the
viewer's name), Clear window (Ctrl+Del), No caption (F11: full screen without menu and bar,
F11 again to return), Duplicate (Ctrl+D), and Spectrum / Export / Record these channels / Save
picture. The menu bar has the same and **Print** (Ctrl+Shift+P); Help > Viewer keys and mouse
lists every key.

### Filter design

The **Highpass** part on red and the **Lowpass** part
on green, each on or off, with its corner in Hz or seconds and its order (1-8); both on is a
band-pass. **Gain (dB)** makes the filtered trace larger or smaller. The graph shows the response
for a chosen sample rate (Log or Lin, frequencies in Hz and seconds). **Click or drag with the
left mouse button** in the graph to move the high-pass corner (red line), **with the right
button** to move the low-pass corner (green line); a line can also be grabbed and dragged with the
left button. Spectra of selected time
ranges (Shift+drag in the viewer, "Use as noise / signal in the filter design") are drawn under
it, to put the corners where the event is stronger than the noise. **Apply** / **OK** use the
design; **Auto-apply** uses every change at once; **Save...** keeps it under a name for every
viewer. Opened from a channel's menu, the design is for that channel only.

### Calibration checks

These compare the **calibration signal** fed into a sensor with the sensor's **output**. You
need both in the viewer: the sensor's channel and the channel that carries the calibration
signal (the instrument must be sending that channel, and the calibration signal must be switched
on at the instrument - SeisZone does not switch it on). Select the time of the calibration with
Shift+drag, choose the check, then pick the two channels and press **Calculate** - or Ctrl+drag
from the calibration signal's channel to the sensor's channel, and both are picked for you.

| Check | Calibration signal | What you get |
|-------|--------------------|--------------|
| Sinewave | a sine | its frequency, the sensor's sensitivity at that frequency, and the phase between input and output |
| Step | a square wave | the output after each step, stacked. Accelerometer: the settled gain. Seismometer (velocity): the natural period and the damping |
| Broadband noise | noise | gain, phase and coherence over frequency; the gain of the flat part of the response |

For results in physical units enter the three values of the calibration sheets: the calibration
channel's **microvolts per count**, the **calibration resistor** (ohm) and the **coil
constant**. SeisZone keeps them for the station. With all three left at 1 the results are
relative (output counts per calibration count). When the channel has a sensitivity (StationXML
or Calibration), the result also says how many percent the measured one differs.

Each trace shows its channel name, the minimum and maximum of what is visible, and its filter,
scale factor and offset when they are set. Breaks in a trace are gaps in the data.

**Live backfill.** When a link was down and the instrument sends the missed data afterwards
(SeedLink instruments always do: SeisZone asks for everything since the last record it got), an
open viewer draws those data into the gap within about two seconds, like the recording gets them.
You do not need to reopen the viewer. Data older than the live buffer (Settings > Display) are
shown from the recordings. Instruments pulled over GCF UDP (port 1567) cannot send missed data
again yet: their gaps stay.

## 8. Recording

Click **Record** (or Ctrl+R). The Recordings window shows all recordings (top) and lets you
start new ones (bottom). It is a window of its own: minimize it, maximize it, or keep it open
beside SeisZone. Drag the **divider** between the two parts up or down to make the recordings
list bigger or smaller; size and divider are remembered. From a viewer, **Record these
channels** opens it with that viewer's channels ticked.

Start a recording:

1. Name: any name, for example "Kumari March 2026".
2. Folder: where the files go. Default: `Documents\SeisZone\Recordings`.
3. Channels: **all channels** (also channels that appear later, e.g. a Gecko that connects
   tomorrow), or a list you choose.
4. File length: 15, 30 or 60 minutes. Files start on the clock (UTC), e.g. 10:00, 11:00.
5. Files: one file per channel, or one file per station with all its channels.
6. Click **Start recording**.

Where the files go:

```
<folder>\WB.KUM\2026-03-14\WB.KUM.0J.HNZ_20260314_1000_1100.mseed   (one file per channel)
<folder>\WB.KUM\2026-03-14\WB.KUM_20260314_1000_1100.mseed          (one file per station)
<folder>\recording_log.txt
```

The name holds the date, the start and the end time of the file (UTC, HHMM): the files above
hold 10:00 to 11:00 UTC. The last file of a day ends at `2400`. Files recorded by SeisZone 1.7
and older are named `WB.KUM.0J.HNZ__20260314T100000Z.mseed`; they are still found by the
viewers and the export, and are never renamed.

**When the computer was switched off or restarted while recording:** the recording goes on by
itself when SeisZone starts again. For SeedLink instruments SeisZone asks each instrument for
the data it missed (from the last record it received, as far back as the instrument still
holds it) and writes it into the same folder and into the file of its hour,
next to what was already recorded - nothing is written twice. `recording_log.txt` says when
data came back and which gap stayed. Instruments pulled over GCF UDP (port 1567) cannot send
old data again: the time the computer was off stays a gap in their files.

The files are standard **miniSEED**: any seismology program can read them. SeisZone writes the
data exactly as the instrument sent it (no conversion, no loss). Data that arrives late (for
example sent again by the instrument after a VPN break) is written into the right older file.

**recording_log.txt** in the recording folder lists when the recording started and stopped,
every new file, gaps per channel, and when links were lost and came back.

Important:

* **A recording runs until you stop it.** Select it and click **Stop...**. The files stay.
* **SeisZone never deletes recordings.** When the disk gets full (less than 5 GB or 5 % free)
  it only warns you in the log and the status bar. Then move old files to another disk
  yourself.
* **Recordings survive restarts.** If SeisZone is closed, crashes or Windows restarts, every
  recording that was running continues as soon as SeisZone runs again. Tick "Start SeisZone
  when I log in to Windows" in Settings so this happens without you.
* **Open folder** shows the files of a recording in Explorer.

You can also start and stop recordings from the tray icon menu (Recording...).

## 9. Export a time window

Click **Export** (or Ctrl+E).

1. Choose the channels.
2. Choose the start and end time (UTC).
3. Choose **One file per channel** or **One file with all channels**.
4. Choose the output folder (default `Documents\SeisZone\Exports`) and click Export.

SeisZone takes the data from your recordings first and, for anything not recorded, from the
live data in memory (the last 60 minutes by default). It cuts exactly the chosen window and
writes miniSEED files plus a report `export_info__<time>.txt` with the number of samples and
any gaps per channel.

## 10. The SeedLink server: data for other programs

SeisZone offers every channel it receives (Fortimus, Gecko, other servers) over SeedLink, so
other seismology programs can read it at the same time.

In the other program, add a SeedLink source with:

* on this PC: **`127.0.0.1`**, port **18000**
* from another PC on the network: **this PC's IP address**, port **18000**
  (the installer's firewall rule allows it)

The other program can ask for recent data again after it was closed (standard SeedLink
sequence numbers), as long as SeisZone still holds it in memory. The Sources list shows the
connected programs under **SeedLink server :18000**.

### The data server: this PC's data on another computer (UDP 1567 and TCP 18000)

The **Data server** button (main window tool bar, also in the File menu) opens a window like the
earlier viewer's "My Server" page. It has two tabs, one per server:

* **GCF over UDP (earlier viewer)**: port 1567, off until you switch it on (below).
* **SeedLink over TCP**: port 18000, on by default. Every SeedLink program reads all of this PC's
  data from it: SeisZone (**Add source**, this PC's IP:18000), SeisComP, Swarm, ObsPy. A SeedLink
  client asks again for what it missed, so its data are backfilled after a network outage. The
  tab shows whether the server runs, this PC's addresses, the connected programs (Client,
  Stations, Connected since, Packets sent) and what happened (Control). Tick or untick it, or
  type another port, and press **Apply**. Listen on and Organization stay in Settings >
  SeedLink server.

GCF over UDP, step by step:

1. Tick **Send all data to other computers on the network (GCF over UDP)** and press **Apply**.
   The port is **1567**; "Channels up to" leaves out channels with a higher sample rate.
2. On the other computer (same network):
   * **earlier viewer**: Windows > Network Control > right-click > **Add UDP Server**, type
     **this PC's IP:1567** (the window shows this PC's IP addresses);
   * **SeisZone**: **Add source**, type **this PC's IP:1567**. SeisZone sees that it is
     SeisZone and reads all its stations from its SeedLink server (port 18000) instead: the exact
     channel names, and data missed while the network was down is fetched again. If that
     SeedLink server is off, it reads GCF (one station per source, with its exact names).
3. The window lists the connected computers (Clients, Params, since when, last request, blocks
   sent) and what happened (Control). The status bar shows **Data server :1567 - 1 client**.

Only one program on a PC can use a port. If the earlier viewer runs on this PC with port 1567,
the window says "port 1567 is in use by another program": choose another port (for example
1568) and type that on the other computer. The server is off until you switch it on, so it never
takes the port from the earlier viewer by itself.

Each channel goes out in blocks of up to 2 seconds starting on whole seconds, as instruments
send them; less than a second at the very start of a stream (or after a gap) is not sent.

## 11. Settings

Click **Settings**. Changes take effect when you click OK; ports are reopened at once.

* **Gecko receiver**: on/off, the TCP port(s) (default 58772, several ports separated by
  commas), and a naming table to give a Gecko's data other network / station / location codes
  (by serial number).
* **SeedLink server**: on/off, listen address (0.0.0.0 = all networks, 127.0.0.1 = only this
  PC), port (default 18000; SeisZone 1.0.0 used 18010 and moves it to 18000 by itself), organization name.
* **Recording**: default folder, file length, one file per channel or per station.
* **Display**: how many minutes of live data are kept in memory per channel, dark or light
  colours, default time window.
* **Program**: closing the window keeps SeisZone running in the tray; start minimized in the
  tray; **Start SeisZone when I log in to Windows**.

If you change the Gecko port, change the USR-W610 to the same port. If you change the SeedLink
server port, change it in the other programs too.

## 12. The tray icon

SeisZone has an icon in the notification area (next to the clock). When "closing the window
keeps SeisZone running in the tray" is on (default), closing the window does **not** stop
SeisZone: data keeps coming in, recordings keep running and the SeedLink server keeps serving.

Right-click the tray icon: **Show SeisZone**, **Recording...**, **Quit**. Only **Quit** (here or
File > Quit) stops SeisZone completely. Running recordings then pause and continue when
SeisZone starts again.

## 13. Troubleshooting

**A Fortimus (or other SeedLink server) shows no data (amber or red)**

* VPN: the Fortimus units (10.8.0.x) are reached over the VPN. Check that the VPN is
  connected. When it comes back SeisZone reconnects by itself and fetches the missed data.
* Test the address: open a Command Prompt and type `ping 10.8.0.182`.
* Wrong port: SeedLink is 18000, GCF is 1567. Remove the source and add it again with Detect.
* Click **Reconnect** on the source, and look at the **Log** panel for the reason.

**The Gecko does not connect (nothing under "Gecko receiver")**

* SeisZone must be running (in the window or the tray).
* The USR-W610 must be a **TCP Client** to **this PC's current IP address**, port **58772**.
  If the PC's IP address changed, change it in the W610 (or give the PC a fixed address).
* Firewall: the installer adds the rule "SeisZone". If you use other security software, allow
  SeisZone or TCP port 58772 there too. Check in Windows Defender Firewall > Advanced settings
  > Inbound Rules that "SeisZone" is enabled.
* Both devices must be on the same network (or a route must exist between them).

**Gecko recorder does not appear / port in use**

The Gecko connects to whatever program listens on port 58772 of this PC. If the **old Gecko
SeedLink service** (the Windows service "GeckoSeedLink" of the earlier Gecko setup) still runs,
it holds port 58772 (Gecko) and its own SeedLink port 18010, the Gecko stays connected to it,
and SeisZone shows on the **Gecko receiver** row:
`error - port 58772 is used by the old Gecko SeedLink service`.

* The first time this happens while the SeisZone window is open, SeisZone asks: *"Your Gecko
  recorder is connected to the old Gecko SeedLink service, not SeisZone. Stop and disable the
  old service now ...?"* Click **Stop old service**. Windows asks once for administrator permission (click Yes).
* Later, use the button **Free the port – stop the old Gecko SeedLink service** above the
  sources list, or right-click the Gecko receiver / SeedLink server row.
* The rows then show **Waiting for the port…**. Within a few seconds SeisZone opens the ports
  itself, the rows turn to **listening** and the Gecko connects to SeisZone (the W610 dials
  again by itself, usually within a minute).
* The installer does the same when its task **"Stop and disable the old Gecko SeedLink
  service..."** stays ticked (it is offered only when that service exists).
* The old service is only stopped and switched off (start type "Disabled"), not removed. To use
  it again: Windows key, type **Services**, open **Gecko SeedLink Server**, set Startup type to
  Automatic and click Start (then SeisZone gets the ports no more).

If the row says `port ... is in use by another program`, some other program holds the port
(another SeedLink server, a second copy of a program). Close or uninstall that program, or
choose another port in Settings (and then change it in the W610 or the other programs too).
SeisZone tries the port again every few seconds and takes it as soon as it is free. The log
names the busy port and the process id of the program that holds it (Task Manager, Details
tab, PID column).

**Physical units are not offered for a channel**

SeisZone has no StationXML for that station. Remove and re-add the source with Detect (with a
working connection to the instrument's web page), and wait until the detection finishes.

**Low disk space warning**

SeisZone never deletes anything. Copy old recording folders to another disk or archive, then
delete them yourself, or choose a folder on a bigger disk for new recordings.

**Something else**

Look in the Log panel, or open the log file (Help > Log folder). Send the file
`seiszone.log` with your question.

## 14. Where SeisZone keeps its files

| What | Where |
|------|-------|
| Recordings (default) | `Documents\SeisZone\Recordings` |
| Exports (default) | `Documents\SeisZone\Exports` |
| Program log | `%LOCALAPPDATA%\SeisZone\logs\seiszone.log` (Help > Log folder) |
| Settings and sources | `%APPDATA%\SeisZone\config.json` |
| Running recordings, SeedLink positions | `%APPDATA%\SeisZone\state.json` |
| StationXML (instrument responses) | `%APPDATA%\SeisZone\stationxml` |
| The program | `C:\Program Files\SeisZone` |

To open a folder like `%APPDATA%\SeisZone`, type it into the Explorer address bar. These files
belong to your Windows user. Neither SeisZone nor its uninstaller deletes recordings, exports,
settings or logs.
