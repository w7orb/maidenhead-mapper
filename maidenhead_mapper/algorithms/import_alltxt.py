"""Processing algorithm: Import WSJT-X ALL.TXT as stations and exchanges."""

import json
import os
import uuid

from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterDateTime,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterString,
    QgsSettings,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from ..core import alltxt, live
from ..core import exchange as x
from ..core import maidenhead as mh
from ..core.psk import base_callsign, report_time
from ..live.exchange_layer import exchange_features, exchange_fields
from ..live.exchange_layer import labels as exchange_labels
from ..live.exchange_layer import renderer as exchange_renderer
from ..live.layer import live_renderer, live_station_attrs, live_station_fields, station_point
from . import styling
from .import_psk import SETTINGS_MYCALL, WGS84

LOG_PROPERTY = "maidenhead_mapper/log"   # JSON: how to rebuild these layers for a time window
SETTINGS_MYGRID = "maidenhead_mapper/mygrid"


def station_features(model):
    fields = live_station_fields()
    out = []
    for st in model.stations.values():
        if not st.grid:
            continue
        f = QgsFeature(fields)
        f.setGeometry(station_point(st))
        f.setAttributes(live_station_attrs(model, st))
        out.append(f)
    return out


class ImportAllTxtAlgorithm(QgsProcessingAlgorithm):
    FILES = "FILES"
    MYCALL = "MYCALL"
    MYGRID = "MYGRID"
    START = "START"
    END = "END"
    STATIONS = "STATIONS"
    EXCHANGES = "EXCHANGES"

    def tr(self, text):
        return QCoreApplication.translate("ImportAllTxtAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return ImportAllTxtAlgorithm()

    def name(self):
        return "import_alltxt"

    def displayName(self):  # noqa: N802
        return self.tr("Import WSJT-X ALL.TXT")

    def group(self):
        return self.tr("Stations")

    def groupId(self):  # noqa: N802
        return "stations"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Replays one or more WSJT-X ALL.TXT logs through the same logic as the live map:\n"
            "• Stations: one marker per station decoded, at its grid, with SNR, bands, modes, "
            "times, distance and likely hop count.\n"
            "• Exchanges: one great-circle line per exchange between two stations (including "
            "ones not involving you), with the report each side sent, your SNR of each side, "
            "stage and transcript. Your own transmissions (Tx lines) fill in your side.\n\n"
            "A station appears once its grid has been seen in a CQ or grid exchange; an "
            "exchange once both grids are known. The Activity timeline can rebuild both "
            "layers for any range of hours.")

    def initAlgorithm(self, config=None):  # noqa: N802
        s = QgsSettings()
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.FILES, self.tr("ALL.TXT file(s)"), QgsProcessing.TypeFile))
        self.addParameter(QgsProcessingParameterString(
            self.MYCALL, self.tr("My callsign (for my own QSOs)"), optional=True,
            defaultValue=s.value(SETTINGS_MYCALL, "", type=str)))
        self.addParameter(QgsProcessingParameterString(
            self.MYGRID, self.tr("My grid (for distance, bearing and hops)"), optional=True,
            defaultValue=s.value(SETTINGS_MYGRID, "", type=str)))
        self.addParameter(QgsProcessingParameterDateTime(self.START, self.tr("From (optional)"), optional=True))
        self.addParameter(QgsProcessingParameterDateTime(self.END, self.tr("To (optional)"), optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.STATIONS, self.tr("WSJT-X stations"), QgsProcessing.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.EXCHANGES, self.tr("WSJT-X exchanges"), QgsProcessing.TypeVectorLine,
            optional=True, createByDefault=True))

    def _when(self, parameters, name, context):
        if not parameters.get(name):
            return None
        q = self.parameterAsDateTime(parameters, name, context)
        return report_time(q.toSecsSinceEpoch()) if q.isValid() else None

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
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
        settings = QgsSettings()
        if mycall:
            settings.setValue(SETTINGS_MYCALL, mycall)
        if mygrid:
            settings.setValue(SETTINGS_MYGRID, mygrid)
        start, end = self._when(parameters, self.START, context), self._when(parameters, self.END, context)

        feedback.setProgressText(self.tr("Reading…"))
        rows = alltxt.read(files, start, end)
        feedback.pushInfo(self.tr("{} line(s) read ({} received, {} transmitted); {} other line(s) skipped").format(
            len(rows), sum(r.rxtx == "Rx" for r in rows), sum(r.rxtx == "Tx" for r in rows), rows.skipped))
        if not rows:
            raise QgsProcessingException(self.tr("No Rx/Tx lines found (WSJT-X 2.1+ ALL.TXT format)."))
        feedback.setProgress(30)
        model = live.replay(alltxt.to_log(rows, mycall, mygrid))
        feedback.setProgress(70)
        feedback.pushInfo(self.tr("{:%Y-%m-%d %H:%M} – {:%Y-%m-%d %H:%M} UTC; {} decodes").format(
            rows[0].time, rows[-1].time, model.decodes))
        feedback.pushInfo(self.tr("Stations: {} placed, {} never sent a grid").format(
            model.placed_count(), model.waiting_count()))

        group = uuid.uuid4().hex
        prop = {"kind": "alltxt", "files": files, "mycall": mycall, "mygrid": mygrid, "group": group,
                "start": int(start.timestamp()) if start else None, "end": int(end.timestamp()) if end else None}
        results = {}

        sink, dest = self.parameterAsSink(parameters, self.STATIONS, context, live_station_fields(),
                                          QgsWkbTypes.Point, WGS84)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.STATIONS))
        for f in station_features(model):
            sink.addFeature(f, QgsFeatureSink.FastInsert)
        results[self.STATIONS] = dest
        styling.attach_styler(context, dest, self.tr("WSJT-X stations (ALL.TXT)"), styling.LayerStyler(
            live_renderer, styling.callsign_labels, {LOG_PROPERTY: json.dumps(dict(prop, role="stations"))}))

        feats, unplaced = exchange_features(model)
        counts = model.exchanges.counts()
        feedback.pushInfo(self.tr("Exchanges: {} ({} confirmed, {} both reports, {} one report, {} started); "
                                  "{} drawn, {} missing a grid").format(
            len(model.exchanges.exchanges), counts[x.CONFIRMED], counts[x.BOTH_REPORTS], counts[x.ONE_REPORT],
            counts[x.STARTED], len(feats), unplaced))
        sink, dest = self.parameterAsSink(parameters, self.EXCHANGES, context, exchange_fields(),
                                          QgsWkbTypes.MultiLineString, WGS84)
        if sink is not None:
            for f in feats:
                sink.addFeature(f, QgsFeatureSink.FastInsert)
            results[self.EXCHANGES] = dest
            styling.attach_styler(context, dest, self.tr("WSJT-X exchanges (ALL.TXT)"), styling.LayerStyler(
                exchange_renderer, exchange_labels, {LOG_PROPERTY: json.dumps(dict(prop, role="exchanges"))}))
        return results
