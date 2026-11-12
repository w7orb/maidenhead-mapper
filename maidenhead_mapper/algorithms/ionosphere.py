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

from ..core import es
from ..core import hops as hops_mod
from ..core import iono
from ..core import maidenhead as mh
from ..core.bands import BAND_NAMES
from . import styling
from .import_psk import WGS84
from .paths import SKIP_GEOMETRY_CHECK, PathError, PathReader, my_grid_for
from .qt_compat import DATETIME, DOUBLE, INT, STRING, field, utc_qdatetime

BASE_URL = "https://prop.kc2g.com"
FILES = {
    "stations.json": "/api/stations.json",
    "mufd.geojson": "/renders/current/mufd-normal-now.geojson",
    "fof2.geojson": "/renders/current/fof2-normal-now.geojson",
    "hmf2.geojson": "/renders/current/hmf2-normal-now.geojson",
    "essn.json": "/api/essn.json",
}
# CCIR (ITU-R P.1239) coefficient files as distributed with IRI, from the
# MIT-licensed space-physics/iri2016 repository, pinned to one commit.
CCIR_URL = ("https://raw.githubusercontent.com/space-physics/iri2016/"
            "d77c22fe387c623eb82b5254fc235979b26b9b99/src/iri2016/data/ccir/ccir{:02d}.asc")
# Sunspot numbers: WDC-SILSO, Royal Observatory of Belgium (CC BY-NC 4.0)
SILSO = {"smoothed.csv": "https://www.sidc.be/SILSO/DATA/SN_ms_tot_V2.0.csv",
         "monthly.csv": "https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.csv"}
SILSO_REFRESH_S = 7 * 24 * 3600
MODE_AUTO, MODE_KC2G, MODE_MODEL = 0, 1, 2
MODES = ["Auto: KC2G when the path is recent, else the CCIR model",
         "KC2G only (current conditions)", "CCIR model only (monthly median, any date)"]
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


def _download(url, path, feedback):
    req = QgsBlockingNetworkRequest()
    err = req.get(QNetworkRequest(QUrl(url)), True, feedback)
    if err != QgsBlockingNetworkRequest.NoError:
        raise QgsProcessingException(f"Download failed ({url}): {req.errorMessage()}")
    data = bytes(req.reply().content())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return data


def ccir_dir():
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "maidenhead_mapper", "ccir")


def load_ccir_model(feedback):
    """The CCIR model; downloads the 12 coefficient files (~0.5 MB) once."""
    from ..core import ccir
    months = {}
    for m in range(1, 13):
        path = os.path.join(ccir_dir(), f"ccir{m + 10}.asc")
        if not os.path.isfile(path):
            if m == 1:
                feedback.pushInfo(QCoreApplication.translate("ionosphere", "Downloading CCIR coefficients (once)"))
            _download(CCIR_URL.format(m + 10), path, feedback)
        with open(path, encoding="ascii", errors="replace") as f:
            months[m] = ccir.parse(f.read())
    return ccir.Model(months)


def load_sunspots(feedback):
    """(smoothed, monthly) SILSO sunspot dicts, refreshed weekly; empty on failure."""
    from ..core import ccir
    out = []
    for name, url in SILSO.items():
        path = os.path.join(ccir_dir(), name)
        try:
            if not os.path.isfile(path) or time.time() - os.path.getmtime(path) > SILSO_REFRESH_S:
                _download(url, path, feedback)
        except QgsProcessingException as exc:
            feedback.pushWarning(f"Sunspot numbers: {exc}")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                out.append(ccir.parse_silso(f.read()))
        except OSError:
            out.append({})
    return out[0], out[1]


