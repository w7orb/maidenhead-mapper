"""Processing algorithms: fetch KC2G ionosphere data; check paths against it."""

import datetime as _dt
import json
import os
import time

from qgis.core import (
    QgsFeatureRequest,
    QgsApplication,
    QgsBlockingNetworkRequest,
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsLineSymbol,
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
from qgis.PyQt.QtCore import QCoreApplication, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..core import iono
from ..core import maidenhead as mh
from ..core.bands import BAND_NAMES
from . import styling
from .import_psk import WGS84
from .paths import PathError, PathReader, my_grid_for
from .qt_compat import DATETIME, DOUBLE, INT, STRING, field, utc_qdatetime

BASE_URL = "https://prop.kc2g.com"
FILES = {
    "stations.json": "/api/stations.json",
    "mufd.geojson": "/renders/current/mufd-normal-now.geojson",
    "fof2.geojson": "/renders/current/fof2-normal-now.geojson",
    "hmf2.geojson": "/renders/current/hmf2-normal-now.geojson",
}
REFRESH_S = 15 * 60       # KC2G updates about every 15 minutes; don't ask more often
STATUS_COLOURS = {iono.STATUS_F2: "#2ca02c", iono.STATUS_ABOVE: "#d62728", iono.STATUS_NO_DATA: "#9e9e9e"}
STATUS_LABELS = {iono.STATUS_F2: "F2 can support it", iono.STATUS_ABOVE: "Above F2 MUF (Es? scatter?)",
                 iono.STATUS_NO_DATA: "No ionosphere data"}


def cache_dir():
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "maidenhead_mapper", "kc2g")


def _meta_path():
    return os.path.join(cache_dir(), "meta.json")


def cached_time():
    """UTC time of the cached download, or None."""
    try:
        with open(_meta_path(), encoding="utf-8") as f:
            return _dt.datetime.fromtimestamp(json.load(f)["fetched"], _dt.timezone.utc)
    except (OSError, ValueError, KeyError):
        return None


def fetch(feedback, force=False):
    """Download KC2G's data into the cache unless it is fresher than REFRESH_S."""
    t = cached_time()
    if t and not force and time.time() - t.timestamp() < REFRESH_S:
        feedback.pushInfo(QCoreApplication.translate("ionosphere", "Using KC2G data from {:%H:%M} UTC "
                                                     "(refreshed at most every 15 min)").format(t))
        return t
    os.makedirs(cache_dir(), exist_ok=True)
    for name, path in FILES.items():
        if feedback.isCanceled():
            raise QgsProcessingException("Cancelled")
        req = QgsBlockingNetworkRequest()
        err = req.get(QNetworkRequest(QUrl(BASE_URL + path)), True, feedback)
        if err != QgsBlockingNetworkRequest.NoError:
            raise QgsProcessingException(f"KC2G download failed ({path}): {req.errorMessage()}")
        data = bytes(req.reply().content())
        try:
            json.loads(data.decode("utf-8"))
        except ValueError:
            raise QgsProcessingException(f"KC2G returned something other than JSON for {path}")
        tmp = os.path.join(cache_dir(), name + ".part")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(cache_dir(), name))
    now = time.time()
    with open(_meta_path(), "w", encoding="utf-8") as f:
        json.dump({"fetched": now, "source": BASE_URL}, f)
    feedback.pushInfo(QCoreApplication.translate("ionosphere", "Downloaded current ionosphere data from KC2G"))
    return _dt.datetime.fromtimestamp(now, _dt.timezone.utc)


def _load(name):
    with open(os.path.join(cache_dir(), name), encoding="utf-8") as f:
        return json.load(f)


def load_conditions(max_age_h=3.0):
    """Conditions from the cache (maps + ionosondes no older than max_age_h)."""
    t = cached_time()
    fresh, _ = iono.parse_stations(_load("stations.json"), t, max_age_h)
    return iono.Conditions(t, iono.ContourField(_load("mufd.geojson")), iono.ContourField(_load("fof2.geojson")),
                           iono.ContourField(_load("hmf2.geojson")), fresh)


def _categorized(attr, geometry):
    cats = []
    for status, colour in STATUS_COLOURS.items():
        if geometry == "line":
            sym = QgsLineSymbol.createSimple({"line_color": colour, "line_width": "0.6"})
        else:
            sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": colour, "size": "2.4",
                                                "outline_color": "255,255,255,200", "outline_width": "0.2"})
        cats.append(QgsRendererCategory(status, sym, STATUS_LABELS[status]))
    return QgsCategorizedSymbolRenderer(attr, cats)


