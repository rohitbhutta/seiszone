import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
DATA = os.path.join(ROOT, "tests", "data")

# never touch the real %APPDATA% / Documents while testing
os.environ.setdefault("SEISZONE_HOME", tempfile.mkdtemp(prefix="seiszone_test_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
