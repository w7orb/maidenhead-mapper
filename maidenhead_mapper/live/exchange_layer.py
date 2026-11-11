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
    QgsProject,
    QgsProperty,
    QgsRuleBasedRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

from ..algorithms.qt_compat import DATETIME, DOUBLE, INT, STRING, field, utc_qdatetime
from ..core import exchange as x
from ..core import maidenhead as mh
from .layer import _OPACITY

LAYER_NAME = "WSJT-X exchanges"

FIELDS = [
    ("id", INT, 0), ("call_a", STRING, 20), ("grid_a", STRING, 10), ("call_b", STRING, 20), ("grid_b", STRING, 10),
    ("band", STRING, 10), ("mode", STRING, 10), ("stage", STRING, 15),
    ("a_hears_b", INT, 0), ("b_hears_a", INT, 0), ("me_hears_a", INT, 0), ("me_hears_b", INT, 0),
    ("paths", STRING, 80), ("involves_me", INT, 0), ("logged", INT, 0), ("messages", INT, 0),
    ("transcript", STRING, 254), ("first_utc", DATETIME, 0), ("last_utc", DATETIME, 0), ("distance_km", DOUBLE, 0),
]

# (filter, label, colour, width mm, dash pattern or None); first match wins
STYLES = [
    ("\"involves_me\" = 1", "My QSO", "#d62728", 0.9, None),
    (f"\"stage\" = '{x.CONFIRMED}'", "Confirmed (both reports + sign-off)", "#2ca02c", 0.7, None),
    (f"\"stage\" = '{x.BOTH_REPORTS}'", "Both reports", "#1f77b4", 0.55, None),
    (f"\"stage\" = '{x.ONE_REPORT}'", "One report", "#9467bd", 0.4, "3;2"),
    (f"\"stage\" = '{x.STARTED}'", "Started (no report yet)", "#7f7f7f", 0.3, "1;2"),
]


def fade_expression(fade_minutes):
    """Like the station markers; my logged QSOs never fade."""
    return (f"if(\"logged\" = 1, 100, max(10, 100 - 90 * ((epoch(now()) - epoch(\"last_utc\")) / 60000.0) "
            f"/ {float(fade_minutes)}))")


def renderer(fade_minutes):
    root = QgsRuleBasedRenderer.Rule(None)
    prop = QgsProperty.fromExpression(fade_expression(fade_minutes))
    for expr, label, colour, width, dash in STYLES:
        props = {"line_color": colour, "line_width": str(width), "capstyle": "round"}
        if dash:
            props.update({"use_custom_dash": "1", "customdash": dash, "customdash_unit": "MM"})
        sym = QgsLineSymbol.createSimple(props)
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


class ExchangeLayer:
    """Owns the exchanges layer; draws an exchange once both grids are known."""

    def __init__(self, model, fade_minutes=30):
        self.model = model
        self.fade_minutes = fade_minutes
        self.layer = None
        self.fids = {}
        self.unplaced = set()    # exchange ids waiting for a grid

    def _create(self):
        layer = QgsVectorLayer("MultiLineString?crs=EPSG:4326", LAYER_NAME, "memory")
        fields = QgsFields()
        for name, kind, length in FIELDS:
            fields.append(field(name, kind, length))
        layer.dataProvider().addAttributes(list(fields))
        layer.updateFields()
        layer.setRenderer(renderer(self.fade_minutes))
        layer.setLabeling(labels())
        layer.setLabelsEnabled(True)
        QgsProject.instance().addMapLayer(layer)
        layer.willBeDeleted.connect(self._forget)
        self.layer, self.fids = layer, {}
        return set(self.model.exchanges.exchanges)

    def _forget(self):
        self.layer, self.fids = None, {}

    def set_fade(self, minutes):
        self.fade_minutes = minutes
        if self.layer is not None:
            self.layer.setRenderer(renderer(minutes))
            self.layer.triggerRepaint()

    def placed_count(self):
        return len(self.fids)

    def _geometry(self, ga, gb):
        (lon1, lat1), (lon2, lat2) = mh.center(ga), mh.center(gb)
        parts = mh.great_circle(lon1, lat1, lon2, lat2)
        return QgsGeometry.fromMultiPolylineXY([[QgsPointXY(lon, lat) for lon, lat in p] for p in parts])

    def _attrs(self, ex, ga, gb):
        dist = round(mh.distance_bearing(ga, gb)[0], 1)
        return [
            ex.id, ex.a, ga, ex.b, gb, ex.band, ex.mode, ex.stage,
            ex.a_hears_b, ex.b_hears_a, ex.me_hears_a, ex.me_hears_b,
            paths_text(ex), int(ex.involves_me), int(ex.logged), ex.messages, ex.transcript()[:254],
            utc_qdatetime(ex.first_utc) if ex.first_utc else None,
            utc_qdatetime(ex.last_utc) if ex.last_utc else None,
            dist,
        ]

    def sync(self, ids):
        """Apply changed exchanges (plus any still waiting for a grid)."""
        ids = set(ids) | self.unplaced
        if self.layer is None:
            if not ids and not self.model.exchanges.exchanges:
                return 0
            ids |= self._create()
        dp = self.layer.dataProvider()
        new, attr_changes, geom_changes = [], {}, {}
        for i in ids:
            ex = self.model.exchanges.exchanges.get(i)
            if ex is None:
                continue
            ga, gb = self.model.grid_of(ex.a), self.model.grid_of(ex.b)
            if not (ga and gb):
                self.unplaced.add(i)
                continue
            self.unplaced.discard(i)
            geom, attrs = self._geometry(ga, gb), self._attrs(ex, ga, gb)
            fid = self.fids.get(i)
            if fid is None:
                f = QgsFeature(self.layer.fields())
                f.setGeometry(geom)
                f.setAttributes(attrs)
                new.append((i, f))
            else:
                attr_changes[fid] = dict(enumerate(attrs))
                geom_changes[fid] = geom
        if new:
            ok, added = dp.addFeatures([f for _, f in new])
            if ok:
                for (i, _), f in zip(new, added):
                    self.fids[i] = f.id()
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
