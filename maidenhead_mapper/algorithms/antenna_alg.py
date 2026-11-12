"""Processing algorithm: compare two antennas (or receivers) from their decodes."""

import datetime as _dt
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
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
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
from ..core import antenna_pattern as ap
from ..core import maidenhead as mh
from . import styling
from .import_psk import WGS84
from .paths import SETTINGS_MYGRID
from .qt_compat import DOUBLE, INT, STRING, field

# (low, high, colour, label): positive = antenna A stronger
CLASSES = [(-99, -6, "#5e3c99", "B ≥ 6 dB better"), (-6, -2, "#b2abd2", "B 2–6 dB better"),
           (-2, 2, "#f7f7f7", "about equal"), (2, 6, "#a6dba0", "A 2–6 dB better"),
           (6, 99, "#1b7837", "A ≥ 6 dB better")]


SOURCES = ["Ionosphere: KC2G when recent, else the CCIR model", "Ionosphere: CCIR model only",
           "Fixed 300 km layer (geometric)"]
SRC_AUTO, SRC_MODEL, SRC_FIXED = 0, 1, 2
SIDES = ["A", "B"]


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
    SOURCE = "SOURCE"
    R12 = "R12"
    SECTORS = "SECTORS"
    STATIONS = "STATIONS"
    TABLE = "TABLE"
    REFERENCE_PATTERN = "REFERENCE_PATTERN"
    REFERENCE_SIDE = "REFERENCE_SIDE"
    ORIENTATION_DEG = "ORIENTATION_DEG"
    DECODES = "DECODES"

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
            "• a table by distance and by estimated takeoff angle\n\n"
            "Takeoff angle is estimated, never measured: from the distance, the F2 layer height at the "
            "reflection points at the time of each decode (KC2G when the decodes are recent, else the "
            "CCIR monthly-median model), the fewest hops off that height, and the ITU-R P.533 basic MUF. "
            "Paths of 500–2,300 km on 12m and up that F2 can't support are taken as sporadic-E (one hop "
            "off ~105 km) and get their own 'Es' bin. Choose 'Fixed 300 km' for the old geometric "
            "estimate; the station layer keeps it as takeoff_300_deg either way. Angles are worked out "
            "per station and 15 minutes, so a station's angle changes as the layer rises and falls; "
            "takeoff_min/max_deg show the range. Expect ±1–2° from layer-height error, and a path may "
            "really be using more hops than the fewest.\n\n"
            "The pattern is relative (A versus B), not absolute gain. Fading makes single pairs noisy: "
            "trust bins with ~100 pairs or more.\n\n"
            "Reference antenna (optional): give a modelled pattern for one antenna (usually B, a simple "
            "dipole at a known height): a CSV of azimuth_deg, elevation_deg, gain_dbi, or a NEC-2 / 4nec2 "
            ".out file. Orientation is the compass bearing of the pattern's azimuth 0 (the model's +X "
            "axis). Each pair then gets the reference's modelled gain at the station's bearing and the "
            "estimated takeoff angle (ref_gain_dbi), and the other antenna's absolute gain = reference "
            "gain + its measured difference (abs_gain_dbi). Bins add abs_dbi (median), abs_lo_dbi and "
            "abs_hi_dbi (95% interval of that median), abs_n and ref_dbi. Only pairs carry absolute "
            "values; decodes one antenna missed are counted as before, so read decode_adv too. The "
            "result is only as good as the model, the takeoff estimate and the receivers' match.")

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
        self.addParameter(QgsProcessingParameterEnum(
            self.SOURCE, self.tr("Takeoff angle from"), options=SOURCES, defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.R12, self.tr("Sunspot number R12 for the model (0 = look it up)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0, maxValue=400.0))
        self.addParameter(QgsProcessingParameterFile(
            self.REFERENCE_PATTERN, self.tr("Reference antenna pattern (CSV or NEC-2 .out; optional)"),
            optional=True, fileFilter="Antenna patterns (*.csv *.txt *.out *.tsv);;All files (*.*)"))
        self.addParameter(QgsProcessingParameterEnum(
            self.REFERENCE_SIDE, self.tr("Reference pattern is antenna"), options=SIDES, defaultValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.ORIENTATION_DEG,
            self.tr("Reference orientation: compass bearing of the pattern's azimuth 0 / +X (°)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=-360.0, maxValue=360.0))
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
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.DECODES, self.tr("Antenna A/B matched decodes"), QgsProcessing.TypeVector,
            optional=True, createByDefault=False))

    # -- inputs ------------------------------------------------------------------
    def _from_session(self, layer, parameters, context, feedback):
        from ..live.session import log_props, read_log
        props = log_props(layer) or {}       # tagged, or recognised from a session GeoPackage
        if props.get("kind") == "session" and props.get("gpkg"):
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

    def _angle_source(self, parameters, context, feedback, matches):
        """(angle_at for antenna.compare or None for the fixed 300 km estimate,
        Counter of reasons a time had no ionosphere)."""
        choice = self.parameterAsEnum(parameters, self.SOURCE, context)
        if choice == SRC_FIXED:
            return None, Counter()
        from . import ionosphere as io
        slots = [m.slot for m in matches if isinstance(m.slot, _dt.datetime)]
        now = _dt.datetime.now(_dt.timezone.utc)
        recent = bool(slots) and (now - max(slots)).total_seconds() < 3 * 3600
        mode = io.MODE_AUTO if (choice == SRC_AUTO and recent) else io.MODE_MODEL
        try:
            src = io.IonoSource(feedback, mode, r12=self.parameterAsDouble(parameters, self.R12, context) or None)
        except (QgsProcessingException, OSError, ValueError) as exc:
            feedback.pushWarning(self.tr("No ionosphere data ({}); using a fixed 300 km layer").format(exc))
            return None, Counter()
        notes = Counter()

        def conditions_for(when):
            cond, label, note = src.conditions(when)
            if cond is None:
                notes[note] += 1
                return None, None
            return cond, antenna.KC2G if label == "KC2G" else antenna.MODEL

        return antenna.iono_angle(conditions_for), notes

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

        reference, side = self._reference(parameters, context, feedback)
        matches = antenna.match(da, db)
        angle_at, notes = self._angle_source(parameters, context, feedback, matches)
        c = antenna.compare(matches, antenna.learn_grids(da, db), grid, angle_at, reference, side)
        for note, n in notes.items():
            feedback.pushWarning(self.tr("{:,} station-slot(s) used the fixed 300 km layer: {}").format(n, note))
        t = c.total
        if not t.n_both:
            raise QgsProcessingException(self.tr("No message was decoded by both in the same slot. Same band, "
                                                 "same time, clocks in sync?"))
        feedback.pushInfo(self.tr("Decoded by both: {:,}; only A: {:,}; only B: {:,}. Median A−B {:+.1f} dB; "
                                  "decode advantage {:+.1%}").format(t.n_both, t.only_a, t.only_b, t.median_db,
                                                                     t.decode_advantage))
        feedback.pushInfo(self.tr("Takeoff angles: {}; modes: {}").format(
            ", ".join(f"{k} {n:,}" for k, n in sorted(c.methods.items())) or "none",
            ", ".join(f"{k} {n:,}" for k, n in sorted(c.modes.items())) or "none"))
        for b in c.by_takeoff.values():
            feedback.pushInfo("  {:<14} {:>5} pairs  +{}/{}  median {}".format(
                b.label, b.n_both, b.only_a, b.only_b,
                "—" if b.median_db is None else f"{b.median_db:+.1f} dB"))
        if c.unplaced:
            feedback.pushInfo(self.tr("{:,} decode(s) from stations that never sent a grid are in the totals "
                                      "but not the direction/distance bins").format(c.unplaced))
        if reference is not None:
            other = "A" if side == "B" else "B"
            for why, n in c.ref_notes.items():
                feedback.pushWarning(self.tr("{:,} pair(s) have no reference gain: {}").format(n, why))
            ci = t.abs_ci_dbi
            feedback.pushInfo(self.tr("Antenna {} absolute gain (reference {}): median {} dBi{} over {:,} pairs; "
                                      "reference median {} dBi").format(
                other, side, "—" if t.median_abs_dbi is None else f"{t.median_abs_dbi:+.1f}",
                f" (95% {ci[0]:+.1f} to {ci[1]:+.1f})" if ci else "", t.abs_n,
                "—" if t.median_ref_dbi is None else f"{t.median_ref_dbi:+.1f}"))
            for b in c.by_takeoff.values():
                if b.abs_n:
                    feedback.pushInfo("  {:<14} {} {:+.1f} dBi over {:,} pairs".format(
                        b.label, other, b.median_abs_dbi, b.abs_n))

        def stats_fields():
            fs = QgsFields()
            for name, kind in (("n_both", INT), ("only_a", INT), ("only_b", INT), ("median_db", DOUBLE),
                               ("iqr_lo_db", DOUBLE), ("iqr_hi_db", DOUBLE), ("decode_adv", DOUBLE)):
                fs.append(field(name, kind, 0))
            if reference is not None:
                for name, kind in (("abs_n", INT), ("abs_dbi", DOUBLE), ("abs_lo_dbi", DOUBLE),
                                   ("abs_hi_dbi", DOUBLE), ("ref_dbi", DOUBLE)):
                    fs.append(field(name, kind, 0))
            return fs

        def stats(b):
            iqr = b.iqr_db or (None, None)
            adv = b.decode_advantage
            out = [b.n_both, b.only_a, b.only_b, b.median_db, iqr[0], iqr[1],
                   None if adv is None else round(adv, 3)]
            if reference is not None:
                ci = b.abs_ci_dbi or (None, None)
                out += [b.abs_n, b.median_abs_dbi, ci[0], ci[1], b.median_ref_dbi]
            return out

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
                           ("takeoff_deg", DOUBLE), ("takeoff_min_deg", DOUBLE), ("takeoff_max_deg", DOUBLE),
                           ("takeoff_300_deg", DOUBLE), ("hops", INT), ("hmf2_km", DOUBLE),
                           ("angle_method", STRING), ("prop_mode", STRING)):
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
                                    info["takeoff_deg"], info["takeoff_min_deg"], info["takeoff_max_deg"],
                                    info["takeoff_300_deg"], info["hops"], info["hmf2_km"], info["method"],
                                    info["mode"]] + stats(b))
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

        # one row per decode from a station with a grid (pairs and singles)
        df = QgsFields()
        cols = [("utc", STRING), ("call", STRING), ("mhz", DOUBLE), ("snr_a", INT), ("snr_b", INT),
                ("diff_db", INT), ("distance_km", DOUBLE), ("bearing_deg", DOUBLE), ("takeoff_deg", DOUBLE),
                ("prop_mode", STRING)]
        if reference is not None:
            cols += [("ref_gain_dbi", DOUBLE), ("abs_gain_dbi", DOUBLE)]
        for name, kind in cols:
            df.append(field(name, kind, 0))
        dsink, ddest = self.parameterAsSink(parameters, self.DECODES, context, df, QgsWkbTypes.NoGeometry)
        if dsink is not None:
            for r in c.decodes:
                slot = r["slot"]
                utc = slot.strftime("%Y-%m-%d %H:%M:%S") if isinstance(slot, _dt.datetime) else str(slot)
                vals = [utc, r["sender"], r["mhz"], r["snr_a"], r["snr_b"], r["diff_db"], r["distance_km"],
                        r["bearing_deg"], r["takeoff_deg"], r["mode"]]
                if reference is not None:
                    vals += [r["ref_gain_dbi"], r["abs_gain_dbi"]]
                feat = QgsFeature(df)
                feat.setAttributes(vals)
                dsink.addFeature(feat, QgsFeatureSink.FastInsert)
            results[self.DECODES] = ddest
        return results

    def _reference(self, parameters, context, feedback):
        """(PatternSet or None, "A"/"B") from the reference parameters."""
        path = self.parameterAsFile(parameters, self.REFERENCE_PATTERN, context)
        side = SIDES[self.parameterAsEnum(parameters, self.REFERENCE_SIDE, context)]
        if not path:
            return None, side
        orient = self.parameterAsDouble(parameters, self.ORIENTATION_DEG, context)
        try:
            ref = ap.read_pattern(path, orient)
        except ap.PatternError as exc:
            raise QgsProcessingException(self.tr("Reference pattern: {}").format(exc)) from None
        for p in ref.patterns:
            feedback.pushInfo(self.tr("Reference pattern ({}, antenna {}): {}{} azimuths × {} elevations "
                                      "({:g}–{:g}°), max {:+.1f} dBi; azimuth 0 points to {:g}°").format(
                ref.kind, side, f"{p.mhz:g} MHz, " if p.mhz else "", len(p.azimuths), len(p.elevations),
                p.elevations[0], p.elevations[-1], p.max_dbi, ref.orientation_deg))
        for w in ref.warnings:
            feedback.pushWarning(self.tr("Reference pattern: {}").format(w))
        return ref, side
