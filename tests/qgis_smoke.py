"""QGIS-side smoke test, run with QGIS's own Python (no pytest needed there):

    "C:\\Program Files\\QGIS 3.44.2\\bin\\python-qgis.bat" tests\\qgis_smoke.py

or through pytest with MM_QGIS_PYTHON set to that launcher (tests/test_qgis_smoke.py).
Checks that the plugin imports, every Processing algorithm builds, and the
ALL.TXT tools, corridor summaries, station balance and Propagation over time
(algorithm, temporal layers and panel) work on a small synthetic ALL.TXT.
Exits 0 and prints OK on success.
"""

import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsDateTimeRange,
    QgsFeatureRequest,
    QgsProcessingContext,
    QgsProcessingUtils,
    QgsProject,
    QgsVectorLayerTemporalContext,
)
from qgis.PyQt.QtCore import QDateTime, Qt  # noqa: E402

app = QgsApplication([], True)
app.initQgis()
sys.path.append(os.path.join(QgsApplication.prefixPath(), "python", "plugins"))

failures = []


def check(name, fn):
    try:
        fn()
        print("ok  ", name)
    except Exception:
        failures.append(name)
        print("FAIL", name)
        traceback.print_exc()


def main():
    import processing
    from processing.core.Processing import Processing
    Processing.initialize()
    import maidenhead_mapper.plugin  # noqa: F401  (imports every menu module lazily used)
    import maidenhead_mapper.timeline.dock  # noqa: F401
    import maidenhead_mapper.live.dock  # noqa: F401
    from maidenhead_mapper.algorithms import corridors_time_alg as cta
    from maidenhead_mapper.provider import MaidenheadProvider
    from maidenhead_mapper.timeline import propagation_dock
    import sample_alltxt as sample

    tmp = tempfile.mkdtemp()
    cache = os.path.join(tmp, "index")
    cta.index_cache_dir = lambda: cache          # keep the user's cache out of it
    path = sample.write(os.path.join(tmp, "ALL.TXT"))

    provider = MaidenheadProvider()
    reg = QgsApplication.processingRegistry()
    reg.addProvider(provider)
    ctx = QgsProcessingContext()
    ctx.setProject(QgsProject.instance())

    def layer(v):
        return v if not isinstance(v, str) else QgsProcessingUtils.mapLayerFromString(v, ctx)

    def algorithms():
        algs = provider.algorithms()
        assert len(algs) >= 13, len(algs)
        for a in algs:
            inst = reg.createAlgorithmById(a.id())
            assert inst is not None, a.id()
            assert inst.parameterDefinitions(), a.id()
            assert inst.shortHelpString(), a.id()
    check("every algorithm builds", algorithms)

    out = {}

    def import_alltxt():
        r = processing.run("maidenhead:import_alltxt", {"FILES": [path], "STATIONS": "memory:",
                                                        "EXCHANGES": "memory:"}, context=ctx)
        out["ex"] = layer(r["EXCHANGES"])
        assert layer(r["STATIONS"]).featureCount() == 4          # K1ABC, W4XYZ, JA1AAA, VK2BBB
        assert out["ex"].featureCount() == 3                     # W9NOG has no grid
    check("import ALL.TXT", import_alltxt)

    def summarize():
        r = processing.run("maidenhead:summarize_exchanges", {"INPUT": [out["ex"]], "SIZE": 0, "MIN_N": 1,
                                                               "OUTPUT": "memory:"}, context=ctx)
        lyr = layer(r["OUTPUT"])
        pairs = {(f["cell_a"], f["cell_b"]) for f in lyr.getFeatures()}
        assert pairs == {("EM", "FN"), ("FN", "PM"), ("EM", "QF")}, pairs
    check("summarize exchanges", summarize)

    def balance():
        r = processing.run("maidenhead:station_balance", {"INPUT": [out["ex"]], "MYCALL": "N0CALL", "PER_BAND": False,
                                                           "OUTPUT": "memory:"}, context=ctx)
        rows = {f["call"]: f for f in layer(r["OUTPUT"]).getFeatures()}
        assert rows["K1ABC"]["partners"] == 2, rows["K1ABC"]["partners"]
        assert rows["K1ABC"]["farthest"] == "JA1AAA"
    check("station balance", balance)

    def over_time():
        r = processing.run("maidenhead:corridors_over_time", {"FILES": [path], "BIN": 0, "SIZE": 0,
                                                               "OUTPUT": "memory:", "CELLS": "memory:"},
                           context=ctx)
        corr, cells = layer(r["OUTPUT"]), layer(r["CELLS"])
        assert corr.featureCount() == 3 and cells.featureCount() == 6, (corr.featureCount(), cells.featureCount())
        seen = sorted(f["seen"] for f in corr.getFeatures())
        assert seen == ["both ways", "both ways", "no reports"], seen
        assert os.listdir(cache), "index not cached"
        for lyr in (corr, cells):
            cta.make_temporal(lyr)
            tp = lyr.temporalProperties()
            assert tp.isActive() and tp.startField() == "bin_start"
            q = QDateTime(2026, 11, 11, 22, 0, 0, 0, Qt.TimeSpec.UTC)
            tc = QgsVectorLayerTemporalContext()
            tc.setLayer(lyr)
            expr = tp.createFilterString(tc, QgsDateTimeRange(q, q.addSecs(3600), True, False))
            hours = {f["bin_start"].toString("HH") for f in lyr.getFeatures(QgsFeatureRequest().setFilterExpression(expr))}
            assert hours == {"22"}, (lyr.name(), hours)
        corr.setRenderer(cta.corridor_renderer())
        cells.setRenderer(cta.cell_renderer())
        r = processing.run("maidenhead:corridors_over_time", {"FILES": [path], "BIN": 0, "BAND": "20m",
                                                               "OUTPUT": "memory:", "CELLS": None}, context=ctx)
        assert layer(r["OUTPUT"]).featureCount() == 1
    check("corridors over time", over_time)

    def panel():
        from maidenhead_mapper.core import alltxt_index
        propagation_dock._cache_dir = lambda: cache
        d = propagation_dock.PropagationDock(None)
        d.files.setFilePath(path)
        assert not d.build_btn.isEnabled()
        d._indexed(None, alltxt_index.index([path], cache))
        assert d.build_btn.isEnabled()
        bands = [d.band.itemData(i) for i in range(d.band.count())]
        assert bands == ["", "15m", "20m"], bands
        d.hist.set_selection(1, 1)
        d._from_hist(1, 1)
        assert d.start.dateTime().toString("HH:mm") == "22:00" and d.end.dateTime().toString("HH:mm") == "23:00"
        d.start.setDateTime(QDateTime(2026, 11, 11, 21, 0, 0, 0, Qt.TimeSpec.UTC))
        assert d.hist.sel == (0, 1), d.hist.sel
        d.deleteLater()
    check("propagation panel", panel)


try:
    main()
except Exception:
    failures.append("setup")
    traceback.print_exc()
app.exitQgis()
print("FAILED: " + ", ".join(failures) if failures else "OK")
sys.exit(1 if failures else 0)
