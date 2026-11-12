"""Processing algorithm: station balance and receive contrasts from exchanges."""

from collections import defaultdict

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsRendererCategory,
    QgsSettings,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..core import balance as bal
from ..core import maidenhead as mh
from ..core.psk import base_callsign
from . import styling
from .import_psk import SETTINGS_MYCALL, WGS84
from .qt_compat import DOUBLE, INT, STRING, field

VERDICT_COLOURS = {"loud but deaf": "#d62728", "balanced": "#2ca02c", "good ears": "#1f77b4",
                   "uncertain": "#bdbdbd"}


def _int(v):
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def _renderer():
    cats = []
    for verdict, colour in VERDICT_COLOURS.items():
        sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": colour, "size": "3",
                                            "outline_color": "255,255,255,220", "outline_width": "0.25"})
        cats.append(QgsRendererCategory(verdict, sym, verdict))
    return QgsCategorizedSymbolRenderer("verdict", cats)


class StationBalanceAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    MYCALL = "MYCALL"
    PER_BAND = "PER_BAND"
    MIN_N = "MIN_N"
    OUTPUT = "OUTPUT"
    EXCHANGES = "EXCHANGES"

    def tr(self, text):
        return QCoreApplication.translate("StationBalanceAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return StationBalanceAlgorithm()

    def name(self):
        return "station_balance"

    def displayName(self):  # noqa: N802
        return self.tr("Station balance from two-way reports")

    def group(self):
        return self.tr("Stations")

    def groupId(self):  # noqa: N802
        return "stations"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Uses FT8 exchanges (from the live map, a saved session or Import WSJT-X ALL.TXT) to "
            "compare stations.\n\n"
            "Balance: in an exchange each side reports how it hears the other. Path loss and both "
            "antennas are the same both ways, so the difference of the two reports is (power + "
            "receiver noise) of one station minus the other's. A fit over every two-way exchange "
            "gives each station's balance, centred on 0 within each group of connected stations:\n"
            "• loud but deaf (above +3 dB): strong signal and/or noisy receiver\n"
            "• good ears (below −3 dB): weak signal and/or quiet receiver\n"
            "• uncertain: too few usable exchanges, or a standard error over 3 dB\n\n"
            "std_err_db assumes the two reports of an exchange differ by about 6–8 dB of fading "
            "(measured from stations seen several times; stations seen once can't show it), so it "
            "is roughly 7 dB ÷ √(exchanges): about 3 dB needs 5–6 exchanges.\n\n"
            "Single reports fade by up to 30 dB and come from different 15 s slots (DK3BA 2024), so "
            "the fit is robust (Huber): one wild exchange barely moves it. Reports are also censored: "
            "at or below {floor} dB a signal is at the decode floor (decoded only on a fade-up); at "
            "or above +{comp} dB WSJT-X compresses strong signals. Such a report is used only as a "
            "bound (Tobit-style), and an exchange whose two bounds point opposite ways is left out. "
            "n_censored and n_dropped count these per station.\n\n"
            "Receive contrast: when you decode a signal and the station it was sent to reports it, "
            "hears_vs_me_db is how that station heard it minus how you did (median). Positive = they "
            "hear these signals better than you. Comparisons where either SNR is censored are "
            "skipped (n_vs_me_skipped). Paths differ, so trust stations with many exchanges in "
            "different directions.\n\n"
            "Needs lots of data: DK3BA recommends averaging at least 100 exchanges per station; an "
            "hour of decoding gives a few.").format(floor=bal.FLOOR_DB, comp=bal.COMPRESS_DB)

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.INPUT, self.tr("Exchange layer(s)"), QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterString(
            self.MYCALL, self.tr("My callsign (marks my own row)"), optional=True,
            defaultValue=QgsSettings().value(SETTINGS_MYCALL, "", type=str)))
        self.addParameter(QgsProcessingParameterBoolean(
            self.PER_BAND, self.tr("Separately per band (noise floors differ by band)"), defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_N, self.tr("Exchanges needed for a verdict"), QgsProcessingParameterNumber.Integer,
            defaultValue=3, minValue=1))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Station balance"), QgsProcessing.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.EXCHANGES, self.tr("Exchanges with contrasts"), QgsProcessing.TypeVectorLine,
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        layers = self.parameterAsLayerList(parameters, self.INPUT, context)
        need = {"call_a", "call_b", "a_hears_b", "b_hears_a", "me_hears_a", "me_hears_b", "grid_a", "grid_b"}
        rows = []   # (layer, feature, record dict)
        for lyr in layers:
            names = set(lyr.fields().names())
            if not need <= names:
                feedback.reportError(self.tr("Skipped {}: not an exchange layer").format(lyr.name()))
                continue
            for f in lyr.getFeatures():
                rows.append((lyr, f, {
                    "a": str(f["call_a"]).upper(), "b": str(f["call_b"]).upper(),
                    "ga": f["grid_a"], "gb": f["grid_b"],
                    "band": (f["band"] if "band" in names else None) or "",
                    "ab": _int(f["a_hears_b"]), "ba": _int(f["b_hears_a"]),
                    "ma": _int(f["me_hears_a"]), "mb": _int(f["me_hears_b"])}))
        if not rows:
            raise QgsProcessingException(self.tr("No exchange layers with report fields given."))
        per_band = self.parameterAsBool(parameters, self.PER_BAND, context)
        min_n = self.parameterAsInt(parameters, self.MIN_N, context)
        me = base_callsign(self.parameterAsString(parameters, self.MYCALL, context))

        grids = {}
        by_band = defaultdict(list)
        contrast_rows = defaultdict(list)
        for _, _, r in rows:
            for c, g in ((r["a"], r["ga"]), (r["b"], r["gb"])):
                if isinstance(g, str) and mh.is_valid(g):
                    grids[c] = g
            key = r["band"] if per_band else ""
            if r["ab"] is not None and r["ba"] is not None:
                by_band[key].append(bal.TwoWay(r["a"], r["b"], r["ab"], r["ba"]))
            contrast_rows[key] += bal.exchange_contrasts(r["a"], r["b"], r["ab"], r["ba"], r["ma"], r["mb"])

        fields = QgsFields()
        for name, kind in (("call", STRING), ("band", STRING), ("grid", STRING), ("balance_db", DOUBLE),
                           ("std_err_db", DOUBLE), ("n_two_way", INT), ("group_size", INT), ("verdict", STRING),
                           ("n_censored", INT), ("n_dropped", INT),
                           ("hears_vs_me_db", DOUBLE), ("n_vs_me", INT), ("n_vs_me_skipped", INT),
                           ("is_me", INT)):
            fields.append(field(name, kind, 0))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, QgsWkbTypes.Point, WGS84)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        written = unplaced = 0
        verdicts = defaultdict(int)
        for key in sorted(set(by_band) | set(contrast_rows)):
            solved = bal.solve(by_band.get(key, []))
            cons = bal.contrasts(contrast_rows.get(key, []))
            n_pairs = len(by_band.get(key, []))
            n_cens = sum(1 for p in by_band.get(key, []) if bal.pair_bound(p) != 0)
            feedback.pushInfo(self.tr("{}: {} two-way exchange(s) ({} with a censored report), {} station(s) "
                                      "balanced, {} with contrasts").format(
                key or "All bands", n_pairs, n_cens, len(solved), len(cons)))
            for call in sorted(set(solved) | set(cons)):
                g = grids.get(call)
                if not g:
                    unplaced += 1
                    continue
                b, c = solved.get(call), cons.get(call)
                v = bal.verdict(b, min_n) if b else "uncertain"
                verdicts[v] += 1
                f = QgsFeature(fields)
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*mh.center(g))))
                f.setAttributes([call, key, g, b.balance_db if b else None, b.std_err if b else None,
                                 b.n if b else 0, b.group_size if b else None, v,
                                 b.n_censored if b else 0, b.n_dropped if b else 0,
                                 round(c.hears_vs_me_db, 1) if c else None, c.n if c else 0,
                                 c.n_skipped if c else 0,
                                 int(bool(me) and base_callsign(call) == me)])
                sink.addFeature(f, QgsFeatureSink.FastInsert)
                written += 1
        feedback.pushInfo(self.tr("{} station row(s): {}; {} without a grid left out").format(
            written, ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items())), unplaced))
        all_c = [t - m for rs in contrast_rows.values() for _, t, m in rs if t is not None and m is not None
                 and not bal.censor_side(t) and not bal.censor_side(m)]
        if all_c:
            import statistics
            feedback.pushInfo(self.tr("Overall: the stations being called heard those signals a median {:+.1f} dB "
                                      "compared with you ({} comparisons)").format(statistics.median(all_c), len(all_c)))
        styling.attach_styler(context, dest, self.tr("Station balance"), styling.LayerStyler(
            _renderer, styling.callsign_labels))
        results = {self.OUTPUT: dest}

        src = layers[0]
        efields = QgsFields(src.fields())
        for name in ("report_diff_db", "b_vs_me_db", "a_vs_me_db"):
            if efields.indexOf(name) < 0:
                efields.append(field(name, INT, 0))
        esink, edest = self.parameterAsSink(parameters, self.EXCHANGES, context, efields,
                                            QgsWkbTypes.MultiLineString, WGS84)
        if esink is not None:
            for lyr, f, r in rows:
                out = QgsFeature(efields)
                out.setGeometry(f.geometry())
                for n in lyr.fields().names():
                    if efields.indexOf(n) >= 0:
                        out[n] = f[n]
                out["report_diff_db"] = r["ba"] - r["ab"] if r["ab"] is not None and r["ba"] is not None else None
                out["b_vs_me_db"] = r["ba"] - r["ma"] if r["ba"] is not None and r["ma"] is not None else None
                out["a_vs_me_db"] = r["ab"] - r["mb"] if r["ab"] is not None and r["mb"] is not None else None
                esink.addFeature(out, QgsFeatureSink.FastInsert)
            results[self.EXCHANGES] = edest
        return results
