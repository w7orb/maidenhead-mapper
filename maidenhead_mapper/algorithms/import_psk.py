"""Processing algorithm: Import PSK Reporter reports as station markers."""

import json
import os
import warnings

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureSink,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterString,
    QgsSettings,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..core import maidenhead as mh
from ..core import psk, stations
from ..core.bands import BAND_NAMES
from . import styling
from .qt_compat import DATETIME, DOUBLE, INT, STRING, field, utc_qdatetime

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
SETTINGS_MYCALL = "maidenhead_mapper/mycall"
PRECISIONS = ["2 (field)", "4 (square)", "6 (subsquare)", "8", "10"]
DIRECTIONS = ["Both", "Stations I heard", "Stations that heard me"]

try:  # QGIS 3.36+
    _FOLDER = Qgis.ProcessingFileParameterBehavior.Folder
except AttributeError:  # QGIS 3.34
    _FOLDER = QgsProcessingParameterFile.Folder


def station_fields():
    f = QgsFields()
    for name, kind, length in [
        ("call", STRING, 20), ("grid", STRING, 10), ("direction", STRING, 10),
        ("reports", INT, 0), ("snr_best", INT, 0), ("snr_median", DOUBLE, 0), ("snr_last", INT, 0),
        ("bands", STRING, 80), ("modes", STRING, 40),
        ("first_utc", DATETIME, 0), ("last_utc", DATETIME, 0),
        ("distance_km", DOUBLE, 0), ("bearing_deg", DOUBLE, 0), ("source", STRING, 10),
    ]:
        f.append(field(name, kind, length))
    return f


def station_feature(fields, s):
    feat = QgsFeature(fields)
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(s.lon, s.lat)))
    feat.setAttributes([
        s.call, s.grid, s.direction, s.reports, s.snr_best, s.snr_median, s.snr_last,
        s.bands, s.modes, utc_qdatetime(s.first_utc), utc_qdatetime(s.last_utc),
        s.distance_km, s.bearing_deg, s.source,
    ])
    return feat


LAYER_PROPERTY = "maidenhead_mapper/psk"  # JSON of the options a station layer was made with


