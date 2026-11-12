"""Processing algorithm: summarize many exchanges into grid-pair corridors."""

from datetime import datetime, timezone

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsGraduatedSymbolRenderer,
    QgsLineSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProperty,
    QgsRendererRange,
    QgsSymbolLayer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QDateTime
from qgis.PyQt.QtGui import QColor

from ..core import corridors as cor
from ..core import maidenhead as mh
from . import styling
from .import_psk import WGS84
from .qt_compat import DATETIME, DOUBLE, INT, STRING, field, utc_qdatetime

SIZES = [cor.FIELD, cor.BLOCK, cor.SQUARE]
NEED = {"call_a", "grid_a", "call_b", "grid_b", "a_hears_b", "b_hears_a"}


def _int(v):
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def _str(v):
    return v if isinstance(v, str) and v else None


def _time(v):
    if isinstance(v, QDateTime) and v.isValid():
        return datetime.fromtimestamp(v.toSecsSinceEpoch(), timezone.utc)
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return None


def read_records(layer, request=None):
    """cor.Record per feature of an exchange layer (live, saved session, ALL.TXT or ADIF)."""
    names = set(layer.fields().names())
    get = (lambda f, n: f[n] if n in names else None)
    out = []
    feats = layer.getFeatures(request or QgsFeatureRequest().setInvalidGeometryCheck(QgsFeatureRequest.GeometryNoCheck))
    for f in feats:
        out.append(cor.Record(
            str(f["call_a"] or ""), _str(f["grid_a"]) or "", str(f["call_b"] or ""), _str(f["grid_b"]) or "",
            _str(get(f, "band")) or "", _int(f["a_hears_b"]), _int(f["b_hears_a"]),
            _time(get(f, "first_utc")), _str(get(f, "f2_status")), _str(get(f, "mode_guess"))))
    return out


def corridor_fields():
    fields = QgsFields()
    for name, kind, length in (
            ("cell_a", STRING, 10), ("cell_b", STRING, 10), ("band", STRING, 10), ("exchanges", INT, 0),
            ("stations_a", INT, 0), ("stations_b", INT, 0), ("a_to_b_db", DOUBLE, 0), ("n_a_to_b", INT, 0),
            ("b_to_a_db", DOUBLE, 0), ("n_b_to_a", INT, 0), ("diff_db", DOUBLE, 0), ("n_two_way", INT, 0),
            ("snr_db", DOUBLE, 0), ("distance_km", DOUBLE, 0), ("first_utc", DATETIME, 0),
            ("last_utc", DATETIME, 0), ("hours_active", INT, 0), ("n_f2", INT, 0), ("n_es", INT, 0),
            ("n_above_muf", INT, 0), ("calls_a", STRING, 254), ("calls_b", STRING, 254), ("label", STRING, 40)):
        fields.append(field(name, kind, length))
    return fields


def _calls(s):
    text = ",".join(sorted(s))
    return text if len(text) <= 254 else text[:250] + ",…"


def corridor_renderer():
    """Colour by median SNR (both directions), width by number of exchanges."""
    ranges = []
    for lo, hi, colour, label in styling.SNR_CLASSES:
        sym = QgsLineSymbol.createSimple({"line_color": colour, "line_width": "0.4", "capstyle": "round"})
        sym.setOpacity(0.85)
        sym.symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.PropertyStrokeWidth,
            QgsProperty.fromExpression("min(2, 0.2 + 0.12 * sqrt(\"exchanges\"))"))
        ranges.append(QgsRendererRange(lo, hi, sym, label))
    r = QgsGraduatedSymbolRenderer("snr_db", ranges)
    return r


def corridor_labels():
    fmt = QgsTextFormat()
    fmt.setSize(7)
    fmt.setColor(QColor(30, 30, 30))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.7)
    buf.setColor(QColor(255, 255, 255, 220))
    fmt.setBuffer(buf)
    s = QgsPalLayerSettings()
    s.fieldName = "label"
    s.placement = Qgis.LabelPlacement.Line
    s.setFormat(fmt)
    s.scaleVisibility = True
    s.minimumScale = 15_000_000
    return QgsVectorLayerSimpleLabeling(s)


class SummarizeExchangesAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    SIZE = "SIZE"
    PER_BAND = "PER_BAND"
    START = "START"
    END = "END"
    MIN_N = "MIN_N"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate("SummarizeExchangesAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return SummarizeExchangesAlgorithm()

    def name(self):
        return "summarize_exchanges"

    def displayName(self):  # noqa: N802
        return self.tr("Summarize exchanges by grid pair (corridors)")

    def group(self):
        return self.tr("Stations")

    def groupId(self):  # noqa: N802
        return "stations"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Turns many exchange lines into one line per pair of grid cells and band: a corridor.\n\n"
            "Input: exchange layers from the live map, a saved session, Import WSJT-X ALL.TXT, Import ADIF "
            "or Check paths. Several layers are combined.\n\n"
            "Cell size: field (e.g. EM, for a world map), 2×2 squares (named by the south-west square, "
            "e.g. EM68 covers EM68, EM69, EM78 and EM79) or square (EM78, for a regional map). Cells are "
            "in alphabetical order, so cell_a is usually the western end.\n\n"
            "Fields:\n"
            "• exchanges; stations_a and stations_b: distinct callsigns at each end (many stations or "
            "one pair talking repeatedly?)\n"
            "• a_to_b_db: median SNR of signals from cell A heard in cell B; b_to_a_db the reverse "
            "(n_a_to_b, n_b_to_a reports)\n"
            "• diff_db: median of (A→B minus B→A) over exchanges with both reports (n_two_way). Path and "
            "antennas cancel within an exchange, so a corridor that stays lopsided means the stations at "
            "one end are louder and/or deafer\n"
            "• snr_db: median of all reports, both ways (the line colour)\n"
            "• first_utc, last_utc, hours_active (distinct UTC hours with an exchange)\n"
            "• n_f2, n_es, n_above_muf: from Check paths' mode_guess / f2_status, when present\n"
            "• calls_a, calls_b: the callsigns at each end\n\n"
            "Line width grows with the number of exchanges. Labels (when zoomed in) read "
            "'12×  +3/-5': exchanges, then the A→B / B→A medians.\n\n"
            "Single FT8 reports fade by 20–30 dB; medians over 5 or more exchanges are much more "
            "trustworthy. Exchanges within one cell have no line and are only counted.")

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.INPUT, self.tr("Exchange layer(s)"), QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterEnum(
            self.SIZE, self.tr("Cell size"),
            options=[self.tr("Field (EM)"), self.tr("2×2 squares"), self.tr("Square (EM78)")], defaultValue=2))
        self.addParameter(QgsProcessingParameterBoolean(
            self.PER_BAND, self.tr("Separately per band"), defaultValue=True))
        self.addParameter(QgsProcessingParameterDateTime(self.START, self.tr("From (UTC)"), optional=True))
        self.addParameter(QgsProcessingParameterDateTime(self.END, self.tr("To (UTC)"), optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_N, self.tr("Least exchanges per corridor"), QgsProcessingParameterNumber.Integer,
            defaultValue=3, minValue=1))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Exchange corridors"), QgsProcessing.TypeVectorLine))

    def _when(self, parameters, name, context):
        if not parameters.get(name):
            return None
        q = self.parameterAsDateTime(parameters, name, context)
        return datetime.fromtimestamp(q.toSecsSinceEpoch(), timezone.utc) if q.isValid() else None

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        layers = self.parameterAsLayerList(parameters, self.INPUT, context)
        records = []
        for lyr in layers:
            if not NEED <= set(lyr.fields().names()):
                feedback.reportError(self.tr("Skipped {}: not an exchange layer").format(lyr.name()))
                continue
            records += read_records(lyr)
        if not records:
            raise QgsProcessingException(self.tr("No exchange layers with report fields given."))
        size = SIZES[self.parameterAsEnum(parameters, self.SIZE, context)]
        per_band = self.parameterAsBool(parameters, self.PER_BAND, context)
        min_n = self.parameterAsInt(parameters, self.MIN_N, context)
        start, end = self._when(parameters, self.START, context), self._when(parameters, self.END, context)
        result = cor.summarize(records, size, per_band, start, end, min_n)

        fields = corridor_fields()
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                          QgsWkbTypes.MultiLineString, WGS84)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        for c in result.corridors:
            if feedback.isCanceled():
                break
            (lon1, lat1), (lon2, lat2) = cor.cell_center(c.cell_a, size), cor.cell_center(c.cell_b, size)
            parts = mh.great_circle(lon1, lat1, lon2, lat2)
            f = QgsFeature(fields)
            f.setGeometry(QgsGeometry.fromMultiPolylineXY([[QgsPointXY(x, y) for x, y in p] for p in parts]))
            f.setAttributes([
                c.cell_a, c.cell_b, c.band or None, c.exchanges, len(c.calls_a), len(c.calls_b),
                c.a_to_b_db, len(c.a_to_b), c.b_to_a_db, len(c.b_to_a), c.diff_db, len(c.diffs), c.snr_db,
                round(cor.cell_distance_km(c.cell_a, c.cell_b, size)),
                utc_qdatetime(c.first_utc) if c.first_utc else None,
                utc_qdatetime(c.last_utc) if c.last_utc else None, c.hours_active,
                c.status.get(cor.MODE_F2, 0), c.status.get(cor.MODE_ES, 0), c.status.get(cor.MODE_ABOVE, 0),
                _calls(c.calls_a), _calls(c.calls_b), c.label()])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
        used = sum(c.exchanges for c in result.corridors)
        feedback.pushInfo(self.tr(
            "{} exchange(s) read; {} corridor(s) with at least {} exchange(s), covering {} exchange(s). "
            "Left out: {} within one cell, {} without a grid, {} outside the time window, {} on corridors "
            "with fewer exchanges.").format(
            len(records), len(result.corridors), min_n, used, result.same_cell, result.no_grid, result.outside,
            len(records) - used - result.same_cell - result.no_grid - result.outside))
        styling.attach_styler(context, dest, self.tr("Exchange corridors"),
                              styling.LayerStyler(corridor_renderer, corridor_labels))
        return {self.OUTPUT: dest}
