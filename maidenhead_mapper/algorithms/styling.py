"""Renderers and labels for station marker layers (built in code, no .qml)."""

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsGraduatedSymbolRenderer,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProcessingLayerPostProcessorInterface,
    QgsRendererCategory,
    QgsRendererRange,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

from ..core.bands import BAND_NAMES

STYLES = ["SNR (best report)", "Band", "Direction", "None (single symbol)"]
STYLE_SNR, STYLE_BAND, STYLE_DIRECTION, STYLE_NONE = range(4)

# Common band colours (close to the scheme used by PSK Reporter / GridTracker).
BAND_COLORS = {
    "2200m": "#ff4500", "630m": "#1e90ff", "160m": "#7cfc00", "80m": "#e550e5",
    "60m": "#00008b", "40m": "#5959ff", "30m": "#62d962", "20m": "#f2c40c",
    "17m": "#f2f261", "15m": "#cca166", "12m": "#b22222", "10m": "#ff69b4",
    "6m": "#ff0000", "4m": "#cc0044", "2m": "#ff1493", "1.25m": "#ccff00",
    "70cm": "#999900", "23cm": "#5ae8e8",
}

# (low, high, colour, label) for best SNR in dB
SNR_CLASSES = [
    (-99, -20, "#440154", "≤ -20 dB"),
    (-20, -15, "#3b528b", "-20 to -15"),
    (-15, -10, "#21918c", "-15 to -10"),
    (-10, -5, "#5ec962", "-10 to -5"),
    (-5, 0, "#b5de2b", "-5 to 0"),
    (0, 99, "#fde725", "> 0 dB"),
]


def _marker(color, shape="circle", size=2.4):
    return QgsMarkerSymbol.createSimple({
        "name": shape, "color": color, "size": str(size),
        "outline_color": "255,255,255,200", "outline_width": "0.2",
    })


def renderer_for(style):
    if style == STYLE_SNR:
        ranges = [QgsRendererRange(lo, hi, _marker(c), label) for lo, hi, c, label in SNR_CLASSES]
        r = QgsGraduatedSymbolRenderer("snr_best", ranges)
        return r
    if style == STYLE_BAND:
        cats = [QgsRendererCategory(b, _marker(BAND_COLORS.get(b, "#888888")), b) for b in BAND_NAMES]
        cats.append(QgsRendererCategory(None, _marker("#888888"), "other / several"))
        # A station heard on several bands is coloured by its lowest band.
        return QgsCategorizedSymbolRenderer("regexp_substr(\"bands\", '^[^,]+')", cats)
    if style == STYLE_DIRECTION:
        cats = [
            QgsRendererCategory("heard", _marker("#1f77b4"), "I heard them"),
            QgsRendererCategory("heard_me", _marker("#ff7f0e", "triangle", 2.8), "They heard me"),
            QgsRendererCategory("worked", _marker("#d62728", "star", 3.4), "Worked (QSO)"),
        ]
        return QgsCategorizedSymbolRenderer("direction", cats)
    return None


def callsign_labels():
    fmt = QgsTextFormat()
    fmt.setSize(7)
    fmt.setColor(QColor(30, 30, 30))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.7)
    buf.setColor(QColor(255, 255, 255, 220))
    fmt.setBuffer(buf)
    s = QgsPalLayerSettings()
    s.fieldName = "call"
    s.placement = Qgis.LabelPlacement.AroundPoint
    s.setFormat(fmt)
    s.scaleVisibility = True
    s.minimumScale = 3_000_000  # callsigns only when zoomed in
    return QgsVectorLayerSimpleLabeling(s)


# Post-processors must outlive the algorithm run; QGIS keeps only a raw pointer.
_keep_alive = []


class StationStyler(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, style, properties=None):
        super().__init__()
        self.style = style
        self.properties = properties or {}

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        r = renderer_for(self.style)
        if r is not None:
            layer.setRenderer(r)
        layer.setLabeling(callsign_labels())
        layer.setLabelsEnabled(True)
        for key, value in self.properties.items():
            layer.setCustomProperty(key, value)
        layer.triggerRepaint()


class LayerStyler(QgsProcessingLayerPostProcessorInterface):
    """Applies renderer/labeling factories (called on the main thread) and properties."""

    def __init__(self, renderer=None, labeling=None, properties=None):
        super().__init__()
        self.renderer, self.labeling, self.properties = renderer, labeling, properties or {}

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        if self.renderer:
            layer.setRenderer(self.renderer())
        if self.labeling:
            layer.setLabeling(self.labeling())
            layer.setLabelsEnabled(True)
        for key, value in self.properties.items():
            layer.setCustomProperty(key, value)
        layer.triggerRepaint()


def attach_styler(context, dest_id, name, styler):
    """Name an output layer and run `styler` on it once Processing loads it."""
    if not context.willLoadLayerOnCompletion(dest_id):
        return
    details = context.layerToLoadOnCompletionDetails(dest_id)
    details.name = name
    _keep_alive.append(styler)
    details.setPostProcessor(styler)


def attach(context, dest_id, name, style, properties=None):
    """Name and style an output layer once Processing loads it.

    properties: custom layer properties to set (saved with the project).
    """
    if not context.willLoadLayerOnCompletion(dest_id):
        return
    details = context.layerToLoadOnCompletionDetails(dest_id)
    details.name = name
    styler = StationStyler(style, properties)
    _keep_alive.append(styler)
    details.setPostProcessor(styler)
