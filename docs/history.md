# SeisZone - work history

One dated entry per piece of work, newest first, so nothing is lost if a session or the
assistant breaks. memory.md holds the current state and the rules; this file holds what was done,
what was found and what is waiting. Reports with the details are in docs/reports/.

## 2026-11-26 20:38 - 2.3.0 merged, pushed and published

* At the user's OK ("Merge, push, publish"): main fast-forwarded to the branch (156edda) and
  pushed. The installer built from b97ee72 (same tree) was published with installer/publish.py:
  release v2.3.0 of rohitbhutta/seismoverse-releases, "latest", three files, SHA-256
  a73a080b02362fac97cd8b230e98ef62b48fc5952160314c591dfe6925edb445.
* Checked from the real GitHub with core/updater.py (unchanged since 2.2.0): latest_release ->
  2.3.0 with a valid signature, offered to 2.2.0 (not to 2.3.0), download's SHA-256 equals the
  signed one and the local build.
* Other computers on 2.2.0 check only at start: Help > Check for updates (or a restart) shows
  2.3.0 there. From 2.3.0 on the daily check also runs while SeismoVerse keeps running.

## 2026-11-26 - 2.3.0: About without names, update check while running (built)

* Asked: remove Author and Supported from About, make the version 1.1.0, and say how the other
  computer (SeismoVerse 2.2.0 installed) gets the update.
* Found: updater.is_newer only offers a higher version, so 2.2.0 would never offer 1.1.0. Asked;
  the user chose **2.3.0**, the daily check also while running, and the names removed from the
  manual too.
* Changes (branch release/about-and-version, b97ee72): About = name, version, one line;
  AUTHOR/SUPPORTED_BY removed; ui/app.py UPDATE_RECHECK_MS (1 h) timer calls
  check_updates_at_start (GitHub at most once a day, not beside a running check or an open
  update window); user guide and manual text; manual (Word + PDF, 29 pages) rebuilt with new
  screenshots, no names on the cover, creator "SeismoVerse".
* Tests: 389 passed, 1 skipped (7 min 20 s). New test: the check while running.
* Waiting: the user's OK to merge into main, push and publish v2.3.0.

## 2026-11-23 (late) - plan for Linux, Mac and iPhone/iPad (plan only, nothing changed)

* Asked: run SeismoVerse on a Linux machine and an "iOS machine"; plan first.
* Read the code for Windows-only parts: paths.py (APPDATA folders), single_instance.py (Windows
  mutex; elsewhere no protection), ui/app.py (registry autostart, native close filter, taskbar
  id), core/updater.py (runs the setup .exe), core/ports.py (old Gecko service, already a no-op
  elsewhere), external tools filter (*.exe), theme font. Everything else is portable.
* Checked: PySide6 6.12 wheels need glibc 2.34+ (Linux) and macOS 14+; Qt for Python 6.12 runs on
  iOS but cannot use numpy/scipy yet -> SeismoVerse cannot be built for iPhone/iPad today.
* This PC has no WSL and no Docker. The source repo rohitbhutta/seiszone is **public** (the notes
  said private).
* Plan: docs/reports/2026-11-23-linux-macos-ios-plan.md - steps 2.3.0 common base, Linux AppImage,
  Linux updates, Mac .app/.dmg, optional Linux service and an iPhone/iPad live web page. Waiting
  for the user's answers (part 8 of the plan).

## 2026-11-23 14:41 - 2.2.0 installed and running

