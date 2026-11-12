"""Activity timeline: reports per hour for a dataset, and a time range that
redraws its layers (see sources.py for what can be driven)."""

from qgis.core import QgsApplication, QgsProject, QgsTask
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import timeline
from .histogram import Histogram
from .sources import LiveSource, SourceError, discover

_FMT = "%Y-%m-%d %H:00"
LIVE_MS = 180       # while dragging, redraw at most this often
REFRESH_MS = 30000  # recount a running live map's bars this often
BASE_NAME = "maidenhead_mapper/base_name"


class TimelineDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Activity timeline", parent)
        self.setObjectName("MaidenheadMapperTimelineDock")
        self.cache = {}       # source key -> (signature, data)
        self.sources = {}
        self.source = None
        self.task = None
        self.hourly = None
        self.windowed = False

        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._source_changed)
        self.hist = Histogram()
        self.hist.dragging.connect(self._dragging)
        self.hist.selected.connect(self._released)
        self.live = QTimer(self)  # throttles redraws while the mouse moves
        self.live.setSingleShot(True)
        self.live.timeout.connect(lambda: self._apply(*self.hist.sel))
        self.recount = QTimer(self)
        self.recount.timeout.connect(self._recount_live)
        self.recount.start(REFRESH_MS)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.prev = QPushButton("◀")
        self.prev.setToolTip("Move the range back by its own length")
        self.prev.clicked.connect(lambda: self._shift(-1))
        self.all = QPushButton("All hours")
        self.all.clicked.connect(self._select_all)
        self.next = QPushButton("▶")
        self.next.setToolTip("Move the range forward by its own length")
        self.next.clicked.connect(lambda: self._shift(1))

        buttons = QHBoxLayout()
        for b in (self.prev, self.all, self.next):
            buttons.addWidget(b)
        lay = QVBoxLayout()
        lay.addWidget(self.combo)
        lay.addWidget(self.hist, 1)
        lay.addLayout(buttons)
        lay.addWidget(self.info)
        w = QWidget()
        w.setLayout(lay)
        self.setWidget(w)

        project = QgsProject.instance()
        project.layersAdded.connect(self._refresh_soon)
        project.layersRemoved.connect(self._refresh_soon)
        self._refresh()

    def _refresh_soon(self, *_):
        # Processing adds an output layer before its post-processor tags it,
        # so look once the current event has finished.
        QTimer.singleShot(0, self._refresh)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._refresh()

    # --- source list ------------------------------------------------------
    def _refresh(self, *_):
        current = self.combo.currentData()
        self.sources = {s.key: s for s in discover()}
        self.combo.blockSignals(True)
        self.combo.clear()
        for s in self.sources.values():
            self.combo.addItem(s.name, s.key)
        idx = self.combo.findData(current)
        self.combo.setCurrentIndex(idx if idx >= 0 else 0 if self.combo.count() else -1)
        self.combo.blockSignals(False)
        if self.combo.currentData() != current or self.source is None:
            self._source_changed()
        else:
            # discover() made a fresh Source for the same key (e.g. after another
            # tool added a layer); keep the data already loaded, or dragging the
            # chart would hit a Source with no data.
            old, new = self.source, self.sources.get(current)
            if new is not old and hasattr(old, "data") and not hasattr(new, "data"):
                new.data, new.hourly = old.data, old.hourly
            self.source = new

    def _source_changed(self, *_):
        self.source = self.sources.get(self.combo.currentData())
        self.hourly = None
        self.hist.set_data(None)
        if self.source is None:
            self._set_info("Station layers from <i>Fetch</i> / <i>Import PSK Reporter</i>, <i>Import WSJT-X "
                           "ALL.TXT</i> and the running or saved <i>WSJT-X live map</i> appear here.")
            return
        self._show_layers(self.source)
        self._load(self.source)

    def _show_layers(self, source):
        root = QgsProject.instance().layerTreeRoot()
        for lyr in source.layers():
            node = root.findLayer(lyr.id())
            while node is not None and hasattr(node, "setItemVisibilityChecked"):
                node.setItemVisibilityChecked(True)
                node = node.parent()

    def _load(self, source):
        sig = source.signature()
        if sig is None:
            self._set_info("The data behind this layer is missing (file or folder moved?).")
            return
        hit = self.cache.get(source.key)
        if hit and hit[0] == sig:
            self._loaded(source, hit[1])
            return
        if isinstance(source, LiveSource):           # in memory: no task needed
            data = source.load()
            self.cache[source.key] = (sig, data)
            self._loaded(source, data)
            return
        if self.task is not None:
            return  # one load at a time; _task_done re-checks the selection
        self._set_info("Reading…")
        self.task = QgsTask.fromFunction(f"Maidenhead Mapper: reading {source.name}", source.load,
                                         on_finished=lambda exc, result=None: self._task_done(exc, result,
                                                                                              source, sig))
        QgsApplication.taskManager().addTask(self.task)

    def _task_done(self, exc, data, source, sig):
        self.task = None
        if exc is not None or data is None:
            self._set_info(f"Could not read {source.name}: {exc}")
            return
        # keep only small caches: the current dataset
        self.cache = {k: v for k, v in self.cache.items() if k.startswith("log:")}
        self.cache[source.key] = (sig, data)
        self._source_changed()  # the selection may have changed meanwhile

    def _loaded(self, source, data):
        source.set_data(data)
        h = self.hourly = source.hourly
        if not len(h):
            self.hist.set_data(None)
            self._set_info("No reports with times in this dataset yet.")
            return
        start, end = source.stored_window()
        sel = (h.index(start) if start else 0, h.index(end) if end else len(h) - 1)
        self.hist.set_data(h, sel)
        if isinstance(source, LiveSource) and not source.ctl.paused:
            self._describe(*sel, source.reset())
        else:
            self._apply(*sel)

    def _recount_live(self):
        """Keep a running live map's bars current (unless a window is shown)."""
        s = self.source
        if isinstance(s, LiveSource) and not s.ctl.paused and self.isVisible():
            sel_all = self.hourly is None or self.hist.sel == (0, len(self.hourly) - 1)
            self._load(s)
            if not sel_all and self.hourly:
                self.hist.set_selection(*self.hist.sel)

    # --- range ------------------------------------------------------------
    def _dragging(self, i0, i1):
        self._describe(i0, i1)
        if not self.live.isActive():
            self.live.start(LIVE_MS)

    def _released(self, i0, i1):
        self.live.stop()
        self._apply(i0, i1)

    def _describe(self, i0, i1, extra=None):
        h = self.hourly
        if not h:
            return
        start, _ = h.span(i0, i1)
        lines = [f"<b>{start:{_FMT}} – {h.hour(i1 + 1):{_FMT}} UTC</b> ({i1 - i0 + 1} h): "
                 f"{h.reports(i0, i1):,} of {h.reports():,} report(s)"]
        lines += extra or []
        lines.append("<small>Drag across the chart to choose hours; double-click for all.</small>")
        self._set_info("<br>".join(lines))

    def _apply(self, i0, i1):
        s, h = self.source, self.hourly
        if s is None or not h:
            return
        if not hasattr(s, "data"):       # not loaded yet: load, then the chart is redrawn
            self._load(s)
            return
        whole = (i0, i1) == (0, len(h) - 1)
        start, end = (None, None) if whole else h.span(i0, i1)
        try:
            extra = s.reset() if whole else s.apply(start, end)
        except SourceError as exc:
            self._set_info(str(exc))
            return
        label = None if whole else timeline.window_label(h.hour(i0), h.hour(i1 + 1))
        for lyr in s.layers():
            base = lyr.customProperty(BASE_NAME) or lyr.name().split(" · ")[0]
            lyr.setCustomProperty(BASE_NAME, base)
            lyr.setName(base if label is None else f"{base} · {label}")
        self._describe(i0, i1, extra)

    def _shift(self, direction):
        if not self.hourly:
            return
        i0, i1 = self.hist.sel
        n, width = len(self.hourly), i1 - i0 + 1
        i0 = max(0, min(n - width, i0 + direction * width))
        self.hist.set_selection(i0, i0 + width - 1)
        self._apply(*self.hist.sel)

    def _select_all(self):
        if self.hourly:
            self.hist.set_selection(0, len(self.hourly) - 1)
            self._apply(*self.hist.sel)

    def _set_info(self, html):
        self.info.setText(html)

    def shutdown(self):
        self.recount.stop()
        project = QgsProject.instance()
        for sig, slot in ((project.layersAdded, self._refresh_soon), (project.layersRemoved, self._refresh_soon)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        if self.task is not None:
            self.task.cancel()
