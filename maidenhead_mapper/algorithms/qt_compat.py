"""Small helpers that differ across the supported QGIS / Qt versions."""

from qgis.core import Qgis, QgsField
from qgis.PyQt.QtCore import QDateTime, QMetaType, Qt, QVariant

# QGIS 3.38 switched QgsField from QVariant.Type to QMetaType.Type.
if Qgis.QGIS_VERSION_INT >= 33800:
    STRING, INT, DOUBLE, DATETIME = (QMetaType.Type.QString, QMetaType.Type.Int,
                                     QMetaType.Type.Double, QMetaType.Type.QDateTime)
else:
    STRING, INT, DOUBLE, DATETIME = QVariant.String, QVariant.Int, QVariant.Double, QVariant.DateTime


def field(name, kind, length=0):
    return QgsField(name, kind, len=length)


def utc_qdatetime(dt):
    """Python/pandas datetime (tz-aware) -> QDateTime in UTC."""
    secs = int(dt.timestamp())
    return QDateTime.fromSecsSinceEpoch(secs, Qt.TimeSpec.UTC)
