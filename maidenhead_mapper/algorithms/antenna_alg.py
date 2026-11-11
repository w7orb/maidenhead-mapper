"""Processing algorithm: compare two antennas (or receivers) from their decodes."""

import json
from collections import Counter

from qgis.core import (
    QgsClassificationRange,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsGraduatedSymbolRenderer,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsRendererRange,
    QgsSettings,
    QgsSymbol,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor

from ..core import alltxt, antenna
from ..core import maidenhead as mh
from . import styling
from .import_alltxt import LOG_PROPERTY
from .import_psk import WGS84
from .paths import SETTINGS_MYGRID
from .qt_compat import DOUBLE, INT, STRING, field

# (low, high, colour, label): positive = antenna A stronger
CLASSES = [(-99, -6, "#5e3c99", "B ≥ 6 dB better"), (-6, -2, "#b2abd2", "B 2–6 dB better"),
           (-2, 2, "#f7f7f7", "about equal"), (2, 6, "#a6dba0", "A 2–6 dB better"),
           (6, 99, "#1b7837", "A ≥ 6 dB better")]


def _graduated(attr, geometry):
    ranges = []
    for lo, hi, colour, label in CLASSES:
        sym = QgsSymbol.defaultSymbol(geometry)
        sym.setColor(QColor(colour))
        if geometry == QgsWkbTypes.PolygonGeometry:
            sym.setOpacity(0.75)
        else:
            sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": colour, "size": "2.6",
                                                "outline_color": "80,80,80,200", "outline_width": "0.2"})
        ranges.append(QgsRendererRange(QgsClassificationRange(label, lo, hi), sym))
    r = QgsGraduatedSymbolRenderer(attr, ranges)
    return r


def _labels(expr):
    s = QgsPalLayerSettings()
    s.fieldName = expr
    s.isExpression = True
    return QgsVectorLayerSimpleLabeling(s)


