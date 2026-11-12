"""Processing algorithm: which areas could work which, per hour or day, from
ALL.TXT, as time-enabled layers for QGIS's Temporal Controller (time slider)."""

import os
from datetime import datetime, timedelta, timezone

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsDateTimeRange,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsFillSymbol,
    QgsGeometry,
    QgsGraduatedSymbolRenderer,
    QgsInterval,
    QgsLineSymbol,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProject,
    QgsProperty,
    QgsRendererRange,
    QgsSettings,
    QgsSymbolLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..core import alltxt, alltxt_index, live
from ..core import corridors as cor
from ..core import maidenhead as mh
from ..core.bands import band_for_mhz
from . import styling
from .import_psk import SETTINGS_MYCALL, WGS84
from .paths import SETTINGS_MYGRID
from .qt_compat import DATETIME, DOUBLE, INT, STRING, field, utc_qdatetime

SIZES = [cor.FIELD, cor.BLOCK, cor.SQUARE]
BINS = [1, 3, 24]
WARMUP = timedelta(hours=6)     # read this much before the start, to learn grids


def index_cache_dir():
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "maidenhead_mapper", "alltxt_index")


def _corridor_fields():
    fields = QgsFields()
    for name, kind, length in (
            ("bin_start", DATETIME, 0), ("bin_end", DATETIME, 0), ("cell_a", STRING, 10), ("cell_b", STRING, 10),
            ("seen", STRING, 12), ("exchanges", INT, 0), ("stations_a", INT, 0), ("stations_b", INT, 0),
            ("a_to_b_db", DOUBLE, 0), ("n_a_to_b", INT, 0), ("b_to_a_db", DOUBLE, 0), ("n_b_to_a", INT, 0),
            ("diff_db", DOUBLE, 0), ("snr_db", DOUBLE, 0), ("distance_km", DOUBLE, 0), ("bands", STRING, 40),
            ("calls_a", STRING, 254), ("calls_b", STRING, 254), ("label", STRING, 40)):
        fields.append(field(name, kind, length))
    return fields


def _cell_fields():
    fields = QgsFields()
    for name, kind, length in (("bin_start", DATETIME, 0), ("bin_end", DATETIME, 0), ("cell", STRING, 10),
                               ("stations", INT, 0), ("exchanges", INT, 0), ("partner_cells", INT, 0),
                               ("partners", STRING, 254)):
        fields.append(field(name, kind, length))
    return fields


def _cell_bounds(name, size):
    if size == cor.BLOCK:
        w, s, _, _ = mh.bounds(name)
        return w, s, w + 4.0, s + 2.0
    return mh.bounds(name)


def _calls(s):
    text = ",".join(sorted(s))
    return text if len(text) <= 254 else text[:250] + ",…"


def corridor_renderer():
    """Colour by median SNR, width by exchanges, dashed when only one direction is reported."""
    ranges = []
    for lo, hi, colour, label in styling.SNR_CLASSES:
        sym = QgsLineSymbol.createSimple({"line_color": colour, "line_width": "0.4", "capstyle": "round"})
        sym.setOpacity(0.9)
        sl = sym.symbolLayer(0)
        sl.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeWidth,
                                  QgsProperty.fromExpression("min(2, 0.25 + 0.15 * sqrt(\"exchanges\"))"))
        sl.setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeStyle,
                                  QgsProperty.fromExpression(f"if(\"seen\" = '{cor.BOTH}', 'solid', 'dash')"))
        ranges.append(QgsRendererRange(lo, hi, sym, label))
    no_snr = QgsLineSymbol.createSimple({"line_color": "#9e9e9e", "line_width": "0.3", "line_style": "dot"})
    ranges.append(QgsRendererRange(-1e9, -99, no_snr, "no reports"))
    r = QgsGraduatedSymbolRenderer("coalesce(\"snr_db\", -1000)", ranges)
    return r


