// SeismoVerse user manual (Word). Usage: node make_manual.js <shots dir> <icon png> <out.docx>
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, ImageRun, Table, TableRow,
  TableCell, WidthType, ShadingType, BorderStyle, LevelFormat, TableOfContents, Footer, Header,
  PageNumber, PageBreak, TabStopType, Bookmark, InternalHyperlink,
} = require("docx");

const [SHOTS, ICON, OUT] = process.argv.slice(2);
const VERSION = "2.0.0";
const TEAL = "1F8A7E";
const DARK = "1B2A33";
const MUTED = "5B6B73";
const FONT = "Calibri";
const MONO = "Consolas";
const CONTENT_W = 9026;           // A4 width 11906 - 2 x 1440 margins (DXA)
const MAX_IMG_PX = 600;           // content width at 96 dpi is about 602 px

// ---------------------------------------------------------------------------------------------
// inline text: **bold**, `code`
function runs(text, opts = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...opts }));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(new TextRun({ text: tok.slice(2, -2), bold: true, ...opts }));
    else out.push(new TextRun({ text: tok.slice(1, -1), font: MONO, size: 20, ...opts }));
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...opts }));
  return out;
}

const body = [];
const model = [];          // the same content for the PDF (make_pdf.py)
const chapters = [];       // [bookmark id, title] for the contents list
let listInstance = 0;
function H1(t, first = false) {
  const id = `ch${chapters.length + 1}`;
  chapters.push([id, t]);
  model.push({ k: "h1", t, id });
  body.push(new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: !first,
    children: [new Bookmark({ id, children: [new TextRun(t)] })] }));
}
const H2 = (t) => { model.push({ k: "h2", t }); body.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] })); };
const H3 = (t) => { model.push({ k: "h3", t }); body.push(new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(t)] })); };
const P = (t) => { model.push({ k: "p", t }); body.push(new Paragraph({ children: runs(t), spacing: { after: 120 } })); };
const BULLETS = (items) => { model.push({ k: "bullets", items }); items.forEach((t) => body.push(new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: runs(t), spacing: { after: 60 } }))); };
function STEPS(items) {
  model.push({ k: "steps", items });
  listInstance += 1;
  items.forEach((t) => body.push(new Paragraph({ numbering: { reference: "steps", level: 0, instance: listInstance }, children: runs(t), spacing: { after: 60 } })));
}
function NOTE(t, label = "Note") {
  model.push({ k: "note", t, label });
  body.push(new Paragraph({
    children: [new TextRun({ text: label + ": ", bold: true, color: TEAL }), ...runs(t)],
    shading: { type: ShadingType.CLEAR, color: "auto", fill: "E8F4F2" },
    border: { left: { style: BorderStyle.SINGLE, size: 18, color: TEAL, space: 8 } },
    spacing: { before: 120, after: 160 }, indent: { left: 160, right: 160 },
  }));
}

function pngSize(file) {
  const b = fs.readFileSync(file);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20), data: b };
}
let figure = 0;
function IMG(name, caption, maxW = MAX_IMG_PX) {
  const { w, h, data } = pngSize(path.join(SHOTS, name));
  const scale = Math.min(1, maxW / w);
  figure += 1;
  model.push({ k: "img", file: name, caption: `Figure ${figure}. ${caption}`, maxW });
  body.push(new Paragraph({
    alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 120, after: 60 },
    children: [new ImageRun({ type: "png", data, transformation: { width: Math.round(w * scale), height: Math.round(h * scale) },
                              altText: { title: caption, description: caption, name: name } })],
  }));
  body.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
    children: [new TextRun({ text: `Figure ${figure}. ${caption}`, italics: true, size: 18, color: MUTED })] }));
}

const border = { style: BorderStyle.SINGLE, size: 4, color: "B8C4C9" };
const borders = { top: border, bottom: border, left: border, right: border };
function TABLE(header, rows, widths) {
  model.push({ k: "table", header, rows, widths });
  const total = widths.reduce((a, b) => a + b, 0);
  const cols = widths.map((w) => Math.round((w / total) * CONTENT_W));
  cols[cols.length - 1] += CONTENT_W - cols.reduce((a, b) => a + b, 0);
  let keep = false;
  const cell = (t, i, head) => new TableCell({
    borders, width: { size: cols[i], type: WidthType.DXA },
    shading: head ? { fill: TEAL, type: ShadingType.CLEAR, color: "auto" } : undefined,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: [new Paragraph({ keepNext: keep, children: runs(t, head ? { bold: true, color: "FFFFFF" } : {}) })],
  });
  body.push(new Table({
    width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: cols,
    rows: [(() => { keep = rows.length <= 8; return new TableRow({ tableHeader: true, children: header.map((t, i) => cell(t, i, true)) }); })(),
           ...rows.map((r, n) => { keep = rows.length <= 8 && n < rows.length - 1;
                                   return new TableRow({ cantSplit: true, children: r.map((t, i) => cell(t, i, false)) }); })],
  }));
  body.push(new Paragraph({ spacing: { after: 120 }, children: [] }));
}

