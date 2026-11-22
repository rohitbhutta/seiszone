"""Where SeisZone keeps its files (per Windows user, no administrator rights needed).

    %APPDATA%\\SeisZone\\config.json        sources and settings
    %APPDATA%\\SeisZone\\state.json         SeedLink resume positions, recordings in progress
    %APPDATA%\\SeisZone\\stationxml\\       StationXML per NET.STA (instrument responses)
    %LOCALAPPDATA%\\SeisZone\\logs\\         program log (rotated)
    %LOCALAPPDATA%\\SeisZone\\ServerBuffer\\ the SeedLink server's packets of the last days (not synced)
    %USERPROFILE%\\Documents\\SeisZone\\    recordings and exports (default, changeable)

SEISZONE_HOME (environment) moves everything below one folder - used by the tests.
"""

import os

from . import APP_NAME


def _base(env, fallback):
    home = os.environ.get("SEISZONE_HOME")
    if home:
        return home
    return os.path.join(os.environ.get(env) or os.path.expanduser(fallback), APP_NAME)


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
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), APP_NAME)


def recordings_dir():
    return os.path.join(documents_dir(), "Recordings")


def exports_dir():
    return os.path.join(documents_dir(), "Exports")


def ensure(path):
    os.makedirs(path, exist_ok=True)
    return path
