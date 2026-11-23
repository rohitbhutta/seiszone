"""Keep a window on the screen: never taller or wider than the screen's free area (without the
taskbar), and never with its title bar above the top edge or off a side - the title bar must
stay reachable so the window can be moved. Used for every window the main window opens
(MainWindow._open) and by the Data server window itself.

On a small screen or with Windows display scaling (125 % on a 1920 x 1080 screen leaves 824
points of height) a window centred over the main window could start above the top of the
screen; its title bar was then out of reach."""

from PySide6.QtCore import QRect, QTimer
from PySide6.QtGui import QGuiApplication

MARGIN = 8               # points kept free around the window
TITLE_GUESS = 32         # title bar + frame height before Windows has drawn the frame


def available_rect(w):
    """The free area (without the taskbar) of the screen the window is on."""
    scr = None
    try:
        scr = w.screen()
    except RuntimeError:
        scr = None
    if scr is None:
        scr = QGuiApplication.screenAt(w.frameGeometry().center()) or QGuiApplication.primaryScreen()
    return scr.availableGeometry() if scr is not None else QRect()


def fit_to_screen(w, avail=None):
    """Shrink the window to the free screen area if it is larger, and move it so its whole frame
    (title bar included) is inside. Returns True when the window was changed."""
    avail = QRect(avail) if avail is not None else available_rect(w)
    if not avail.isValid() or avail.isEmpty():
        return False
    frame, geo = w.frameGeometry(), w.geometry()
    extra_w = frame.width() - geo.width()
    extra_h = frame.height() - geo.height()
    if extra_h <= 0 and w.isWindow():
        extra_h = TITLE_GUESS            # not drawn yet: allow for the title bar
    max_w = max(1, avail.width() - extra_w - 2 * MARGIN)
    max_h = max(1, avail.height() - extra_h - 2 * MARGIN)
    changed = False
    if geo.width() > max_w or geo.height() > max_h:
        w.resize(min(geo.width(), max_w), min(geo.height(), max_h))
        changed = True
    frame = w.frameGeometry()
    fw = frame.width() if frame.width() > w.width() else w.width() + extra_w
    fh = frame.height() if frame.height() > w.height() else w.height() + extra_h
    x = min(max(frame.x(), avail.left() + MARGIN), avail.left() + avail.width() - fw - MARGIN)
    y = min(max(frame.y(), avail.top() + MARGIN), avail.top() + avail.height() - fh - MARGIN)
    x, y = max(x, avail.left()), max(y, avail.top())     # a window still too large: top-left first
    if (x, y) != (frame.x(), frame.y()):
        w.move(x, y)                     # move() places the frame (title bar) of a window
        changed = True
    return changed


def keep_on_screen(w):
    """fit_to_screen now and once more when Windows has drawn the frame (its real size)."""
    fit_to_screen(w)
    QTimer.singleShot(0, lambda: _later(w))


def _later(w):
    try:
        if w.isVisible():
            fit_to_screen(w)
    except RuntimeError:                 # closed (deleted) in the meantime
        pass
