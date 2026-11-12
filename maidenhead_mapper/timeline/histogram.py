"""Hourly bar chart with a draggable range selection."""

from qgis.PyQt.QtCore import QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.PyQt.QtWidgets import QSizePolicy, QToolTip, QWidget

# Same colours as the direction style
HEARD = QColor("#1f77b4")
HEARD_ME = QColor("#ff7f0e")
DIM_ALPHA = 70
PAD_L, PAD_R, PAD_T, PAD_B = 4, 4, 16, 18


def _x(event):
    return event.position().x() if hasattr(event, "position") else event.x()  # Qt6 / Qt5


def _global(event):
    return event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()


class Histogram(QWidget):
    """Reports per hour, heard stacked under heard_me. Drag to pick hours;
    double-click to pick all. Emits `selected(i0, i1)` (inclusive bins) when
    the mouse is released and `dragging(i0, i1)` while it moves."""

    selected = pyqtSignal(int, int)
    dragging = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)
        self.data = None
        self.labels = ("heard", "heard me")   # tooltip names of the two series (None hides one)
        self.sel = (0, 0)
        self._anchor = None

    # --- data -------------------------------------------------------------
    def set_data(self, hourly, sel=None):
        self.data = hourly if hourly is not None and len(hourly) else None
        n = len(self.data) if self.data else 1
        self.sel = sel if sel is not None else (0, n - 1)
        self.update()

    def set_selection(self, i0, i1):
        n = len(self.data) if self.data else 1
        i0, i1 = sorted((max(0, min(n - 1, i0)), max(0, min(n - 1, i1))))
        self.sel = (i0, i1)
        self.update()

    # --- geometry ---------------------------------------------------------
    def _plot(self):
        return QRectF(PAD_L, PAD_T, max(1, self.width() - PAD_L - PAD_R), max(1, self.height() - PAD_T - PAD_B))

    def _bin_at(self, x):
        r = self._plot()
        n = len(self.data)
        return max(0, min(n - 1, int((x - r.left()) * n / r.width())))

    # --- painting ---------------------------------------------------------
    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.base())
        text = pal.text().color()
        if not self.data:
            p.setPen(text)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No reports with times")
            return
        d, r = self.data, self._plot()
        n = len(d)
        top = max(max(d.total(i) for i in range(n)), 1)
        bw = r.width() / n

        hl = QColor(pal.highlight().color())
        hl.setAlpha(45)
        i0, i1 = self.sel
        p.fillRect(QRectF(r.left() + i0 * bw, r.top(), (i1 - i0 + 1) * bw, r.height()), hl)

        def bar(x, w, i):
            alpha = 255 if i0 <= i <= i1 else DIM_ALPHA
            y = r.bottom()
            for count, colour in ((d.heard[i], HEARD), (d.heard_me[i], HEARD_ME)):
                if count:
                    h = count / top * r.height()
                    c = QColor(colour)
                    c.setAlpha(alpha)
                    p.fillRect(QRectF(x, y - h, w, h), c)
                    y -= h

        if bw >= 1:
            gap = 1 if bw > 3 else 0
            for i in range(n):
                bar(r.left() + i * bw, bw - gap, i)
        else:  # more hours than pixels: each pixel column shows its busiest hour
            cols = int(r.width())
            for x in range(cols):
                b0 = x * n // cols
                b1 = max(b0 + 1, (x + 1) * n // cols)
                i = max(range(b0, b1), key=d.total)
                bar(r.left() + x, 1, i)

        p.setPen(text)
        f = p.font()
        f.setPointSizeF(max(6.0, f.pointSizeF() * 0.85))
        p.setFont(f)
        p.drawText(QRectF(PAD_L, 0, r.width(), PAD_T), Qt.AlignmentFlag.AlignVCenter, f"max {top}/h")
        fmt = "%Y-%m-%d %H:00"
        bottom = QRectF(PAD_L, r.bottom() + 2, r.width(), PAD_B - 2)
        p.drawText(bottom, Qt.AlignmentFlag.AlignLeft, d.hour(0).strftime(fmt))
        p.drawText(bottom, Qt.AlignmentFlag.AlignRight, d.hour(n - 1).strftime(fmt))

    # --- mouse ------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if self.data and event.button() == Qt.MouseButton.LeftButton:
            self._anchor = self._bin_at(_x(event))
            self.set_selection(self._anchor, self._anchor)
            self.dragging.emit(*self.sel)

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self.data:
            return
        i = self._bin_at(_x(event))
        if self._anchor is not None:
            self.set_selection(self._anchor, i)
            self.dragging.emit(*self.sel)
        d = self.data
        counts = ", ".join(f"{name} {n}" for name, n in zip(self.labels, (d.heard[i], d.heard_me[i])) if name)
        QToolTip.showText(_global(event), f"{d.hour(i):%Y-%m-%d %H:00} UTC\n{counts}", self)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._anchor is not None and event.button() == Qt.MouseButton.LeftButton:
            self._anchor = None
            self.selected.emit(*self.sel)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if self.data:
            self._anchor = None
            self.set_selection(0, len(self.data) - 1)
            self.selected.emit(*self.sel)
