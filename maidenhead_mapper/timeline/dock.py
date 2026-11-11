"""Activity timeline: reports per hour for a PSK station layer, and a time
range that re-summarizes the layer's markers."""

import json
import os
import warnings

from qgis.core import QgsApplication, QgsFeature, QgsProject, QgsTask, QgsVectorDataProvider
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

from ..algorithms.import_psk import LAYER_PROPERTY, station_feature, station_fields
from ..core import psk, stations, timeline
from .histogram import Histogram

_FMT = "%Y-%m-%d %H:00"
LIVE_MS = 180      # while dragging, rebuild the layer at most this often


def _signature(folder):
    """Changes whenever a psk_*.json file is added, removed or rewritten."""
    try:
        names = psk.iter_psk_files(folder)
        return tuple((n, os.path.getmtime(os.path.join(folder, n))) for n in names)
    except OSError:
        return None


def _load(task, folder, mycall):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        reports = psk.load_reports(folder, progress=lambda i, n: task.setProgress(100.0 * i / max(n, 1)))
    return psk.add_remote_station(reports, mycall)


class TimelineDock(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Activity timeline", parent)
        self.setObjectName("MaidenheadMapperTimelineDock")
        self.cache = {}       # (folder, mycall) -> (signature, reports)
        self.task = None
        self.layer = None
        self.opts = None
        self.reports = None
        self.hourly = None

        self.layers = QComboBox()
        self.layers.currentIndexChanged.connect(self._layer_changed)
        self.hist = Histogram()
        self.hist.dragging.connect(self._dragging)
        self.hist.selected.connect(self._released)
        self.live = QTimer(self)  # throttles rebuilds while the mouse moves
        self.live.setSingleShot(True)
        self.live.timeout.connect(lambda: self._apply(*self.hist.sel))
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
        lay.addWidget(self.layers)
        lay.addWidget(self.hist, 1)
        lay.addLayout(buttons)
        lay.addWidget(self.info)
        w = QWidget()
        w.setLayout(lay)
        self.setWidget(w)

        project = QgsProject.instance()
        project.layersAdded.connect(self._refresh_soon)
        project.layersRemoved.connect(self._refresh_soon)
        self._refresh_layers()

    def _refresh_soon(self, *_):
        # Processing adds an output layer before its post-processor tags it,
        # so look once the current event has finished.
        QTimer.singleShot(0, self._refresh_layers)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._refresh_layers()

    # --- layer list -------------------------------------------------------
    def _refresh_layers(self, *_):
        current = self.layers.currentData()
        self.layers.blockSignals(True)
        self.layers.clear()
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.customProperty(LAYER_PROPERTY):
                self.layers.addItem(lyr.name(), lyr.id())
        idx = self.layers.findData(current)
        self.layers.setCurrentIndex(idx if idx >= 0 else 0 if self.layers.count() else -1)
        self.layers.blockSignals(False)
        if self.layers.currentData() != current or self.layer is None:
            self._layer_changed()

    def _layer_changed(self, *_):
        self.layer = QgsProject.instance().mapLayer(self.layers.currentData() or "")
        self.reports = self.hourly = None
        self.hist.set_data(None)
        if self.layer is None:
            self.opts = None
            self._set_info("Run <i>Import PSK Reporter reports</i> or <i>Fetch from PSK Reporter</i>; "
                           "their station layers appear here.")
            return
        try:
            self.opts = json.loads(self.layer.customProperty(LAYER_PROPERTY))
        except (TypeError, ValueError):
            self.opts = None
            self._set_info("This layer's import settings are unreadable.")
            return
        node = QgsProject.instance().layerTreeRoot().findLayer(self.layer.id())
        if node is not None and not node.isVisible():
            node.setItemVisibilityChecked(True)
            parent = node.parent()
            while parent is not None and hasattr(parent, "setItemVisibilityChecked"):
                parent.setItemVisibilityChecked(True)
                parent = parent.parent()
        self.opts.setdefault("name", self._base_name(self.layer.name()))
        folder, mycall = self.opts["folder"], self.opts["mycall"]
        sig = _signature(folder)
        if sig is None:
            self._set_info(f"Folder not found: {folder}")
            return
        hit = self.cache.get((folder, mycall))
        if hit and hit[0] == sig:
            self._loaded(hit[1])
            return
        if self.task is not None:
            return  # a load is running; _loaded re-checks the layer
        self._set_info(f"Reading {len(sig)} file(s) from {folder}…")
        self.task = QgsTask.fromFunction(f"Maidenhead Mapper: reading {folder}", _load, folder, mycall,
                                         on_finished=lambda exc, result=None: self._task_done(exc, result, folder,
                                                                                              mycall, sig))
        QgsApplication.taskManager().addTask(self.task)

    def _task_done(self, exc, reports, folder, mycall, sig):
        self.task = None
        if exc is not None or reports is None:
            self._set_info(f"Could not read {folder}: {exc}")
            return
        self.cache = {(folder, mycall): (sig, reports)}  # keep one folder in memory
        self._layer_changed()  # the selected layer may have changed meanwhile

    def _loaded(self, reports):
        self.reports = reports
        kept, _ = stations.select(reports, self.opts["directions"], self.opts["bands"])
        self.hourly = timeline.hourly(kept)
        if not len(self.hourly):
            self.hist.set_data(None)
            self._set_info("No usable reports in this layer's folder.")
            return
        h = self.hourly
        start, end = (psk.report_time(self.opts.get(k)) for k in ("start", "end"))
        sel = (h.index(start) if start else 0, h.index(end) if end else len(h) - 1)
        self.hist.set_data(h, sel)
        self._apply(*sel)  # also fills in the window summary

    # --- range ------------------------------------------------------------
    @staticmethod
    def _base_name(name):
        return name.split(" · ")[0]

    def _dragging(self, i0, i1):
        self._describe(i0, i1)
        if not self.live.isActive():
            self.live.start(LIVE_MS)

    def _released(self, i0, i1):
        self.live.stop()
        self._apply(i0, i1)

    def _describe(self, i0, i1, window=None):
        h = self.hourly
        if not h:
            return
        start, _ = h.span(i0, i1)
        hours = i1 - i0 + 1
        lines = [f"<b>{start:{_FMT}} – {h.hour(i1 + 1):{_FMT}} UTC</b> ({hours} h): "
                 f"{h.reports(i0, i1):,} of {h.reports():,} report(s)"]
        if window is not None:
            w = window
            lines.append(f"<b>{w.heard + w.heard_me:,}</b> station(s): "
                         f"<span style='color:#1f77b4'>{w.heard:,} heard</span>, "
                         f"<span style='color:#ff7f0e'>{w.heard_me:,} heard me</span>")
            if w.bands:
                lines.append("Bands: " + ", ".join(f"{b} {n:,}" for b, n in w.bands))
            if w.farthest is not None:
                f = w.farthest
                lines.append(f"Farthest: {f.call} {f.grid}, {f.distance_km:,.0f} km "
                             f"({'heard me' if f.direction == 'heard_me' else 'heard'})")
            if w.strongest is not None:
                st = w.strongest
                lines.append(f"Strongest: {st.call} {st.snr_best:+d} dB")
        lines.append("<small>Drag across the chart to choose hours; double-click for all.</small>")
        self._set_info("<br>".join(lines))

    def _apply(self, i0, i1):
        if self.layer is None or self.reports is None:
            return
        h = self.hourly
        whole = (i0, i1) == (0, len(h) - 1)
        start, end = (None, None) if whole else h.span(i0, i1)
        o = self.opts
        summary, _ = stations.summarize(self.reports, precision=o["precision"], my_grid=o["my_grid"],
                                        directions=o["directions"], bands=o["bands"], start=start, end=end)
        if not self._rewrite(summary):
            return
        o["start"] = None if start is None else int(start.timestamp())
        o["end"] = None if end is None else int(end.timestamp())
        self.layer.setCustomProperty(LAYER_PROPERTY, json.dumps(o))
        base = o.get("name") or self._base_name(self.layer.name())
        self.layer.setName(base if whole else f"{base} · {timeline.window_label(h.hour(i0), h.hour(i1 + 1))}")
        self.layers.setItemText(self.layers.currentIndex(), self.layer.name())
        self._describe(i0, i1, window=timeline.summarize_window(summary))

    def _rewrite(self, summary):
        lyr = self.layer
        if lyr.isEditable():
            self._set_info("Stop editing this layer first; the timeline replaces its features.")
            return False
        pr = lyr.dataProvider()
        need = QgsVectorDataProvider.AddFeatures | QgsVectorDataProvider.DeleteFeatures
        if (int(pr.capabilities()) & int(need)) != int(need):
            self._set_info("This layer's format can't be rewritten; import into a temporary layer or GeoPackage.")
            return False
        src_fields, dst_fields = station_fields(), lyr.fields()
        names = [f.name() for f in src_fields if dst_fields.indexOf(f.name()) >= 0]
        feats = []
        for s in summary:
            src = station_feature(src_fields, s)
            f = QgsFeature(dst_fields)
            f.setGeometry(src.geometry())
            for n in names:
                f.setAttribute(n, src[n])
            feats.append(f)
        # An edit session (not the provider directly) so attribute tables,
        # statistics and labels hear about the change.
        lyr.startEditing()
        lyr.deleteFeatures(list(lyr.allFeatureIds()))
        lyr.addFeatures(feats)
        if not lyr.commitChanges():
            errors = "; ".join(lyr.commitErrors())
            lyr.rollBack()
            self._set_info(f"Could not update the layer: {errors}")
            return False
        lyr.updateExtents()
        lyr.triggerRepaint()
        return True

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
        project = QgsProject.instance()
        for sig, slot in ((project.layersAdded, self._refresh_soon), (project.layersRemoved, self._refresh_soon)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        if self.task is not None:
            self.task.cancel()
