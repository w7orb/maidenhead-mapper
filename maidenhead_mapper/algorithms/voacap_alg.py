"""Processing algorithm: VOACAP predictions for observed paths, and the gap
between what was heard and what VOACAP expects (see core/voacap.py)."""

import os
from datetime import datetime, timezone

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsFields,
    QgsGraduatedSymbolRenderer,
    QgsLineSymbol,
    QgsPalLayerSettings,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProject,
    QgsRendererRange,
    QgsSettings,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QDateTime
from qgis.PyQt.QtGui import QColor

from ..core import maidenhead as mh
from ..core import voacap as vc
from . import styling
from .paths import SKIP_GEOMETRY_CHECK
from .qt_compat import DATETIME, DOUBLE, INT, STRING, field, utc_qdatetime

MONTHS = ["From each path's date", "January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
TIME_FIELDS = ("bin_start", "bin_end", "first_utc", "last_utc")
VALUE_FIELDS = ("band", "bands", "freq_mhz", "a_to_b_db", "b_to_a_db", "snr_db", "a_hears_b", "b_hears_a")

NEW_FIELDS = [("voacap_ok", INT), ("voacap_note", STRING), ("voacap_utc", DATETIME), ("voacap_ssn", DOUBLE),
              ("voacap_freq", DOUBLE), ("voacap_muf", DOUBLE), ("voacap_mufday", DOUBLE), ("voacap_mode", STRING),
              ("voacap_tangle_deg", DOUBLE), ("voacap_rel", DOUBLE), ("voacap_snr_hz", DOUBLE),
              ("voacap_snr_db", DOUBLE), ("voacap_snr_ab_db", DOUBLE), ("voacap_snr_ba_db", DOUBLE),
              ("voacap_sdbw", DOUBLE), ("obs_snr_db", DOUBLE), ("snr_gap_db", DOUBLE),
              ("gap_a_to_b_db", DOUBLE), ("gap_b_to_a_db", DOUBLE)]

# observed - predicted, dB: red = weaker than VOACAP expects, blue = stronger
GAP_CLASSES = [(-999, -20, "#b2182b", "heard ≥ 20 dB weaker than predicted"),
               (-20, -10, "#ef8a62", "10–20 dB weaker"),
               (-10, -3, "#f4c6a8", "3–10 dB weaker"),
               (-3, 3, "#9e9e9e", "within 3 dB"),
               (3, 10, "#a6cee3", "3–10 dB stronger"),
               (10, 20, "#4393c3", "10–20 dB stronger"),
               (20, 999, "#2166ac", "≥ 20 dB stronger than predicted")]
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


def cache_dir():
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "maidenhead_mapper", "voacap")


def _time(v):
    if isinstance(v, QDateTime) and v.isValid():
        return datetime.fromtimestamp(v.toSecsSinceEpoch(), timezone.utc)
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return None


def _plain(v):
    """QVariant NULLs and Qt types -> Python values."""
    if v is None:
        return None
    try:
        from qgis.PyQt.QtCore import QVariant
        if isinstance(v, QVariant) and v.isNull():
            return None
    except ImportError:
        pass
    if isinstance(v, (int, float, str)):
        return v
    return None


def gap_renderer():
    ranges = []
    for lo, hi, colour, label in GAP_CLASSES:
        sym = QgsLineSymbol.createSimple({"line_color": colour, "line_width": "0.6", "capstyle": "round"})
        ranges.append(QgsRendererRange(lo, hi, sym, label))
    none = QgsLineSymbol.createSimple({"line_color": "#bdbdbd", "line_width": "0.3", "line_style": "dot"})
    ranges.append(QgsRendererRange(-1e9, -1000, none, "no gap (nothing observed or no prediction)"))
    return QgsGraduatedSymbolRenderer("coalesce(\"snr_gap_db\", -10000)", ranges)


def gap_labels():
    fmt = QgsTextFormat()
    fmt.setSize(7)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.7)
    buf.setColor(QColor(255, 255, 255, 220))
    fmt.setBuffer(buf)
    s = QgsPalLayerSettings()
    s.isExpression = True
    s.fieldName = ("if(\"snr_gap_db\" IS NULL, '', concat(if(\"snr_gap_db\" > 0, '+', ''), "
                   "format_number(\"snr_gap_db\", 0), ' dB'))")
    s.setFormat(fmt)
    return QgsVectorLayerSimpleLabeling(s)


class VoacapPathsAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    FOLDER = "FOLDER"
    MONTH = "MONTH"
    SSN = "SSN"
    POWER = "POWER"
    TX_GAIN = "TX_GAIN"
    RX_GAIN = "RX_GAIN"
    NOISE = "NOISE"
    REQ_SNR = "REQ_SNR"
    LABELS = "LABELS"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate("VoacapPathsAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return VoacapPathsAlgorithm()

    def name(self):
        return "voacap_paths"

    def displayName(self):  # noqa: N802
        return self.tr("Predict paths with VOACAP")

    def group(self):
        return self.tr("Ionosphere")

    def groupId(self):  # noqa: N802
        return "ionosphere"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Runs VOACAP (installed by you: the free NTIA/ITS package in C:\\itshfbc on Windows, or voacapl "
            "on Linux/macOS) for every path in a line layer from this plugin: exchanges, corridors, "
            "corridors over time, or Check paths output. It adds VOACAP's prediction and the gap between "
            "what was heard and what VOACAP expects.\n\n"
            "Each path is predicted both ways (A→B and B→A) at its band's FT8 frequency and its hour "
            "(corridors over time: the bin's start hour; corridors: the middle of first..last; exchanges: "
            "the nearest hour), with the month's sunspot number.\n\n"
            "Fields:\n"
            "• voacap_muf, voacap_mode (e.g. 1F2, 2F2), voacap_tangle_deg (takeoff angle)\n"
            "• voacap_rel: share of days in the month the FT8 decode threshold is reached (0–1)\n"
            "• voacap_snr_db: predicted SNR as WSJT-X reports it (2500 Hz) = voacap_snr_hz (VOACAP's own, in "
            "1 Hz) − 34 dB; the mean of both directions, with each direction in voacap_snr_ab_db / _ba_db\n"
            "• snr_gap_db = observed − predicted (obs_snr_db: the layer's snr_db, or the mean of the two "
            "reports); gap_a_to_b_db / gap_b_to_a_db per direction. Positive: heard louder than VOACAP "
            "expects.\n\n"
            "Read the gaps over many paths and days, not one by one. VOACAP gives monthly medians, and "
            "assumes both stations have the power and antennas set here. Only decoded signals are "
            "reported, so observed SNRs near the −21 dB floor make the gap look better than it is.\n\n"
            "VOACAP is not included with the plugin. Get it free from NTIA/ITS: "
            "https://its.ntia.gov/software/high-frequency/voacap-propagation-model/ (or see voacap.com). "
            "Results are cached, so a second run on the same paths is fast.")

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Paths (exchange or corridor lines)"), [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFile(
            self.FOLDER, self.tr("VOACAP folder (itshfbc; empty = the saved one or C:\\itshfbc)"),
            behavior=QgsProcessingParameterFile.Folder, optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.MONTH, self.tr("Month"), options=[self.tr(m) for m in MONTHS], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SSN, self.tr("Sunspot number SSN (0 = look it up for each path's month)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0, maxValue=300.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.POWER, self.tr("Transmit power at each end (W)"), QgsProcessingParameterNumber.Double,
            defaultValue=100.0, minValue=0.1, maxValue=100000.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.TX_GAIN, self.tr("Transmit antenna gain (dBi, all directions and angles)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=-30.0, maxValue=30.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.RX_GAIN, self.tr("Receive antenna gain (dBi, all directions and angles)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=-30.0, maxValue=30.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.NOISE, self.tr("Man-made noise at the receiver (at 3 MHz, in 1 Hz)"),
            options=[self.tr(n) for n, _ in vc.NOISE_LEVELS], defaultValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.REQ_SNR, self.tr("Required SNR for REL (dB, as WSJT-X reports it; FT8 decodes to about −21)"),
            QgsProcessingParameterNumber.Double, defaultValue=-21.0, minValue=-40.0, maxValue=30.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.LABELS, self.tr("Label paths with the gap"), defaultValue=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Paths with VOACAP"), QgsProcessing.TypeVectorLine))

    # -- reading ---------------------------------------------------------------
    def _endpoints(self, f, names, xform):
        """(lon_a, lat_a, lon_b, lat_b): the line's first and last points, else grid_a/grid_b."""
        g = f.geometry()
        if g is not None and not g.isEmpty():
            pts = [p for part in g.constParts() for p in part.vertices()]
            if len(pts) >= 2:
                a, b = pts[0], pts[-1]
                if xform is not None:
                    from qgis.core import QgsPointXY
                    pa, pb = xform.transform(QgsPointXY(a.x(), a.y())), xform.transform(QgsPointXY(b.x(), b.y()))
                    return pa.x(), pa.y(), pb.x(), pb.y()
                return a.x(), a.y(), b.x(), b.y()
        if "grid_a" in names and "grid_b" in names:
            ga, gb = f["grid_a"], f["grid_b"]
            if isinstance(ga, str) and isinstance(gb, str) and mh.is_valid(ga) and mh.is_valid(gb):
                (x1, y1), (x2, y2) = mh.center(ga), mh.center(gb)
                return x1, y1, x2, y2
        return None

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        names = source.fields().names()
        if not any(n in names for n in ("band", "bands", "freq_mhz")):
            raise QgsProcessingException(self.tr(
                "The layer needs a band (band, bands or freq_mhz field): use exchanges, corridors or "
                "Check paths output from this plugin."))
        folder = (self.parameterAsFile(parameters, self.FOLDER, context) or "").strip()
        settings_folder = QgsSettings().value(vc.SETTINGS_KEY, "", type=str)
        install, why = None, ""
        try:
            install = vc.find_install(folder or settings_folder)
            feedback.pushInfo(self.tr("VOACAP: {} ({})").format(install.exe, install.root))
            if folder and folder != settings_folder:
                QgsSettings().setValue(vc.SETTINGS_KEY, folder)
        except vc.NotInstalled as exc:
            why = str(exc)

        settings = vc.Settings(
            power_w=self.parameterAsDouble(parameters, self.POWER, context),
            tx_gain_dbi=self.parameterAsDouble(parameters, self.TX_GAIN, context),
            rx_gain_dbi=self.parameterAsDouble(parameters, self.RX_GAIN, context),
            noise_dbw=vc.NOISE_LEVELS[self.parameterAsEnum(parameters, self.NOISE, context)][1],
            req_snr_ft8=self.parameterAsDouble(parameters, self.REQ_SNR, context))
        month_override = self.parameterAsEnum(parameters, self.MONTH, context)
        ssn_manual = self.parameterAsDouble(parameters, self.SSN, context) or None
        smoothed = monthly = essn = None
        if not ssn_manual:
            from .ionosphere import _load, load_sunspots
            smoothed, monthly = load_sunspots(feedback)
            try:
                essn = _load("essn.json")
            except (OSError, ValueError):
                essn = None

        xform = None
        if source.sourceCrs().isValid() and source.sourceCrs() != WGS84:
            xform = QgsCoordinateTransform(source.sourceCrs(), WGS84, QgsProject.instance())

        # 1. read every path
        paths, requests, ssn_sources = [], [], {}
        for f in source.getFeatures(QgsFeatureRequest(), SKIP_GEOMETRY_CHECK):
            if feedback.isCanceled():
                return {}
            attrs = {n: _time(f[n]) for n in TIME_FIELDS if n in names}
            attrs.update({n: _plain(f[n]) for n in VALUE_FIELDS if n in names})
            if "cell_a" in names:
                attrs["cell_a"] = _plain(f["cell_a"])
            p = {"f": f, "attrs": attrs, "notes": [], "req": None}
            paths.append(p)
            ends = self._endpoints(f, names, xform)
            when, tnote = vc.path_time(attrs)
            band, freq, bnote = vc.path_band(attrs)
            p["notes"] += [n for n in (tnote, bnote) if n]
            if ends is None:
                p["notes"].insert(0, "no endpoints")
                continue
            if when is None or freq is None:
                continue
            month = month_override or when.month
            if ssn_manual:
                ssn, src = ssn_manual, "given"
            else:
                ssn, src = vc.ssn_for(when.replace(month=month, day=1), smoothed, monthly, essn)
                if ssn is None:
                    p["notes"].insert(0, f"no sunspot number for {when.year}-{month:02d}: enter one")
                    continue
            ssn_sources[src.split(" (")[0]] = ssn_sources.get(src.split(" (")[0], 0) + 1
            lon_a, lat_a, lon_b, lat_b = ends
            ab = vc.Request(lat_a, lon_a, lat_b, lon_b, when.year, month, ssn, when.hour, freq)
            ba = vc.Request(lat_b, lon_b, lat_a, lon_a, when.year, month, ssn, when.hour, freq)
            p.update(req=(ab, ba), when=when, ssn=ssn, freq=freq)
            requests += [ab, ba]
        feedback.pushInfo(self.tr("{} path(s); {} to predict (both directions)").format(
            len(paths), sum(1 for p in paths if p["req"])))
        if ssn_sources:
            feedback.pushInfo(self.tr("Sunspot number: {}").format(
                ", ".join(f"{k} ({n})" for k, n in ssn_sources.items())))

        # 2. predict (cache, then VOACAP in batches)
        cache = vc.Cache(os.path.join(cache_dir(), "predictions.json"))
        feedback.setProgressText(self.tr("Running VOACAP…"))
        try:
            preds = vc.predict(requests, settings, install, cache,
                               progress=lambda d, n: feedback.setProgress(100.0 * d / max(n, 1)),
                               cancelled=feedback.isCanceled, keep_dir=cache_dir())
        except vc.NotInstalled:
            raise QgsProcessingException(why or vc.not_installed_message())
        except vc.VoacapError as exc:
            raise QgsProcessingException(self.tr("VOACAP failed: {}").format(exc))
        finally:
            cache.save()

        # 3. write
        out_fields = QgsFields(source.fields())
        for n, kind in NEW_FIELDS:
            if out_fields.indexOf(n) < 0:
                out_fields.append(field(n, kind, 0))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, out_fields, source.wkbType(),
                                          source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        rows = []
        for p in paths:
            f = p["f"]
            out = QgsFeature(out_fields)
            out.setGeometry(f.geometry())
            for n in names:
                out[n] = f[n]
            vals = {"voacap_ok": 0}
            if p["req"]:
                ab, ba = p["req"]
                vals = vc.combine(preds.get(ab.key(settings)), preds.get(ba.key(settings)),
                                  vc.observed(p["attrs"]))
                out["voacap_utc"] = utc_qdatetime(p["when"])
                out["voacap_ssn"], out["voacap_freq"] = round(p["ssn"], 1), p["freq"]
            notes = ([vals.pop("voacap_note")] if "voacap_note" in vals else []) + p["notes"]
            if not p["req"] and not notes:
                notes = ["no time or band"]
            out["voacap_note"] = "; ".join(notes) or None
            for k, v in vals.items():
                out[k] = v
            rows.append(vals)
            sink.addFeature(out, QgsFeatureSink.FastInsert)

        s = vc.summary(rows)
        feedback.pushInfo(self.tr("{} of {} path(s) predicted; {} with an observed SNR to compare").format(
            s["predicted"], s["paths"], s["with_gap"]))
        if "median_gap" in s:
            feedback.pushInfo(self.tr("Gap (observed − predicted, dB): median {:+.1f}, middle half {:+.1f} to "
                                      "{:+.1f}").format(s["median_gap"], s["q1_gap"], s["q3_gap"]))
        if "share_rel_below_10" in s:
            feedback.pushInfo(self.tr("{} of {} observed path(s) ({:.0%}) were given REL below 10% by VOACAP, "
                                      "i.e. heard on a path it rates as rarely open").format(
                s["rel_below_10"], s["observed"], s["share_rel_below_10"]))
        feedback.pushInfo(self.tr("VOACAP predicts monthly medians: judge the gap over many paths and days, "
                                  "not one evening."))
        labels = self.parameterAsBool(parameters, self.LABELS, context)
        styling.attach_styler(context, dest, self.tr("{} · VOACAP").format(source.sourceName() or "Paths"),
                              styling.LayerStyler(gap_renderer, gap_labels if labels else None))
        return {self.OUTPUT: dest}
