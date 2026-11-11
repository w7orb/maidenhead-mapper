"""Processing provider exposing the plugin's algorithms."""

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithms.create_grid import CreateGridAlgorithm

ICON = os.path.join(os.path.dirname(__file__), "icons", "icon.svg")


class MaidenheadProvider(QgsProcessingProvider):
    def id(self):
        return "maidenhead"

    def name(self):
        return "Maidenhead Mapper"

    def icon(self):
        return QIcon(ICON)

    def loadAlgorithms(self):  # noqa: N802
        self.addAlgorithm(CreateGridAlgorithm())
        # Phase 2: self.addAlgorithm(ImportWsjtxAlgorithm())
