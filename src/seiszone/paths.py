"""Where SeismoVerse keeps its files (per Windows user, no administrator rights needed). The folders
are named SeismoVerse (DATA_FOLDER). On a PC that ran the program under its old name SeisZone
(LEGACY_DATA_FOLDER) the old folder of a place is used as long as it exists and no SeismoVerse
folder is there, so nothing moves and nothing is lost; a new PC gets SeismoVerse folders.
The listing below shows the old name, as on the first PC.

    %APPDATA%\\SeisZone\\config.json        sources and settings
    %APPDATA%\\SeisZone\\state.json         SeedLink resume positions, recordings in progress
    %APPDATA%\\SeisZone\\stationxml\\       StationXML per NET.STA (instrument responses)
    %LOCALAPPDATA%\\SeisZone\\logs\\         program log (rotated)
    %LOCALAPPDATA%\\SeisZone\\ServerBuffer\\ the SeedLink server's packets of the last days (not synced)
    %USERPROFILE%\\Documents\\SeisZone\\    recordings and exports (default, changeable)

SEISZONE_HOME (environment) moves everything below one folder - used by the tests.
"""

import os

from . import DATA_FOLDER, LEGACY_DATA_FOLDER


def _base(env, fallback):
    home = os.environ.get("SEISZONE_HOME")
    if home:
        return home
    return data_folder(os.environ.get(env) or os.path.expanduser(fallback))


def data_folder(parent):
    """<parent>\\SeismoVerse, or <parent>\\SeisZone on a PC that has that folder of an earlier
    version and no SeismoVerse folder yet (its files stay where they are)."""
    new = os.path.join(parent, DATA_FOLDER)
    old = os.path.join(parent, LEGACY_DATA_FOLDER)
    if not os.path.isdir(new) and os.path.isdir(old):
        return old
    return new


def config_dir():
    return _base("APPDATA", "~/AppData/Roaming")


def local_dir():
    return _base("LOCALAPPDATA", "~/AppData/Local")


def config_file():
    return os.path.join(config_dir(), "config.json")


def state_file():
    return os.path.join(config_dir(), "state.json")


def stationxml_dir():
    return os.path.join(config_dir(), "stationxml")


def log_dir():
    return os.path.join(local_dir(), "logs")


def server_buffer_dir():
    return os.path.join(local_dir(), "ServerBuffer")


def documents_dir():
    home = os.environ.get("SEISZONE_HOME")
    if home:
        return os.path.join(home, "Documents")
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return data_folder(docs if os.path.isdir(docs) else os.path.expanduser("~"))


def recordings_dir():
    return os.path.join(documents_dir(), "Recordings")


def exports_dir():
    return os.path.join(documents_dir(), "Exports")


def ensure(path):
    os.makedirs(path, exist_ok=True)
    return path