class IonoSource:
    """Ionosphere for a path's time: KC2G (current) or the CCIR model (any date)."""

    def __init__(self, feedback, mode=MODE_AUTO, max_age_h=3.0, r12=None, refresh=True):
        self.mode, self.max_age, self.r12_manual = mode, max_age_h * 3600, r12
        self.kc2g = self.model = None
        self.smoothed = self.monthly = self.essn = None
        self._model_cache = {}
        self.used = {}
        if mode in (MODE_AUTO, MODE_KC2G):
            try:
                if refresh or cached_time() is None:
                    fetch(feedback)
                self.kc2g = load_conditions()
                feedback.pushInfo(QCoreApplication.translate("ionosphere", "KC2G data from {:%Y-%m-%d %H:%M} UTC; "
                                                             "{} current ionosonde(s)").format(self.kc2g.time,
                                                                                               len(self.kc2g.ionosondes)))
            except (QgsProcessingException, OSError, ValueError) as exc:
                if mode == MODE_KC2G:
                    raise QgsProcessingException(f"No KC2G data: {exc}")
                feedback.pushWarning(f"No KC2G data ({exc}); using the CCIR model")
        if mode in (MODE_AUTO, MODE_MODEL):
            self.model = load_ccir_model(feedback)
            if not r12:
                self.smoothed, self.monthly = load_sunspots(feedback)
                try:
                    self.essn = _load("essn.json")
                except (OSError, ValueError):
                    self.essn = None

    def r12(self, when):
        from ..core import ccir
        if self.r12_manual:
            return self.r12_manual, "given"
        return ccir.r12_for(when, self.smoothed, self.monthly, self.essn)

    def conditions(self, when):
        """(conditions or None, label, note) for a path at `when` (None = now)."""
        k = self.kc2g
        if k is not None and (when is None or abs((when - k.time).total_seconds()) <= self.max_age):
            return k, "KC2G", ""
        if self.model is None:
            gap = abs((when - k.time).total_seconds()) / 3600 if (k and when) else None
            return None, "", (f"path {gap:,.1f} h from the KC2G data" if gap else "no ionosphere data")
        t = when or _dt.datetime.now(_dt.timezone.utc)
        t = t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)
        r, src = self.r12(t)
        if r is None:
            return None, "", f"no sunspot number for {t:%Y-%m}; enter one"
        key = (t, round(r))
        if key not in self._model_cache:
            self._model_cache[key] = self.model.conditions(t, r)
        label = f"CCIR median, R12 {r:.0f} ({src})"
        return self._model_cache[key], label, ""


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
    SOURCE = "SOURCE"
    R12 = "R12"
    OUTPUT = "OUTPUT"
    REFLECTIONS = "REFLECTIONS"
    UPDATE_INPUT = "UPDATE_INPUT"

    NEW_FIELDS = [("iono_utc", DATETIME), ("iono_source", STRING), ("iono_note", STRING), ("freq_mhz", DOUBLE), ("f2_status", STRING),
                  ("muf_mhz", DOUBLE), ("fof2_mhz", DOUBLE), ("hmf2_km", DOUBLE), ("hops_iono", INT),
                  ("takeoff_iono", DOUBLE), ("hops_muf", INT), ("mode_guess", STRING), ("bands_f2", STRING)]

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
            "For each path in a station or exchange layer, uses the ionosphere at the path's time "
            "(see Ionosphere source below) to work out:\n"
            "• hops_iono / takeoff_iono: likely hop count and takeoff angle with the measured layer "
            "height at the reflection points\n"
            "• muf_mhz: the F2 basic MUF of the path by ITU-R P.533-14 (the lower of its control "
            "points: the mid-point for short paths, points half a hop in from each end for longer "
            "ones), with fof2_mhz and hmf2_km at those control points\n"
            "• f2_status: 'F2' if the band is at or below the MUF, 'above F2 MUF' if not (the "
            "contact used sporadic-E, scatter, or the map is off), 'no data'\n"
            "• hops_muf: the hop count the MUF is computed with (P.533: hops of at most 4,000 km at "
            "3° or more). It can be one more than hops_iono, which allows 2°. More hops never raise "
            "the MUF (shorter hops have a lower one), so a path above the MUF is above it at any "
            "hop count\n"
            "• mode_guess: 'Es' where sporadic-E is the likely explanation (6m and up at single-Es-hop "
            "range, 500–2,300 km; or 12m/10m there when F2 can't support it), else 'F2', 'above MUF' "
            "or 'no data'. hops_iono/takeoff_iono stay the F2 values\n"
            "• bands_f2: which of the path's bands F2 supports\n\n"
            "Exchange layers use grid_a/grid_b; station layers use grid and My grid. The band's FT8 "
            "frequency stands for it; a station on several bands is judged on its highest.\n\n"
            "Ionosphere source (iono_source says which was used per path):\n"
            "• Auto (default): KC2G for paths within the time limit of its data, the CCIR model "
            "for older ones, path by path\n"
            "• KC2G only: current ionosonde-corrected maps (downloaded if the cache is over 15 "
            "minutes old); older paths get 'no data'\n"
            "• CCIR model only (ITU-R P.1239, as in IRI): the monthly median for the path's own date, "
            "time and sunspot number, so past logs can be checked. Typical conditions, not that "
            "day's: expect foF2 within ~20%. The sunspot number comes from SILSO (Royal "
            "Observatory of Belgium) or KC2G, or enter one.\n\n"
            "Also write the results into the input layer: adds these fields to the input layer "
            "itself (memory or GeoPackage, e.g. a saved live session) as well as making the output "
            "layer. Skipped with a warning if the layer can't be changed or is being edited. On a "
            "live or session layer, exchanges that arrive later get empty values, and an Activity "
            "timeline rebuild empties them; run the tool again to refill.")

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
        self.addParameter(QgsProcessingParameterEnum(
            self.SOURCE, self.tr("Ionosphere source"), options=MODES, defaultValue=MODE_AUTO))
        self.addParameter(QgsProcessingParameterNumber(
            self.R12, self.tr("Sunspot number R12 for the model (0 = look it up)"), QgsProcessingParameterNumber.Double,
            defaultValue=0.0, minValue=0.0, maxValue=400.0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Paths with ionosphere"), QgsProcessing.TypeVectorAnyGeometry))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.REFLECTIONS, self.tr("Reflection points"), QgsProcessing.TypeVectorPoint,
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.UPDATE_INPUT, self.tr("Also write the results into the input layer"), defaultValue=False))

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
        self._update = None          # (layer id, {fid: {field: value}}), applied in postProcessAlgorithm
        if self.parameterAsBool(parameters, self.UPDATE_INPUT, context):
            in_layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
            if in_layer is None:
                feedback.reportError(self.tr("Can't write into the input: it isn't a loaded layer."))
            else:
                self._update = (in_layer.id(), {})

        src = IonoSource(feedback, self.parameterAsEnum(parameters, self.SOURCE, context),
                         self.parameterAsDouble(parameters, self.MAX_AGE, context),
                         self.parameterAsDouble(parameters, self.R12, context) or None,
                         self.parameterAsBool(parameters, self.REFRESH, context))

        out_fields = QgsFields(source.fields())
        for name, kind in self.NEW_FIELDS:
            if out_fields.indexOf(name) < 0:
                out_fields.append(field(name, kind, 0))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, out_fields, source.wkbType(),
                                          source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        rfields = QgsFields()
        for name, kind in (("path_fid", INT), ("calls", STRING), ("kind", STRING), ("n", INT), ("hops", INT),
                           ("fof2_mhz", DOUBLE), ("hmf2_km", DOUBLE), ("mufd_mhz", DOUBLE), ("point_muf_mhz", DOUBLE),
                           ("freq_mhz", DOUBLE), ("f2_status", STRING)):
            rfields.append(field(name, kind, 0))
        rsink, rdest = self.parameterAsSink(parameters, self.REFLECTIONS, context, rfields, QgsWkbTypes.Point, WGS84)

        counts = {iono.STATUS_F2: 0, iono.STATUS_ABOVE: 0, iono.STATUS_NO_DATA: 0}
        labels = {}
        total = source.featureCount() or 1
        for i, f in enumerate(source.getFeatures(QgsFeatureRequest(), SKIP_GEOMETRY_CHECK)):
            if feedback.isCanceled():
                break
            feedback.setProgress(100.0 * i / total)
            p = reader.read(f)
            ga, gb, calls, freqs, when = p["ga"], p["gb"], p["calls"], p["bands"], p["when"]
            note, result, freq, cond, label = "", None, None, None, ""
            if freqs:
                band, freq = max(freqs, key=lambda bf: bf[1])
            if not p["ok"]:
                note = "no grid"
            elif freq is None:
                note = "no band"
            else:
                cond, label, note = src.conditions(when)
                if cond is not None:
                    (x1, y1), (x2, y2) = mh.center(ga), mh.center(gb)
                    result = iono.analyse_path(x1, y1, x2, y2, freq, cond)
                    labels[label.split(",")[0]] = labels.get(label.split(",")[0], 0) + 1
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
            out["iono_utc"] = utc_qdatetime(cond.time) if cond is not None and cond.time else None
            out["iono_source"], out["iono_note"], out["freq_mhz"], out["f2_status"] = label, note, freq, status
            if result:
                out["muf_mhz"], out["fof2_mhz"], out["hmf2_km"] = result.muf_mhz, result.fof2_mhz, result.hmf2_km
                out["hops_iono"], out["takeoff_iono"], out["bands_f2"] = result.hops, result.takeoff_deg, bands_ok
                out["hops_muf"] = result.hops_muf
            if p["ok"] and freq is not None:
                out["mode_guess"] = es.mode_guess(mh.distance_bearing(ga, gb)[0], band, status)
            sink.addFeature(out, QgsFeatureSink.FastInsert)
            if self._update is not None:
                self._update[1][f.id()] = {name: out[name] for name, _ in self.NEW_FIELDS}
            if rsink is not None and result:
                # Hop reflection points (where each hop meets the layer) ...
                (x1, y1), (x2, y2) = mh.center(ga), mh.center(gb)
                for k, (x, y) in enumerate(hops_mod.reflection_points(x1, y1, x2, y2, result.hops or 1), 1):
                    f2, h, mufd = cond.at(x, y)
                    r = QgsFeature(rfields)
                    r.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
                    r.setAttributes([f.id(), calls, "hop", k, result.hops, _r(f2, 2), _r(h, 1), _r(mufd, 2),
                                     None, freq, None])
                    rsink.addFeature(r, QgsFeatureSink.FastInsert)
                # ... and the ITU-R P.533 control points the path MUF is judged at
                for k, (x, y, f2, h, mufd, pmuf) in enumerate(result.points, 1):
                    r = QgsFeature(rfields)
                    r.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
                    r.setAttributes([f.id(), calls, "control point", k, result.hops, _r(f2, 2), _r(h, 1),
                                     _r(mufd, 2), _r(pmuf, 2), freq,
                                     iono.STATUS_F2 if freq <= pmuf else iono.STATUS_ABOVE])
                    rsink.addFeature(r, QgsFeatureSink.FastInsert)

        feedback.pushInfo(self.tr("{} path(s): {} F2, {} above the F2 MUF, {} no data").format(
            sum(counts.values()), counts[iono.STATUS_F2], counts[iono.STATUS_ABOVE], counts[iono.STATUS_NO_DATA]))
        if labels:
            feedback.pushInfo(self.tr("Ionosphere used: {}").format(", ".join(f"{k} {n}" for k, n in labels.items())))
        geometry = "line" if QgsWkbTypes.geometryType(source.wkbType()) == QgsWkbTypes.LineGeometry else "point"
        styling.attach_styler(context, dest, self.tr("{} · ionosphere").format(source.sourceName() or "Paths"),
            styling.LayerStyler(lambda: _categorized("f2_status", geometry)))
        results = {self.OUTPUT: dest}
        if rsink is not None:
            results[self.REFLECTIONS] = rdest
            styling.attach_styler(context, rdest, self.tr("{} · reflection points").format(
                source.sourceName() or "Paths"),
                                  styling.LayerStyler(_reflection_renderer))
        return results


    def postProcessAlgorithm(self, context, feedback):  # noqa: N802
        """Main thread: write the results into the input layer if asked."""
        upd, self._update = getattr(self, "_update", None), None
        if upd:
            write_into_layer(upd[0], upd[1], self.NEW_FIELDS, context, feedback, self.tr)
        return {}


