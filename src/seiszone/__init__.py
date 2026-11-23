"""SeismoVerse (until 1.9.0 named SeisZone) - live seismic data acquisition, viewing and recording.

Our own application, written from scratch on open standards (SeedLink, GCF, miniSEED,
FDSN StationXML). See plan.md and memory.md at the project root. The Python package keeps
the name "seiszone".
"""

APP_NAME = "SeismoVerse"
VERSION = "2.2.0"
ORGANIZATION = "SeismoVerse"
AUTHOR = "Arnab Kole"
SUPPORTED_BY = "Rohit Santra"
# The per-user folders: %APPDATA%\SeismoVerse, %LOCALAPPDATA%\SeismoVerse, Documents\SeismoVerse.
# A PC that ran SeisZone 1.9.0 or older keeps its folders of the old name (LEGACY_DATA_FOLDER)
# as long as it has them, so its settings, sources, recordings, logs and server buffer stay
# where they are; nothing is moved or deleted (paths.py).
DATA_FOLDER = "SeismoVerse"
LEGACY_DATA_FOLDER = "SeisZone"
