"""QGIS-side smoke test, run with QGIS's own Python (no pytest needed there):

    "C:\\Program Files\\QGIS 3.44.2\\bin\\python-qgis.bat" tests\\qgis_smoke.py

or through pytest with MM_QGIS_PYTHON set to that launcher (tests/test_qgis_smoke.py).
Checks that the plugin imports, every Processing algorithm builds, and the
ALL.TXT tools, corridor summaries, station balance, Propagation over time
(algorithm, temporal layers and panel), the research export and the antenna
A/B comparison with a reference pattern work on a
small synthetic ALL.TXT.
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
    import maidenhead_mapper
    import maidenhead_mapper.plugin  # noqa: F401  (imports every menu module lazily used)
    pkg = os.path.dirname(os.path.abspath(maidenhead_mapper.__file__))
    assert pkg == os.path.join(os.path.dirname(HERE), "maidenhead_mapper"), "imported from " + pkg
    print("package:", pkg)
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
        assert len(algs) >= 14, len(algs)
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
        out["corr_time"] = corr
    check("corridors over time", over_time)

    def voacap_paths():
        from qgis.core import QgsProcessingException
        from maidenhead_mapper.algorithms import voacap_alg
        from maidenhead_mapper.core import voacap as vc
        voacap_alg.cache_dir = lambda: os.path.join(tmp, "voacap")    # keep the user's cache out of it
        try:
            inst = vc.find_install(os.environ.get("MM_VOACAP_DIR", ""))
        except vc.NotInstalled:
            inst = None
        params = {"SSN": 120, "OUTPUT": "memory:"}
        # a folder without VOACAP always fails with the clear message
        empty = os.path.join(tmp, "no_voacap")
        os.makedirs(empty, exist_ok=True)
        try:
            processing.run("maidenhead:voacap_paths", dict(params, INPUT=out["ex"], FOLDER=empty), context=ctx)
            raise AssertionError("ran without VOACAP")
        except QgsProcessingException as exc:
            assert "VOACAP was not found" in str(exc) and "its.ntia.gov" in str(exc), str(exc)
        if inst is None:
            print("     (VOACAP not installed: only the error path was checked)")
            return
        before = set(os.listdir(inst.run_dir)) if inst.kind == "windows" else set()
        for name, src in (("exchanges", out["ex"]), ("corridors over time", out["corr_time"])):
            r = processing.run("maidenhead:voacap_paths", dict(params, INPUT=src), context=ctx)
            lyr = layer(r["OUTPUT"])
            rows = list(lyr.getFeatures())
            assert len(rows) == src.featureCount(), name
            ok = [f for f in rows if f["voacap_ok"] == 1]
            print(f"     {name}: {len(ok)}/{len(rows)} predicted;",
                  [(f["voacap_mode"], f["voacap_snr_db"], f["snr_gap_db"], f["voacap_note"]) for f in rows])
            assert ok, [f["voacap_note"] for f in rows]
            assert all(f["voacap_snr_db"] == round(f["voacap_snr_hz"] - 33.98, 1) or
                       abs(f["voacap_snr_db"] - (f["voacap_snr_hz"] - 33.98)) < 0.06 for f in ok)
            lyr.setRenderer(voacap_alg.gap_renderer())
            lyr.setLabeling(voacap_alg.gap_labels())
        if inst.kind == "windows":
            assert set(os.listdir(inst.run_dir)) == before, "files left in the VOACAP run folder"
    check("VOACAP paths", voacap_paths)

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

    def timeline_refresh():
        # Another tool adding a layer makes the Activity timeline rediscover its
        # sources; dragging the chart afterwards must still work (it used to
        # fail with "'LogSource' object has no attribute 'data'").
        from maidenhead_mapper.core import alltxt, timeline as tl
        from maidenhead_mapper.timeline import dock as tdock, sources as tsrc
        log = alltxt.to_log(alltxt.read([path]), "N0CALL", "CN85")

        class Fake(tsrc.Source):
            key, name, sig, applied = "log:fake", "fake", None, []

            def signature(self):
                return Fake.sig          # None at first: the dock doesn't start a load task

            def load(self, task=None):
                return log

            def count(self, data):
                return tl.hourly_from_log(data, "N0CALL")

            def apply(self, start, end):
                Fake.applied.append(len(self.data))
                return []

        real_discover = tdock.discover
        tdock.discover = lambda: [Fake()]
        try:
            d = tdock.TimelineDock(None)
            Fake.sig = ("x",)
            d._loaded(d.source, log)
            assert d.hourly and len(d.hourly) >= 2, d.hourly
            first = d.source
            d._refresh()                         # e.g. a VOACAP output layer was added
            assert d.source is not first
            d._released(0, 0)                    # drag one hour
            assert Fake.applied and Fake.applied[-1] == len(log), Fake.applied
            d.deleteLater()
        finally:
            tdock.discover = real_discover
    check("activity timeline after a layer is added", timeline_refresh)

    def research_export():
        import csv
        import json
        from maidenhead_mapper.core import research_export as rx
        dest = os.path.join(tmp, "research.csv")
        r = processing.run("maidenhead:research_export", {"FILES": [path], "MYCALL": "N0CALL", "MYGRID": "CN85",
                                                           "OUTPUT": dest}, context=ctx)
        assert r["OUTPUT"] == dest and r["ROWS"] == 17, r
        with open(dest, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 17 and list(rows[0]) == rx.COLUMNS, list(rows[0])
        kinds = [x["report_kind"] for x in rows]
        assert (kinds.count("exchange_report"), kinds.count("my_decode")) == (5, 12), kinds
        with open(r["SCHEMA"], encoding="utf-8") as f:
            doc = json.load(f)
        assert r["SCHEMA"] == os.path.join(tmp, "research.schema.json")
        assert doc["row_counts"]["total"] == 17 and doc["pseudonymised"] is False and doc["plugin_version"]
        dest2 = os.path.join(tmp, "research_20m.csv")
        r = processing.run("maidenhead:research_export", {"FILES": [path], "MYCALL": "N0CALL", "MYGRID": "CN85",
                                                           "BAND": "20m", "PSEUDONYMISE": True, "SALT": "x",
                                                           "OUTPUT": dest2}, context=ctx)
        assert r["ROWS"] == 7, r
        with open(dest2, encoding="utf-8") as f:
            text = f.read()
        assert "K1ABC" not in text and "N0CALL" not in text and "FN31" in text
    check("research export", research_export)

    def antenna_reference():
        # B hears every Rx line 3 dB weaker and misses the last one; B is a flat 2 dBi (synthetic) reference
        path_b = os.path.join(tmp, "ALL_B.TXT")
        rx = [ln for ln in sample.lines() if " Rx " in ln]
        with open(path_b, "w", encoding="utf-8", newline="\n") as f:
            for ln in rx[:-1]:
                head, snr, rest = ln[:31], int(ln[31:37]), ln[37:]
                f.write(f"{head}{snr - 3:6d}{rest}\n")
        pat = os.path.join(tmp, "reference.csv")
        with open(pat, "w", encoding="utf-8") as f:
            f.write("azimuth_deg,elevation_deg,gain_dbi\n")
            f.write("".join(f"{a},{e},2.0\n" for a in range(0, 360, 10) for e in (0, 30, 60, 90)))
        params = {"FILES_A": [path], "FILES_B": [path_b], "MYGRID": "DN06", "SOURCE": 2,
                  "REFERENCE_PATTERN": pat, "REFERENCE_SIDE": 1, "ORIENTATION_DEG": 0,
                  "SECTORS": "memory:", "STATIONS": "memory:", "TABLE": "memory:", "DECODES": "memory:"}
        r = processing.run("maidenhead:antenna_compare", params, context=ctx)
        table = {(f["by"], f["bin"]): f for f in layer(r["TABLE"]).getFeatures()}
        row = table[("all", "all")]
        assert (row["n_both"], row["only_a"], row["only_b"]) == (11, 1, 0), (row["n_both"], row["only_a"])
        assert row["abs_dbi"] == 5.0 and row["ref_dbi"] == 2.0 and row["abs_n"] == 11, row.attributes()
        dec = list(layer(r["DECODES"]).getFeatures())
        pairs = [f for f in dec if f["diff_db"] == 3]
        assert len(pairs) == 11 and len(dec) == 12 and all(f["abs_gain_dbi"] == 5.0 for f in pairs)
        assert "abs_dbi" in layer(r["SECTORS"]).fields().names()
        # without a pattern the outputs keep their old fields
        params.update({"REFERENCE_PATTERN": None, "DECODES": None})
        r = processing.run("maidenhead:antenna_compare", params, context=ctx)
        assert "abs_dbi" not in layer(r["TABLE"]).fields().names()
        # a bad pattern file gives a clear error
        with open(pat, "w", encoding="utf-8") as f:
            f.write("azimuth_deg,gain_dbi\n0,1\n")
        params["REFERENCE_PATTERN"] = pat
        try:
            processing.run("maidenhead:antenna_compare", params, context=ctx)
        except Exception as exc:          # QgsProcessingException
            assert "elevation_deg" in str(exc), exc
        else:
            raise AssertionError("bad pattern accepted")
    check("antenna A/B with a reference pattern", antenna_reference)


try:
    main()
except Exception:
    failures.append("setup")
    traceback.print_exc()
app.exitQgis()
print("FAILED: " + ", ".join(failures) if failures else "OK")
sys.exit(1 if failures else 0)