// =============================================================================================
// CONTENT
// =============================================================================================
H1("1. Introduction", true);
P("**SeismoVerse** is a Windows program for **live seismic data**. It reads your instruments over the network, shows every channel live, records them to standard miniSEED files and shares all data with other computers and seismology programs.");
P("With SeismoVerse you can:");
BULLETS([
  "add an instrument or SeedLink server by typing only its **IP address** (and a port, if it is not the usual 18000);",
  "see many channels at once in **viewer windows**, with filters, spectra, spectrograms and analysis tools;",
  "**record** channels without gaps, also across network breaks and restarts of the PC;",
  "**export** any time window to miniSEED files;",
  "receive **Gecko recorders** that connect to this PC by themselves;",
  "offer all data to other computers with its own **SeedLink server** (port 18000), including the last two days kept on disk.",
]);
H2("About this manual");
BULLETS([
  "Buttons, menus and boxes on the screen are written in **bold**, for example **Add source**.",
  "Text you type and file names are written like `10.8.0.182`.",
  "All times in SeismoVerse are **UTC**.",
  "SeismoVerse was called **SeisZone** up to version 1.9.0. Your sources, settings, recordings and logs from that version stay where they are and are used as before.",
  "Inside the program, press **F1** (or **Help > User guide**) for the detailed technical guide.",
]);

// ---------------------------------------------------------------------------------------------
H1("2. Installing and updating");
H2("Install");
STEPS([
  "Run **SeismoVerseSetup.exe**. Windows asks for administrator permission: click **Yes**. Setup needs it only to copy the program to `C:\\Program Files\\SeismoVerse` and to add one Windows Firewall rule. SeismoVerse itself always runs with your normal user rights.",
  "Choose whether you want a desktop icon. A Start menu icon is always made.",
  "If the old Gecko SeedLink service is installed on the PC, leave **Stop and disable the old Gecko SeedLink service** ticked, so your Gecko recorder connects to SeismoVerse (see chapter 5).",
  "At the end leave **Launch SeismoVerse** ticked and click **Finish**.",
]);
P("The firewall rule is called **SeismoVerse**. It lets Gecko recorders (port 58772) and other programs (the SeedLink server, port 18000) connect to this PC.");
H2("Update (also from SeisZone)");
STEPS([
  "Run the new **SeismoVerseSetup.exe**. You do not need to uninstall the old version first.",
  "If SeismoVerse (or SeisZone) is running, setup says it will be closed. Click **OK**. Recordings that were running continue by themselves when SeismoVerse starts again.",
  "Finish the setup as above.",
]);
NOTE("An update of SeisZone stays in its folder `C:\\Program Files\\SeisZone`. The old SeisZone program file and its shortcuts are removed; the new Start menu entry is **SeismoVerse**. Your instruments, settings and recordings are kept.");
H2("Uninstall");
P("Open **Windows Settings > Apps**, choose **SeismoVerse** and click **Uninstall**. This removes the program and the firewall rule. Your recordings, exports, settings and log files are **not** deleted.");