def _contour_labels():
    s = QgsPalLayerSettings()
    s.fieldName = "level"
    s.placement = QgsPalLayerSettings.Line if hasattr(QgsPalLayerSettings, "Line") else 2
    try:
        from qgis.core import Qgis
        s.placement = Qgis.LabelPlacement.Line
    except AttributeError:
        pass
    return QgsVectorLayerSimpleLabeling(s)


# ---------------------------------------------------------------------------
class FetchIonosphereAlgorithm(QgsProcessingAlgorithm):
    MAX_AGE = "MAX_AGE"
    MAP = "MAP"
    IONOSONDES = "IONOSONDES"
    CONTOURS = "CONTOURS"
    MAPS = [("mufd.geojson", "MUF(3000)F2, MHz"), ("fof2.geojson", "foF2, MHz"), ("hmf2.geojson", "hmF2, km")]

    def tr(self, text):
        return QCoreApplication.translate("FetchIonosphereAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return FetchIonosphereAlgorithm()

    def name(self):
        return "fetch_ionosphere"

    def displayName(self):  # noqa: N802
        return self.tr("Fetch ionosphere (KC2G)")

    def group(self):
        return self.tr("Ionosphere")

    def groupId(self):  # noqa: N802
        return "ionosphere"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Downloads the current state of the F2 layer from KC2G (prop.kc2g.com): ionosonde "
            "measurements (GIRO/INGV) and worldwide contour maps of MUF(3000), foF2 and hmF2 built "
            "from them over the IRI-2020 model.\n\n"
            "Outputs the ionosondes that reported recently and one contour map. The data is cached "
            "and re-downloaded at most every 15 minutes (KC2G's update rate). KC2G is a volunteer "
            "service: please don't automate frequent requests. There is no history: this is "
            "'now' only.")

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_AGE, self.tr("Ionosondes reported within (hours)"), QgsProcessingParameterNumber.Double,
            defaultValue=3.0, minValue=0.1, maxValue=72.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.MAP, self.tr("Contour map"), options=[m[1] for m in self.MAPS], defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.IONOSONDES, self.tr("Ionosondes"), QgsProcessing.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.CONTOURS, self.tr("Ionosphere contours"), QgsProcessing.TypeVectorLine,
            optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        t = fetch(feedback)
        max_age = self.parameterAsDouble(parameters, self.MAX_AGE, context)
        fresh, stale = iono.parse_stations(_load("stations.json"), t, max_age)
        feedback.pushInfo(self.tr("{} ionosonde(s) reported within {} h; {} older ones left out").format(
            len(fresh), max_age, stale))

        fields = QgsFields()
        for name, kind in (("code", STRING), ("name", STRING), ("utc", DATETIME), ("age_min", INT),
                           ("fof2", DOUBLE), ("hmf2", DOUBLE), ("mufd", DOUBLE), ("md", DOUBLE),
                           ("foes", DOUBLE), ("confidence", DOUBLE), ("source", STRING)):
            fields.append(field(name, kind, 0))
        sink, dest = self.parameterAsSink(parameters, self.IONOSONDES, context, fields, QgsWkbTypes.Point, WGS84)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.IONOSONDES))
        for s in fresh:
            f = QgsFeature(fields)
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(s.lon, s.lat)))
            f.setAttributes([s.code, s.name, utc_qdatetime(s.time), int((t - s.time).total_seconds() // 60),
                             s.fof2, s.hmf2, s.mufd, s.md, s.foes, s.confidence, s.source])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
        results = {self.IONOSONDES: dest}
        styling.attach_styler(context, dest, self.tr("Ionosondes {:%H:%M} UTC").format(t), styling.LayerStyler(
            None, lambda: _simple_labels("code")))

        fname, label = self.MAPS[self.parameterAsEnum(parameters, self.MAP, context)]
        cfields = QgsFields()
        cfields.append(field("level", DOUBLE, 0))
        sink, dest = self.parameterAsSink(parameters, self.CONTOURS, context, cfields,
                                          QgsWkbTypes.LineString, WGS84)
        if sink is not None:
            for feat in _load(fname).get("features", []):
                try:
                    coords = feat["geometry"]["coordinates"]
                    level = float(feat["properties"]["level-value"])
                except (KeyError, TypeError, ValueError):
                    continue
                # split at the antimeridian seam
                parts, part = [], [coords[0]]
                for a, b in zip(coords, coords[1:]):
                    if abs(b[0] - a[0]) > 180:
                        parts.append(part)
                        part = []
                    part.append(b)
                parts.append(part)
                for p in parts:
                    if len(p) < 2:
                        continue
                    f = QgsFeature(cfields)
                    f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y, *_ in p]))
                    f.setAttributes([level])
                    sink.addFeature(f, QgsFeatureSink.FastInsert)
            results[self.CONTOURS] = dest
            styling.attach_styler(context, dest, self.tr("{} {:%H:%M} UTC (KC2G)").format(label, t),
                                  styling.LayerStyler(None, _contour_labels))
        return results


