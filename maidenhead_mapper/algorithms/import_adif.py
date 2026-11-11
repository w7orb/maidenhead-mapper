"""Processing algorithm: Import an ADIF log (.adi / .adx) as QSO lines and worked stations."""

import json
from collections import Counter

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsLineSymbol,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterString,
    QgsRendererCategory,
    QgsSettings,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..core import adif
from ..core import exchange as x
from ..core import hops as _hops
from ..core import maidenhead as mh
from ..core.bands import BAND_NAMES
from ..core.psk import base_callsign, report_time
from ..live.exchange_layer import exchange_fields, exchange_geometry
from . import styling
from .import_alltxt import LOG_PROPERTY, SETTINGS_MYGRID
from .import_psk import SETTINGS_MYCALL, WGS84
from .qt_compat import DATETIME, DOUBLE, INT, STRING, field, utc_qdatetime

# Added to the exchange-layer fields on QSO lines
QSO_EXTRA = [("freq_mhz", DOUBLE, 0), ("rst_sent", STRING, 10), ("rst_rcvd", STRING, 10),
             ("confirmed", STRING, 20), ("prop_mode", STRING, 10), ("country", STRING, 60),
             ("comment", STRING, 254), ("source", STRING, 254)]

WORKED_FIELDS = [("call", STRING, 20), ("grid", STRING, 10), ("direction", STRING, 10), ("qsos", INT, 0),
                 ("bands", STRING, 80), ("modes", STRING, 80), ("first_utc", DATETIME, 0),
                 ("last_utc", DATETIME, 0), ("sent_best_db", INT, 0), ("rcvd_best_db", INT, 0),
                 ("confirmed", INT, 0), ("country", STRING, 60), ("distance_km", DOUBLE, 0),
                 ("bearing_deg", DOUBLE, 0), ("hops", INT, 0), ("takeoff_deg", DOUBLE, 0), ("source", STRING, 10)]


def qso_fields():
    fields = exchange_fields()
    for name, kind, length in QSO_EXTRA:
        fields.append(field(name, kind, length))
    return fields


def worked_fields():
    fields = QgsFields()
    for name, kind, length in WORKED_FIELDS:
        fields.append(field(name, kind, length))
    return fields


def _paths_text(me, them, sent, rcvd):
    """Like the exchange layer: each arrow is a signal path with the SNR it arrived at."""
    parts = []
    if rcvd is not None:
        parts.append(f"{me}→{them} {rcvd:+03d}")
    if sent is not None:
        parts.append(f"{them}→{me} {sent:+03d}")
    return "  ".join(parts) or f"{me} – {them}"


def qso_attrs(i, q, me, my_grid):
    """Exchange-layer attributes for one QSO: A is me, B is them. a_hears_b is the
    report I sent (how I heard them), b_hears_a the one I received."""
    dist = round(mh.distance_bearing(my_grid, q.grid)[0], 1)
    n, takeoff = _hops.likely_hops(dist)
    sent, rcvd = q.sent_db, q.rcvd_db
    first = q.time_on
    last = q.time_off or q.time_on
    return {
        "id": i, "call_a": me, "grid_a": my_grid, "call_b": q.call, "grid_b": q.grid,
        "band": q.band, "mode": q.mode, "stage": x.CONFIRMED,
        "a_hears_b": sent, "b_hears_a": rcvd, "me_hears_a": None, "me_hears_b": None,   # I am A: a_hears_b already says it
        "paths": _paths_text(me, q.call, sent, rcvd), "involves_me": 1, "logged": 1, "messages": None,
        "transcript": (q.comment or "")[:254] or None,
        "first_utc": utc_qdatetime(first), "last_utc": utc_qdatetime(last),
        "distance_km": dist, "hops": n, "takeoff_deg": takeoff,
        "report_diff_db": rcvd - sent if sent is not None and rcvd is not None else None,
        "b_vs_me_db": None, "a_vs_me_db": None,
        "freq_mhz": q.freq_mhz, "rst_sent": q.rst_sent, "rst_rcvd": q.rst_rcvd,
        "confirmed": q.confirmed or None, "prop_mode": q.prop_mode, "country": q.country,
        "comment": (q.comment or "")[:254] or None, "source": q.source[:254],
    }