// ---------------------------------------------------------------------------------------------
H1("3. The main window");
P("When SeismoVerse starts it connects to all your saved sources by itself. Only one SeismoVerse runs at a time: starting it again brings the open window to the front.");
IMG("01_main_window.png", "The main window: sources (left), channels (centre), log (bottom) and the status bar.");
TABLE(["Part", "What it shows"], [
  ["**Toolbar**", "Add source, Remove, Reconnect, Open viewer, Record, Export, Data server, Settings, About."],
  ["**Sources** (left)", "Your instruments and servers, the **Gecko receiver** (Gecko recorders connected to this PC) and the **SeedLink server** (programs reading data from this PC)."],
  ["**Channels** (centre)", "One row per channel with its rate, newest sample, latency, packets and gaps (chapter 6)."],
  ["**Log** (bottom)", "What SeismoVerse is doing, with warnings and errors. Type in the filter box to find lines."],
  ["**Status bar**", "Sources receiving, Gecko links, SeedLink clients, recording state, free disk space and the PC clock check."],
], [1, 3]);
P("The coloured dot in front of each source means:");
TABLE(["Dot", "Meaning"], [
  ["Green", "receiving data"],
  ["Amber", "connecting, or waiting for data"],
  ["Red", "error (the State column and the log say why)"],
  ["Grey", "stopped or switched off"],
], [1, 3]);
P("The panels can be moved, closed and opened again from the **View** menu. The window size and layout are remembered. The **Window** menu opens a new viewer, brings an open viewer to the front or closes all viewers.");
NOTE("If the status bar says for example **PC clock 19.0 s fast - corrected**, the PC clock is wrong, but SeismoVerse still uses the correct UTC from internet time servers. To set the PC clock right: Windows Settings > Time & language > Date & time > Sync now.", "Tip");

// ---------------------------------------------------------------------------------------------
H1("4. Adding an instrument");
P("Instruments and SeedLink servers are added with their IP address. SeismoVerse finds the stations, channels, sample rates and the instrument response (StationXML) by itself.");
IMG("03_add_source.png", "Add source: type the IP address; leave the Port box empty for SeedLink on port 18000.", 520);
STEPS([
  "Click **Add source** in the toolbar (or press **Ctrl+N**).",
  "Type the **IP address**, for example `10.8.0.182`.",
  "Leave the **Port** box empty for the usual SeedLink port **18000**. Type a port only if the instrument or server uses another one (for example `16000`); it is then used exactly as typed.",
  "Click **Detect**. SeismoVerse asks the instrument what it is. Over a slow VPN this can take a minute or two; the progress lines show what it is doing.",
  "Check the result: the display name (you can change it, for example KUMARI) and the channels to read (all data channels are ticked).",
  "Click **Add**.",
]);
P("What you type decides how SeismoVerse reads the instrument:");
TABLE(["You type", "SeismoVerse reads"], [
  ["IP address, Port empty", "**SeedLink** on port **18000** (the default). Only if nothing answers there does it try GCF on port 1567, and says so."],
  ["Port `18000`", "SeedLink only."],
  ["Port `1567`", "GCF only."],
  ["Any other port, e.g. `16000`", "SeedLink on that port first (any SeedLink server: an instrument, SeisComP, ringserver or another SeismoVerse), then GCF on that port."],
], [1.2, 3]);
H2("Why SeedLink is the best choice");
P("After a break (VPN down, PC switched off) a SeedLink instrument sends the missed data again, so viewers and recordings have **no gaps**. SeismoVerse shows the live data first and fetches the missed time in the background; the Sources list then says for example \"live - fetching missed data, 5 h 10 min to go\". The first time it reads a station it also fetches the instrument's last day.");
P("Use GCF (port 1567) only when SeedLink is switched off on the instrument. Gaps in GCF data cannot be filled later.");
H2("When detection finds nothing");
P("The notes say why: wrong address, VPN not connected, or port closed. If you know a SeedLink server is there, click **Add anyway as SeedLink**: SeismoVerse adds it with the port in the Port box (empty = 18000) and keeps trying to connect in the background.");
H2("Remove or reconnect a source");
P("Select the source in the Sources list and click **Remove**. **Reconnect** drops the connection and opens it again at once. Recordings already written are never removed.");

// ---------------------------------------------------------------------------------------------
H1("5. Gecko recorders");
P("A Gecko recorder is **not** added with Add source. Its USR-W610 (serial to WiFi/LAN converter) connects **to this PC**, and SeismoVerse receives it on TCP port **58772**.");
P("Set up the USR-W610 once, in its own web page:");
TABLE(["Setting", "Value"], [
  ["Work mode / socket", "**TCP Client**"],
  ["Server address", "the IP address of **this PC**"],
  ["Server port", "**58772**"],
  ["Serial port", "as the Gecko's telemetry port is set (the W610 passes the data through)"],
], [1.3, 3]);
P("To find this PC's IP address, open a Command Prompt and type `ipconfig`; use the IPv4 Address of the network the W610 is on. Give the PC a fixed IP address (or a DHCP reservation in the router), otherwise the W610 cannot find it after the address changes.");
P("Nothing else is needed on the PC. The Gecko appears in the Sources list under **Gecko receiver :58772** with its serial number and site name, and its channels appear in the channels table.");
NOTE("If the old Gecko SeedLink service still runs on this PC, the Gecko connects to it instead and the Gecko receiver row says that port 58772 is used by the old service. SeismoVerse offers to stop it: click **Stop old service** (Windows asks once for administrator permission). See chapter 13.");

