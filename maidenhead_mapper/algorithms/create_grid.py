"""Processing algorithm: Create Maidenhead grid (fields and/or squares)."""

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsGeometry,
    QgsPalLayerSettings,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSink,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QColor, QFont

from ..core import maidenhead as mh
from .qt_compat import INT as _INT
from .qt_compat import STRING as _STRING

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

# Post-processors must outlive the algorithm run; QGIS only holds a raw pointer.
_post_processors = []


class _GridStyler(QgsProcessingLayerPostProcessorInterface):
    """Applies default symbology and labels after the layer is loaded."""

    def __init__(self, precision):
        super().__init__()
        self.precision = precision

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        is_field = self.precision == mh.FIELD
        symbol = QgsFillSymbol.createSimple({
            "color": "70,130,180,40" if is_field else "0,0,0,0",
            "outline_color": "30,60,120,220" if is_field else "30,60,120,140",
            "outline_width": "0.5" if is_field else "0.15",
        })
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

        fmt = QgsTextFormat()
        fmt.setFont(QFont("Arial"))
        fmt.setSize(12 if is_field else 8)
        fmt.setColor(QColor(20, 40, 90))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(0.8)
        buf.setColor(QColor(255, 255, 255, 200))
        fmt.setBuffer(buf)

        settings = QgsPalLayerSettings()
        settings.fieldName = "locator"
        settings.placement = Qgis.LabelPlacement.OverPoint
        settings.setFormat(fmt)
        if not is_field:
            # 32,400 squares are an unreadable mesh at world scale: hide the
            # layer beyond 1:20M and its labels beyond 1:15M.
            layer.setScaleBasedVisibility(True)
            layer.setMinimumScale(20_000_000)  # most zoomed-out scale shown
            settings.scaleVisibility = True
            settings.minimumScale = 15_000_000
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()


class CreateGridAlgorithm(QgsProcessingAlgorithm):
    LEVEL = "LEVEL"
    EXTENT = "EXTENT"
    OUTPUT_FIELDS = "OUTPUT_FIELDS"
    OUTPUT_SQUARES = "OUTPUT_SQUARES"

    LEVELS = ["Fields (2-char)", "Squares (4-char)", "Fields and squares"]

    def tr(self, text):
        return QCoreApplication.translate("CreateGridAlgorithm", text)

    def createInstance(self):  # noqa: N802
        return CreateGridAlgorithm()

    def name(self):
        return "create_grid"

    def displayName(self):  # noqa: N802
        return self.tr("Create Maidenhead grid")

    def group(self):
        return self.tr("Grid")

    def groupId(self):  # noqa: N802
        return "grid"

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            "Creates Maidenhead locator polygons with a 'locator' attribute.\n\n"
            "Fields are 20°×10° (AA–RR, 324 worldwide). Squares are 2°×1° "
            "(e.g. CN85, 32,400 worldwide).\n\n"
            "Leave Extent empty for the whole world. The grid always lines up "
            "with -180..180 / -90..90, whatever the extent or basemap."
        )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterEnum(
            self.LEVEL, self.tr("Grid level"), options=self.LEVELS, defaultValue=2))
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Extent (empty = whole world)"), optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_FIELDS, self.tr("Maidenhead fields"),
            QgsProcessing.TypeVectorPolygon, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_SQUARES, self.tr("Maidenhead squares"),
            QgsProcessing.TypeVectorPolygon, optional=True, createByDefault=True))

    def _extent_wgs84(self, parameters, context):
        if not parameters.get(self.EXTENT):
            return None
        rect = self.parameterAsExtent(parameters, self.EXTENT, context)
        if rect.isNull() or rect.isEmpty():
            return None
        crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
        if crs.isValid() and crs != WGS84:
            xform = QgsCoordinateTransform(crs, WGS84, context.transformContext())
            rect = xform.transformBoundingBox(rect)
        return (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        level = self.parameterAsEnum(parameters, self.LEVEL, context)
        extent = self._extent_wgs84(parameters, context)

        jobs = []
        if level in (0, 2):
            jobs.append((self.OUTPUT_FIELDS, mh.FIELD, "Maidenhead fields"))
        if level in (1, 2):
            jobs.append((self.OUTPUT_SQUARES, mh.SQUARE, "Maidenhead squares"))

        fields = QgsFields()
        fields.append(QgsField("locator", _STRING, len=4))
        fields.append(QgsField("field", _STRING, len=2))
        fields.append(QgsField("level", _INT))

        results = {}
        for out_param, precision, layer_name in jobs:
            if parameters.get(out_param) is None:
                continue  # output skipped by the user
            sink, dest_id = self.parameterAsSink(
                parameters, out_param, context, fields, QgsWkbTypes.Polygon, WGS84)
            if sink is None:
                raise QgsProcessingException(self.invalidSinkError(parameters, out_param))

            cells = list(mh.iter_cells(precision, extent))
            if not cells:
                feedback.reportError(self.tr("Extent does not intersect the globe."))
            total = max(len(cells), 1)
            for i, (loc, (w, s, e, n)) in enumerate(cells):
                if feedback.isCanceled():
                    break
                feat = QgsFeature(fields)
                feat.setGeometry(QgsGeometry.fromRect(QgsRectangle(w, s, e, n)))
                feat.setAttributes([loc, loc[:2], precision])
                sink.addFeature(feat, QgsFeatureSink.FastInsert)
                if i % 500 == 0:
                    feedback.setProgress(100.0 * i / total)
            feedback.pushInfo(f"{layer_name}: {len(cells)} cells")

            if context.willLoadLayerOnCompletion(dest_id):
                details = context.layerToLoadOnCompletionDetails(dest_id)
                details.name = layer_name
                styler = _GridStyler(precision)
                _post_processors.append(styler)
                details.setPostProcessor(styler)
            results[out_param] = dest_id
        return results
