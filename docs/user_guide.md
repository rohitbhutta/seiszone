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
programs (the SeisZone SeedLink server, port 18010) connect to this PC.

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

* **Port 18000 = SeedLink.** Best choice: after a break (VPN down, PC off) the instrument sends
  the missed data again, so recordings have no gaps.
* **Port 1567 = GCF** (Scream protocol). Use it if SeedLink is switched off on the instrument.
* You can also add any other SeedLink server on the Internet or your network, for example a
  public data centre server on port 18000.
* If you do not know the port, type only the IP address: SeisZone tries SeedLink (18000) and
  GCF (1567) itself.

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

* **Toolbar**: Add source, Remove, Reconnect, Record, Export, Spectrum, Settings, About.
* **Sources** (left): your instruments and servers with a coloured dot:
  green = receiving data, amber = connecting or waiting for data, red = error,
  grey = stopped or switched off. Also listed: the **Gecko receiver** (Geckos connected to this
  PC) and the **SeedLink server** (programs reading data from SeisZone).
* **Waveforms** (centre): the live traces (section 7).
* **Channels** (bottom): one row per channel (section 6).
* **Log** (bottom): what SeisZone is doing, also errors and warnings.
* **Status bar**: sources receiving, Gecko links, SeedLink server clients, recording state and
  free disk space.

The panels can be moved, closed and opened again from the **View** menu. The window size and
layout are remembered.

## 6. The channels table and its colours

Columns: **Show** (tick = plot this channel), Channel (NET.STA.LOC.CHA), Description, Rate
(samples per second), Latest sample (UTC), Latency, Packets, Gaps, Source.

**Latency** is how old the newest sample is. The colour of the row tells you at a glance:

| Colour | Meaning |
|--------|---------|
| Green | receiving: the newest data is fresh (less than about 30 s, or within two packets) |
| Amber | late: no new data for up to 5 minutes |
| Red | no recent data for more than 5 minutes: check the instrument, the VPN or the network |

A slow channel (for example 5 samples per second) sends a packet only every minute or so, so
its latency is naturally larger; SeisZone allows for that.

Use the filter box to find channels, **Show all** / **Hide all** to tick or untick the listed
channels, and click a column title to sort.

## 7. The waveform view

The bar above the traces:

* **Window**: how much time is shown (30 s up to 60 min).
* **Filter**: No filter, High-pass 0.1 Hz or 1 Hz, Low-pass 5 Hz or 20 Hz, Band-pass
  0.5-5 Hz, 1-10 Hz or 1-20 Hz. The filter changes only the display, never the recorded data.
* **Units**: Counts (raw numbers from the digitiser) or Physical units (for example m/s or
  m/s²), calculated with the instrument response from the StationXML. Physical units are
  only available for channels whose StationXML SeisZone has.
* **Scale**: Auto (each trace fills its row), Common (all traces with the same scale, good to
  compare amplitudes), or Fixed ± a value you type.
* **Remove mean**: centres each trace on zero.
* **Live / Paused**: Live follows the newest data.

Mouse:

* **Mouse wheel** zooms in time, **drag** moves the view. Zooming or moving pauses the live view so
  you can look at an event calmly.
* **Double-click** a trace (or click **Paused**) to follow the newest data again.

Each trace shows its channel name and the minimum and maximum of what is visible. Breaks in a
trace are gaps in the data.

**Spectrum** (toolbar) shows the amplitude spectrum of the shown channels for the time range
that is visible when you open it.

## 8. Recording

Click **Record** (or Ctrl+R). The window shows all recordings and lets you start new ones.

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
<folder>\WB.KUM\2026-03-14\WB.KUM.0J.HNZ__20260314T100000Z.mseed     (one file per channel)
<folder>\WB.KUM\2026-03-14\WB.KUM__20260314T100000Z.mseed            (one file per station)
<folder>\recording_log.txt
```

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

* on this PC: **`127.0.0.1`**, port **18010**
* from another PC on the network: **this PC's IP address**, port **18010**
  (the installer's firewall rule allows it)

The other program can ask for recent data again after it was closed (standard SeedLink
sequence numbers), as long as SeisZone still holds it in memory. The Sources list shows the
connected programs under **SeedLink server :18010**.

## 11. Settings

Click **Settings**. Changes take effect when you click OK; ports are reopened at once.

* **Gecko receiver**: on/off, the TCP port(s) (default 58772, several ports separated by
  commas), and a naming table to give a Gecko's data other network / station / location codes
  (by serial number).
* **SeedLink server**: on/off, listen address (0.0.0.0 = all networks, 127.0.0.1 = only this
  PC), port (default 18010), organization name.
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
it holds port 58772 (Gecko) and 18010 (SeedLink), the Gecko stays connected to it, and SeisZone
shows on the **Gecko receiver** and **SeedLink server** rows:
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