// ---------------------------------------------------------------------------------------------
H1("6. The channels table");
P("Every channel SeismoVerse receives has one row: **View** (tick to show it), Channel (NET.STA.LOC.CHA), Description (the site name), Rate (samples per second), Latest sample (UTC), Latency, Packets, Gaps and Source.");
P("**Latency** is how old the newest sample is. The colour of the row tells you at a glance:");
TABLE(["Colour", "Meaning"], [
  ["Green", "receiving: the newest data is fresh"],
  ["Amber", "late: no new data for up to 5 minutes"],
  ["Red", "no data for more than 5 minutes: check the instrument, the VPN or the network"],
], [1, 3]);
P("A slow channel (for example 5 samples per second) sends a packet only every minute or so; SeismoVerse allows for that. Use the filter box to find channels, **Tick all** / **Untick all** for the listed channels, and click a column title to sort.");
H2("Opening channels in a viewer");
BULLETS([
  "tick channels and click **Open viewer** (or **Ctrl+Shift+N**): a new viewer with those channels;",
  "**double-click** a channel: a viewer with that channel alone;",
  "right-click selected channels: **Open in a new viewer** or **Add to viewer**;",
  "**drag** channels from the table into an open viewer;",
  "in the Sources list, right-click a source or station: **Open in a viewer**.",
]);

