"""SeismoVerse (until 1.9.0 named SeisZone) - live seismic data acquisition, viewing and recording.

Our own application, written from scratch on open standards (SeedLink, GCF, miniSEED,
FDSN StationXML). See plan.md and memory.md at the project root. The Python package keeps
the name "seiszone".
"""

APP_NAME = "SeismoVerse"
VERSION = "2.1.1"
ORGANIZATION = "SeismoVerse"
AUTHOR = "Arnab Kole"
SUPPORTED_BY = "Rohit Santra"
# The per-user folders keep the old name, so settings, sources, recordings, logs and the
# SeedLink server's buffer of earlier versions stay where they are:
# %APPDATA%\SeisZone, %LOCALAPPDATA%\SeisZone, Documents\SeisZone.
DATA_FOLDER = "SeisZone"