def cell_renderer():
    """Active cells: more stations, stronger fill."""
    classes = [(1, 2, "#fff3c4", "1 station"), (2, 5, "#ffd27a", "2–4"), (5, 10, "#fb9a4b", "5–9"),
               (10, 1e9, "#e5533d", "10 or more")]
    ranges = []
    for lo, hi, colour, label in classes:
        sym = QgsFillSymbol.createSimple({"color": colour, "outline_color": "#b0703a", "outline_width": "0.15"})
        sym.setOpacity(0.3)
        ranges.append(QgsRendererRange(lo, hi - 1e-9, sym, label))
    return QgsGraduatedSymbolRenderer("stations", ranges)


def make_temporal(layer):
    """Show each feature only during its bin [bin_start, bin_end)."""
    tp = layer.temporalProperties()
    mode = getattr(Qgis, "VectorTemporalMode", None)
    tp.setMode(mode.FeatureDateTimeStartAndEndFromFields if mode else tp.ModeFeatureDateTimeStartAndEndFromFields)
    tp.setStartField("bin_start")
    tp.setEndField("bin_end")
    limit = getattr(Qgis, "VectorTemporalLimitMode", None)
    if limit is not None:
        tp.setLimitMode(limit.IncludeBeginExcludeEnd)
    tp.setIsActive(True)


def set_time_slider(start, end, hours):
    """Point the project and the Temporal Controller at start..end in steps of the bin size, and show it."""
    rng = QgsDateTimeRange(utc_qdatetime(start), utc_qdatetime(end))
    ts = QgsProject.instance().timeSettings()
    ts.setTemporalRange(rng)
    try:
        from qgis.utils import iface
    except ImportError:
        iface = None
    if iface is None or iface.mapCanvas() is None:
        return
    nav = iface.mapCanvas().temporalController()
    if nav is None:
        return
    nav.setTemporalExtents(rng)
    nav.setFrameDuration(QgsInterval(hours * 3600))
    modes = getattr(Qgis, "TemporalNavigationMode", None)
    nav.setNavigationMode(modes.Animated if modes else nav.Animated)
    nav.setCurrentFrameNumber(0)
    from qgis.PyQt.QtWidgets import QAction
    act = iface.mainWindow().findChild(QAction, "mActionTemporalController")
    if act is not None and not act.isChecked():
        act.trigger()


class _TemporalStyler(styling.LayerStyler):
    def __init__(self, renderer, labeling, window):
        super().__init__(renderer, labeling)
        self.window = window

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        super().postProcessLayer(layer, context, feedback)
        make_temporal(layer)
        if self.window:
            set_time_slider(*self.window)
        layer.triggerRepaint()


