"""Processing algorithm: export a research dataset (one row per observed SNR) from ALL.TXT."""

import os
from datetime import datetime, timezone

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsSettings,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..core import alltxt, alltxt_index
from ..core import research_export as rx
from ..core.bands import band_for_mhz
from . import corridors_time_alg as cta
from .import_psk import SETTINGS_MYCALL
from .paths import SETTINGS_MYGRID

GRID_CHARS = [6, 4]


class ResearchExportAlgorithm(QgsProcessingAlgorithm):
    FILES = "FILES"
    START = "START"
    END = "END"
    BAND = "BAND"
    MYCALL = "MYCALL"
    MYGRID = "MYGRID"
    FLOOR = "FLOOR"
    GRID = "GRID"
    PSEUDONYMISE = "PSEUDONYMISE"
    SALT = "SALT"
    MUF = "MUF"
    R12 = "R12"
    OUTPUT = "OUTPUT"
    SCHEMA = "SCHEMA"
    ROWS = "ROWS"

    def tr(self, text):
        return QCoreApplication.translate("ResearchExportAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return ResearchExportAlgorithm()

    def name(self):
        return "research_export"

    def displayName(self):  # noqa: N802
        return self.tr("Export research dataset")

    def group(self):
        return self.tr("Stations")

    def groupId(self):  # noqa: N802
        return "stations"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Writes one CSV row per observed SNR from WSJT-X ALL.TXT files, for sharing and merging with "
            "other operators' data (e.g. HamSCI, Zenodo).\n\n"
            "Two kinds of row:\n"
            "• exchange_report: the report one station sent another in an exchange (rx = the station that "
            "sent the report, tx = the one it heard). A report repeated in one exchange is one row.\n"
            "• my_decode: your receiver's SNR of each decoded message (rx = you).\n\n"
            "exchange_id links both directions of an exchange and your decodes of each side. Each row also "
            "has distance, bearing, fewest hops (300 km layer), control points (midpoint up to 2000 km, "
            "else 1000 km from each end), day or night there, and a censored flag (SNR at or below the "
            "decode floor).\n\n"
            "Schema 1.1 adds, per row: muf_basic_mhz, the path's basic MUF at its time from the CCIR "
            "monthly-median model (the same method as Check paths' CCIR option; KC2G only has the present), "
            "f_over_muf, es_candidate (the sporadic-E detector's high-confidence rule: 12m-2m at single-Es-hop "
            "range, on 12m/10m above the basic MUF) and foes_min_mhz. not_decoded rows record a transmission "
            "that rx evidently didn't decode: tx answered rx's message and rx sent the same message again in "
            "its next slot. They have no SNR and censored = 1; they don't prove the path was closed (QRM, "
            "or rx's operator, can do the same).\n\n"
            "The MUF needs the CCIR coefficients (downloaded once) and a sunspot number: looked up as in "
            "Check paths, or the one you enter. Untick the MUF option to leave those columns empty.\n\n"
            "Pseudonymise replaces every callsign with a salted SHA-256 code; grids are kept. Use the same "
            "secret salt for all your exports so codes match; never publish the salt.\n\n"
            "A <name>.schema.json file next to the CSV describes every field, the time span, the row counts "
            "and the settings. Reading starts 6 hours before From, to learn grids.")

    def initAlgorithm(self, config=None):  # noqa: N802
        s = QgsSettings()
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.FILES, self.tr("ALL.TXT file(s)"), QgsProcessing.TypeFile))
        self.addParameter(QgsProcessingParameterDateTime(self.START, self.tr("From (UTC)"), optional=True))
        self.addParameter(QgsProcessingParameterDateTime(self.END, self.tr("To (UTC)"), optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.BAND, self.tr("Band (empty = all bands)"), optional=True, defaultValue=""))
        self.addParameter(QgsProcessingParameterString(
            self.MYCALL, self.tr("My callsign (the receiver of my decodes)"),
            defaultValue=s.value(SETTINGS_MYCALL, "", type=str)))
        self.addParameter(QgsProcessingParameterString(
            self.MYGRID, self.tr("My grid"), optional=True, defaultValue=s.value(SETTINGS_MYGRID, "", type=str)))
        self.addParameter(QgsProcessingParameterNumber(
            self.FLOOR, self.tr("Decode floor: SNR at or below is censored (dB)"),
            QgsProcessingParameterNumber.Integer, defaultValue=rx.DECODE_FLOOR_DB, minValue=-50, maxValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.GRID, self.tr("Grid precision at most"),
            options=[self.tr("6 characters (as known)"), self.tr("4 characters")], defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.PSEUDONYMISE, self.tr("Pseudonymise callsigns"), defaultValue=False))
        self.addParameter(QgsProcessingParameterString(
            self.SALT, self.tr("Salt for pseudonyms (keep it secret)"), optional=True, defaultValue=""))
        self.addParameter(QgsProcessingParameterBoolean(
            self.MUF, self.tr("Add the basic MUF, f/MUF and sporadic-E flag (CCIR model)"), defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.R12, self.tr("Sunspot number R12 for the model (0 = look it up)"), QgsProcessingParameterNumber.Double,
            defaultValue=0.0, minValue=0.0, maxValue=400.0))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, self.tr("Research dataset (CSV)"), self.tr("CSV files (*.csv)")))
        self.addOutput(QgsProcessingOutputFile(self.SCHEMA, self.tr("Schema (JSON)")))
        self.addOutput(QgsProcessingOutputNumber(self.ROWS, self.tr("Rows written")))

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
        band = self.parameterAsString(parameters, self.BAND, context).strip().lower() or None
        mycall = self.parameterAsString(parameters, self.MYCALL, context).strip().upper()
        mygrid = self.parameterAsString(parameters, self.MYGRID, context).strip()
        if not mycall:
            raise QgsProcessingException(self.tr("Enter My callsign: your decodes are measured at your station."))
        pseudo = self.parameterAsBool(parameters, self.PSEUDONYMISE, context)
        salt = self.parameterAsString(parameters, self.SALT, context)
        if pseudo and not salt:
            feedback.reportError(self.tr("No salt: anyone can rebuild the pseudonyms from a list of callsigns. "
                                         "Give a secret salt to make them hard to reverse."), False)
        out_path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        opts = rx.Options(my_call=mycall, my_grid=mygrid, start=start, end=end, band=band,
                          floor_db=self.parameterAsInt(parameters, self.FLOOR, context), pseudonymise=pseudo,
                          salt=salt, grid_chars=GRID_CHARS[self.parameterAsEnum(parameters, self.GRID, context)])

        if self.parameterAsBool(parameters, self.MUF, context):
            from .ionosphere import MODE_MODEL, IonoSource
            try:
                opts.muf_source = IonoSource(feedback, MODE_MODEL,
                                             r12=self.parameterAsDouble(parameters, self.R12, context) or None)
            except (QgsProcessingException, OSError, ValueError) as exc:
                feedback.reportError(self.tr("No CCIR model ({}); the MUF columns stay empty.").format(exc), False)

        feedback.setProgressText(self.tr("Indexing…"))
        ix = alltxt_index.index(files, cta.index_cache_dir(), lambda p: feedback.setProgress(p * 0.3),
                                feedback.isCanceled)
        if ix is None:
            return {}
        feedback.pushInfo(self.tr("Index: {}").format(ix.summary()))
        if ix.first_time is None:
            raise QgsProcessingException(self.tr("No decodes found (WSJT-X 2.1+ ALL.TXT format)."))
        if band and band not in ix.bands():
            raise QgsProcessingException(self.tr("No decodes on {} in these files (bands: {}).").format(
                band, ", ".join(sorted(ix.bands()))))

        feedback.setProgressText(self.tr("Reading…"))
        read_from = start - cta.WARMUP if start else None
        rows = alltxt.read(files, read_from, end, ix.offsets(read_from), {f.path for f in ix.files if f.in_order})
        if band:
            rows = [r for r in rows if band_for_mhz(r.mhz) == band]
        feedback.pushInfo(self.tr("{} line(s) read").format(len(rows)))
        if feedback.isCanceled():
            return {}
        feedback.setProgress(40)
        feedback.setProgressText(self.tr("Building rows…"))
        out, stats = rx.build(rows, opts)
        if not out:
            raise QgsProcessingException(self.tr("No SNR observations in this window."))
        feedback.setProgress(80)
        rx.write_csv(out_path, out)
        schema = rx.schema_path(out_path)
        rx.write_schema(schema, rx.schema_doc(stats, opts, rx.plugin_version(), os.path.basename(out_path)))
        feedback.pushInfo(self.tr("{} row(s): {} exchange report(s), {} of my decodes; {} censored; {} with both "
                                  "grids; {} exchange(s), {} station(s); {} – {} UTC").format(
            stats.rows, stats.by_kind.get(rx.EXCHANGE_REPORT, 0), stats.by_kind.get(rx.MY_DECODE, 0),
            stats.censored, stats.with_both_grids, stats.exchanges, stats.stations,
            f"{stats.first_utc:%Y-%m-%d %H:%M}", f"{stats.last_utc:%Y-%m-%d %H:%M}"))
        if stats.by_kind.get(rx.NOT_DECODED):
            feedback.pushInfo(self.tr("{} not_decoded row(s)").format(stats.by_kind[rx.NOT_DECODED]))
        if opts.muf_source is not None:
            feedback.pushInfo(self.tr("Basic MUF on {} of {} row(s) with both grids; {} sporadic-E candidate(s). "
                                      "Model: {}").format(stats.with_muf, stats.with_both_grids, stats.es_candidates,
                                                          "; ".join(stats.muf_labels) or "-"))
            for why, n in stats.muf_missing.items():
                feedback.pushWarning(self.tr("No MUF for {} row(s): {}").format(n, why))
        feedback.pushInfo(self.tr("Wrote {} and {}").format(out_path, schema))
        feedback.setProgress(100)
        return {self.OUTPUT: out_path, self.SCHEMA: schema, self.ROWS: stats.rows}