class AntennaCompareAlgorithm(QgsProcessingAlgorithm):
    FILES_A = "FILES_A"
    FILES_B = "FILES_B"
    SESSION = "SESSION"
    CLIENT_A = "CLIENT_A"
    CLIENT_B = "CLIENT_B"
    MYGRID = "MYGRID"
    RADIUS = "RADIUS"
    SECTORS = "SECTORS"
    STATIONS = "STATIONS"
    TABLE = "TABLE"

    def tr(self, text):
        return QCoreApplication.translate("AntennaCompareAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return AntennaCompareAlgorithm()

    def name(self):
        return "antenna_compare"

    def displayName(self):  # noqa: N802
        return self.tr("Antenna A/B comparison")

    def group(self):
        return self.tr("Stations")

    def groupId(self):  # noqa: N802
        return "stations"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Compares two antennas (or receivers) that decoded the same band at the same time. The "
            "same message in the same slot on both means the same transmitter and path, so the SNR "
            "difference (A minus B) is the difference between the antennas towards that station, plus "
            "a fixed receiver offset.\n\n"
            "Give either two sets of ALL.TXT files (one per receiver, same period), or a live/saved "
            "session layer with two WSJT-X instances (their ids; empty = the two busiest).\n\n"
            "Outputs:\n"
            "• direction wedges around your grid: median A−B per 16-point direction, with decodes only "
            "A or only B got (below the decode floor the weaker antenna reports nothing, so these "
            "counts matter as much as the median)\n"
            "• one point per station heard, with its own median and counts\n"
            "• a table by distance and by estimated takeoff angle (F2 at 300 km)\n\n"
            "The pattern is relative (A versus B), not absolute gain. Fading makes single pairs noisy: "
            "trust bins with ~100 pairs or more.")

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.FILES_A, self.tr("Receiver A: ALL.TXT file(s)"), QgsProcessing.TypeFile, optional=True))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.FILES_B, self.tr("Receiver B: ALL.TXT file(s)"), QgsProcessing.TypeFile, optional=True))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.SESSION, self.tr("…or a live / saved session layer (two WSJT-X instances)"), optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.CLIENT_A, self.tr("Session: WSJT-X id for A (empty = busiest)"), optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.CLIENT_B, self.tr("Session: WSJT-X id for B (empty = second busiest)"), optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.MYGRID, self.tr("My grid (where both antennas are)"), optional=True,
            defaultValue=QgsSettings().value(SETTINGS_MYGRID, "", type=str)))
        self.addParameter(QgsProcessingParameterNumber(
            self.RADIUS, self.tr("Wedge radius on the map (km)"), QgsProcessingParameterNumber.Double,
            defaultValue=2500.0, minValue=100.0, maxValue=15000.0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.SECTORS, self.tr("Antenna A/B by direction"), QgsProcessing.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.STATIONS, self.tr("Antenna A/B by station"), QgsProcessing.TypeVectorPoint,
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.TABLE, self.tr("Antenna A/B by distance and takeoff"), QgsProcessing.TypeVector,
            optional=True, createByDefault=True))

    # -- inputs ------------------------------------------------------------------
    def _from_session(self, layer, parameters, context, feedback):
        try:
            props = json.loads(layer.customProperty(LOG_PROPERTY) or "{}")
        except ValueError:
            props = {}
        if props.get("kind") == "session" and props.get("gpkg"):
            from ..live.session import read_log
            log = read_log(props["gpkg"])
        else:
            from ..live.dock import CONTROLLERS
            ctl = CONTROLLERS.get(props.get("group", ""))
            if ctl is None:
                raise QgsProcessingException(self.tr("That layer isn't a saved session or the running live map."))
            log = list(ctl.model.log)
        from ..core import wsjtx_udp as w
        busy = Counter(m.id for _, m in log if isinstance(m, w.Decode))
        feedback.pushInfo(self.tr("WSJT-X instances in the session: {}").format(
            ", ".join(f"{k} ({n:,} decodes)" for k, n in busy.most_common()) or "none"))
        ids = [k for k, _ in busy.most_common()]
        a = self.parameterAsString(parameters, self.CLIENT_A, context).strip() or (ids[0] if ids else "")
        b = self.parameterAsString(parameters, self.CLIENT_B, context).strip() or next((k for k in ids if k != a), "")
        if not a or not b or a == b:
            raise QgsProcessingException(self.tr("The session needs two WSJT-X instances decoding at once."))
        grid = props.get("mygrid") or next((m.de_grid for _, m in log if getattr(m, "id", None) == a
                                            and getattr(m, "de_grid", "")), "")
        return antenna.decodes_from_log(log, a), antenna.decodes_from_log(log, b), a, b, grid

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        files_a = [p for p in self.parameterAsFileList(parameters, self.FILES_A, context) if p]
        files_b = [p for p in self.parameterAsFileList(parameters, self.FILES_B, context) if p]
        session = self.parameterAsVectorLayer(parameters, self.SESSION, context)
        grid = ""
        if files_a and files_b:
            da = antenna.decodes_from_rows(alltxt.read(files_a))
            db = antenna.decodes_from_rows(alltxt.read(files_b))
            label_a, label_b = "A", "B"
        elif session is not None:
            da, db, label_a, label_b, grid = self._from_session(session, parameters, context, feedback)
        else:
            raise QgsProcessingException(self.tr("Give ALL.TXT files for both A and B, or a session layer."))
        grid = self.parameterAsString(parameters, self.MYGRID, context).strip() or grid
        if not mh.is_valid(grid or ""):
            raise QgsProcessingException(self.tr("Enter My grid (where the antennas are)."))
        feedback.pushInfo(self.tr("A ({}): {:,} decodes; B ({}): {:,} decodes; my grid {}").format(
            label_a, len(da), label_b, len(db), grid))

        c = antenna.compare(antenna.match(da, db), antenna.learn_grids(da, db), grid)
        t = c.total
        if not t.n_both:
            raise QgsProcessingException(self.tr("No message was decoded by both in the same slot. Same band, "
                                                 "same time, clocks in sync?"))
        feedback.pushInfo(self.tr("Decoded by both: {:,}; only A: {:,}; only B: {:,}. Median A−B {:+.1f} dB; "
                                  "decode advantage {:+.1%}").format(t.n_both, t.only_a, t.only_b, t.median_db,
                                                                     t.decode_advantage))
        if c.unplaced:
            feedback.pushInfo(self.tr("{:,} decode(s) from stations that never sent a grid are in the totals "
                                      "but not the direction/distance bins").format(c.unplaced))

        def stats_fields():
            fs = QgsFields()
            for name, kind in (("n_both", INT), ("only_a", INT), ("only_b", INT), ("median_db", DOUBLE),
                               ("iqr_lo_db", DOUBLE), ("iqr_hi_db", DOUBLE), ("decode_adv", DOUBLE)):
                fs.append(field(name, kind, 0))
            return fs

        def stats(b):
            iqr = b.iqr_db or (None, None)
            adv = b.decode_advantage
            return [b.n_both, b.only_a, b.only_b, b.median_db, iqr[0], iqr[1],
                    None if adv is None else round(adv, 3)]

        # direction wedges
        sf = QgsFields()
        sf.append(field("sector", STRING, 4))
        sf.append(field("bearing_deg", DOUBLE, 0))
        for f_ in stats_fields():
            sf.append(f_)
        sink, dest = self.parameterAsSink(parameters, self.SECTORS, context, sf, QgsWkbTypes.Polygon, WGS84)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.SECTORS))
        lon0, lat0 = mh.center(grid)
        radius = self.parameterAsDouble(parameters, self.RADIUS, context)
        width = 360.0 / antenna.SECTORS
        for k in range(antenna.SECTORS):
            b = c.by_sector[k]
            if not (b.n_both or b.only_a or b.only_b):
                continue
            centre = k * width
            arc = [antenna.destination(lon0, lat0, centre - width / 2 + i * width / 8, radius) for i in range(9)]
            ring = [QgsPointXY(lon0, lat0)] + [QgsPointXY(x, y) for x, y in arc] + [QgsPointXY(lon0, lat0)]
            feat = QgsFeature(sf)
            feat.setGeometry(QgsGeometry.fromPolygonXY([ring]))
            feat.setAttributes([b.label, centre] + stats(b))
            sink.addFeature(feat, QgsFeatureSink.FastInsert)
        results = {self.SECTORS: dest}
        styling.attach_styler(context, dest, self.tr("Antenna {} vs {} by direction").format(label_a, label_b),
                              styling.LayerStyler(
                                  lambda: _graduated("median_db", QgsWkbTypes.PolygonGeometry),
                                  lambda: _labels("\"sector\" || '  ' || if(\"median_db\" IS NULL, '—', "
                                                  "format_number(\"median_db\", 0) || ' dB') || '\\n' || "
                                                  "\"n_both\" || ' pairs  +' || \"only_a\" || '/' || \"only_b\"")))

        # stations
        pf = QgsFields()
        for name, kind in (("call", STRING), ("grid", STRING), ("distance_km", DOUBLE), ("bearing_deg", DOUBLE),
                           ("takeoff_deg", DOUBLE)):
            pf.append(field(name, kind, 0))
        for f_ in stats_fields():
            pf.append(f_)
        psink, pdest = self.parameterAsSink(parameters, self.STATIONS, context, pf, QgsWkbTypes.Point, WGS84)
        if psink is not None:
            for call, info in c.placed.items():
                b = c.by_station[call]
                feat = QgsFeature(pf)
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*mh.center(info["grid"]))))
                feat.setAttributes([call, info["grid"], info["distance_km"], info["bearing_deg"],
                                    info["takeoff_deg"]] + stats(b))
                psink.addFeature(feat, QgsFeatureSink.FastInsert)
            results[self.STATIONS] = pdest
            styling.attach_styler(context, pdest, self.tr("Antenna {} vs {} by station").format(label_a, label_b),
                                  styling.LayerStyler(lambda: _graduated("median_db", QgsWkbTypes.PointGeometry)))

        # table
        tf = QgsFields()
        tf.append(field("by", STRING, 10))
        tf.append(field("bin", STRING, 20))
        for f_ in stats_fields():
            tf.append(f_)
        tsink, tdest = self.parameterAsSink(parameters, self.TABLE, context, tf, QgsWkbTypes.NoGeometry)
        if tsink is not None:
            rows = [("all", t)] + [("distance", b) for b in c.by_distance.values()] + \
                   [("takeoff", b) for b in c.by_takeoff.values()]
            for by, b in rows:
                feat = QgsFeature(tf)
                feat.setAttributes([by, b.label] + stats(b))
                tsink.addFeature(feat, QgsFeatureSink.FastInsert)
            results[self.TABLE] = tdest
        return results
