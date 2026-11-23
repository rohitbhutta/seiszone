# SeismoVerse on Linux, Mac and iPhone/iPad - plan (2026-11-23, nothing built yet)

The user asked to make SeismoVerse run on a Linux machine and on an "iOS machine", plan first.

"iOS" can mean two different things, and the answer is very different:

| | What it is | Can SeismoVerse run there? |
|---|---|---|
| **Mac** (MacBook, iMac, Mac mini) | macOS | **Yes.** Same program, same code as Windows and Linux. |
| **iPhone / iPad** | iOS / iPadOS | **Not as it is today** (see part 7). A live web page for iPhone/iPad is the practical way. |

## 1. Where SeismoVerse stands

SeismoVerse is written in Python with Qt (PySide6), pyqtgraph, numpy and scipy. All four exist
for Linux and macOS, so almost all of the program carries over unchanged: SeedLink, GCF/UDP,
Gecko, the viewers, filters, spectra, the data server, recording, export, the updates' signature
check. **Recordings will be the same miniSEED files with the same names** on every system.

What only works on Windows today (found by reading the code), and what replaces it:

| Part | Windows today | Linux | Mac |
|---|---|---|---|
| Settings, logs, server buffer (paths.py) | %APPDATA%, %LOCALAPPDATA% | ~/.config/SeismoVerse, ~/.local/share/SeismoVerse | ~/Library/Application Support/SeismoVerse, ~/Library/Logs/SeismoVerse |
| Recordings and exports | Documents\SeismoVerse | ~/Documents/SeismoVerse | ~/Documents/SeismoVerse |
| Only one SeismoVerse at a time (single_instance.py) | Windows mutex | a lock file (today: no protection at all, two copies could write the same files) | a lock file |
| Start with the computer (ui/app.py) | registry Run key | ~/.config/autostart/SeismoVerse.desktop | a login item (~/Library/LaunchAgents) |
| Tray icon | taskbar tray | works on Ubuntu, KDE, Mint; plain GNOME needs the AppIndicator extension, otherwise closing the window quits instead of hiding (already handled) | icon in the menu bar |
| Install | Inno Setup installer | one-file **AppImage** (no root/admin needed) | **SeismoVerse.app** in a .dmg |
| Updates (core/updater.py) | download setup .exe, check signature, run it | download the new AppImage, check signature, replace the file, restart | download the new app, check signature, replace it, restart |
| Old Gecko service tool (core/ports.py) | stops the old Windows service | not needed (hidden) | not needed (hidden) |
| External tools dialog | *.exe *.bat *.cmd | any program or script | any program or .app |
| Font, taskbar identity, close-message filter | Windows only | already skipped | already skipped |

Network: SeismoVerse uses ports 18000 (SeedLink server), 1567/UDP (GCF) and 58772 (Gecko). All
are above 1024, so no root/admin rights are needed. Linux firewalls (ufw/firewalld) may need
these ports opened once; the Mac asks "allow incoming connections" the first time. The Linux or
Mac computer needs the same VPN as this PC to reach the instruments (10.8.0.x).

## 2. Which computers it will run on

* **Linux (64-bit Intel/AMD):** Ubuntu 22.04 or newer, Debian 12+, Linux Mint 21+, Rocky/Alma/RHEL 9+,
  Fedora. Not Ubuntu 20.04 or older (Qt 6.12 needs glibc 2.34 or newer). ARM Linux (Raspberry Pi)
  only later, if wanted (Qt needs an even newer system there).
* **Mac:** macOS 14 Sonoma or newer (Qt 6.12 needs it). Apple-silicon Macs (M1-M4) first; an
  Intel Mac build only if you have Intel Macs.

## 3. How the Linux and Mac versions are built

A Linux program has to be built on Linux, and a Mac program on a Mac (PyInstaller cannot build
for another system). Plan:

