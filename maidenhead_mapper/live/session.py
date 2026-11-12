"""Saving a live session to a GeoPackage as it happens.

One file per session, wsjtx_session_YYYYMMDD_HHMMSS.gpkg (UTC), with:
    stations   the live station markers (same fields as the live layer)
    exchanges  the exchange lines
    log        every WSJT-X message the map used: utc, kind, data (JSON)

The layers in QGIS are the GeoPackage tables themselves, so every update is
on disk within the 2-second batch. The log lets the Activity timeline
rebuild any time window of a saved session after QGIS restarts.
"""

import datetime as _dt
import os

from qgis.core import QgsFeature, QgsProject, QgsVectorFileWriter, QgsVectorLayer

from ..algorithms.qt_compat import DATETIME, STRING, field, utc_qdatetime
from ..core import live
from .synced import memory_layer

TABLES = {"WSJT-X live": "stations", "WSJT-X exchanges": "exchanges"}


def _write_empty(path, table, geometry, fields):
    mem = memory_layer(table, geometry, fields)
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = table
    opts.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteLayer if os.path.exists(path)
                                 else QgsVectorFileWriter.CreateOrOverwriteFile)
    res = QgsVectorFileWriter.writeAsVectorFormatV3(mem, path, QgsProject.instance().transformContext(), opts)
    if res[0] != QgsVectorFileWriter.NoError:
        raise OSError(f"Could not create {table} in {path}: {res[1]}")


def _log_fields():
    from qgis.core import QgsFields
    f = QgsFields()
    for name, kind in (("utc", DATETIME), ("kind", STRING), ("data", STRING)):
        f.append(field(name, kind, 0))
    return f


class SessionStore:
    def __init__(self, folder, now=None):
        now = now or _dt.datetime.now(_dt.timezone.utc)
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, f"wsjtx_session_{now:%Y%m%d_%H%M%S}.gpkg")
        self.written = 0
        self._log = None
        self._log_created = False

    def make_layer(self, name, geometry, fields):
        """SyncedLayer.make_layer: a GeoPackage table instead of a memory layer."""
        table = TABLES.get(name, name)
        _write_empty(self.path, table, geometry, fields)
        layer = QgsVectorLayer(f"{self.path}|layername={table}", name, "ogr")
        if not layer.isValid():
            raise OSError(f"Could not open {table} in {self.path}")
        return layer

    def append_log(self, log):
        """Write log entries not yet saved; returns how many were written."""
        if self.written >= len(log):
            return 0
        if self._log is None:
            if not self._log_created:            # never overwrite a table written before close()
                _write_empty(self.path, "log", "None", _log_fields())
                self._log_created = True
            self._log = QgsVectorLayer(f"{self.path}|layername=log", "log", "ogr")
        fields = self._log.fields()
        feats = []
        for t, msg in log[self.written:]:
            kind, data = live.encode_entry(msg)
            f = QgsFeature(fields)
            f["utc"], f["kind"], f["data"] = utc_qdatetime(t), kind, data
            feats.append(f)
        ok, _ = self._log.dataProvider().addFeatures(feats)
        if ok:
            self.written = len(log)
        return len(feats) if ok else 0

    def close(self):
        """Release the open log table. It is a layer owned by Python, not the
        project; if it outlives QGIS's data providers, QGIS crashes on exit.
        append_log() reopens it (without overwriting) if needed."""
        self._log = None


def log_props(layer, tag=True):
    """The plugin's log properties for a layer (dict), or None.

    Layers made by the plugin carry them as a custom property. A layer loaded
    straight from a saved session GeoPackage (drag and drop, a new project)
    doesn't, so they are inferred from the file (core.session_file) and, with
    tag=True, stored on the layer so the rest of the plugin treats it the same.
    """
    import json
    from qgis.core import QgsProviderRegistry
    from ..algorithms.import_alltxt import LOG_PROPERTY
    from ..core import session_file

    if layer is None:
        return None
    raw = layer.customProperty(LOG_PROPERTY)
    if raw:
        try:
            props = json.loads(raw)
            return props if isinstance(props, dict) else None
        except (TypeError, ValueError):
            return None
    try:
        if layer.providerType() != "ogr":
            return None
    except AttributeError:          # not a vector layer
        return None
    parts = QgsProviderRegistry.instance().decodeUri("ogr", layer.source())
    path, table = parts.get("path"), parts.get("layerName")
    if not table:                   # single-table URI: not a session layer
        return None
    props = session_file.infer(path, table)
    if props and tag:
        layer.setCustomProperty(LOG_PROPERTY, json.dumps(props))
    return props


def read_log(path):
    """[(time, message)] from a saved session, in time order."""
    layer = QgsVectorLayer(f"{path}|layername=log", "log", "ogr")
    if not layer.isValid():
        raise OSError(f"No session log in {path}")
    out = []
    for f in layer.getFeatures():
        msg = live.decode_entry(f["kind"], f["data"])
        if msg is None:
            continue
        t = _dt.datetime.fromtimestamp(f["utc"].toSecsSinceEpoch(), _dt.timezone.utc)
        out.append((t, f.id(), msg))
    out.sort(key=lambda e: (e[0], e[1]))
    return [(t, msg) for t, _, msg in out]
