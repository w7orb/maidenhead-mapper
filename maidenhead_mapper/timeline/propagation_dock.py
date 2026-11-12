"""Propagation over time: index ALL.TXT files, pick dates on a chart, and build
time-enabled corridor layers that the Temporal Controller (time slider) plays."""

import os

from qgis.core import (
    QgsApplication,
    QgsProcessing,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingOutputLayerDefinition,
    QgsProject,
    QgsSettings,
    QgsTask,
)
from qgis.gui import QgsFileWidget
from qgis.PyQt.QtCore import QDateTime, Qt
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDockWidget,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core import alltxt_index
from .histogram import Histogram

FILES_KEY = "maidenhead_mapper/propagation_files"
ALG_ID = "maidenhead:corridors_over_time"
_FMT = "yyyy-MM-dd HH:mm"


def _default_alltxt():
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "WSJT-X", "ALL.TXT")
    return p if os.path.isfile(p) else ""


def _utc(dt):
    q = QDateTime.fromSecsSinceEpoch(int(dt.timestamp()), Qt.TimeSpec.UTC)
    return q


def _cache_dir():
    from ..algorithms.corridors_time_alg import index_cache_dir
    return index_cache_dir()


class PropagationDock(QDockWidget):
    alg_id = ALG_ID

    def __init__(self, parent=None):
        super().__init__("Propagation over time", parent)
        self.setObjectName("MaidenheadMapperPropagationDock")
        self.index = None
        self.hourly = None
        self.task = None
        self.run = None         # (task, context, feedback, algorithm) while building

        self.files = QgsFileWidget()
        self.files.setStorageMode(QgsFileWidget.StorageMode.GetMultipleFiles)
        self.files.setFilter("WSJT-X ALL.TXT (*.TXT *.txt);;All files (*)")
        saved = QgsSettings().value(FILES_KEY, "", type=str)
        self.files.setFilePath(saved or _default_alltxt())
        self.files.fileChanged.connect(lambda *_: self._invalidate())
        self.index_btn = QPushButton("Index")
        self.index_btn.setToolTip("Scan the files for their dates and bands (cached; only new lines are read again)")
        self.index_btn.clicked.connect(self._index)
        top = QHBoxLayout()
        top.addWidget(self.files, 1)
        top.addWidget(self.index_btn)

        self.summary = QLabel("Choose ALL.TXT file(s) and press Index.")
        self.summary.setWordWrap(True)
        self.hist = Histogram()
        self.hist.labels = ("decodes", None)
        self.hist.dragging.connect(self._from_hist)
        self.hist.selected.connect(self._from_hist)

        self.start = QDateTimeEdit()
        self.end = QDateTimeEdit()
        for e in (self.start, self.end):
            e.setTimeSpec(Qt.TimeSpec.UTC)
            e.setDisplayFormat(_FMT)
            e.setCalendarPopup(True)
            e.dateTimeChanged.connect(self._to_hist)
        self.bin = QComboBox()
        self.bin.addItems(["Hour", "3 hours", "Day"])
        self.size = QComboBox()
        self.size.addItems(["Field (EM)", "2×2 squares", "Square (EM78)"])
        self.band = QComboBox()
        self.min_n = QSpinBox()
        self.min_n.setRange(1, 1000)
        self.min_n.setValue(1)
        self.min_n.setToolTip("Hide corridors with fewer exchanges in a bin")

        form = QFormLayout()
        form.addRow("From (UTC)", self.start)
        form.addRow("To (UTC)", self.end)
        form.addRow("Group by", self.bin)
        form.addRow("Cell size", self.size)
        form.addRow("Band", self.band)
        form.addRow("Least exchanges", self.min_n)

        self.build_btn = QPushButton("Build time-slider layers")
        self.build_btn.clicked.connect(self._build)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.status = QLabel()
        self.status.setWordWrap(True)

        lay = QVBoxLayout()
        lay.addLayout(top)
        lay.addWidget(self.summary)
        lay.addWidget(self.hist, 1)
        lay.addLayout(form)
        lay.addWidget(self.build_btn)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        w = QWidget()
        w.setLayout(lay)
        self.setWidget(w)
        self._enable()

    # --- state --------------------------------------------------------------
    def _paths(self):
        return [p for p in QgsFileWidget.splitFilePaths(self.files.filePath()) if p]

    def _enable(self):
        busy = self.task is not None or self.run is not None
        ready = self.index is not None and self.index.first_time is not None
        self.index_btn.setEnabled(not busy)
        self.build_btn.setEnabled(ready and not busy)
        for wdg in (self.start, self.end, self.bin, self.size, self.band, self.min_n):
            wdg.setEnabled(ready)

    def _invalidate(self):
        self.index = None
        self.hist.set_data(None)
        self.summary.setText("Press Index to scan the file(s).")
        self._enable()

    # --- indexing -------------------------------------------------------------
    def _index(self):
        paths = self._paths()
        missing = [p for p in paths if not os.path.isfile(p)]
        if not paths or missing:
            self.summary.setText("File not found: " + (", ".join(missing) or "(none chosen)"))
            return
        QgsSettings().setValue(FILES_KEY, self.files.filePath())
        cache = _cache_dir()
        self.summary.setText("Indexing…")
        self.progress.setValue(0)
        self.progress.show()

        def work(task):
            return alltxt_index.index(paths, cache, task.setProgress, task.isCanceled)

        self.task = QgsTask.fromFunction("Index ALL.TXT", work, on_finished=self._indexed)
        self.task.progressChanged.connect(lambda p: self.progress.setValue(int(p)))
        QgsApplication.taskManager().addTask(self.task)
        self._enable()

    def _indexed(self, exc, result=None):
        self.task = None
        self.progress.hide()
        if exc is not None or result is None:
            self.summary.setText(f"Indexing failed: {exc}" if exc else "Indexing cancelled.")
            self._enable()
            return
        self.index = result
        self.summary.setText(result.summary())
        self.hourly = result.hourly()
        self.band.clear()
        self.band.addItem("All bands", "")
        for b, n in sorted(result.bands().items(), key=lambda kv: -kv[1]):
            self.band.addItem(f"{b}  ({n:,} decode{'' if n == 1 else 's'})", b)
        if self.hourly is not None and len(self.hourly):
            # default: the last day with data
            n = len(self.hourly)
            self.hist.set_data(self.hourly, (max(0, n - 24), n - 1))
            self._from_hist(*self.hist.sel)
        self._enable()

    # --- range sync -------------------------------------------------------------
    def _from_hist(self, i0, i1):
        if not self.hourly or not len(self.hourly):
            return
        s, e = self.hourly.hour(i0), self.hourly.hour(i1 + 1)
        for edit, t in ((self.start, s), (self.end, e)):
            edit.blockSignals(True)
            edit.setDateTime(_utc(t))
            edit.blockSignals(False)

    def _to_hist(self, *_):
        if not self.hourly or not len(self.hourly):
            return
        from datetime import datetime, timedelta, timezone
        s = datetime.fromtimestamp(self.start.dateTime().toSecsSinceEpoch(), timezone.utc)
        e = datetime.fromtimestamp(self.end.dateTime().toSecsSinceEpoch(), timezone.utc)
        # 'To' is exclusive: select up to the hour holding its last instant
        last = e - timedelta(microseconds=1) if e > s else e
        self.hist.set_selection(self.hourly.index(s), self.hourly.index(last))

    # --- building ---------------------------------------------------------------
    def _build(self):
        alg = QgsApplication.processingRegistry().createAlgorithmById(self.alg_id)
        if alg is None:
            self.status.setText(f"Processing algorithm {self.alg_id} not found.")
            return
        if self.end.dateTime() <= self.start.dateTime():
            self.status.setText("'To' must be after 'From'.")
            return
        params = {
            "FILES": self._paths(),
            "START": self.start.dateTime(),
            "END": self.end.dateTime(),
            "BIN": self.bin.currentIndex(),
            "SIZE": self.size.currentIndex(),
            "BAND": self.band.currentData() or "",
            "MIN_N": self.min_n.value(),
            # an output definition with a project, so the layers are loaded when done
            "OUTPUT": QgsProcessingOutputLayerDefinition(QgsProcessing.TEMPORARY_OUTPUT, QgsProject.instance()),
            "CELLS": QgsProcessingOutputLayerDefinition(QgsProcessing.TEMPORARY_OUTPUT, QgsProject.instance()),
        }
        context = QgsProcessingContext()
        context.setProject(QgsProject.instance())
        feedback = QgsProcessingFeedback()
        feedback.progressChanged.connect(lambda p: self.progress.setValue(int(p)))
        task = QgsProcessingAlgRunnerTask(alg, params, context, feedback)
        task.executed.connect(self._built)
        self.run = (task, context, feedback, alg, params)
        self.status.setText("Building…")
        self.progress.setValue(0)
        self.progress.show()
        QgsApplication.taskManager().addTask(task)
        self._enable()

    def _built(self, ok, results):
        task, context, feedback, alg, params = self.run
        self.run = None
        self.progress.hide()
        if not ok:
            self.status.setText("Failed: " + (feedback.textLog().strip().splitlines() or ["see the log"])[-1])
            self._enable()
            return
        try:
            from processing.gui.Postprocessing import handleAlgorithmResults
            handleAlgorithmResults(alg, context, feedback, params)
        except Exception as exc:   # loading falls back to a plain add
            self.status.setText(f"Built, but loading the layers failed: {exc}")
            self._enable()
            return
        lines = [ln for ln in feedback.textLog().splitlines() if ln.strip()]
        info = [ln for ln in lines if "corridor line" in ln or "exchange(s) in the window" in ln]
        self.status.setText("Done. Press ▶ in the Temporal Controller (or drag its slider).\n" + "\n".join(info))
        self._enable()

    def shutdown(self):
        if self.task is not None:
            self.task.cancel()