def _simple_labels(name):
    s = QgsPalLayerSettings()
    s.fieldName = name
    return QgsVectorLayerSimpleLabeling(s)


# ---------------------------------------------------------------------------
class CheckPathsAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    MYGRID = "MYGRID"
    MAX_AGE = "MAX_AGE"
    REFRESH = "REFRESH"
    OUTPUT = "OUTPUT"
    REFLECTIONS = "REFLECTIONS"

    NEW_FIELDS = [("iono_utc", DATETIME), ("iono_note", STRING), ("freq_mhz", DOUBLE), ("f2_status", STRING),
                  ("muf_mhz", DOUBLE), ("fof2_mhz", DOUBLE), ("hmf2_km", DOUBLE), ("hops_iono", INT),
                  ("takeoff_iono", DOUBLE), ("bands_f2", STRING)]

    def tr(self, text):
        return QCoreApplication.translate("CheckPathsAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return CheckPathsAlgorithm()

    def name(self):
        return "check_paths"

    def displayName(self):  # noqa: N802
        return self.tr("Check paths against the ionosphere")

    def group(self):
        return self.tr("Ionosphere")

    def groupId(self):  # noqa: N802
        return "ionosphere"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "For each path in a station or exchange layer, uses KC2G's current ionosphere (fetched "
            "if needed) to work out:\n"
            "• hops_iono / takeoff_iono: likely hop count and takeoff angle with the measured layer "
            "height at the reflection points\n"
            "• muf_mhz: the highest frequency F2 supports on this path (lowest MUF of its hops), "
            "with fof2_mhz and hmf2_km at the reflection points\n"
            "• f2_status: 'F2' if the band is at or below the MUF, 'above F2 MUF' if not (the "
            "contact used sporadic-E, scatter, or the map is off), 'no data'\n"
            "• bands_f2: which of the path's bands F2 supports\n\n"
            "Exchange layers use grid_a/grid_b; station layers use grid and My grid. The band's FT8 "
            "frequency stands for it; a station on several bands is judged on its highest.\n\n"
            "Paths whose last_utc is more than the time limit from the data get 'no data': KC2G "
            "has no history, so this is for current activity only.")

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Station or exchange layer"),
            [QgsProcessing.TypeVectorPoint, QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterString(
            self.MYGRID, self.tr("My grid (station layers; empty = from the layer or last used)"), optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_AGE, self.tr("Only paths within this many hours of the ionosphere data"),
            QgsProcessingParameterNumber.Double, defaultValue=3.0, minValue=0.1, maxValue=48.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.REFRESH, self.tr("Download fresh data if the cache is over 15 minutes old"), defaultValue=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Paths with ionosphere"), QgsProcessing.TypeVectorAnyGeometry))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.REFLECTIONS, self.tr("Reflection points"), QgsProcessing.TypeVectorPoint,
            optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        # Paths are read from grid fields, not geometry: don't reject odd geometries
        context.setInvalidGeometryCheck(QgsFeatureRequest.GeometryNoCheck)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        names = source.fields().names()
        my_grid = my_grid_for(self.parameterAsVectorLayer(parameters, self.INPUT, context),
                              self.parameterAsString(parameters, self.MYGRID, context))
        try:
            reader = PathReader(source.fields(), my_grid)
        except PathError as exc:
            raise QgsProcessingException(self.tr(str(exc)))
        if not reader.exchanges:
            feedback.pushInfo(self.tr("My grid: {}").format(my_grid))

        if self.parameterAsBool(parameters, self.REFRESH, context) or cached_time() is None:
            fetch(feedback)
        try:
            cond = load_conditions()
        except (OSError, ValueError) as exc:
            raise QgsProcessingException(self.tr("No KC2G data cached; run with download on. ({})").format(exc))
        feedback.pushInfo(self.tr("Ionosphere from KC2G at {:%Y-%m-%d %H:%M} UTC; {} current ionosonde(s) "
                                  "used to correct the maps").format(cond.time, len(cond.ionosondes)))
        max_age = self.parameterAsDouble(parameters, self.MAX_AGE, context) * 3600

        out_fields = QgsFields(source.fields())
        for name, kind in self.NEW_FIELDS:
            if out_fields.indexOf(name) < 0:
                out_fields.append(field(name, kind, 0))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, out_fields, source.wkbType(),
                                          source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        rfields = QgsFields()
        for name, kind in (("path_fid", INT), ("calls", STRING), ("hop", INT), ("hops", INT), ("fof2_mhz", DOUBLE),
                           ("hmf2_km", DOUBLE), ("mufd_mhz", DOUBLE), ("hop_muf_mhz", DOUBLE),
                           ("freq_mhz", DOUBLE), ("f2_status", STRING)):
            rfields.append(field(name, kind, 0))
        rsink, rdest = self.parameterAsSink(parameters, self.REFLECTIONS, context, rfields, QgsWkbTypes.Point, WGS84)

        counts = {iono.STATUS_F2: 0, iono.STATUS_ABOVE: 0, iono.STATUS_NO_DATA: 0}
        total = source.featureCount() or 1
        iono_q = utc_qdatetime(cond.time)
        for i, f in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(100.0 * i / total)
            p = reader.read(f)
            ga, gb, calls, freqs, when = p["ga"], p["gb"], p["calls"], p["bands"], p["when"]
            note, result, freq = "", None, None
            if freqs:
                band, freq = max(freqs, key=lambda bf: bf[1])
            if not p["ok"]:
                note = "no grid"
            elif freq is None:
                note = "no band"
            elif when is not None and abs((when - cond.time).total_seconds()) > max_age:
                note = f"path {abs((when - cond.time).total_seconds()) / 3600:,.1f} h from the ionosphere data"
            else:
                (x1, y1), (x2, y2) = mh.center(ga), mh.center(gb)
                result = iono.analyse_path(x1, y1, x2, y2, freq, cond)
            status = result.status if result else iono.STATUS_NO_DATA
            counts[status] += 1
            bands_ok = ""
            if result and result.muf_mhz is not None:
                bands_ok = ",".join(b for b, fr in sorted(freqs, key=lambda bf: BAND_NAMES.index(bf[0])
                                                          if bf[0] in BAND_NAMES else 99) if fr <= result.muf_mhz)
            out = QgsFeature(out_fields)
            out.setGeometry(f.geometry())
            for name in names:
                out[name] = f[name]
            out["iono_utc"], out["iono_note"], out["freq_mhz"], out["f2_status"] = iono_q, note, freq, status
            if result:
                out["muf_mhz"], out["fof2_mhz"], out["hmf2_km"] = result.muf_mhz, result.fof2_mhz, result.hmf2_km
                out["hops_iono"], out["takeoff_iono"], out["bands_f2"] = result.hops, result.takeoff_deg, bands_ok
            sink.addFeature(out, QgsFeatureSink.FastInsert)
            if rsink is not None and result:
                for k, (x, y, f2, h, mufd, hmuf) in enumerate(result.points, 1):
                    r = QgsFeature(rfields)
                    r.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
                    r.setAttributes([f.id(), calls, k, result.hops, round(f2, 2), round(h, 1), round(mufd, 2),
                                     round(hmuf, 2), freq, iono.STATUS_F2 if freq <= hmuf else iono.STATUS_ABOVE])
                    rsink.addFeature(r, QgsFeatureSink.FastInsert)

        feedback.pushInfo(self.tr("{} path(s): {} F2, {} above the F2 MUF, {} no data").format(
            sum(counts.values()), counts[iono.STATUS_F2], counts[iono.STATUS_ABOVE], counts[iono.STATUS_NO_DATA]))
        geometry = "line" if QgsWkbTypes.geometryType(source.wkbType()) == QgsWkbTypes.LineGeometry else "point"
        styling.attach_styler(context, dest, self.tr("{} · ionosphere {:%H:%M} UTC").format(
            source.sourceName() or "Paths", cond.time),
            styling.LayerStyler(lambda: _categorized("f2_status", geometry)))
        results = {self.OUTPUT: dest}
        if rsink is not None:
            results[self.REFLECTIONS] = rdest
            styling.attach_styler(context, rdest, self.tr("{} · reflection points {:%H:%M} UTC").format(
                source.sourceName() or "Paths", cond.time),
                                  styling.LayerStyler(lambda: _categorized("f2_status", "point")))
        return results
