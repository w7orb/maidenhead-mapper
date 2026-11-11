"""Temporary point layer mirroring LiveModel, with markers that fade by age."""

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProject,
    QgsProperty,
    QgsRuleBasedRenderer,
    QgsSymbol,
    QgsVectorLayer,
)

from ..algorithms import styling
from ..algorithms.import_psk import station_fields
from ..algorithms.qt_compat import INT, utc_qdatetime
from ..core import hops as _hops
from ..core import maidenhead as mh

LAYER_NAME = "WSJT-X live"
_OPACITY = getattr(getattr(QgsSymbol, "Property", None), "Opacity", None) or QgsSymbol.PropertyOpacity


def fade_expression(fade_minutes):
    """Opacity 0-100: 100 when just heard, down to 15 after `fade_minutes`.
    Worked stations never fade. now() is evaluated at each repaint."""
    return (
        "if(\"direction\" = 'worked', 100, "
        f"max(15, 100 - 85 * ((epoch(now()) - epoch(\"last_utc\")) / 60000.0) / {float(fade_minutes)}))"
    )


def live_renderer(fade_minutes):
    root = QgsRuleBasedRenderer.Rule(None)
    worked = QgsMarkerSymbol.createSimple({"name": "star", "color": "#d62728", "size": "4",
                                           "outline_color": "255,255,255,220", "outline_width": "0.25"})
    root.appendChild(QgsRuleBasedRenderer.Rule(worked, 0, 0, "\"direction\" = 'worked'", "Worked (QSO logged)"))
    for lo, hi, color, label in styling.SNR_CLASSES:
        sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": color, "size": "2.6",
                                            "outline_color": "255,255,255,200", "outline_width": "0.2"})
        expr = f"\"direction\" = 'heard' AND \"snr_best\" > {lo} AND \"snr_best\" <= {hi}"
        root.appendChild(QgsRuleBasedRenderer.Rule(sym, 0, 0, expr, f"Heard, best SNR {label}"))
    prop = QgsProperty.fromExpression(fade_expression(fade_minutes))
    for rule in root.children():
        rule.symbol().setDataDefinedProperty(_OPACITY, prop)
    return QgsRuleBasedRenderer(root)


def live_station_fields():
    """Station fields plus called_me (live and ALL.TXT layers)."""
    fields = station_fields()
    fields.append(QgsField("called_me", INT))
    return fields


def live_station_attrs(model, st):
    """Attribute list for a LiveModel StationState, matching live_station_fields()."""
    dist, bear = model.distance_bearing(st.grid)
    n, takeoff = _hops.likely_hops(dist)
    return [
        st.call, st.grid, st.direction, st.reports, st.snr_best, None, st.snr_last,
        st.bands_str(), ",".join(sorted(st.modes)),
        utc_qdatetime(st.first_utc) if st.first_utc else None,
        utc_qdatetime(st.last_utc) if st.last_utc else None,
        dist, bear, "live", n, takeoff, st.called_me,
    ]


def station_point(st):
    lon, lat = mh.center(st.grid)
    return QgsGeometry.fromPointXY(QgsPointXY(lon, lat))


class LiveLayer:
    """Owns the live layer and applies batches of changed stations to it."""

    def __init__(self, model, fade_minutes=30):
        self.model = model
        self.fade_minutes = fade_minutes
        self.layer = None
        self.fids = {}

    def _create(self):
        layer = QgsVectorLayer("Point?crs=EPSG:4326", LAYER_NAME, "memory")
        layer.dataProvider().addAttributes(list(live_station_fields()))
        layer.updateFields()
        layer.setRenderer(live_renderer(self.fade_minutes))
        layer.setLabeling(styling.callsign_labels())
        layer.setLabelsEnabled(True)
        QgsProject.instance().addMapLayer(layer)
        layer.willBeDeleted.connect(self._forget)
        self.layer, self.fids = layer, {}
        # Re-add every placed station (e.g. after the user deleted the layer)
        return set(self.model.stations)

    def _forget(self):
        self.layer, self.fids = None, {}

    def set_fade(self, minutes):
        self.fade_minutes = minutes
        if self.layer is not None:
            self.layer.setRenderer(live_renderer(minutes))
            self.layer.triggerRepaint()

    def sync(self, keys):
        """Apply changed stations; returns the number of features touched."""
        if self.layer is None:
            keys = set(keys) | self._create()
        dp = self.layer.dataProvider()
        new, attr_changes, geom_changes = [], {}, {}
        for key in keys:
            st = self.model.stations.get(key)
            if st is None or not st.grid:
                continue
            geom = station_point(st)
            attrs = live_station_attrs(self.model, st)
            fid = self.fids.get(key)
            if fid is None:
                f = QgsFeature(self.layer.fields())
                f.setGeometry(geom)
                f.setAttributes(attrs)
                new.append((key, f))
            else:
                attr_changes[fid] = dict(enumerate(attrs))
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

    def repaint(self):
        if self.layer is not None:
            self.layer.triggerRepaint()
