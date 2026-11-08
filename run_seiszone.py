"""Run SeisZone from the source folder (no installation): python run_seiszone.py [options]

Same options as `python -m seiszone`; src/ is put on the import path first."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from seiszone.__main__ import main  # noqa: E402  (needs the path above)

if __name__ == "__main__":
    sys.exit(main())
