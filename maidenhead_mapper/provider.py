"""Processing provider exposing the plugin's algorithms."""

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithms.create_grid import CreateGridAlgorithm
from .algorithms.fetch_psk import FetchPskAlgorithm
from .algorithms.import_alltxt import ImportAllTxtAlgorithm
from .algorithms.ionosphere import CheckPathsAlgorithm, FetchIonosphereAlgorithm
from .algorithms.antenna_alg import AntennaCompareAlgorithm
from .algorithms.balance_alg import StationBalanceAlgorithm
from .algorithms.propagation import SkipDistanceAlgorithm, SporadicEAlgorithm
from .algorithms.import_psk import ImportPskAlgorithm

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
        self.addAlgorithm(ImportPskAlgorithm())
        self.addAlgorithm(FetchPskAlgorithm())
        self.addAlgorithm(ImportAllTxtAlgorithm())
        self.addAlgorithm(FetchIonosphereAlgorithm())
        self.addAlgorithm(CheckPathsAlgorithm())
        self.addAlgorithm(SporadicEAlgorithm())
        self.addAlgorithm(SkipDistanceAlgorithm())
        self.addAlgorithm(StationBalanceAlgorithm())
        self.addAlgorithm(AntennaCompareAlgorithm())
