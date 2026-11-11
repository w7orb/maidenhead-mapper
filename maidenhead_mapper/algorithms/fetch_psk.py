"""Processing algorithm: Fetch reports from PSK Reporter into a folder."""

import time

from qgis.core import (
    QgsBlockingNetworkRequest,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsSettings,
)
from qgis.PyQt.QtCore import QCoreApplication, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..core import maidenhead as mh
from ..core import psk, pskfetch
from . import styling
from .import_psk import _FOLDER, SETTINGS_MYCALL, read_folder, write_stations

SETTINGS_FOLDER = "maidenhead_mapper/psk_folder"
SETTINGS_LAST_FETCH = "maidenhead_mapper/psk_last_fetch"  # epoch seconds of the last request


class FetchPskAlgorithm(QgsProcessingAlgorithm):
    CALLSIGN = "CALLSIGN"
    HOURS = "HOURS"
    MODE = "MODE"
    FOLDER = "FOLDER"
    STYLE = "STYLE"
    OUTPUT = "OUTPUT"
    SAVED = "SAVED"

    def tr(self, text):
        return QCoreApplication.translate("FetchPskAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return FetchPskAlgorithm()

    def name(self):
        return "fetch_psk"

    def displayName(self):  # noqa: N802
        return self.tr("Fetch from PSK Reporter")

    def group(self):
        return self.tr("Stations")

    def groupId(self):  # noqa: N802
        return "stations"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Downloads the reports PSK Reporter has for a callsign (both stations it "
            "heard and stations that heard it) and saves them to the folder as "
            "psk_YYYY_MM_DD-HH_MM_SS.json. Then, optionally, maps every pull in "
            "that folder, so repeated fetches build up a history. Overlapping pulls "
            "are counted once.\n\n"
            "PSK Reporter keeps about 24 hours of reports and asks clients to query "
            "no more than once every 5 minutes; this tool enforces that.\n\n"
            "For direction, band, time and precision filters, run 'Import PSK "
            "Reporter reports' on the same folder."
        )

    def initAlgorithm(self, config=None):  # noqa: N802
        s = QgsSettings()
        self.addParameter(QgsProcessingParameterString(
            self.CALLSIGN, self.tr("Callsign"), defaultValue=s.value(SETTINGS_MYCALL, "", type=str)))
        self.addParameter(QgsProcessingParameterNumber(
            self.HOURS, self.tr("Hours back (max 24)"), QgsProcessingParameterNumber.Integer,
            defaultValue=24, minValue=1, maxValue=24))
        self.addParameter(QgsProcessingParameterString(
            self.MODE, self.tr("Mode, e.g. FT8 (empty = all modes)"), optional=True))
        self.addParameter(QgsProcessingParameterFile(
            self.FOLDER, self.tr("Save into folder"), behavior=_FOLDER,
            defaultValue=s.value(SETTINGS_FOLDER, "", type=str) or None))
        self.addParameter(QgsProcessingParameterEnum(
            self.STYLE, self.tr("Style"), options=styling.STYLES, defaultValue=styling.STYLE_SNR))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("PSK Reporter stations (whole folder)"), QgsProcessing.TypeVectorPoint,
            optional=True, createByDefault=True))
        self.addOutput(QgsProcessingOutputFile(self.SAVED, self.tr("Saved file")))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        call = self.parameterAsString(parameters, self.CALLSIGN, context).strip()
        if not psk.base_callsign(call):
            raise QgsProcessingException(self.tr("Enter a callsign."))
        folder = self.parameterAsFile(parameters, self.FOLDER, context)
        if not folder:
            raise QgsProcessingException(self.tr("Choose a folder to save into."))
        hours = self.parameterAsInt(parameters, self.HOURS, context)
        mode = self.parameterAsString(parameters, self.MODE, context).strip() or None

        settings = QgsSettings()
        wait = pskfetch.wait_seconds(settings.value(SETTINGS_LAST_FETCH, 0, type=int), time.time())
        if wait:
            raise QgsProcessingException(self.tr(
                "PSK Reporter allows one query every 5 minutes. Try again in {} min {} s, "
                "or map what is already saved with 'Import PSK Reporter reports'.").format(wait // 60, wait % 60))
        settings.setValue(SETTINGS_LAST_FETCH, int(time.time()))  # failed attempts count too
        settings.setValue(SETTINGS_MYCALL, call)
        settings.setValue(SETTINGS_FOLDER, folder)

        url = pskfetch.query_url(call, hours * 3600, mode)
        feedback.setProgressText(self.tr("Querying PSK Reporter…"))
        feedback.pushInfo(url)
        req = QgsBlockingNetworkRequest()
        err = req.get(QNetworkRequest(QUrl(url)), True, feedback)
        if feedback.isCanceled():
            return {}
        if err != QgsBlockingNetworkRequest.NoError:
            raise QgsProcessingException(self.tr("Download failed: {}").format(req.errorMessage()))
        text = bytes(req.reply().content()).decode("utf-8", errors="replace")
        try:
            raw, data = pskfetch.parse_response(text)
        except pskfetch.FetchError as exc:
            raise QgsProcessingException(str(exc))

        path = pskfetch.save(raw, folder)
        n = len(data.get("receptionReport") or [])
        feedback.pushInfo(self.tr("Saved {} report(s) to {}").format(n, path))
        if not n:
            feedback.pushWarning(self.tr("PSK Reporter has no reports for {} in the last {} h.").format(call, hours))
        results = {self.SAVED: path}

        if parameters.get(self.OUTPUT) is None:  # output unchecked: just save
            return results
        try:
            reports = read_folder(self, folder, call, feedback)
        except QgsProcessingException:  # nothing in the folder yet: not an error
            feedback.pushWarning(self.tr("No reports in {} yet, so no station layer.").format(folder))
            return results
        opts = {"folder": folder, "mycall": call, "my_grid": None, "directions": ["heard", "heard_me"],
                "bands": None, "precision": mh.SUBSQUARE, "start": None, "end": None}
        style = self.parameterAsEnum(parameters, self.STYLE, context)
        results[self.OUTPUT] = write_stations(self, parameters, self.OUTPUT, context, feedback, reports, opts, style)
        return results