// ---------------------------------------------------------------------------------------------
H1("7. Viewer windows");
P("A viewer is a window of its own with its own taskbar button. Open as many as you like, with any channels; the same channel can be in several viewers with different settings. Closing a viewer closes only that viewer, never SeismoVerse.");
IMG("04_viewer.png", "A viewer with four channels. The label box on the left shows the site name on top.");
H2("The label box");
P("On the left of every trace a box shows, from top to bottom:");
STEPS([
  "the **site name** (for example KUMARI or NAGDHANIBA), in bold;",
  "the **station** (NET.STA), or the label you gave the channel;",
  "the **location and channel** (for example 0J.HNZ);",
  "the **units** (counts, m/s, m/s² or g), or the channel's scale factor.",
]);
P("The colours follow the component: **Z green, N red, E sky blue**, others blue. The box of the channel under the mouse is drawn in full colour.");
H2("The bar above the traces");
TABLE(["Control", "What it does"], [
  ["**Channels**", "tick which channels this viewer shows"],
  ["**Time** (two arrow buttons)", "the time scale in pixels per second: expand or squash the traces in time, up to one day in the window"],
  ["**Amplitude** - ×1 +", "makes every trace larger or smaller (keys Up / Down, or the mouse wheel)"],
  ["**Filter**", "switch the filter of the window on or off (key **F**); the arrow chooses the filter: default band-pass, ready-made filters, your saved filters, or **Design...** (Ctrl+F). Filters change only the display, never recorded data."],
  ["**Units**", "Counts, physical units (m/s, m/s²) or acceleration in **g**"],
  ["**Scale**", "Common per instrument, Common for all channels, Auto (each trace fills its row), Fixed, or Per pixel (1:N)"],
  ["**Remove mean** / **Null**", "centre each trace on zero / fix each trace's offset at its mean now (key **N**)"],
  ["**The eye**", "show or hide the details line above each trace (key **I**)"],
  ["**Time cursors** / **Ampl cursors**", "two lines to measure time (and frequency) or amplitude differences"],
  ["**Live** / **Paused**", "Live follows the newest data. A paused viewer goes live by itself after 2 minutes without mouse use."],
], [1.3, 3]);
H2("Using the mouse");
TABLE(["Mouse", "Action"], [
  ["Wheel", "larger / smaller traces (stays live)"],
  ["Shift + wheel", "shorter / longer time window"],
  ["Ctrl + wheel", "scroll back / forward in time (pauses)"],
  ["Shift + drag", "move in time (pauses)"],
  ["Double-click", "live again"],
  ["Drag", "select a time range on the channels from where you start to where you let go"],
  ["Ctrl + drag", "select exactly two channels (for a calibration check)"],
  ["Right-click", "the channel menu (below)"],
], [1.2, 3]);
H2("What you can do with a selection");
P("After a drag a menu opens. Every item works on the selected channels only:");
BULLETS([
  "**Save... (miniSEED)** - export the selection; **Zoom In** - show only the selection;",
  "**Save on selection** - when ticked, every new selection is saved at once;",
  "**Spectrum...**, **PSD in Acceleration Units**, **PSD in Velocity Units** - power spectral density;",
  "**Peak values** - peak, peak-to-peak, RMS, and for acceleration channels the peak in g and the peak velocity;",
  "**Relative Orientation** - the angle between the horizontal sensors of two stations;",
  "**Strong-motion analysis...** - PGA, PGV, PGD, RMS, Arias intensity, CAV, 5-95 % duration, predominant period and response spectra;",
  "**Sinewave / Step / Broadband Noise Calibration...** - calibration checks (below);",
  "**External tools** - start your own programs with the selected data as miniSEED files.",
]);
H2("The channel menu (right-click)");
TABLE(["Entry", "Key", "What it does"], [
  ["Colour...", "C", "the trace colour"],
  ["Scale...", "S", "a scale factor for this channel, its station or the whole viewer"],
  ["Scale others to match", "Ctrl+S", "every other channel is scaled to look as high as this one"],
  ["Details...", "D", "mean, minimum, maximum and RMS of the visible data; offsets"],
  ["Filter", "", "a filter for this channel only"],
  ["Offset", "Z", "follow Remove mean, Null now, or Zero"],
  ["Spectrogram", "P", "a colour spectrogram under the trace"],
  ["Label...", "", "a name for the channel, shown in its label box"],
  ["Calibration...", "", "counts per m/s or m/s² for a channel without StationXML"],
  ["Remove from this viewer", "Del", ""],
], [1.6, 0.6, 3]);
P("The viewer menu also offers background colour, lane lines (L), overlay of all channels in one row (O), clean display (K), traces that may overlap (Shift+O), sorting, window title, clear window (Ctrl+Del), full screen without caption (F11), duplicate (Ctrl+D), print (Ctrl+Shift+P) and save picture. **Help > Viewer keys and mouse** lists every key.");
H2("Filter design");
P("**Design...** opens the filter design. The **high-pass** part is drawn in red and the **low-pass** part in green; each can be on or off, with its corner in Hz or seconds and its order (1 to 8). Both on is a band-pass. Click or drag in the graph with the left mouse button to move the high-pass corner, with the right button to move the low-pass corner. **Save...** keeps a design under a name for every viewer.");
H2("Calibration checks");
P("These compare the calibration signal fed into a sensor with the sensor's output. Both channels must be in the viewer, and the calibration signal must be switched on at the instrument (SeismoVerse does not switch it on). Select the time of the calibration, choose the check, pick the two channels and press **Calculate** - or Ctrl+drag from the calibration channel to the sensor channel.");
TABLE(["Check", "Calibration signal", "What you get"], [
  ["Sinewave", "a sine", "frequency, sensitivity at that frequency and phase"],
  ["Step", "a square wave", "accelerometer: the settled gain; seismometer: natural period and damping"],
  ["Broadband noise", "noise", "gain, phase and coherence over frequency"],
], [1, 1, 2.5]);
P("For results in physical units enter the calibration channel's **microvolts per count**, the **calibration resistor** (ohm) and the **coil constant** from the calibration sheets. SeismoVerse keeps them for the station.");
H2("History and missed data");
BULLETS([
  "A viewer can show up to **one day**, also of times before SeismoVerse was started.",
  "Older times are read from your recordings and from the SeedLink server's disk buffer (the last 2 days of every channel), also when nothing is being recorded.",
  "When a SeedLink instrument sends missed data after a break, an open viewer draws it into the gap within about two seconds. You do not need to reopen the viewer.",
]);

