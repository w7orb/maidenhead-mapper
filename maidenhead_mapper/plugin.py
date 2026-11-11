"""QGIS plugin: registers the Processing provider and a menu/toolbar."""

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import ICON, MaidenheadProvider

MENU = "&Maidenhead Mapper"

# (algorithm id, menu text)
ACTIONS = [
    ("maidenhead:create_grid", "Create Maidenhead grid…"),
]


class MaidenheadMapperPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []

    def initProcessing(self):  # noqa: N802
        self.provider = MaidenheadProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        for alg_id, text in ACTIONS:
            action = QAction(QIcon(ICON), text, self.iface.mainWindow())
            action.triggered.connect(lambda _=False, a=alg_id: self._open(a))
            self.iface.addPluginToMenu(MENU, action)
            self.iface.addToolBarIcon(action)
            self.actions.append(action)

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
            self.iface.removeToolBarIcon(action)
        self.actions = []
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    @staticmethod
    def _open(alg_id):
        import processing  # QGIS-bundled; import lazily

        processing.execAlgorithmDialog(alg_id)
