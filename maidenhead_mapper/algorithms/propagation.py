"""Processing algorithms: sporadic-E candidates; skip distance by hour."""

import math

from qgis.core import (
    QgsFeatureRequest,
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsRendererCategory,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..core import es, hops, iono
from ..core import maidenhead as mh
from . import styling
from .import_psk import WGS84
from .paths import PathError, PathReader, my_grid_for
from .qt_compat import DATETIME, DOUBLE, INT, STRING, field, utc_qdatetime

ES_COLOURS = {es.HIGH: "#d62728", es.POSSIBLE: "#ff7f0e"}


def _es_renderer():
    cats = []
    for conf, colour in ES_COLOURS.items():
        sym = QgsMarkerSymbol.createSimple({"name": "diamond", "color": colour, "size": "3",
                                            "outline_color": "255,255,255,220", "outline_width": "0.25"})
        cats.append(QgsRendererCategory(conf, sym, {"high": "Sporadic-E: high confidence",
                                                    "possible": "Sporadic-E: possible"}[conf]))
    return QgsCategorizedSymbolRenderer("confidence", cats)


def _cloud_renderer():
    from qgis.core import QgsProperty, QgsSingleSymbolRenderer, QgsSymbolLayer
    sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": "214,39,40,70",
                                        "outline_color": "#d62728", "outline_width": "0.5"})
    sym.symbolLayer(0).setDataDefinedProperty(
        QgsSymbolLayer.PropertySize, QgsProperty.fromExpression('4 + 3 * sqrt("n_paths")'))
    return QgsSingleSymbolRenderer(sym)


def _labels(expr):
    s = QgsPalLayerSettings()
    s.fieldName = expr
    s.isExpression = True
    return QgsVectorLayerSimpleLabeling(s)


def _mean_point(points):
    """Centroid of lon/lat points on the sphere."""
    x = y = z = 0.0
    for lon, lat in points:
        lo, la = math.radians(lon), math.radians(lat)
        x += math.cos(la) * math.cos(lo)
        y += math.cos(la) * math.sin(lo)
        z += math.sin(la)
    return math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))


class SporadicEAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    MYGRID = "MYGRID"
    BANDS = "BANDS"
    USE_IONO = "USE_IONO"
    MAX_AGE = "MAX_AGE"
    RADIUS = "RADIUS"
    WINDOW = "WINDOW"
    OUTPUT = "OUTPUT"
    CLOUDS = "CLOUDS"

    def tr(self, text):
        return QCoreApplication.translate("SporadicEAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return SporadicEAlgorithm()

    def name(self):
        return "sporadic_e"

    def displayName(self):  # noqa: N802
        return self.tr("Find sporadic-E paths")

    def group(self):
        return self.tr("Ionosphere")

    def groupId(self):  # noqa: N802
        return "ionosphere"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Finds paths that sporadic-E most likely carried: one hop of 500–2,300 km on 12m, 10m, "
            "6m or higher that the F2 layer can't explain.\n\n"
            "• high: 6m and up (F2 rarely supports such short paths there), or the band is above "
            "the F2 MUF\n"
            "• possible: 12m/10m without ionosphere data\n\n"
            "F2 status comes from the layer itself if it has been through 'Check paths against "
            "the ionosphere', else (optionally) from KC2G's current data for recent paths.\n\n"
            "Each candidate is a point at the path midpoint (where it met the Es layer) with "
            "foes_min_mhz: the lowest Es critical frequency that returns that band at that range. "
            "Nearby candidates close in time are grouped into Es clouds.")

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Station or exchange layer"),
            [QgsProcessing.TypeVectorPoint, QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterString(
            self.MYGRID, self.tr("My grid (station layers; empty = from the layer or last used)"), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.BANDS, self.tr("Bands"), options=list(es.ES_BANDS), allowMultiple=True,
            defaultValue=list(range(len(es.ES_BANDS)))))
        self.addParameter(QgsProcessingParameterBoolean(
            self.USE_IONO, self.tr("Use KC2G ionosphere data for recent 12m/10m paths"), defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_AGE, self.tr("…only paths within this many hours of it"),
            QgsProcessingParameterNumber.Double, defaultValue=3.0, minValue=0.1, maxValue=48.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.RADIUS, self.tr("Cloud: midpoints within (km)"), QgsProcessingParameterNumber.Double,
            defaultValue=500.0, minValue=50.0, maxValue=2000.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.WINDOW, self.tr("Cloud: and within (minutes)"), QgsProcessingParameterNumber.Integer,
            defaultValue=60, minValue=5, maxValue=24 * 60))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Sporadic-E candidates"), QgsProcessing.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.CLOUDS, self.tr("Sporadic-E clouds"), QgsProcessing.TypeVectorPoint,
            optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        import datetime as _dt
        # Paths are read from grid fields, not geometry: don't reject odd geometries
        context.setInvalidGeometryCheck(QgsFeatureRequest.GeometryNoCheck)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        my_grid = my_grid_for(self.parameterAsVectorLayer(parameters, self.INPUT, context),
                              self.parameterAsString(parameters, self.MYGRID, context))
        try:
            reader = PathReader(source.fields(), my_grid)
        except PathError as exc:
            raise QgsProcessingException(self.tr(str(exc)))
        names = reader.names
        picked = self.parameterAsEnums(parameters, self.BANDS, context)
        bands = [es.ES_BANDS[i] for i in picked] if parameters.get(self.BANDS) not in (None, "", []) else list(es.ES_BANDS)
        has_check = "f2_status" in names and "bands_f2" in names

        cond = None
        if not has_check and self.parameterAsBool(parameters, self.USE_IONO, context):
            from .ionosphere import cached_time, fetch, load_conditions
            try:
                fetch(feedback)
                cond = load_conditions()
            except QgsProcessingException as exc:
                feedback.pushWarning(self.tr("No ionosphere data ({}); using band rules only").format(exc))
            except (OSError, ValueError):
                cond = None
            if cached_time() is None:
                cond = None
        max_age = self.parameterAsDouble(parameters, self.MAX_AGE, context) * 3600

        cands = []   # dict per candidate
        for f in source.getFeatures():
            if feedback.isCanceled():
                break
            p = reader.read(f)
            if not p["ok"]:
                continue
            dist = mh.distance_bearing(p["ga"], p["gb"])[0]
            if not es.MIN_KM <= dist <= es.max_single_hop_km():
                continue
            for band, freq in p["bands"]:
                if band not in bands:
                    continue
                status = None
                if has_check and f["f2_status"] not in (None, iono.STATUS_NO_DATA):
                    status = iono.STATUS_F2 if band in str(f["bands_f2"] or "").split(",") else iono.STATUS_ABOVE
                elif cond is not None and band not in es.VHF_BANDS and (
                        p["when"] is None or abs((p["when"] - cond.time).total_seconds()) <= max_age):
                    (x1, y1), (x2, y2) = mh.center(p["ga"]), mh.center(p["gb"])
                    status = iono.analyse_path(x1, y1, x2, y2, freq, cond).status
                conf = es.classify(dist, band, status, bands)
                if conf is None:
                    continue
                (x1, y1), (x2, y2) = mh.center(p["ga"]), mh.center(p["gb"])
                (mx, my), = hops.reflection_points(x1, y1, x2, y2, 1)
                cands.append({"fid": f.id(), "calls": p["calls"], "ga": p["ga"], "gb": p["gb"], "band": band,
                              "freq": freq, "dist": round(dist, 1), "takeoff": round(es.takeoff_deg(dist), 1),
                              "foes": round(es.foes_min_mhz(freq, dist), 2), "status": status or iono.STATUS_NO_DATA,
                              "conf": conf, "when": p["when"], "x": mx, "y": my})

        radius = self.parameterAsDouble(parameters, self.RADIUS, context)
        window = _dt.timedelta(minutes=self.parameterAsInt(parameters, self.WINDOW, context))
        labels = es.cluster([(c["x"], c["y"], c["when"]) for c in cands], radius, window)
        for c, k in zip(cands, labels):
            c["cluster"] = k
        n_high = sum(c["conf"] == es.HIGH for c in cands)
        feedback.pushInfo(self.tr("{} sporadic-E candidate(s): {} high confidence, {} possible").format(
            len(cands), n_high, len(cands) - n_high))

        fields = QgsFields()
        for name, kind in (("path_fid", INT), ("calls", STRING), ("grid_a", STRING), ("grid_b", STRING),
                           ("band", STRING), ("freq_mhz", DOUBLE), ("distance_km", DOUBLE), ("takeoff_es", DOUBLE),
                           ("foes_min_mhz", DOUBLE), ("f2_status", STRING), ("confidence", STRING),
                           ("cloud", INT), ("last_utc", DATETIME)):
            fields.append(field(name, kind, 0))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, QgsWkbTypes.Point, WGS84)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        for c in cands:
            out = QgsFeature(fields)
            out.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(c["x"], c["y"])))
            out.setAttributes([c["fid"], c["calls"], c["ga"], c["gb"], c["band"], c["freq"], c["dist"], c["takeoff"],
                               c["foes"], c["status"], c["conf"], c["cluster"],
                               utc_qdatetime(c["when"]) if c["when"] else None])
            sink.addFeature(out, QgsFeatureSink.FastInsert)
        results = {self.OUTPUT: dest}
        styling.attach_styler(context, dest, self.tr("Sporadic-E candidates"), styling.LayerStyler(_es_renderer))

        cfields = QgsFields()
        for name, kind in (("cloud", INT), ("n_paths", INT), ("bands", STRING), ("foes_max_mhz", DOUBLE),
                           ("high", INT), ("first_utc", DATETIME), ("last_utc", DATETIME)):
            cfields.append(field(name, kind, 0))
        csink, cdest = self.parameterAsSink(parameters, self.CLOUDS, context, cfields, QgsWkbTypes.Point, WGS84)
        if csink is not None:
            groups = {}
            for c in cands:
                groups.setdefault(c["cluster"], []).append(c)
            clouds = 0
            for k, cs in sorted(groups.items()):
                if len(cs) < 2:
                    continue
                clouds += 1
                lon, lat = _mean_point([(c["x"], c["y"]) for c in cs])
                times = [c["when"] for c in cs if c["when"]]
                band_set = sorted({c["band"] for c in cs}, key=es.ES_BANDS.index)
                out = QgsFeature(cfields)
                out.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon, lat)))
                out.setAttributes([k, len(cs), ",".join(band_set), max(c["foes"] for c in cs),
                                   sum(c["conf"] == es.HIGH for c in cs),
                                   utc_qdatetime(min(times)) if times else None,
                                   utc_qdatetime(max(times)) if times else None])
                csink.addFeature(out, QgsFeatureSink.FastInsert)
            feedback.pushInfo(self.tr("{} Es cloud(s) of 2 or more paths").format(clouds))
            results[self.CLOUDS] = cdest
            styling.attach_styler(context, cdest, self.tr("Sporadic-E clouds"), styling.LayerStyler(
                _cloud_renderer, lambda: _labels("'Es ×' || \"n_paths\" || '  ≥' || round(\"foes_max_mhz\", 1) || ' MHz'")))
        return results


class SkipDistanceAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate("SkipDistanceAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return SkipDistanceAlgorithm()

    def name(self):
        return "skip_distance"

    def displayName(self):  # noqa: N802
        return self.tr("Skip distance by hour")

    def group(self):
        return self.tr("Ionosphere")

    def groupId(self):  # noqa: N802
        return "ionosphere"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "For a dataset (a PSK Reporter layer from Fetch/Import, an ALL.TXT import or a saved live "
            "session), a table per band and UTC hour of how far contacts reached:\n"
            "• skip_km: the skip edge, the 10th percentile of sky-wave distances (over 300 km); "
            "needs 20+ contacts in the hour\n"
            "• median_km, p90_km, and n_local (under 300 km: ground wave or local)\n"
            "• fof2_est_mhz: foF2 implied by the skip edge (secant law, F2 at 300 km), only when "
            "the edge is within one hop. An indicator for comparing hours, not a measurement.\n"
            "• kc2g_fof2_mhz: KC2G's foF2 at your location, when its cached data is within 3 hours.\n\n"
            "Distances are from your grid. Plot the table with the DataPlotly plugin, or join it to "
            "a layer.")

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Layer from Fetch/Import PSK Reporter, Import ALL.TXT or a saved session"),
            [QgsProcessing.TypeVectorPoint, QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Skip distance by hour"), QgsProcessing.TypeVector))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        import json
        from ..core import skip
        from ..timeline.sources import LogSource, PskSource
        from .import_alltxt import LOG_PROPERTY
        from .import_psk import LAYER_PROPERTY

        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        if layer is None:
            raise QgsProcessingException(self.tr("Choose a layer in the project."))
        if layer.customProperty(LAYER_PROPERTY):
            src = PskSource(layer)
            feedback.pushInfo(self.tr("Reading PSK Reporter files in {}").format(src.opts["folder"]))
            reports = src.load()
            my_grid = src.opts.get("my_grid") or ""
            if not mh.is_valid(my_grid):
                from ..core.stations import guess_my_grid
                my_grid = guess_my_grid(reports) or ""
            samples = skip.psk_samples(reports, my_grid, tuple(src.opts.get("directions") or ("heard", "heard_me")))
        elif layer.customProperty(LOG_PROPERTY):
            props = json.loads(layer.customProperty(LOG_PROPERTY))
            if props.get("kind") not in ("alltxt", "session"):
                raise QgsProcessingException(self.tr("A running live map has no saved log; tick 'Save session' "
                                                     "in the live panel, or use a saved session layer."))
            src = LogSource([layer])
            feedback.pushInfo(self.tr("Reading the WSJT-X log"))
            log = src.load()
            my_grid = props.get("mygrid") or ""
            samples = skip.log_samples(log, my_grid)
            if not my_grid:
                my_grid = next((m.de_grid for _, m in log if getattr(m, "de_grid", "")), "")
        else:
            raise QgsProcessingException(self.tr("This layer doesn't come from a PSK Reporter fetch/import, an "
                                                 "ALL.TXT import or a saved session, so its raw data can't be found."))
        if not samples:
            raise QgsProcessingException(self.tr("No contacts with both a grid and a time (is My grid known?)."))
        feedback.pushInfo(self.tr("{} contact(s) from {}").format(len(samples), my_grid))

        cond = None
        try:
            from .ionosphere import cached_time, load_conditions
            if cached_time() is not None:
                cond = load_conditions()
        except (OSError, ValueError):
            cond = None
        here = mh.center(my_grid) if mh.is_valid(my_grid) else None

        fields = QgsFields()
        for name, kind in (("band", STRING), ("hour_utc", DATETIME), ("n_sky", INT), ("n_local", INT),
                           ("skip_km", DOUBLE), ("median_km", DOUBLE), ("p90_km", DOUBLE),
                           ("fof2_est_mhz", DOUBLE), ("kc2g_fof2_mhz", DOUBLE)):
            fields.append(field(name, kind, 0))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, QgsWkbTypes.NoGeometry)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        rows = skip.by_hour(samples)
        with_skip = 0
        for (band, hour), s in rows.items():
            kc2g = None
            if cond is not None and here and abs((hour - cond.time).total_seconds()) <= 3 * 3600:
                kc2g = round(cond.at(*here)[0], 2)
            f = QgsFeature(fields)
            f.setAttributes([band, utc_qdatetime(hour), s.n, s.local_n, s.skip_km, s.median_km, s.p90_km,
                             s.fof2_est_mhz, kc2g])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
            with_skip += s.skip_km is not None
        feedback.pushInfo(self.tr("{} band-hour row(s), {} with enough contacts for a skip edge").format(
            len(rows), with_skip))
        return {self.OUTPUT: dest}