// ---------------------------------------------------------------------------------------------
H1("8. Recording");
P("Click **Record** (or **Ctrl+R**). The Recordings window lists all recordings at the top and starts new ones at the bottom. From a viewer, **Record these channels** opens it with that viewer's channels ticked.");
IMG("05_record.png", "The Recordings window: running recordings (top) and a new recording (bottom).", 540);
STEPS([
  "**Name**: any name, for example \"Kumari March 2026\".",
  "**Folder**: where the files go. Default: `Documents\\SeisZone\\Recordings`.",
  "**Channels**: all channels (also channels that appear later), or only the channels you tick.",
  "**File length**: 15, 30 or 60 minutes. Files start on the clock (UTC), for example 10:00, 11:00.",
  "**Files**: one file per channel, or one file per station with all its channels.",
  "Click **Start recording**.",
]);
H2("Where the files go");
P("`<folder>\\WB.KUM\\2026-03-14\\WB.KUM.0J.HNZ_20260314_1000_1100.mseed` (one file per channel)");
P("`<folder>\\WB.KUM\\2026-03-14\\WB.KUM_20260314_1000_1100.mseed` (one file per station)");
P("The name holds the date and the start and end time of the file (UTC). The files are standard **miniSEED**, written exactly as the instrument sent the data. `recording_log.txt` in the folder lists starts, stops, new files, gaps and link breaks.");
H2("Important");
BULLETS([
  "**A recording runs until you stop it.** Select it and click **Stop...**. The files stay.",
  "**SeismoVerse never deletes recordings.** When the disk is nearly full (less than 5 GB or 5 % free) it warns you in the log and the status bar; move old files to another disk yourself.",
  "**Recordings survive restarts.** If SeismoVerse is closed, crashes or Windows restarts, every running recording continues when SeismoVerse runs again. SeedLink instruments then send the data that was missed, and it is written into the right files.",
  "Tick **Start SeismoVerse when I log in to Windows** in Settings so this happens without you.",
  "**Open folder** shows the files of a recording in Explorer.",
]);

// ---------------------------------------------------------------------------------------------
H1("9. Exporting a time window");
P("Click **Export** (or **Ctrl+E**).");
IMG("06_export.png", "The Export window.", 480);
STEPS([
  "Tick the channels.",
  "Choose the start and end time (UTC), or use **Last 10 min**, **Last 1 h** or **The view's range**.",
  "Choose **One file per channel** or **One file with all channels**.",
  "Choose the folder (default `Documents\\SeisZone\\Exports`) and click **Export**.",
]);
P("SeismoVerse takes the data from your recordings first and, for anything not recorded, from the live data in memory and the disk buffer. It writes miniSEED files plus a report with the number of samples and any gaps per channel.");

// ---------------------------------------------------------------------------------------------
H1("10. Sharing data with other computers");
P("SeismoVerse offers every channel it receives over **SeedLink**, so other computers and seismology programs (SeismoVerse, SeisComP, Swarm, ObsPy) can read it at the same time. Click **Data server** in the toolbar to see and change it.");
IMG("07_data_server_seedlink.png", "Data server, SeedLink tab: state, disk buffer, this PC's address and connected programs.", 470);
H2("SeedLink over TCP (port 18000, on)");
BULLETS([
  "In the other program add a SeedLink source: on this PC `127.0.0.1`, port **18000**; from another PC: **this PC's IP address**, port **18000**.",
  "In another SeismoVerse simply use **Add source** with this PC's IP address.",
  "The tab shows whether the server runs, this PC's addresses, the connected programs and what happened (Control).",
  "**Keep on disk** (default **2 days**): every packet is also kept on disk, so a computer that was switched off for hours - even while SeismoVerse restarted - gets everything it missed when it connects again. It uses about 1.5 GB per day with 38 instruments. Old files are removed by themselves; your recordings are never touched.",
]);
H2("GCF over UDP (port 1567, off)");
P("For the earlier viewer on another computer. Tick **Send all data to other computers on the network (GCF over UDP)** and press **Apply**. On the other computer add a UDP server with **this PC's IP address:1567**. If another program already uses port 1567 on this PC, choose another port (for example 1568) and use that on the other computer.");

// ---------------------------------------------------------------------------------------------
H1("11. Settings");
P("Click **Settings** (or **Ctrl+,**). Changes take effect when you click **OK**; ports are reopened at once.");
IMG("09_settings.png", "Settings, Gecko receiver tab.", 460);
TABLE(["Tab", "What you can set"], [
  ["**Gecko receiver**", "on/off, the TCP port(s) (default 58772), and a naming table to give a Gecko's data other network, station or location codes"],
  ["**SeedLink server**", "on/off, listen address (0.0.0.0 = all networks, 127.0.0.1 = this PC only), port (default 18000), organization name"],
  ["**Recording**", "default folder, file length, one file per channel or per station"],
  ["**Display**", "minutes of live data kept in memory, dark or light colours, default time window"],
  ["**Program**", "keep running in the tray when the window is closed, start minimized, start when you log in to Windows, reopen the viewers that were open"],
], [1.2, 3]);
NOTE("If you change the Gecko port, change the USR-W610 to the same port. If you change the SeedLink server port, change it in the other programs too.");

