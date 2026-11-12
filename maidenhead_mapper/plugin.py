"""QGIS plugin: registers the Processing provider, menu/toolbar and docks."""

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu, QToolButton

from .provider import ICON, MaidenheadProvider

MENU = "&Maidenhead Mapper"


def _live_dock(parent):
    from .live.dock import LiveDock
    return LiveDock(parent)


def _propagation_dock(parent):
    from .timeline.propagation_dock import PropagationDock
    return PropagationDock(parent)


def _timeline_dock(parent):
    from .timeline.dock import TimelineDock
    return TimelineDock(parent)


# dock key -> (menu text, factory, area)
DOCKS = {
    "timeline": ("Activity timeline", _timeline_dock, Qt.DockWidgetArea.BottomDockWidgetArea),
    "live": ("WSJT-X live map", _live_dock, Qt.DockWidgetArea.RightDockWidgetArea),
    "propagation": ("Propagation over time (ALL.TXT)", _propagation_dock, Qt.DockWidgetArea.RightDockWidgetArea),
}

# The menu, by purpose. ("alg", id, text) opens a Processing tool; ("dock", key)
# toggles a panel; None is a separator. The ionosphere tools work on any
# station or exchange layer and stay out of the WSJT-X section on purpose.
LAYOUT = [
    ("Grid", [("alg", "maidenhead:create_grid", "Create Maidenhead grid…")]),
    ("PSK Reporter", [
        ("alg", "maidenhead:fetch_psk", "Fetch from PSK Reporter…"),
        ("alg", "maidenhead:import_psk", "Import PSK Reporter reports…"),
    ]),
    ("WSJT-X", [
        ("dock", "live"),
        ("alg", "maidenhead:import_alltxt", "Import WSJT-X ALL.TXT…"),
        ("alg", "maidenhead:import_adif", "Import ADIF log (.adi / .adx)…"),
        None,
        ("alg", "maidenhead:summarize_exchanges", "Summarize exchanges by grid pair…"),
        ("dock", "propagation"),
        ("alg", "maidenhead:station_balance", "Station balance from two-way reports…"),
        ("alg", "maidenhead:antenna_compare", "Antenna A/B comparison…"),
    ]),
    ("Ionosphere", [
        ("alg", "maidenhead:fetch_ionosphere", "Fetch ionosphere (KC2G)…"),
        ("alg", "maidenhead:check_paths", "Check paths against the ionosphere (MUF)…"),
        None,
        ("alg", "maidenhead:sporadic_e", "Find sporadic-E paths…"),
        ("alg", "maidenhead:skip_distance", "Skip distance by hour…"),
    ]),
    None,
    ("dock", "timeline"),
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
        self.toggles = []           # _DockToggle, in DOCKS order
        self._toggle_by_key = {}
        self.root = None            # QMenu holding the whole layout
        self.menu_actions = []      # top-level actions added to Plugins > Maidenhead Mapper
        self.toolbar_action = None

    def initProcessing(self):  # noqa: N802
        self.provider = MaidenheadProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        win = self.iface.mainWindow()
        for key, (text, factory, area) in DOCKS.items():
            t = _DockToggle(self.iface, text, factory, area)
            self.toggles.append(t)
            self._toggle_by_key[key] = t

        self.root = QMenu("Maidenhead Mapper", win)
        self.root.setIcon(QIcon(ICON))
        for entry in LAYOUT:
            if entry is None:
                self.root.addSeparator()
            elif entry[0] == "dock":
                self.root.addAction(self._toggle_by_key[entry[1]].action)
            else:
                title, items = entry
                sub = self.root.addMenu(title)
                for item in items:
                    if item is None:
                        sub.addSeparator()
                    elif item[0] == "dock":
                        sub.addAction(self._toggle_by_key[item[1]].action)
                    else:
                        _, alg_id, text = item
                        action = QAction(text, win)
                        action.triggered.connect(lambda _=False, a=alg_id: self._open(a))
                        sub.addAction(action)

        # Plugins > Maidenhead Mapper shows the same sections
        for action in self.root.actions():
            if action.isSeparator():
                continue
            self.iface.addPluginToMenu(MENU, action)
            self.menu_actions.append(action)

        # One toolbar button that drops down the whole menu
        button = QToolButton(win)
        button.setIcon(QIcon(ICON))
        button.setToolTip("Maidenhead Mapper")
        button.setMenu(self.root)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.toolbar_action = self.iface.addToolBarWidget(button)

    def unload(self):
        for toggle in self.toggles:
            toggle.unload()
        self.toggles, self._toggle_by_key = [], {}
        for action in self.menu_actions:
            self.iface.removePluginMenu(MENU, action)
        self.menu_actions = []
        if self.toolbar_action is not None:
            self.iface.removeToolBarIcon(self.toolbar_action)
            self.toolbar_action = None
        if self.root is not None:
            self.root.deleteLater()
            self.root = None
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    @staticmethod
    def _open(alg_id):
        import processing  # QGIS-bundled; import lazily

        processing.execAlgorithmDialog(alg_id)
