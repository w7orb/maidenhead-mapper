"""Temporary line layer: one great-circle line per FT8/FT4 exchange, with
the signal report each way."""

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsFields,
    QgsGeometry,
    QgsLineSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProperty,
    QgsRuleBasedRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

from ..algorithms.qt_compat import DATETIME, DOUBLE, INT, STRING, field, utc_qdatetime
from ..core import exchange as x
from ..core import hops as _hops
from ..core import maidenhead as mh
from .layer import _OPACITY
from .synced import SyncedLayer

LAYER_NAME = "WSJT-X exchanges"

FIELDS = [
    ("id", INT, 0), ("call_a", STRING, 20), ("grid_a", STRING, 10), ("call_b", STRING, 20), ("grid_b", STRING, 10),
    ("band", STRING, 10), ("mode", STRING, 10), ("stage", STRING, 15),
    ("a_hears_b", INT, 0), ("b_hears_a", INT, 0), ("me_hears_a", INT, 0), ("me_hears_b", INT, 0),
    ("paths", STRING, 80), ("involves_me", INT, 0), ("logged", INT, 0), ("messages", INT, 0),
    ("transcript", STRING, 254), ("first_utc", DATETIME, 0), ("last_utc", DATETIME, 0), ("distance_km", DOUBLE, 0),
    ("hops", INT, 0), ("takeoff_deg", DOUBLE, 0),
]

# (filter, label, colour, width mm, dash pattern or None); first match wins
STYLES = [
    (f"\"stage\" = '{x.CONFIRMED}'", "Confirmed (both reports + sign-off)", "#2ca02c", 0.7, None),
    (f"\"stage\" = '{x.BOTH_REPORTS}'", "Both reports", "#1f77b4", 0.55, None),
    (f"\"stage\" = '{x.ONE_REPORT}'", "One report", "#9467bd", 0.4, "3;2"),
    (f"\"stage\" = '{x.STARTED}'", "Started (no report yet)", "#7f7f7f", 0.3, "1;2"),
]


def fade_expression(fade_minutes):
    """Like the station markers; my logged QSOs never fade."""
    return (f"if(\"logged\" = 1, 100, max(10, 100 - 90 * ((epoch(now()) - epoch(\"last_utc\")) / 60000.0) "
            f"/ {float(fade_minutes)}))")


def renderer(fade_minutes=None):
    """Lines styled by stage; fading unless fade_minutes is None."""
    root = QgsRuleBasedRenderer.Rule(None)
    prop = QgsProperty.fromExpression(fade_expression(fade_minutes)) if fade_minutes is not None else None
    for expr, label, colour, width, dash in STYLES:
        props = {"line_color": colour, "line_width": str(width), "capstyle": "round"}
        if dash:
            props.update({"use_custom_dash": "1", "customdash": dash, "customdash_unit": "MM"})
        sym = QgsLineSymbol.createSimple(props)
        if prop is not None:
            sym.setDataDefinedProperty(_OPACITY, prop)
        root.appendChild(QgsRuleBasedRenderer.Rule(sym, 0, 0, expr, label))
    # Rules are exclusive top-down: later rules only catch what earlier ones didn't
    for i, rule in enumerate(root.children()):
        earlier = [f"NOT ({STYLES[j][0]})" for j in range(i)]
        rule.setFilterExpression(" AND ".join(earlier + [STYLES[i][0]]))
    return QgsRuleBasedRenderer(root)


def labels():
    s = QgsPalLayerSettings()
    s.fieldName = "paths"
    s.placement = Qgis.LabelPlacement.Line
    fmt = QgsTextFormat()
    fmt.setSize(7.5)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.8)
    buf.setColor(QColor(255, 255, 255, 230))
    fmt.setBuffer(buf)
    s.setFormat(fmt)
    s.scaleVisibility = True
    s.minimumScale = 10_000_000  # reports only when zoomed in past 1:10M
    return QgsVectorLayerSimpleLabeling(s)


def paths_text(ex):
    """'N5ER→AH6U +07  AH6U→N5ER -04': each arrow is a signal path, with the
    SNR it arrived at."""
    parts = []
    if ex.b_hears_a is not None:
        parts.append(f"{ex.a}→{ex.b} {ex.b_hears_a:+03d}")
    if ex.a_hears_b is not None:
        parts.append(f"{ex.b}→{ex.a} {ex.a_hears_b:+03d}")
    return "  ".join(parts) or f"{ex.a} – {ex.b}"


def exchange_fields():
    fields = QgsFields()
    for name, kind, length in FIELDS:
        fields.append(field(name, kind, length))
    return fields


def exchange_geometry(ga, gb):
    """Great-circle MultiLineString between two grid centres."""
    (lon1, lat1), (lon2, lat2) = mh.center(ga), mh.center(gb)
    if (lon1, lat1) == (lon2, lat2):
        lon2 += 1e-4   # same square: a ~10 m line, as zero-length lines are invalid geometries
    parts = mh.great_circle(lon1, lat1, lon2, lat2)
    return QgsGeometry.fromMultiPolylineXY([[QgsPointXY(lon, lat) for lon, lat in p] for p in parts])


def exchange_attrs(ex, ga, gb):
    dist = round(mh.distance_bearing(ga, gb)[0], 1)
    n, takeoff = _hops.likely_hops(dist)
    return [
        ex.id, ex.a, ga, ex.b, gb, ex.band, ex.mode, ex.stage,
        ex.a_hears_b, ex.b_hears_a, ex.me_hears_a, ex.me_hears_b,
        paths_text(ex), int(ex.involves_me), int(ex.logged), ex.messages, ex.transcript()[:254],
        utc_qdatetime(ex.first_utc) if ex.first_utc else None,
        utc_qdatetime(ex.last_utc) if ex.last_utc else None,
        dist, n, takeoff,
    ]


def exchange_features(model):
    """(features, unplaced count) for every exchange in a LiveModel with both grids known."""
    fields = exchange_fields()
    out, unplaced = [], 0
    for ex in model.exchanges.exchanges.values():
        ga, gb = model.grid_of(ex.a), model.grid_of(ex.b)
        if not (ga and gb):
            unplaced += 1
            continue
        f = QgsFeature(fields)
        f.setGeometry(exchange_geometry(ga, gb))
        f.setAttributes(exchange_attrs(ex, ga, gb))
        out.append(f)
    return out, unplaced


class ExchangeLayer(SyncedLayer):
    """One line per exchange; drawn once both grids are known."""

    NAME = LAYER_NAME
    GEOMETRY = "MultiLineString"

    def __init__(self, model, fade_minutes=30):
        super().__init__(model, fade_minutes)
        self.unplaced = set()    # exchange ids waiting for a grid

    def fields(self):
        return exchange_fields()

    def style(self, layer):
        layer.setRenderer(renderer(self.fade_minutes))
        layer.setLabeling(labels())
        layer.setLabelsEnabled(True)

    def keys(self, model):
        return list(model.exchanges.exchanges)

    def item(self, model, key):
        ex = model.exchanges.exchanges.get(key)
        if ex is None:
            return None
        ga, gb = model.grid_of(ex.a), model.grid_of(ex.b)
        if not (ga and gb):
            if model is self.model:
                self.unplaced.add(key)
            return None
        if model is self.model:
            self.unplaced.discard(key)
        return exchange_geometry(ga, gb), exchange_attrs(ex, ga, gb)

    def key_of(self, feature):
        return feature["id"]

    def sync(self, ids):
        """Changed exchanges plus any still waiting for a grid."""
        return super().sync(set(ids) | self.unplaced)

    def placed_count(self):
        return len(self.fids)