* The user ran the installer (setup log #004 at 14:40:24). SeismoVerse.exe 2.2.0 runs as the
  normal user from C:\Program Files\SeisZone (started 14:40:23; "starting" logged at 14:40:55 -
  a slow start on a busy PC; two more starts at 14:40:36 and 14:40:57 found it still starting,
  "did not answer", and closed - normal single-instance behaviour).
* Uses the existing SeisZone folders (config, server buffer from 07:00 UTC, logs); the new log
  file is seismoverse.log, the old seiszone.log is kept. Ready 14:41:01, 39 sources connecting;
  status 14:42 and 14:43: 37/39 receiving, 147 channels (channels still coming back). One
  warning: GODHVANI (10.8.0.97) no answer within 15 s - it had already lost its link at
  14:30:33 under 2.1.0 ("semaphore timeout"; 37/39 at 14:37-14:38 before the update), so not
  caused by the update; SeismoVerse keeps retrying.
* The automatic update check ran 23 s after start (14:41:24): "SeismoVerse 2.2.0 is the newest
  version", last_check saved - the installed program talks to the real releases repository.

## 2026-11-23 14:40 - 2.2.0 merged, built and published (the first signed release)

* At the user's OK ("Merge, build, publish"): pull request #6 merged (a6903b5); main identical to
  the tested branch.
* Build: build/dist/SeismoVerseSetup.exe 68.9 MB, SeismoVerse.exe answers "SeismoVerse 2.2.0";
  the frozen program contains core.ed25519, core.updater, ui.update_dialog, ui.screen_fit. Same
  three PyInstaller warnings as before.
* Published with installer/publish.py (dry run first): release v2.2.0 of
  rohitbhutta/seismoverse-releases, "latest", three files (installer, .sha256, .sig), SHA-256
  1fd113dd...4e56, author shown as rohitbhutta, tag on the noreply commit 3c17cd0.
  https://github.com/rohitbhutta/seismoverse-releases/releases/tag/v2.2.0
* End-to-end check with the program's own updater code against the real GitHub: release found,
  signature valid, offered to 2.1.1 and not to 2.2.0, the installer downloaded through
  github.com -> release-assets.githubusercontent.com (both allowed) in 96 s, identical to the
  build.
* This PC still runs 2.1.0 (no updater): it needs one manual install of 2.2.0; from then on
  updates come through Help > Check for updates / the daily check.

## 2026-11-23 14:30 - 2.2.0 (branch fix/signed-updates): updates must be signed by the publisher

* After the user's "Merge, build, publish": pull request #5 merged (16b08c8). Before building, the
  automatic security review flagged the updater: the SHA-256 came from the same GitHub release as
  the installer, so whoever got into the GitHub account could publish a harmful installer that
  every SeismoVerse installs. Agreed - fixed before the first release (the first published version
  checks every later one). Publishing waits for the user's OK on this change.
* core/ed25519.py: Ed25519 (RFC 8032 reference code, no extra library). Checked: RFC 8032 test
  vectors 1-3; byte-identical signatures and keys with OpenSSL 3.5 (vectors 2-3 and a fresh key).
* Publisher key: %USERPROFILE%\.seismoverse-release\signing.key on this PC (not in the repo, not
  in OneDrive, not on GitHub); public key d812e2fb...f19d in core/updater.py TRUSTED_KEYS. The
  user must keep an offline copy.
* updater: a release needs SeismoVerseSetup.exe.sig = signature of "SeismoVerse release /
  version / sha256" by a trusted key (also stops an old installer being offered as newer);
  only https GitHub addresses (ALLOWED_HOSTS), each redirect checked before it is followed
  (a test showed urllib follows the whole chain first - now refused before asking).
* publish.py: --new-key (never overwrites), refuses a key not in TRUSTED_KEYS, writes and
  self-checks the .sig, uploads three files.
* Tests: 16 in test_updater.py (vectors, foreign / replayed / missing / invalid signature, http
  and foreign hosts, redirect away refused and within GitHub followed, key handling).
* Docs: README (key, backup, rotation), manual step "publisher's signature", user guide 15.
* Second security finding (key file permissions): the existing key and its folder now allow only
  the user's account (icacls, inheritance removed); publish.py --new-key does the same (tested).
* Tests: one full run was stopped after 9.5 min (little CPU; looked stuck); the rerun with
  per-test progress and a 3-min hang dump finished in 11:50 on a busy PC: 387 passed, 1 skipped,
  1 failed - test_many_waiting_records_cost_little_per_record (0.513 s against a 0.5 s limit;
  recorder code unchanged; alone it failed 1 of 3 times, its file passes): a load-sensitive
  timing test, to watch. No test hung.

## 2026-11-23 14:00 - 2.2.0: updates from GitHub Releases (public releases-only repository)

* The user chose "Separate public repo" for the update plan (docs/reports/2026-11-23-update-plan.md).
* Created the public repository rohitbhutta/seismoverse-releases (README only; no installer
  yet). Mistake: its first README commit (dbe469c, made with the GitHub API) showed the user's
  personal Gmail as author. main was force-updated to the same README committed as Rohit Santra
  <282983013+rohitbhutta@users.noreply.github.com> (3c17cd0); the old commit stays reachable by
  its id until GitHub's cleanup - full removal needs deleting and recreating the repository
  (the gh token has no delete_repo scope): asked the user. Auto-memory note written.
* core/updater.py: latest_release (GitHub API, User-Agent "SeismoVerse/<v> update check", no
  other data), parse_version / is_newer, download (temp folder, max 300 MB, SHA-256 must match
  SeismoVerseSetup.exe.sha256 or the file is removed), automatic_check_allowed (installed
  program only, Settings switch, once per 20 h, SEISZONE_UPDATE_CHECK=off in the tests),
  start_installer (ShellExecute "/SILENT /SUPPRESSMSGBOXES /SHOWAPP=1").
* ui/update_dialog.py: Help > Check for updates..., daily check 20 s after start, update window
  (What's new, Download and install with progress / cancel, Later, Skip this version);
  Settings > Program "Check for updates once a day" (config "updates": check, last_check, skip).
* Installer: /SHOWAPP=1 opens SeismoVerse in its window after a silent update.
* installer/publish.py: checks the build is this version, writes the .sha256, creates release
  v<version> with gh (--dry-run shows the command). Each version once.
* Tests: tests/test_updater.py (local fake GitHub on 127.0.0.1, never the real one) and window
  tests in test_ui_shell.py.
* Manual: chapter 13 "Getting updates" (+ picture), download address in chapter 2, a
  troubleshooting row; chapters 14/15 renumbered. 29 pages, 2.2.0, no SeisZone name. User guide
  section 15, README "Publishing an update". Version 2.2.0.
* Not done: nothing published yet (first release needs the user's OK).

## 2026-11-23 13:35 - 2.1.1 (same branch): no SeisZone name in the manual; new PCs get SeismoVerse folders

* The user: there are still SeisZone names in the user manual - remove them all and give the final
  document; and asked for a plan to publish updates that computers anywhere can install
  (no server, only the GitHub account).
* Folders: DATA_FOLDER is now "SeismoVerse"; paths.data_folder() uses the old "SeisZone" folder
  of a place (AppData Roaming / Local / Documents) as long as it exists and no SeismoVerse
  folder is there. A new PC gets SeismoVerse folders; this PC keeps its SeisZone folders
  (checked: config, logs, buffer and Documents still resolve to them; nothing moved). The log
  file is now seismoverse.log (older seiszone.log files stay). The uninstall message names the
  SeismoVerse folders.
* Manual and user guide: no "SeisZone" left (the PDF's text and metadata and the Word file's XML
  were searched); folders shown as SeismoVerse with a note that a PC of an earlier version keeps
  the folders it used. Screenshots retaken (paths show SeismoVerse). Manual 27 pages, 2.1.1.
* Update plan: docs/reports/2026-11-23-update-plan.md (GitHub Releases in a public
  releases-only repository, an in-app "Check for updates"); not built, waiting for the user.

## 2026-11-23 13:25 - 2.1.1: windows always fit the screen (Data server title bar was hidden)

* 2.1.0 installed by the user and running (13:15:40, C:\Program Files\SeisZone\SeismoVerse.exe
  2.1.0; the first "installed" check at 13:1x found 2.0.0 still there and no setup log - the
  user ran the installer again).
* The user (screenshot): the Data server window "is not opening on the full screen ... the upper
  side is hidden"; only dragging an edge brought it back. This PC: 1920 x 1080 at 125 % = 1536 x
  824 points of free area. With the user's data the window was placed with its title bar above
  the top edge (with test data it opens at y 102 - not reproduced with them).
* Fix: ui/screen_fit.py fit_to_screen / keep_on_screen: a window is shrunk to the free area
  (taskbar excluded, room for the title bar) and moved so its whole frame is inside. Applied to
  every window MainWindow._open opens (Add source, Record, Export, Data server, Settings, About)
  right after it is shown and once more when Windows has drawn its frame. The Data server tabs
  are in scroll areas (minimum 480 x 360; natural height 472 instead of 861 points), so long
  texts make a tab scroll instead of the window grow past the screen.
* Checked on the real screen with an invisible window (WA_DontShowOnScreen): forced to y -150,
  1000 points tall -> (385, 8, 760, 776), inside the free area.
* tools/manual/shots.py turns keep_on_screen off (the offscreen screen is only 800 x 600).
  Manual rebuilt (version 2.1.1). Version 2.1.1.

## 2026-11-23 13:13 - 2.1.0 merged into main and built

* At the user's OK ("Merge and build"): pull request #4 merged (merge commit 4b0324d), local main
  pulled; identical to the tested branch (367 passed, 1 skipped).
* `python installer/build.py`: build/dist/SeismoVerseSetup.exe 68.9 MB; SeismoVerse.exe answers
  "SeismoVerse 2.1.0", file details 2.1.0. Same three PyInstaller warnings as before.
* Not installed (the user runs the installer).

## 2026-11-23 afternoon - 2.1.0: Data server, send to a computer added by hand

Branch feature/gcf-manual-clients. The user (screenshots of both Data server tabs): "Here is no
option to add or type anything in this both sections" (Clients, Control). Asked what to enter;
the user chose "Add a client by hand"; plan approved ("Yes, build it").
* GCF over UDP tab: "Send to a computer" row (IP address, Port - empty = 1567, Add, Remove).
  Saved at once in config["gcf_server"]["clients"] (["ip:port"]) and applied (apply_settings).
  core/gcf_server.py: parse_client / client_list, GcfServer.set_fixed(): a hand-added client
  gets every block and the status blocks without asking, is never removed for being idle, its
  GCFSTOP is answered and noted but it keeps its data; only Remove (set_fixed without it) ends it.
  A computer that asked by itself and is then added becomes hand-added. engine: set_fixed when
  the server opens and at every apply; status["gcf_server"]["fixed"].
* Clients list: rows selectable; hand-added ones show "B (added by hand)" / "sent without asking",
  or "waiting: the GCF server is off". Remove works only on hand-added ones (others: says why).
  Add / Remove are not default buttons (Enter in another field does nothing to the list).
* SeedLink tab: no Add (SeedLink cannot send to a computer that did not ask); a line under its
  Clients list says so.
* Not known yet: whether the earlier viewer accepts GCF it did not ask for - test with it on
  another computer only with the user's OK.
* Docs: user guide section 10, manual chapter 10 "Sending to a computer by hand" with a picture
  of the GCF tab (27 pages, version 2.1.0). Version 2.1.0.
* Tests: full suite 367 passed, 1 skipped. The first full run had one failure plus its teardown
  error in tests/test_probe.py::test_probe_gcf_offline (UDP probe against a local fake unit,
  code not touched here); it passed alone, 3 x the whole probe file, and the second full run -
  a timing flake under load, to watch.
* My line-ending helper rewrote the new PDF as if it were text (it would have been damaged); it
  was restored from the build before any commit and the helper now skips binary files.

## 2026-11-23 12:42 - 2.0.0 installed and running

* The user ran SeismoVerseSetup.exe. SeismoVerse.exe 2.0.0 runs as the normal user from
  C:\Program Files\SeisZone (updated in place; SeisZone.exe removed). Start menu and Public
  desktop show SeismoVerse (no SeisZone shortcut left); firewall rule "SeismoVerse" enabled, the
  old "SeisZone" rule deleted. Start with Windows was off before and stays off (no Run value).
* Log: "SeedLink server organization changed from SeisZone to SeismoVerse", "SeismoVerse 2.0.0
  starting", Gecko receiver on 58772 and SeedLink server on 18000 listening, ready at 12:42:21;
  sources 38/39 receiving (the same 38/39 as before the update), missed data fetched. One
  warning: SeedLink THA (10.8.0.7) sent a packet that is no valid miniSEED record (skipped).
* The user then asked about the Data server window: "no option to add or type anything" in the
  Clients and Control lists (they are read-only lists filled by connections) - asked what they
  want to enter there.

## 2026-11-23 12:39 - 2.0.0 merged into main and built

* At the user's OK ("Merge and build"): pull request #3 merged (merge commit 65df26a), local main
  pulled; its files are identical to the tested branch (363 passed, 1 skipped).
* `python installer/build.py`: build/dist/SeismoVerseSetup.exe 68.8 MB; SeismoVerse.exe answers
  "SeismoVerse 2.0.0" and its file details say SeismoVerse 2.0.0. The three PyInstaller warnings
  (pyqtgraph.opengl, pycparser tables, scipy _cdflib) were in the 1.9.0 build too.
* Not installed (the user runs the installer). After installing: check the version in About, that
  the 38 sources are back, and that the Start menu shows SeismoVerse and no SeisZone.

## 2026-11-23 midday - 2.0.0: renamed SeismoVerse; About, Add source, label box; user manual

Branch feature/seismoverse-name, pull request #3 (opened at the user's OK, commit c39ad8a;
full suite 363 passed, 1 skipped; not merged, built or installed). The user asked (with four screenshots):
* "Change the application name as SeismoVerse": APP_NAME, window titles, tray, messages, the
  SeedLink server's software string ("SeismoVerse v2.0.0"), GCF status text, probe user agent,
  export report, installer (SeismoVerseSetup.exe, SeismoVerse.exe, Start menu, firewall rule),
  version_info, README and user guide. Version 2.0.0.
  * Kept on purpose: the per-user folders (DATA_FOLDER = "SeisZone": %APPDATA%\SeisZone,
    %LOCALAPPDATA%\SeisZone, Documents\SeisZone), so the 38 sources, settings, recordings, logs
    and the server buffer stay; the Python package "seiszone"; the single-instance mutex and
    activate names; the Run value name "SeisZone" (an entry that starts the old SeisZone.exe is
    taken over by SeismoVerse.exe); the installer's AppId (an update stays in
    C:\Program Files\SeisZone and removes SeisZone.exe and its shortcuts; both exes are closed
    before installing; the old "SeisZone" firewall rule is deleted).
  * The saved SeedLink organization "SeisZone" (the old default) becomes "SeismoVerse"; a typed
    one stays. Another computer's status blocks are recognised under both names.
* About: the "Open standards" list and the Folders list removed; it shows the name, version,
  "Author: Arnab Kole" and "Supported: Rohit Santra" (AUTHOR / SUPPORTED_BY in __init__.py).
* Add source: the example addresses line and the Gecko note under it removed.
* Viewer label box: the site name first (bold), then station, location.channel, units.
* Bug found by the rename: probe.seedlink_info dropped trailing spaces of every 456-byte INFO
  piece; when a piece ended on a space between two XML attributes the station list came back
  empty (the 3 characters longer software name moved a boundary onto a space). Only NUL padding
  is dropped now; regression test splits right after a space (fails on the old code).
* User manual (the user: "Make a user manual of the application", format "Word and PDF file"):
  docs/manual/SeismoVerse_User_Manual.docx and .pdf, 25 pages, 14 chapters + keyboard appendix,
  8 screenshots made offscreen from the test data (tools/manual/shots.py). Built by
  tools/manual (docx-js for Word, reportlab for the PDF from the same content). Word on this PC
  is an unlicensed product (opens read-only, cannot save or export), so the PDF is not made by
  Word and the Word file's contents is a clickable chapter list instead of a TOC field. The Word
  file was checked for unique bookmark ids; it could not be opened in Word here.

## 2026-11-23 - 1.9.0 merged into main and built

* At the user's OK ("Merge and build"): pull request #2 merged (merge commit 15e1f18), local
  main pulled. Full suite on main: 357 passed, 1 skipped.
* `python installer/build.py`: build/dist/SeisZoneSetup.exe 68.9 MB, SeisZone.exe reports 1.9.0.
  Not installed (the user runs the installer).

## 2026-11-23 03:00 - 1.9.0: Port box in Add source; any SeedLink server (same pull request)

* Asked: SeedLink sources by IP and a port typed by hand; no port = 18000 (default); a typed port
  is used as given; detect any type of SeedLink server.
* Add source: a **Port** box beside the address (placeholder 18000). Empty = the probe's default
  (SeedLink 18000, then GCF 1567); a typed port is used exactly (SeedLink first, then GCF).
  "IP:port" in the address box still works; two different ports are refused; "Add anyway as
  SeedLink" uses the Port box (empty = 18000). Picture docs/reports/2026-11-23-add-source-port-box.png.
* probe: one banner reader (_read_banner) for HELLO and INFO: lines ending CR LF, LF or CR, and
  servers that send only one banner line (after BANNER_SECOND_LINE_WAIT 1.5 s) are accepted; the
  first line must name SeedLink or SLPROTO (another service on the port is not taken for it).
* Tests: test_any_seedlink_server_banner (5 server answers), test_add_dialog_port_box.

## 2026-11-23 02:00-03:00 - 1.9.0: SeedLink server disk buffer (same pull request)

* Asked: "fix the SeedLink backfill in any kind of option", plan first. Measured before planning
  (SeisZone's own SeedLink server read on this PC with ObsPy and raw sessions):
  * 36 SeedLink stations: no gaps in the last hour, also across the disconnects 00:37-00:50
    (resume + TIME fetch work). (ObsPy's helper fails on 1-character location codes with "*":
    an ObsPy limit, "??"/"???" works - not a SeisZone fault.)
  * XX.PRAT / XX.SIRA (the GCF-UDP IIT instruments): real gaps (10 s, 60-83 s): no backfill.
  * SeisZone's SeedLink server reached back only ~1 h 40 min (200,000 packets in memory) and
    nothing across a SeisZone restart: clients of this PC could not backfill longer outages.
* Plan approved ("Yes, do all of it"):
  1. Read-only probe of 10.8.0.121:18000 and 10.8.0.163:18000: nothing answered - they stay on GCF
     (SeedLink may be switchable in their web pages; then add them with port 18000).
  2. core/packet_store.py PacketStore: every packet also on disk (packets/<YYYYMMDD>_<HH>.slp:
     seq + length + raw, per arrival hour; and <NET.STA>/<day>/<NET.STA>_<date>_<HHMM>_<HHMM>.mseed
     per station and data hour). Default 2 days (config seedlink_server.buffer_days, 0 = off),
     folder %LOCALAPPDATA%\SeisZone\ServerBuffer (not synced; another config: beside it). Kept
     below MAX_SPAN 0x700000 sequence numbers (24-bit half range). Purge every 10 min in a worker
     thread (old files only; recordings never touched); pauses below 2 GB free.
     hub.first_seq / has_seq / packets_from / seq_for_time reach into it; the server starts TIME
     requests at the hour of the begin time and warns "fell behind" only for packets really gone.
     Engine: numbers continue after the newest packet on disk (+ SEQ_SAFETY_GAP); status
     server.buffer; the buffer folder is one of recording_folders() (viewers) and Export's folders.
  3. Viewers show data older than the live buffer from it (RecordedData reads it like a recording).
  4. Data server window, SeedLink tab: "Keep on disk" days (0 = off) and a line with size, folder,
     oldest time. Picture docs/reports/2026-11-23-data-server-seedlink-buffer.png.
* GCF gap recovery findings (v45 trailer: version 0x2d, 3-byte block number, source name;
  "Request oldest v45" / "Request block N" over TCP on the same port; bytes unknown):
  docs/reports/2026-11-23-gcf-gap-recovery-findings.md. The user then chose the SeedLink work.
* New tests: tests/test_packet_store.py (8: round trip + restart, skipped numbers, purge, hub,
  a client away for hours across a SeisZone restart in both resume styles, TIME start, viewers);
  engine (buffer default, status, folders, numbers after a crash, off); Data server window.
* Version 1.9.0.

## 2026-11-23 night - 1.8.1 on branch feature/backfill-seedlink-server (pull request)

* Asked (2 screenshots): "live data viewing backfill" like the recording backfill, with the
  earlier viewer's manual and its protocol document as reference; and the SeedLink server
  (port 18000) in the Data server window next to the GCF server.
* Reference read: the user's manual (Downloads, MAN-SWA-0001 (1).pdf; sections 4.3.2 and 7.4) and
  BdsImportStreamGcf.pdf (a third party's notes). GCF gap recovery: each block carries the
  server's block number; a client that sees a jump opens TCP on the same port and asks for the
  missing blocks one by one, newest first. The command format is only in SWA-RFC-SCRM.pdf, which is
  NOT on this PC (searched Downloads, Desktop, Documents, the user folder, D Drive and the earlier
  viewer's own help files; not found on the web either). Not guessed: waiting for the document.
* Found: SeisZone's live viewers already backfill. The hub inserts late records into the live
  buffer (StreamBuffer._insert) and an open viewer re-reads the hub every PAUSED_RELOAD (2 s).
  Shown with a 20 s gap that closed within one refresh. New test
  test_open_viewer_fills_a_gap_when_the_missed_data_arrive. User guide: "Live backfill".
  Limits: data older than the live buffer come from the recordings; GCF UDP sources (2) cannot
  backfill until the gap recovery above is built.
* Data server window (ui/server_window.py) now has two tabs: "GCF over UDP (earlier viewer)" and
  "SeedLink over TCP" (enable, TCP port, state, this PC's addresses, Clients: Client / Stations /
  Connected since / Packets sent, Control). Apply saves both sections. The SeedLink port is
  checked like Settings (number, not a Gecko port). Bind and organization are kept, still edited in
  Settings. SeedLinkServer.control (connect / streaming / disconnect lines) goes into
  status()["server"]["control"]. Picture: docs/reports/2026-11-23-data-server-seedlink-tab.png.
* Version 1.8.1. Full suite 343 passed, 1 skipped. Pull request rohitbhutta/seiszone#2.
* The user then put SWA-RFC-GCFR.pdf (GCF block format, Issue F 2021) in Downloads and pointed
  at the manual again. Neither has the network wire format: the manual's chapters 4.3 and 7 only
  describe GCFPING / GCFSEND:B / GCFSTOP and that backfill goes over TCP on the same port, and
  GCFR says the network protocol is in "SWA-RFC-SCRM". GCF gap recovery is still waiting for that
  document.

## 2026-11-23 00:26 - 1.8.0 installed and running

* First run of the installer (00:15): SeisZone was closed ("SeisZone stopping" 00:15:38), then
  the installer ended without installing (1.7.0 still in Program Files and in Windows' list, no
  error event). The user ran it again; 1.8.0 installed and started 00:26:21 (pid 6104).
* Log: "engine started: 38 source(s), Gecko on 58772, SeedLink server on 18000, GCF server off";
  36 SeedLink sources connected and fetched the data missed since 18:45 UTC (33 of 36 done
  within 30 s); both GCF sources answered GCFPING; Gecko SS.SMR03 sending. No errors or warnings.
* No recording is active (the last one stopped 2026-11-15), so no file with the new names yet.
* Next: a test with a second computer pulling from this PC's data server.

## 2026-11-23 - 1.8.0 merged into main

* At the user's OK ("Yes, merge it"): branch pushed with the build note, pull request #1 merged
  (merge commit e7cdd87); local main pulled. The branch is kept. Installer unchanged (same code).

## 2026-11-23 00:10 - 1.8.0 built

* The user wrote "Merged, build the installer". GitHub still showed pull request #1 open and
  main at 338dffa, so the installer was built from the branch head c28970e (the same code).
* `python installer/build.py`: build/dist/SeisZoneSetup.exe 69.0 MB (00:09), SeisZone.exe
  reports 1.8.0; the frozen archive holds seiszone.core.gcf_server and seiszone.ui.server_window.
* Next: the user merges the pull request and runs the installer (UAC); then a test with the earlier
  viewer or SeisZone on a second computer pulling from this PC's data server.

## 2026-11-22 night - 1.8.0 on branch feature/viewer-recording-server (pull request)

* Asked (with 5 screenshots): site name into the label box, an eye button to hide the details
  above the traces (between Remove mean and Time cursors), the selection menu of the earlier
  viewer, recording file names with start and end time like the earlier viewer's, backfill of
  recordings after a PC restart, and the earlier viewer's "My Server" (UDP 1567) in SeisZone; a
  pull request the user reviews and merges.
* Viewer: label box = station / location.channel / **site** / units (site no longer repeated
  in the caption); **eye** button + key I (state "captions"); a **plain drag selects** a time
  range and opens its menu, as in the earlier viewer (Shift+drag pans; View > "Drag selects a
  time range" switches back, state "drag_selects"). The selection menu already had Save, Zoom
  In, Save on selection, PSD acc/vel, the three calibrations, Relative Orientation and external
  tools (where the earlier viewer has its add-on); before this a Shift+drag was needed.
* Recording names: `<code>_<YYYYMMDD>_<HHMM>_<HHMM>.mseed` (UTC, last of a day ends 2400), e.g.
  KA.KAND.9J.MNE_20261110_1600_1700.mseed. Old `__<stamp>Z` names are still read (viewers,
  export) and never renamed; a period begun under the old name goes on in that file.
* Backfill after a restart: already there for SeedLink sources (resume + TIME fetch from the
  last record); now covered by test_computer_off_while_recording_is_backfilled_into_the_same_files
  (power cut 12:02-12:40: every record once, in its hour file). GCF-UDP sources (2: IIT Roorkee)
  cannot backfill - written in the user guide.
* **Data server** (core/gcf_server.py, ui/server_window.py): GCF over UDP, port 1567, **off by
  default** (the earlier viewer on this PC uses 1567: a busy port is reported, retried every 3 s,
  never fatal). GCFPING/GCFSEND:B/GCFSTOP, clients removed after 180 s without a request, max
  32, "Channels up to" 1000 sps. Records re-cut into GCF blocks on whole seconds (<= 2 s, 8/16/32
  bit, checked with ObsPy's GCF reader); live and backfilled pieces kept apart. Names kept in
  State "gcf_server_ids" (KA.KAND.0J.HNZ -> KAND HN0JZ0; GCF sources keep their own). Status
  block per station names SeisZone + its SeedLink port + the channel table: Add source
  "<IP>:1567" on another SeisZone then uses that SeedLink server (exact names, backfill), else
  GCF with the exact names. Status bar "Data server :1567 - n clients".
* Version 1.8.0. Full suite 342 passed, 1 skipped. Pull request rohitbhutta/seiszone#1 (branch
  feature/viewer-recording-server), waiting for the user's review and merge. Not built, not installed.

## 2026-11-22 19:55 - 1.7.0 pushed to GitHub

* At the user's OK: main pushed to origin (90f0834..f02bd41): the checks against the earlier
  viewer (reports sections 8-11, pictures, GCF comparison list), the viewer plan and 1.7.0.

## 2026-11-22 19:50 - 1.7.0 installed

* The user quit SeisZone from the tray and chose "start the installer"; Claude started
  build/dist/SeisZoneSetup.exe, the user clicked through. SeisZone did not start by itself after
  the install; Claude started it from the desktop shortcut at 19:48. Log: "SeisZone 1.7.0
  starting", "sources 38/38 receiving, 153 channels" (it grows to ~170 as slow channels arrive,
  as before). One "THA packet is no valid miniSEED record; skipped" at connect - seen at every
  THA connect since 16:15, not new. Off 19:46:15-19:48:41. Not pushed.

## 2026-11-22 20:20 - 1.7.0 built

* Full suite 328 passed, 1 skipped. Commit ee9c838. build/dist/SeisZoneSetup.exe (68.8 MB, exe
  reports 1.7.0). Not installed yet (the user quits SeisZone from the tray, then runs it). Not pushed.

## 2026-11-22 20:10 - 1.7.0: a viewer mode like the earlier viewer's WaveView

* At the user's OK ("Yes, build it"): Scale "Per pixel (1:N)" (cpp, power of two, Up/Down/wheel
  x2, Home fits), factor shown in the label box, Scale others to match like that viewer (factors
  + fitted zoom), Scale dialog "auto" / a channel's name, Clean display (K), Traces may overlap
  (Shift+O). Tests: test_per_pixel_zoom_like_the_earlier_viewer, test_clean_display_and_overlap,
  scale-to-match test rewritten. Version 1.7.0.
* Side by side with its 12-stream window in the same settings: median trace height ratio 1.00
  (0.84-1.06 per row). Report section 11; picture 2026-11-22-side-by-side-per-pixel-mode.png.
* Not built: its tick rhythm (minute ticks, longer every 10 min).

## 2026-11-22 19:25 - multi-instrument side by side; viewer plan written

* Its ~130-channel window: one zoom 1:536 870 912, all flat but IS.PAZ.0J.BHZ raw (glitching
  sensor) - the same flat effect the user reported, in that viewer.
* Its 12-stream window vs SeisZone (Fixed +- with per-trace gain = its factors): same events,
  same relative sizes; each of its traces = zoom / factor (within 4-17 %).
* Filter: IIR and FIR designs all give 0.90-0.92 of its drawn amplitude - no filter change needed.
* Plan for a viewer mode like that WaveView: docs/reports/2026-11-22-viewer-like-plan.md
  (counts-per-pixel zoom, factor in the label + match others, pixels per second, clean rows,
  overlap, minute ticks). Waiting for the user's OK.

## 2026-11-22 19:10 - all instruments added to the third-party viewer; data identical

* At the user's request (user closed it first): 32 UDP pull servers added to C:\scream\scream.ini
  (36 in all; backup scream.ini.backup-2026-11-22-before-adding), restarted from the desktop
  shortcut, 57 streams after 20 s. Not added: THA .7, Centaur .119, Gecko.
* GCF (what it receives) vs SeisZone SeedLink, every instrument: 135 channels, 415 059 of 415 059
  samples identical. Report section 9; list 2026-11-22-gcf-vs-seedlink-all.txt.
* Waiting: its WaveViews refill only from its start (~20 min); then compare the pictures of the
  multi-instrument window and plan the viewer changes the user asked for ("like that viewer").

## 2026-11-22 18:55 - same instrument, same minutes: SeisZone vs the third-party viewer

* Tuljapur (7465) over 12:57-13:21 UTC: same band (0.1-0.9 x Nyquist), same events at the same
  time (within 1 s), peaks within 5-11 %, envelope correlation 0.79-0.91. Only intended
  difference: its scale is one fixed counts-per-pixel zoom (1:16, DefAmpScale 2048); SeisZone's
  default fits each sensor. Scale "Fixed +-" 2048 + Units "Counts" looks the same. Details:
  docs/reports/2026-11-22-multi-instrument-check.md section 8; picture
  docs/reports/2026-11-22-side-by-side-tuljapur.png. Its settings were only read
  (C:\scream\scream.ini, calvals.txt); no code read.

## 2026-11-22 18:40 - check: band-pass with every instrument in one viewer (1.6.0)

* The user asked whether the viewer now filters correctly with many instruments, like the
  third-party viewer. Checked offscreen with the 1.6.0 code and the last 10 minutes of every
  station read from SeisZone's own server (127.0.0.1:18000; no instrument contacted): 118
  waveform channels from 42 sensors, Default band-pass, Scale "Common per instrument".
* All 118 rows filtered. With the app's 250 ms refresh: 89 rows at once, all 118 within 1.3 s
  (the refresh budget works round-robin, about 2.5 ms per row). Picture of 6 sensors:
  docs/reports/2026-11-22-many-instruments-bandpass.png.
* Rows that look flat are real, not the filter: KS.ALM.0J.HHE is dead (raw value constant, range
  0); IS.PAZ.0J.BHZ has huge glitches (raw range 16.6 million counts against ~5000 on E/N), so
  its E and N look flat on the shared per-instrument scale (use Scale "Auto" or a per-trace
  gain to see them). Quieter Z components (PATSI, BHD, SRG, HID, KAND) are simply smaller.
* Not seen on the user's screen: no SeisZone viewer was open, and the third-party viewer showed
  one instrument (7465), so there was no many-instrument side-by-side.

## 2026-11-22 16:50 - pushed to GitHub

* At the user's OK: main pushed to origin (f4d3c0c..6799cdb), all 1.6.0 work.

## 2026-11-22 16:46 - 1.6.0 installed

* The user quit SeisZone from the tray and chose "start the installer"; Claude started
  build/dist/SeisZoneSetup.exe, the user clicked through. Log: "SeisZone 1.6.0 starting",
  "sources 38/38 receiving, 153 channels", no refused selector, no error. No viewer was open, so
  the new scale / selection menu were not seen on screen yet (they are covered by the tests).

## 2026-11-22 (evening, 2) - selection menu implemented; version 1.6.0

* Shift+drag over several rows / Ctrl+drag for two channels; tools on the selected channels;
  Save on selection; External tools (Set up external tools...). Tests added (viewer, analysis).
* Version bumped to 1.6.0 (with the Common-per-instrument and selector fixes). Full suite
  326 passed, 1 skipped. Built 16:31: build/dist/SeisZoneSetup.exe (68.8 MB, exe reports 1.6.0);
  not installed yet (the user installs it). See memory.md
  "Version 1.6.0".

## 2026-11-22 16:11 - 33 instruments added

* The user quit SeisZone from the tray; tools/add_from_survey.py write added 33 sources (38 in
  all) and fixed Tuljapur's selector ("1HH?" -> "1?HH?"). SeisZone 1.5.0 started again: "sources
  38/38 receiving, 149 channels", no refused selector. PC clock now 18.4 s fast.
* Next: the selection menu (user OK'd the plan), then build 1.6.0 (Common per instrument needs it).

## 2026-11-22 (evening) - fixes for many instruments; sources prepared; selection plan

* Fixed (not built yet): Common scale per instrument (`waveform_view.instrument`; old behaviour
  as "Common, all channels"); one-character location selectors ("1HH?" -> "1?HH?",
  `seedlink_client.wire_selector`, `probe.selectors_for`; "1?HH?" checked read-only on
  Tuljapur). Tests added; suite 321+ passed (2 failures from negated selectors fixed after).
* tools/add_from_survey.py: probed the answering units read-only; 33 sources ready (skipped
  10.8.0.137 = this PC, and 10.8.0.21 / .48 = GCF relays of Ralej and Kavdej, which are added
  directly). Writing them needs SeisZone quit (config would be overwritten) - quitting it from
  here was not allowed, the user is asked to quit it from the tray. Config backup:
  %APPDATA%\SeisZone\config.backup-2026-11-22-before-adding.json.
* Selection menu plan (Shift+drag over several channels): docs/reports/2026-11-22-selection-menu-plan.md
  - waiting for the user's OK.

## 2026-11-22 (afternoon, 2) - side-by-side behaviour check (report only)

* Behaviour only (no code of the third-party viewer read): its windows, its User's Guide and its
  settings. It pulls GCF/UDP 1567 from Tuljapur, Nagdhaniba, Panchavada, PATSIO.
* Data: 30 s of Tuljapur GCF vs SeisZone's SeedLink data: 9 400 / 9 400 samples identical.
* Filter: both apply it to every channel, same default band. Amplitude: the viewer uses a
  user zoom + per-stream / per-instrument factors; SeisZone's Common scale (largest of all
  channels) is the cause of the "only one instrument filtered" look.
* Report section 7. Still waiting for the user's OK to fix.

## 2026-11-22 (afternoon) - multi-instrument check (report only, nothing changed)

* Asked: add all IPs of `Downloads\IP Distrubation.xlsx`, check SeedLink 18000 vs GCF 1567,
  fix "band-pass only on the last instrument's channels", make a plan, then (second message)
  "check everything first, then tell me".
* Found: the filter does reach every channel; the "Common" amplitude scale makes all but the
  largest instrument flat lines. Proven with 60 min of the 27 real live channels.
* Survey of 97 IPs (read-only): 34 Guralp SeedLink units, 1 Nanometrics Centaur (INFO timed
  out), 4 GCF-only sites, 10.8.0.137 = this PC, 57 without a service / no answer.
* Bugs found: selector `1HH?` refused for one-character location "1" (17 units); local
  server TIME with end time sent nothing.
* Report: docs/reports/2026-11-22-multi-instrument-check.md (+ survey table/json, image).
* Waiting for the user's OK on the fix plan (report section 1 and 4) and on which instruments to
  add.

## 2026-11-22 (night) - version 1.5.0

* True UTC for latency (time servers), Open viewer button removed from Channels, speed fixes
  1-5; pushed to main, installer built, installed by the user (see memory.md "Version 1.5.0").