def band_line_renderer():
    cats = []
    for b in BAND_NAMES:
        sym = QgsLineSymbol.createSimple({"line_color": styling.BAND_COLORS.get(b, "#888888"),
                                          "line_width": "0.45", "capstyle": "round"})
        sym.setOpacity(0.8)
        cats.append(QgsRendererCategory(b, sym, b))
    cats.append(QgsRendererCategory(None, QgsLineSymbol.createSimple({"line_color": "#888888",
                                                                      "line_width": "0.3"}), "other"))
    return QgsCategorizedSymbolRenderer("band", cats)


def worked_renderer():
    return styling.renderer_for(styling.STYLE_BAND)


class ImportAdifAlgorithm(QgsProcessingAlgorithm):
    FILES = "FILES"
    MYCALL = "MYCALL"
    MYGRID = "MYGRID"
    START = "START"
    END = "END"
    STATIONS = "STATIONS"
    QSOS = "QSOS"

    def tr(self, text):
        return QCoreApplication.translate("ImportAdifAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return ImportAdifAlgorithm()

    def name(self):
        return "import_adif"

    def displayName(self):  # noqa: N802
        return self.tr("Import ADIF log")

    def group(self):
        return self.tr("Stations")

    def groupId(self):  # noqa: N802
        return "stations"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Maps your logged QSOs from one or more ADIF files (.adi or .adx): WSJT-X's "
            "wsjtx_log.adi, JTDX, MSHV, N1MM, Log4OM, QRZ or a LoTW download.\n\n"
            "• Worked stations: one marker per callsign at its logged grid (or LAT/LON), with "
            "QSO count, bands, modes, best reports both ways and how many QSOs are confirmed.\n"
            "• QSOs: one great-circle line per QSO from your grid (MY_GRIDSQUARE, else My grid "
            "below). The fields match the WSJT-X exchanges layer, so Check paths, Station "
            "balance, Find sporadic-E paths and Skip distance accept it. call_a/grid_a are you; "
            "a_hears_b is the report you sent, b_hears_a the one you received (digital modes "
            "only: phone/CW RST stays in rst_sent/rst_rcvd).\n\n"
            "The same QSO in several files (e.g. your log and a LoTW download) is merged: same "
            "call, band and mode within {} minutes. Confirmations from either are kept.\n\n"
            "My callsign keeps only QSOs made as that call (any /P etc.); leave it empty for "
            "all.").format(adif.MERGE_MINUTES)

    def initAlgorithm(self, config=None):  # noqa: N802
        s = QgsSettings()
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.FILES, self.tr("ADIF file(s)"), QgsProcessing.TypeFile))
        self.addParameter(QgsProcessingParameterString(
            self.MYCALL, self.tr("Only QSOs made as this callsign (optional)"), optional=True,
            defaultValue=s.value(SETTINGS_MYCALL, "", type=str)))
        self.addParameter(QgsProcessingParameterString(
            self.MYGRID, self.tr("My grid, when the log has no MY_GRIDSQUARE"), optional=True,
            defaultValue=s.value(SETTINGS_MYGRID, "", type=str)))
        self.addParameter(QgsProcessingParameterDateTime(self.START, self.tr("From (optional)"), optional=True))
        self.addParameter(QgsProcessingParameterDateTime(self.END, self.tr("To (optional)"), optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.STATIONS, self.tr("Worked stations"), QgsProcessing.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.QSOS, self.tr("QSOs"), QgsProcessing.TypeVectorLine, optional=True, createByDefault=True))

    def _when(self, parameters, name, context):
        if not parameters.get(name):
            return None
        q = self.parameterAsDateTime(parameters, name, context)
        return report_time(q.toSecsSinceEpoch()) if q.isValid() else None

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        import os
        files = [p for p in self.parameterAsFileList(parameters, self.FILES, context) if p]
        missing = [p for p in files if not os.path.isfile(p)]
        if not files or missing:
            raise QgsProcessingException(self.tr("File not found: {}").format(", ".join(missing) or "(none)"))
        mycall = self.parameterAsString(parameters, self.MYCALL, context).strip().upper()
        mygrid = self.parameterAsString(parameters, self.MYGRID, context).strip()
        if mycall and not base_callsign(mycall):
            raise QgsProcessingException(self.tr("'{}' is not a callsign.").format(mycall))
        if mygrid and not mh.is_valid(mygrid):
            raise QgsProcessingException(self.tr("My grid '{}' is not a valid locator.").format(mygrid))
        start, end = self._when(parameters, self.START, context), self._when(parameters, self.END, context)

        try:
            r = adif.read(files, start, end, mycall or None)
        except Exception as exc:   # malformed XML etc.
            raise QgsProcessingException(self.tr("Could not read the log: {}").format(exc))
        feedback.pushInfo(self.tr("{} record(s) in {} file(s); {} without a call or date/time; {} outside the "
                                  "filters; {} duplicate(s) merged; {} QSO(s)").format(
            r.records, len(files), r.unusable, r.filtered_out, r.merged, len(r.qsos)))
        if not r.qsos:
            raise QgsProcessingException(self.tr("No QSOs found."))
        qsos = r.qsos
        feedback.pushInfo(self.tr("{:%Y-%m-%d} – {:%Y-%m-%d}; {} confirmed").format(
            qsos[0].time_on, qsos[-1].time_on, sum(1 for q in qsos if q.confirmed)))

        logged_grids = Counter(mh.display(q.my_grid) for q in qsos if q.my_grid)
        home = mygrid or (logged_grids.most_common(1)[0][0] if logged_grids else "") or \
            QgsSettings().value(SETTINGS_MYGRID, "", type=str)
        home = home if mh.is_valid(home or "") else ""
        if len(logged_grids) > 1:
            feedback.pushInfo(self.tr("Logged from {} grids: {}. Lines start at each QSO's own MY_GRIDSQUARE; "
                                      "station distances are from {}.").format(
                len(logged_grids), ", ".join(g for g, _ in logged_grids.most_common(8)), home or "?"))
        if mycall:
            QgsSettings().setValue(SETTINGS_MYCALL, mycall)
        if mygrid:
            QgsSettings().setValue(SETTINGS_MYGRID, mygrid)
        my_calls = Counter(q.my_call for q in qsos if q.my_call)
        me = mycall or (my_calls.most_common(1)[0][0] if my_calls else "ME")

        # Worked stations
        fields = worked_fields()
        sink, dest = self.parameterAsSink(parameters, self.STATIONS, context, fields, QgsWkbTypes.Point, WGS84)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.STATIONS))
        placed = no_grid = 0
        for w in adif.worked(qsos):
            if not w.grid:
                no_grid += 1
                continue
            dist = bear = None
            if home:
                dist, bear = (round(v, 1) for v in mh.distance_bearing(home, w.grid))
            n, takeoff = _hops.likely_hops(dist)
            f = QgsFeature(fields)
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*mh.center(w.grid))))
            f.setAttributes([w.call, w.grid, "worked", w.qsos, w.bands, w.modes, utc_qdatetime(w.first_utc),
                             utc_qdatetime(w.last_utc), w.sent_best_db, w.rcvd_best_db, w.confirmed, w.country,
                             dist, bear, n, takeoff, "adif"])
            sink.addFeature(f, QgsFeatureSink.FastInsert)
            placed += 1
        feedback.pushInfo(self.tr("Worked stations: {} placed, {} without a grid or location").format(placed, no_grid))
        prop = {"kind": "adif", "files": files, "mycall": me, "mygrid": home}
        styling.attach_styler(context, dest, self.tr("Worked stations (ADIF)"), styling.LayerStyler(
            worked_renderer, styling.callsign_labels, {LOG_PROPERTY: json.dumps(dict(prop, role="stations"))}))
        results = {self.STATIONS: dest}

        # QSO lines
        qfields = qso_fields()
        qsink, qdest = self.parameterAsSink(parameters, self.QSOS, context, qfields,
                                            QgsWkbTypes.MultiLineString, WGS84)
        if qsink is not None:
            drawn = skipped = 0
            for i, q in enumerate(qsos, 1):
                start_grid = q.my_grid or home
                if not (q.grid and start_grid and mh.is_valid(start_grid)):
                    skipped += 1
                    continue
                f = QgsFeature(qfields)
                f.setGeometry(exchange_geometry(start_grid, q.grid))
                for name, value in qso_attrs(i, q, q.my_call or me, mh.display(start_grid)).items():
                    f[name] = value
                qsink.addFeature(f, QgsFeatureSink.FastInsert)
                drawn += 1
            feedback.pushInfo(self.tr("QSO lines: {} drawn, {} missing their grid or yours").format(drawn, skipped))
            styling.attach_styler(context, qdest, self.tr("QSOs (ADIF)"), styling.LayerStyler(
                band_line_renderer, None, {LOG_PROPERTY: json.dumps(dict(prop, role="qsos"))}))
            results[self.QSOS] = qdest
        return results
