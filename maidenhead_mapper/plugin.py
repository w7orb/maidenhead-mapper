"""QGIS plugin: registers the Processing provider, menu/toolbar and live dock."""

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import ICON, MaidenheadProvider

MENU = "&Maidenhead Mapper"

# (algorithm id, menu text)
ACTIONS = [
    ("maidenhead:create_grid", "Create Maidenhead grid…"),
    ("maidenhead:import_psk", "Import PSK Reporter reports…"),
]


class MaidenheadMapperPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []
        self.dock = None
        self.live_action = None

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

        self.live_action = QAction(QIcon(ICON), "WSJT-X live map", self.iface.mainWindow())
        self.live_action.setCheckable(True)
        self.live_action.toggled.connect(self._toggle_dock)
        self.iface.addPluginToMenu(MENU, self.live_action)
        self.iface.addToolBarIcon(self.live_action)
        self.actions.append(self.live_action)

    def _ensure_dock(self):
        if self.dock is None:
            from .live.dock import LiveDock
            self.dock = LiveDock(self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.visibilityChanged.connect(self._dock_visibility)
        return self.dock

    def _toggle_dock(self, on):
        if on:
            self._ensure_dock().show()
            self.dock.raise_()
        elif self.dock is not None:
            self.dock.hide()  # hiding keeps the listener running

    def _dock_visibility(self, visible):
        if self.live_action is not None and self.live_action.isChecked() != visible:
            self.live_action.blockSignals(True)
            self.live_action.setChecked(visible)
            self.live_action.blockSignals(False)

    def unload(self):
        if self.dock is not None:
            self.dock.shutdown()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
            self.iface.removeToolBarIcon(action)
        self.actions = []
        self.live_action = None
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    @staticmethod
    def _open(alg_id):
        import processing  # QGIS-bundled; import lazily

        processing.execAlgorithmDialog(alg_id)