// ---------------------------------------------------------------------------------------------
H1("12. The tray icon and quitting");
P("SeismoVerse has an icon in the notification area next to the clock. When **Closing the window keeps SeismoVerse running in the tray** is on (the default), closing the window does **not** stop SeismoVerse: data keeps coming in, recordings keep running and the SeedLink server keeps serving.");
P("Right-click the tray icon for **Show SeismoVerse**, **Recording...** and **Quit**. Only **Quit** (here, **File > Quit** or **Ctrl+Q**) stops SeismoVerse completely. Running recordings then pause and continue when SeismoVerse starts again.");
H2("About SeismoVerse");
P("**About** in the toolbar shows the program's name, its version, the author and the supporter.");
IMG("02_about.png", "About SeismoVerse.", 360);

// ---------------------------------------------------------------------------------------------
H1("13. Troubleshooting");
TABLE(["Problem", "What to do"], [
  ["An instrument shows no data (amber or red)", "Check that the VPN is connected (SeismoVerse reconnects by itself and fetches the missed data). Test the address with `ping 10.8.0.182` in a Command Prompt. Check the port (SeedLink 18000, GCF 1567). Click **Reconnect** and read the reason in the **Log**."],
  ["The Gecko does not connect", "SeismoVerse must be running (window or tray). The USR-W610 must be a **TCP Client** to this PC's current IP address, port **58772**. Check that the Windows firewall rule **SeismoVerse** is enabled, and allow SeismoVerse in other security software."],
  ["Gecko receiver says the port is used by the old Gecko SeedLink service", "Click **Free the port - stop the old Gecko SeedLink service** above the Sources list (or right-click the Gecko receiver row). Within a few seconds SeismoVerse opens the port and the Gecko connects (the W610 dials again within about a minute). The old service is only switched off, not removed."],
  ["A port is in use by another program", "Close that program, or choose another port in Settings (and change it in the W610 or other programs too). SeismoVerse tries again every few seconds; the log names the program's process id."],
  ["Physical units are not offered", "SeismoVerse has no StationXML for that station. Remove the source and add it again with **Detect**, and wait until detection finishes."],
  ["Low disk space warning", "SeismoVerse never deletes anything. Copy old recording folders to another disk, then delete them yourself, or record to a folder on a bigger disk."],
  ["Something else", "Look in the **Log** panel, or open **Help > Log folder** and send the file `seiszone.log` with your question."],
], [1.3, 3]);

// ---------------------------------------------------------------------------------------------
H1("14. Files and folders");
P("The folders keep the program's earlier name **SeisZone**, so nothing moved when the program was renamed.");
TABLE(["What", "Where"], [
  ["Recordings (default)", "`Documents\\SeisZone\\Recordings`"],
  ["Exports (default)", "`Documents\\SeisZone\\Exports`"],
  ["Program log", "`%LOCALAPPDATA%\\SeisZone\\logs\\seiszone.log` (Help > Log folder)"],
  ["Settings and sources", "`%APPDATA%\\SeisZone\\config.json`"],
  ["Running recordings, SeedLink positions", "`%APPDATA%\\SeisZone\\state.json`"],
  ["StationXML (instrument responses)", "`%APPDATA%\\SeisZone\\stationxml`"],
  ["SeedLink server disk buffer", "`%LOCALAPPDATA%\\SeisZone\\ServerBuffer`"],
  ["The program", "`C:\\Program Files\\SeismoVerse` (updated from SeisZone: `C:\\Program Files\\SeisZone`)"],
], [1.3, 3]);
P("To open a folder like `%APPDATA%\\SeisZone`, type it into the Explorer address bar. Neither SeismoVerse nor its uninstaller deletes recordings, exports, settings or logs.");

// ---------------------------------------------------------------------------------------------
H1("Appendix: keyboard shortcuts");
H2("Main window");
TABLE(["Key", "Action"], [
  ["Ctrl+N", "Add source"], ["Ctrl+Shift+N", "Open viewer with the ticked channels"], ["Ctrl+R", "Record"],
  ["Ctrl+E", "Export"], ["Ctrl+,", "Settings"], ["F1", "User guide"], ["Ctrl+Q", "Quit SeismoVerse"],
], [1, 3]);
H2("Viewer windows");
TABLE(["Key", "Action"], [
  ["Up / Down", "amplitude larger / smaller"], ["Left / Right", "window length"],
  ["Ctrl+Left / Right", "scroll in time"], ["Home", "amplitude ×1 (Per pixel: fit the zoom)"],
  ["Space / Pause", "live"], ["F", "filter on / off"], ["Ctrl+F", "filter design"],
  ["N", "null offsets"], ["Z", "zero offset"], ["P / Ctrl+P", "spectrogram / all spectrograms"],
  ["S / Ctrl+S", "scale / scale others to match"], ["1 / Ctrl+1", "scale ×1 / all ×1"],
  ["D", "details"], ["C", "colour"], ["I", "details above the traces (the eye)"],
  ["L", "lane lines"], ["O", "overlay"], ["K", "clean display"], ["Shift+O", "traces may overlap"],
  ["Del / Ctrl+Del", "remove channel / clear window"], ["F5", "redraw"], ["F11", "full screen without caption"],
  ["Ctrl+D", "duplicate the viewer"], ["Ctrl+Shift+P", "print"], ["Ctrl+W", "close the viewer"],
], [1, 3]);