def read_folder(alg, folder, mycall, feedback, progress_to=80.0):
    """Load and label every report in a folder, logging what was skipped."""
    feedback.setProgressText(alg.tr("Reading files…"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # skipped files are reported below
        reports = psk.load_reports(folder, progress=lambda i, n: feedback.setProgress(progress_to * i / max(n, 1)))
    feedback.pushInfo(alg.tr("{} file(s) read, {} unique report(s)").format(reports.files_read, len(reports)))
    for name in reports.skipped_files:
        feedback.reportError(alg.tr("Skipped unreadable file: {}").format(name))
    if not reports:
        raise QgsProcessingException(alg.tr("No reception reports found in {}").format(folder))
    return psk.add_remote_station(reports, mycall)


def write_stations(alg, parameters, sink_name, context, feedback, reports, opts, style):
    """Summarize labelled reports per station into the sink; return its dest id.

    opts: folder, mycall, my_grid, directions, bands, precision, start, end
    (epoch seconds or None). They are stored on the loaded layer so the
    activity timeline can re-summarize it for other time ranges.
    """
    mycall = opts["mycall"]
    summary, st = stations.summarize(
        reports, precision=opts["precision"], my_grid=opts["my_grid"], directions=opts["directions"],
        bands=opts["bands"], start=psk.report_time(opts["start"]), end=psk.report_time(opts["end"]))
    my_grid = opts["my_grid"] or stations.guess_my_grid(reports)
    opts = dict(opts, my_grid=my_grid)
    feedback.pushInfo(alg.tr("My grid: {}").format(my_grid or "unknown (no distance/bearing)"))
    if st.no_direction:
        feedback.pushWarning(alg.tr("{} report(s) don't involve {} and were ignored").format(st.no_direction, mycall))
    if st.self_spots:
        feedback.pushInfo(alg.tr("{} self-spot(s) (my own signal) ignored").format(st.self_spots))
    if st.no_locator:
        feedback.pushInfo(alg.tr("{} report(s) had no valid locator for the other station").format(st.no_locator))
    if st.filtered_out:
        feedback.pushInfo(alg.tr("{} report(s) outside the direction/band/time filters").format(st.filtered_out))

    fields = station_fields()
    sink, dest_id = alg.parameterAsSink(parameters, sink_name, context, fields, QgsWkbTypes.Point, WGS84)
    if sink is None:
        raise QgsProcessingException(alg.invalidSinkError(parameters, sink_name))
    for i, s in enumerate(summary):
        if feedback.isCanceled():
            break
        sink.addFeature(station_feature(fields, s), QgsFeatureSink.FastInsert)
        if i % 200 == 0:
            feedback.setProgress(80 + 20.0 * i / max(len(summary), 1))
    heard = sum(1 for s in summary if s.direction == "heard")
    feedback.pushInfo(alg.tr("{} station(s): {} heard, {} heard me").format(len(summary), heard, len(summary) - heard))

    styling.attach(context, dest_id, alg.tr("PSK Reporter stations"), style,
                   properties={LAYER_PROPERTY: json.dumps(opts)})
    return dest_id


class ImportPskAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    MYCALL = "MYCALL"
    MYGRID = "MYGRID"
    DIRECTION = "DIRECTION"
    BANDS = "BANDS"
    START = "START"
    END = "END"
    PRECISION = "PRECISION"
    STYLE = "STYLE"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate("ImportPskAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return ImportPskAlgorithm()

    def name(self):
        return "import_psk"

    def displayName(self):  # noqa: N802
        return self.tr("Import PSK Reporter reports")

    def group(self):
        return self.tr("Stations")

    def groupId(self):  # noqa: N802
        return "stations"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Reads a folder of PSK Reporter pulls (psk_*.json, as saved by "
            "pskreporter-pull.py) and makes one marker per remote station and "
            "direction:\n"
            "• heard: stations my receiver decoded\n"
            "• heard_me: stations that decoded me\n\n"
            "Reports repeated across overlapping pulls are counted once. Each marker "
            "sits at the centre of the station's most recent locator, capped at the "
            "chosen precision, and carries report count, best/median/last SNR, bands, "
            "modes, first/last time (UTC) and distance/bearing from my grid.\n\n"
            "My grid defaults to the locator most often reported for my callsign."
        )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFile(
            self.INPUT, self.tr("Folder of psk_*.json files"), behavior=_FOLDER))
        self.addParameter(QgsProcessingParameterString(
            self.MYCALL, self.tr("My callsign"),
            defaultValue=QgsSettings().value(SETTINGS_MYCALL, "", type=str)))
        self.addParameter(QgsProcessingParameterString(
            self.MYGRID, self.tr("My grid (empty = from the reports)"), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.DIRECTION, self.tr("Direction"), options=DIRECTIONS, defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(
            self.BANDS, self.tr("Bands (none selected = all)"), options=list(BAND_NAMES),
            allowMultiple=True, optional=True))
        self.addParameter(QgsProcessingParameterDateTime(
            self.START, self.tr("From (optional)"), optional=True))
        self.addParameter(QgsProcessingParameterDateTime(
            self.END, self.tr("To (optional)"), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.PRECISION, self.tr("Locator precision for positions"), options=PRECISIONS, defaultValue=2))
        self.addParameter(QgsProcessingParameterEnum(
            self.STYLE, self.tr("Style"), options=styling.STYLES, defaultValue=styling.STYLE_SNR))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("PSK Reporter stations"), QgsProcessing.TypeVectorPoint))

    def _epoch(self, parameters, name, context):
        if not parameters.get(name):
            return None
        qdt = self.parameterAsDateTime(parameters, name, context)
        return qdt.toSecsSinceEpoch() if qdt.isValid() else None

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        folder = self.parameterAsFile(parameters, self.INPUT, context)
        if not folder or not os.path.isdir(folder):
            raise QgsProcessingException(self.tr("Not a folder: {}").format(folder))
        mycall = self.parameterAsString(parameters, self.MYCALL, context).strip()
        if not psk.base_callsign(mycall):
            raise QgsProcessingException(self.tr("Enter your callsign."))
        QgsSettings().setValue(SETTINGS_MYCALL, mycall)
        my_grid = self.parameterAsString(parameters, self.MYGRID, context).strip() or None
        if my_grid and not mh.is_valid(my_grid):
            raise QgsProcessingException(self.tr("My grid '{}' is not a valid locator.").format(my_grid))
        direction = self.parameterAsEnum(parameters, self.DIRECTION, context)
        directions = [("heard", "heard_me"), ("heard",), ("heard_me",)][direction]
        # parameterAsEnums falls back to [0] (the first band) when nothing is
        # selected, so only read it when the user actually picked bands.
        bands = None
        if parameters.get(self.BANDS) not in (None, "", []):
            bands = [BAND_NAMES[i] for i in self.parameterAsEnums(parameters, self.BANDS, context)] or None
        precision = mh.PRECISIONS[self.parameterAsEnum(parameters, self.PRECISION, context)]
        style = self.parameterAsEnum(parameters, self.STYLE, context)

        opts = {
            "folder": folder, "mycall": mycall, "my_grid": my_grid, "directions": list(directions),
            "bands": bands, "precision": precision,
            "start": self._epoch(parameters, self.START, context),
            "end": self._epoch(parameters, self.END, context),
        }
        reports = read_folder(self, folder, mycall, feedback)
        dest_id = write_stations(self, parameters, self.OUTPUT, context, feedback, reports, opts, style)
        return {self.OUTPUT: dest_id}
