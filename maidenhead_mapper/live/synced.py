"""A map layer kept in step with a LiveModel, in memory or in a GeoPackage.

Subclasses say which keys exist and how to turn one into (geometry,
attributes). Attributes are written by field *name*, so storage that adds
columns (a GeoPackage's fid) doesn't shift them.
"""

from qgis.core import QgsFeature, QgsProject, QgsVectorLayer


def memory_layer(name, geometry, fields):
    layer = QgsVectorLayer(f"{geometry}?crs=EPSG:4326", name, "memory")
    layer.dataProvider().addAttributes(list(fields))
    layer.updateFields()
    return layer


class SyncedLayer:
    NAME = ""
    GEOMETRY = "Point"

    def __init__(self, model, fade_minutes=30):
        self.model = model
        self.fade_minutes = fade_minutes
        self.layer = None
        self.fids = {}
        self.make_layer = memory_layer      # replaced by SessionStore.make_layer when saving
        self.properties = {}                # custom layer properties (timeline needs them)
        self._idx = []

    # -- subclass hooks ------------------------------------------------------
    def fields(self):
        raise NotImplementedError

    def style(self, layer):
        raise NotImplementedError

    def keys(self, model):
        raise NotImplementedError

    def item(self, model, key):
        """(geometry, attribute list) or None if it can't be drawn yet."""
        raise NotImplementedError

    def key_of(self, feature):
        """The key a stored feature represents."""
        raise NotImplementedError

    # -- layer lifecycle -------------------------------------------------------
    def _create(self):
        fields = self.fields()
        layer = self.make_layer(self.NAME, self.GEOMETRY, fields)
        self.style(layer)
        for k, v in self.properties.items():
            layer.setCustomProperty(k, v)
        QgsProject.instance().addMapLayer(layer)
        layer.willBeDeleted.connect(self._forget)
        self.layer, self.fids = layer, {}
        self._idx = [layer.fields().indexOf(f.name()) for f in fields]

    def _forget(self):
        self.layer, self.fids = None, {}

    def detach(self):
        """Stop tracking the current layer (it stays in the project)."""
        if self.layer is not None:
            try:
                self.layer.willBeDeleted.disconnect(self._forget)
            except (TypeError, RuntimeError):
                pass
        self._forget()

    def set_fade(self, minutes):
        self.fade_minutes = minutes
        if self.layer is not None:
            self.style(self.layer)
            self.layer.triggerRepaint()

    def repaint(self):
        if self.layer is not None:
            self.layer.triggerRepaint()

    # -- writing ---------------------------------------------------------------
    def _feature(self, geom, attrs):
        f = QgsFeature(self.layer.fields())
        f.setGeometry(geom)
        for i, v in zip(self._idx, attrs):
            if i >= 0:
                f.setAttribute(i, v)
        return f

    def sync(self, keys):
        """Apply changed keys incrementally; returns the number of features touched."""
        if self.layer is None:
            if not keys and not self.keys(self.model):
                return 0
            self._create()
            keys = set(keys) | set(self.keys(self.model))
        dp = self.layer.dataProvider()
        new, attr_changes, geom_changes = [], {}, {}
        for key in keys:
            it = self.item(self.model, key)
            if it is None:
                continue
            geom, attrs = it
            fid = self.fids.get(key)
            if fid is None:
                new.append((key, self._feature(geom, attrs)))
            else:
                attr_changes[fid] = {i: v for i, v in zip(self._idx, attrs) if i >= 0}
                geom_changes[fid] = geom
        if new:
            ok, added = dp.addFeatures([f for _, f in new])
            if ok:
                for (key, _), f in zip(new, added):
                    self.fids[key] = f.id()
        if attr_changes:
            dp.changeAttributeValues(attr_changes)
            dp.changeGeometryValues(geom_changes)
        touched = len(new) + len(attr_changes)
        if touched:
            self.layer.updateExtents()
            self.layer.triggerRepaint()
        return touched

    def rebuild(self, model):
        """Replace every feature with `model`'s (a time window, or the live model
        to resume). Uses an edit session so attribute tables refresh."""
        if self.layer is None:
            self._create()
        lyr = self.layer
        keyed = []
        for key in self.keys(model):
            it = self.item(model, key)
            if it is not None:
                keyed.append((key, self._feature(*it)))
        lyr.startEditing()
        lyr.deleteFeatures(list(lyr.allFeatureIds()))
        lyr.addFeatures([f for _, f in keyed])
        lyr.commitChanges()
        self.fids = {}
        if model is self.model:  # resume incremental updates: learn the new ids
            self.fids = {self.key_of(f): f.id() for f in lyr.getFeatures()}
        lyr.updateExtents()
        lyr.triggerRepaint()
