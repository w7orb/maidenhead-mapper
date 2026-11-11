"""Temporary point layer mirroring LiveModel, with markers that fade by age."""

from qgis.core import (
    QgsField,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProperty,
    QgsRuleBasedRenderer,
    QgsSymbol,
)

from ..algorithms import styling
from ..algorithms.import_psk import station_fields
from ..algorithms.qt_compat import INT, utc_qdatetime
from ..core import hops as _hops
from ..core import maidenhead as mh
from .synced import SyncedLayer

LAYER_NAME = "WSJT-X live"
_OPACITY = getattr(getattr(QgsSymbol, "Property", None), "Opacity", None) or QgsSymbol.PropertyOpacity


def fade_expression(fade_minutes):
    """Opacity 0-100: 100 when just heard, down to 15 after `fade_minutes`.
    Worked stations never fade. now() is evaluated at each repaint."""
    return (
        "if(\"direction\" = 'worked', 100, "
        f"max(15, 100 - 85 * ((epoch(now()) - epoch(\"last_utc\")) / 60000.0) / {float(fade_minutes)}))"
    )


def live_renderer(fade_minutes=None):
    """Worked stars and SNR-coloured circles; fading unless fade_minutes is None."""
    root = QgsRuleBasedRenderer.Rule(None)
    worked = QgsMarkerSymbol.createSimple({"name": "star", "color": "#d62728", "size": "4",
                                           "outline_color": "255,255,255,220", "outline_width": "0.25"})
    root.appendChild(QgsRuleBasedRenderer.Rule(worked, 0, 0, "\"direction\" = 'worked'", "Worked (QSO logged)"))
    for lo, hi, color, label in styling.SNR_CLASSES:
        sym = QgsMarkerSymbol.createSimple({"name": "circle", "color": color, "size": "2.6",
                                            "outline_color": "255,255,255,200", "outline_width": "0.2"})
        expr = f"\"direction\" = 'heard' AND \"snr_best\" > {lo} AND \"snr_best\" <= {hi}"
        root.appendChild(QgsRuleBasedRenderer.Rule(sym, 0, 0, expr, f"Heard, best SNR {label}"))
    if fade_minutes is not None:
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


class LiveLayer(SyncedLayer):
    """Live station markers, fading by age."""

    NAME = LAYER_NAME
    GEOMETRY = "Point"

    def fields(self):
        return live_station_fields()

    def style(self, layer):
        layer.setRenderer(live_renderer(self.fade_minutes))
        layer.setLabeling(styling.callsign_labels())
        layer.setLabelsEnabled(True)

    def keys(self, model):
        return list(model.stations)

    def item(self, model, key):
        st = model.stations.get(key)
        if st is None or not st.grid:
            return None
        return station_point(st), live_station_attrs(model, st)

    def key_of(self, feature):
        return (feature["call"], feature["direction"])