1. **GitHub Actions** (GitHub's own build machines, Windows, Linux and Mac): every change is
   tested on Linux and Mac, and on request they build `SeismoVerse-x86_64.AppImage` and
   `SeismoVerse-macOS-arm64.dmg`. Free and unlimited while the source repository is public; for a
   private repository GitHub gives a monthly free amount (2,000 minutes on the free plan) and a
   Mac minute counts as ten - enough for a few releases a month.
2. **The signing key never goes to GitHub.** The cloud only builds. `installer/publish.py` on this
   PC downloads the built files, checks them, signs them with the key in
   %USERPROFILE%\.seismoverse-release and publishes one release with all files.
3. **Testing here:** for Linux, WSL2 (Linux inside Windows) on this PC lets me run the tests and
   the real Linux program here, with its window on this screen. Installing WSL2 needs one
   administrator command and a restart, done by you. For the Mac, the cloud runs the tests, but
   someone has to try it on a real Mac before the first Mac release.

## 4. Updates on every system (one release, several files)

Each release in `rohitbhutta/seismoverse-releases` gets:

| File | For |
|---|---|
| SeismoVerseSetup.exe (+ .sha256, .sig) | Windows, unchanged, so the installed 2.2.0 keeps updating |
| SeismoVerse-x86_64.AppImage (+ .sha256, .sig) | Linux |
| SeismoVerse-macOS-arm64.dmg (+ .sha256, .sig) | Mac |

Each SeismoVerse picks only its own file and installs it only with a valid publisher signature,
the same check as on Windows. The signature of the new files also covers the file name, so a
file cannot be passed off as another system's file.

## 5. Mac: Apple's warning on unknown programs

Without an **Apple Developer account** (99 US$ a year), the Mac refuses to open the program the
first time: the user has to go to System Settings > Privacy & Security > **Open Anyway** once,
like "More info > Run anyway" on Windows. With the account, the app is signed and checked
(notarised) by Apple and opens normally. Your decision; it can also be added later.

## 6. Steps (each ends with tests passing, an entry in history.md, and your OK before pushing)

| Step | What | Size |
|---|---|---|
| 1. Common base (2.3.0) | Folders for Linux/Mac, lock file, start with the computer, external tools; Windows tests stay green; test runs on Linux and Mac in GitHub Actions; Windows-only tests marked as such | medium |
| 2. Linux | AppImage build, menu entry and icon, tray check, firewall note; tests and a 1-hour live recording in WSL2 (read-only connections, with your OK) compared file by file with the Windows recording; manual chapter "Linux" | medium |
| 3. Linux updates | AppImage self-update, publish.py builds and signs all files; first release with Windows + Linux | small |
| 4. Mac | .app and .dmg build, menu bar icon, login item, Cmd shortcuts, test on a real Mac; manual chapter "Mac"; Mac self-update | medium-large |
| 5. Linux server (optional) | run SeismoVerse without a screen as a Linux service (`--engine` exists already), for 24/7 recording on a server | small |
| 6. iPhone/iPad (optional) | see part 7 | medium |

The Windows program, its installer, its updates and the recording format do not change.

## 7. iPhone and iPad

* Qt for Python added iOS support in 6.12 (2026), but it cannot use packages with compiled
  parts such as numpy yet, and the iPhone simulator is not supported. SeismoVerse needs numpy and
  scipy (filters, Steim, spectra) and pyqtgraph needs numpy, so **the program cannot be built for
  iPhone/iPad today**.
* Even later, iOS stops apps soon after they leave the screen: an iPhone app cannot receive or
  record 24/7, cannot keep listening for GCF/Gecko, and can only be installed through the App
  Store or TestFlight (Apple Developer account and a Mac with Xcode needed).

Practical choices:

* **A. Live web page (recommended):** SeismoVerse on Windows/Linux/Mac serves a small web page
  (for example http://<PC>:8080) with the live traces and channel status. Any iPhone, iPad,
  Android phone or other computer opens it in the browser over the VPN or the local network.
  Nothing to install, no App Store, updated with SeismoVerse. Viewing only; recording stays on
  the computer.
* **B. Wait:** check again when Qt for Python on iOS supports numpy and scipy.
* **C. A separate iPhone app** written for iOS: a new project of its own (Apple account, Mac,
  App Store review); only worth it with many iPhone users.

## 8. What I need from you

1. Does "iOS" mean **Mac computers** or **iPhone/iPad** (or both)?
2. Which Linux will you use (Ubuntu version?), and on which machine? For testing here: are you
   OK to install WSL2 on this PC (one administrator command + restart)?
3. Is there a Mac to test on (Apple silicon or Intel)?
4. Apple Developer account (99 US$/year): yes, later or no?
5. OK to add the GitHub Actions build/test setup to the repository?
6. The source repository `rohitbhutta/seiszone` is **public** today (checked 2026-11-23), not
   private as the notes said: should it stay public or become private?

Sources: Qt blog "Python mobile app development: bringing PySide6 on iOS" and "Qt for Python
release 6.12 is here" (qt.io); PySide6 6.12.0 wheel tags on PyPI (Linux glibc 2.34+, macOS 14+).