def write_into_layer(layer_id, values, new_fields, context, feedback, tr=lambda t: t):
    """Add any missing new_fields to the layer and set values {fid: {name: value}}
    through its data provider. Returns the number of features written."""
    from qgis.core import QgsProject, QgsVectorDataProvider
    layer = QgsProject.instance().mapLayer(layer_id) or context.getMapLayer(layer_id)
    if layer is None:
        feedback.reportError(tr("The input layer is no longer loaded; it was not updated."))
        return 0
    dp = layer.dataProvider()
    caps = dp.capabilities()
    if layer.isEditable():
        feedback.reportError(tr("{} is being edited: save or cancel the edits and run again to write the "
                                "results into it.").format(layer.name()))
        return 0
    if layer.readOnly() or not (caps & QgsVectorDataProvider.ChangeAttributeValues):
        feedback.reportError(tr("{} can't be changed; the results are only in the output layer.").format(
            layer.name()))
        return 0
    have = set(layer.fields().names())
    missing = [field(n, k, 0) for n, k in new_fields if n not in have]
    if missing:
        if not (caps & QgsVectorDataProvider.AddAttributes) or not dp.addAttributes(missing):
            feedback.reportError(tr("Couldn't add fields to {}; the results are only in the output layer.").format(
                layer.name()))
            return 0
        layer.updateFields()
    idx = {n: layer.fields().indexOf(n) for n, _ in new_fields}
    changes = {fid: {idx[n]: v for n, v in vals.items() if idx.get(n, -1) >= 0} for fid, vals in values.items()}
    if changes and not dp.changeAttributeValues(changes):
        feedback.reportError(tr("Writing into {} failed: {}").format(layer.name(), "; ".join(dp.errors()[-3:])))
        return 0
    layer.triggerRepaint()
    feedback.pushInfo(tr("Wrote the results into {} ({} feature(s), {} new field(s)).").format(
        layer.name(), len(changes), len(missing)))
    return len(changes)


def _r(v, n):
    return None if v is None else round(v, n)


def _reflection_renderer():
    """Grey dots for hop reflection points; diamonds coloured by status for P.533 control points."""
    from qgis.core import QgsRuleBasedRenderer
    root = QgsRuleBasedRenderer.Rule(None)
    for expr, label, props in (
            ("\"kind\" = 'hop'", "Hop reflection point",
             {"name": "circle", "color": "#7f7f7f", "size": "1.4", "outline_style": "no"}),
            (f"\"kind\" = 'control point' AND \"f2_status\" = '{iono.STATUS_F2}'", "Control point: F2 supports it",
             {"name": "diamond", "color": STATUS_COLOURS[iono.STATUS_F2], "size": "3"}),
            (f"\"kind\" = 'control point' AND \"f2_status\" = '{iono.STATUS_ABOVE}'", "Control point: above F2 MUF",
             {"name": "diamond", "color": STATUS_COLOURS[iono.STATUS_ABOVE], "size": "3"})):
        root.appendChild(QgsRuleBasedRenderer.Rule(QgsMarkerSymbol.createSimple(props), 0, 0, expr, label))
    return QgsRuleBasedRenderer(root)
