"""QGIS plugin: registers the Processing provider, menu/toolbar and docks."""

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import ICON, MaidenheadProvider

MENU = "&Maidenhead Mapper"

# (algorithm id, menu text)
ACTIONS = [
    ("maidenhead:create_grid", "Create Maidenhead grid…"),
    ("maidenhead:fetch_psk", "Fetch from PSK Reporter…"),
    ("maidenhead:import_psk", "Import PSK Reporter reports…"),
]


def _live_dock(parent):
    from .live.dock import LiveDock
    return LiveDock(parent)


def _timeline_dock(parent):
    from .timeline.dock import TimelineDock
    return TimelineDock(parent)


# (menu text, dock factory, dock area)
DOCKS = [
    ("Activity timeline", _timeline_dock, Qt.DockWidgetArea.BottomDockWidgetArea),
    ("WSJT-X live map", _live_dock, Qt.DockWidgetArea.RightDockWidgetArea),
]


class _DockToggle:
    """A checkable action that shows/hides a dock created on first use."""

    def __init__(self, iface, text, factory, area):
        self.iface, self.factory, self.area = iface, factory, area
        self.dock = None
        self.action = QAction(QIcon(ICON), text, iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._toggle)

    def _toggle(self, on):
        if on:
            if self.dock is None:
                self.dock = self.factory(self.iface.mainWindow())
                self.iface.addDockWidget(self.area, self.dock)
                self.dock.visibilityChanged.connect(self._visibility)
            self.dock.show()
            self.dock.raise_()
        elif self.dock is not None:
            self.dock.hide()  # hiding keeps it running (e.g. the live listener)

    def _visibility(self, visible):
        if self.action.isChecked() != visible:
            self.action.blockSignals(True)
            self.action.setChecked(visible)
            self.action.blockSignals(False)

    def unload(self):
        if self.dock is not None:
            self.dock.shutdown()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None


class MaidenheadMapperPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []
        self.toggles = []

    def initProcessing(self):  # noqa: N802
        self.provider = MaidenheadProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        for alg_id, text in ACTIONS:
            action = QAction(QIcon(ICON), text, self.iface.mainWindow())
            action.triggered.connect(lambda _=False, a=alg_id: self._open(a))
            self._add(action)
        for text, factory, area in DOCKS:
            toggle = _DockToggle(self.iface, text, factory, area)
            self.toggles.append(toggle)
            self._add(toggle.action)

    def _add(self, action):
        self.iface.addPluginToMenu(MENU, action)
        self.iface.addToolBarIcon(action)
        self.actions.append(action)

    def unload(self):
        for toggle in self.toggles:
            toggle.unload()
        self.toggles = []
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