class CorridorsOverTimeAlgorithm(QgsProcessingAlgorithm):
    FILES = "FILES"
    START = "START"
    END = "END"
    BIN = "BIN"
    SIZE = "SIZE"
    BAND = "BAND"
    MIN_N = "MIN_N"
    OUTPUT = "OUTPUT"
    CELLS = "CELLS"

    def tr(self, text):
        return QCoreApplication.translate("CorridorsOverTimeAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return CorridorsOverTimeAlgorithm()

    def name(self):
        return "corridors_over_time"

    def displayName(self):  # noqa: N802
        return self.tr("Corridors over time from ALL.TXT")

    def group(self):
        return self.tr("Stations")

    def groupId(self):  # noqa: N802
        return "stations"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Which areas could work which, hour by hour (or day by day), from WSJT-X ALL.TXT files. "
            "Every exchange you decoded, including those between other stations, is placed in a time "
            "bin by when it started; per bin, the exchanges between two grid cells make a corridor.\n\n"
            "The outputs are time-enabled: open View → Panels → Temporal Controller (it opens by itself "
            "when run from the 'Propagation over time' panel) and press play, or drag its slider, to "
            "watch openings move.\n\n"
            "• Corridors: one line per pair of cells per bin. seen: 'both ways' when each end "
            "reported the other (solid line), 'A→B only' / 'B→A only' when only one direction was "
            "reported (dashed), 'no reports' when only calls were seen (dotted). Colour: median SNR; "
            "width: exchanges. Medians each way, stations at each end and their callsigns as in "
            "Summarize exchanges by grid pair.\n"
            "• Active cells: one polygon per cell per bin with the stations heard in exchanges, "
            "the exchanges, and how many other cells it reached.\n\n"
            "The files are indexed first (cached, and extended as WSJT-X appends), so only the "
            "chosen dates are read. Reading starts 6 hours early to learn grids. Band: leave empty "
            "for all bands together, or give one (e.g. 20m).")

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.FILES, self.tr("ALL.TXT file(s)"), QgsProcessing.TypeFile))
        self.addParameter(QgsProcessingParameterDateTime(self.START, self.tr("From (UTC)"), optional=True))
        self.addParameter(QgsProcessingParameterDateTime(self.END, self.tr("To (UTC)"), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.BIN, self.tr("Group by"), options=[self.tr("Hour"), self.tr("3 hours"), self.tr("Day")],
            defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.SIZE, self.tr("Cell size"),
            options=[self.tr("Field (EM)"), self.tr("2×2 squares"), self.tr("Square (EM78)")], defaultValue=0))
        self.addParameter(QgsProcessingParameterString(
            self.BAND, self.tr("Band (empty = all bands)"), optional=True, defaultValue=""))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_N, self.tr("Least exchanges per corridor and bin"), QgsProcessingParameterNumber.Integer,
            defaultValue=1, minValue=1))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Corridors over time"), QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.CELLS, self.tr("Active cells over time"), QgsProcessing.TypeVectorPolygon,
            optional=True, createByDefault=True))

    def _when(self, parameters, name, context):
        if not parameters.get(name):
            return None
        q = self.parameterAsDateTime(parameters, name, context)
        return datetime.fromtimestamp(q.toSecsSinceEpoch(), timezone.utc) if q.isValid() else None

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        files = [os.path.abspath(p) for p in self.parameterAsFileList(parameters, self.FILES, context) if p]
        missing = [p for p in files if not os.path.isfile(p)]
        if not files or missing:
            raise QgsProcessingException(self.tr("File not found: {}").format(", ".join(missing) or "(none)"))
        start, end = self._when(parameters, self.START, context), self._when(parameters, self.END, context)
        if start and end and end <= start:
            raise QgsProcessingException(self.tr("'To' must be after 'From'."))
        hours = BINS[self.parameterAsEnum(parameters, self.BIN, context)]
        size = SIZES[self.parameterAsEnum(parameters, self.SIZE, context)]
        band = self.parameterAsString(parameters, self.BAND, context).strip().lower() or None
        min_n = self.parameterAsInt(parameters, self.MIN_N, context)
        s = QgsSettings()
        mycall = s.value(SETTINGS_MYCALL, "", type=str).strip().upper()
        mygrid = s.value(SETTINGS_MYGRID, "", type=str).strip()

        feedback.setProgressText(self.tr("Indexing…"))
        ix = alltxt_index.index(files, index_cache_dir(), lambda p: feedback.setProgress(p * 0.2),
                                feedback.isCanceled)
        if ix is None:
            return {}
        feedback.pushInfo(self.tr("Index: {}").format(ix.summary()))
        if ix.first_time is None:
            raise QgsProcessingException(self.tr("No decodes found (WSJT-X 2.1+ ALL.TXT format)."))
        start = start or cor.bin_floor(ix.first_time, hours)
        end = end or ix.last_time
        if band and band not in ix.bands():
            raise QgsProcessingException(self.tr("No decodes on {} in these files (bands: {}).").format(
                band, ", ".join(sorted(ix.bands()))))

        feedback.setProgressText(self.tr("Reading…"))
        read_from = start - WARMUP
        rows = alltxt.read(files, read_from, end, ix.offsets(read_from),
                           {f.path for f in ix.files if f.in_order})
        if band:
            rows = [r for r in rows if band_for_mhz(r.mhz) == band]
        feedback.pushInfo(self.tr("{} line(s) read from {:%Y-%m-%d %H:%M} (6 h before the start, to learn grids) "
                                  "to {:%Y-%m-%d %H:%M} UTC").format(len(rows), read_from, end))
        if feedback.isCanceled():
            return {}
        feedback.setProgress(30)
        feedback.setProgressText(self.tr("Rebuilding exchanges…"))
        model = live.replay(alltxt.to_log(rows, mycall, mygrid))
        feedback.setProgress(70)
        records, unplaced = cor.records_from_model(model)
        # 'To' is exclusive: an exchange starting exactly then belongs to the next bin
        bins = cor.over_time(records, size, hours, start, end - timedelta(microseconds=1), min_n)
        n_ex = sum(b.records for b in bins)
        feedback.pushInfo(self.tr("{} exchange(s) in the window ({} more without both grids); {} bin(s) of {}").format(
            n_ex, unplaced, len(bins), self.tr("1 day") if hours >= 24 else self.tr("{} h").format(hours)))
        if not bins:
            raise QgsProcessingException(self.tr("No exchanges with both grids known in this window."))

        fields = _corridor_fields()
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                          QgsWkbTypes.MultiLineString, WGS84)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        n_corr = 0
        seen_counts = {}
        for b in bins:
            qs, qe = utc_qdatetime(b.start), utc_qdatetime(b.end)
            for c in b.summary.corridors:
                (lon1, lat1), (lon2, lat2) = cor.cell_center(c.cell_a, size), cor.cell_center(c.cell_b, size)
                parts = mh.great_circle(lon1, lat1, lon2, lat2)
                f = QgsFeature(fields)
                f.setGeometry(QgsGeometry.fromMultiPolylineXY([[QgsPointXY(x, y) for x, y in p] for p in parts]))
                sv = cor.seen(c)
                seen_counts[sv] = seen_counts.get(sv, 0) + 1
                f.setAttributes([qs, qe, c.cell_a, c.cell_b, sv, c.exchanges, len(c.calls_a), len(c.calls_b),
                                 c.a_to_b_db, len(c.a_to_b), c.b_to_a_db, len(c.b_to_a), c.diff_db, c.snr_db,
                                 round(cor.cell_distance_km(c.cell_a, c.cell_b, size)), ",".join(sorted(c.bands, key=cor._band_order)) or None,
                                 _calls(c.calls_a), _calls(c.calls_b), c.label()])
                sink.addFeature(f, QgsFeatureSink.FastInsert)
                n_corr += 1
        window = (bins[0].start, bins[-1].end, hours)
        feedback.pushInfo(self.tr("{} corridor line(s): {}").format(
            n_corr, ", ".join(f"{n} {k}" for k, n in sorted(seen_counts.items()))))
        name = self.tr("Corridors over time") + (f" ({band})" if band else "")
        styling.attach_styler(context, dest, name, _TemporalStyler(corridor_renderer, None, window))
        results = {self.OUTPUT: dest}

        cfields = _cell_fields()
        csink, cdest = self.parameterAsSink(parameters, self.CELLS, context, cfields,
                                            QgsWkbTypes.Polygon, WGS84)
        if csink is not None:
            for b in bins:
                qs, qe = utc_qdatetime(b.start), utc_qdatetime(b.end)
                for act in b.cells.values():
                    f = QgsFeature(cfields)
                    ring = mh.cell_ring(_cell_bounds(act.cell, size))
                    f.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in ring]]))
                    f.setAttributes([qs, qe, act.cell, len(act.calls), act.exchanges, len(act.partners),
                                     _calls(act.partners)])
                    csink.addFeature(f, QgsFeatureSink.FastInsert)
            cname = self.tr("Active cells over time") + (f" ({band})" if band else "")
            styling.attach_styler(context, cdest, cname, _TemporalStyler(cell_renderer, None, None))
            results[self.CELLS] = cdest
        return results
