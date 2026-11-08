"""Draw the SeisZone application icon into assets/seiszone.png and assets/seiszone.ico.

    python tools/make_icon.py

The icon is drawn in code by seiszone.ui.theme.draw_app_pixmap, so the program, the exe and
the installer all show the same picture. Every ICO size is drawn by Qt at its own size (not
scaled down from 256 px), which keeps the small taskbar / title-bar icons sharp. Pillow only
packs the Qt PNGs into the multi-size .ico container.
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
# no window is shown: the offscreen platform also works without a desktop session (build server)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from seiszone.ui import theme  # noqa: E402

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZE = 256


def render(size):
    """The icon at `size` px as a Pillow RGBA image (via an in-memory PNG)."""
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    if not theme.draw_app_pixmap(size).save(buf, "PNG"):
        raise RuntimeError(f"Qt could not encode the {size} px icon")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main(outdir=None):
    outdir = outdir or os.path.join(ROOT, "assets")
    os.makedirs(outdir, exist_ok=True)
    _app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])  # noqa: F841 - QPixmap needs it
    images = {s: render(s) for s in ICO_SIZES}
    png = os.path.join(outdir, "seiszone.png")
    images[PNG_SIZE].save(png, format="PNG")
    ico = os.path.join(outdir, "seiszone.ico")
    largest = images[max(ICO_SIZES)]
    others = [images[s] for s in ICO_SIZES if s != max(ICO_SIZES)]
    largest.save(ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES], append_images=others)
    for p in (png, ico):
        print(f"wrote {p} ({os.path.getsize(p):,} bytes)")
    return png, ico


if __name__ == "__main__":
    main()