// =============================================================================================
// COVER and DOCUMENT
// =============================================================================================
const icon = fs.readFileSync(ICON);
const cover = [
  new Paragraph({ spacing: { before: 2200 }, alignment: AlignmentType.CENTER,
    children: [new ImageRun({ type: "png", data: icon, transformation: { width: 150, height: 150 },
                               altText: { title: "SeismoVerse", description: "SeismoVerse icon", name: "icon" } })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 400, after: 120 },
    children: [new TextRun({ text: "SeismoVerse", bold: true, size: 72, color: DARK, font: FONT })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 },
    children: [new TextRun({ text: "User Manual", size: 40, color: TEAL, font: FONT })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 1600 },
    children: [new TextRun({ text: "Live seismic data acquisition, viewing and recording", size: 24, color: MUTED, font: FONT })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 },
    children: [new TextRun({ text: `Version ${VERSION}`, size: 24, color: DARK })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 },
    children: [new TextRun({ text: "Author: ", bold: true, size: 24 }), new TextRun({ text: "Arnab Kole", size: 24 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 },
    children: [new TextRun({ text: "Supported: ", bold: true, size: 24 }), new TextRun({ text: "Rohit Santra", size: 24 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "November 2026", size: 22, color: MUTED })] }),
];
const toc = [
  new Paragraph({ children: [new TextRun({ text: "Contents", bold: true, size: 36, color: TEAL, font: FONT })], spacing: { after: 240 } }),
  ...chapters.map(([id, title]) => new Paragraph({ spacing: { after: 100 },
    children: [new InternalHyperlink({ anchor: id, children: [new TextRun({ text: title, color: DARK, size: 24 })] })] })),
  new Paragraph({ spacing: { before: 240 }, children: [new TextRun({ text: "Click a chapter to go to it.", italics: true, size: 18, color: MUTED })] }),
];
fs.writeFileSync(OUT.replace(/\.docx$/, ".json"), JSON.stringify({ version: VERSION, chapters, model }, null, 1));

const footer = new Footer({ children: [new Paragraph({
  border: { top: { style: BorderStyle.SINGLE, size: 4, color: "B8C4C9", space: 6 } },
  tabStops: [{ type: TabStopType.RIGHT, position: CONTENT_W }],
  children: [new TextRun({ text: `SeismoVerse ${VERSION} - User Manual`, size: 18, color: MUTED }),
             new TextRun({ children: ["\tPage ", PageNumber.CURRENT], size: 18, color: MUTED })],
})] });

const page = { size: { width: 11906, height: 16838 }, margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } };
const doc = new Document({
  creator: "Arnab Kole", title: "SeismoVerse User Manual", description: `SeismoVerse ${VERSION} user manual`,
  styles: {
    default: { document: { run: { font: FONT, size: 22, color: "222222" }, paragraph: { spacing: { line: 276 } } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 36, bold: true, color: TEAL },
        paragraph: { spacing: { before: 0, after: 200 }, keepNext: true, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 28, bold: true, color: DARK },
        paragraph: { spacing: { before: 280, after: 120 }, keepNext: true, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 24, bold: true, color: DARK },
        paragraph: { spacing: { before: 200, after: 80 }, keepNext: true, outlineLevel: 2 } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 540, hanging: 300 } } } }] },
    { reference: "steps", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 540, hanging: 360 } } } }] },
  ] },
  sections: [
    { properties: { page }, children: cover },
    { properties: { page }, footers: { default: footer }, children: [...toc] },
    { properties: { page }, footers: { default: footer }, children: body },
  ],
});
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(OUT, buf); console.log("written", OUT, buf.length, "bytes"); });
