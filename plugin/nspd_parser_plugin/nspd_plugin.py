"""Класс плагина: кнопка на панели инструментов и док-панель выгрузки."""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

try:
    # В Qt6 QAction переехал из QtWidgets в QtGui.
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction

from .core_loader import load_core
from .nspd_dock import NspdParserDock

MENU_NAME = "НСПД"


class NspdParserPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.core = load_core()
        self.action = None
        self.dock = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "Выгрузка объектов НСПД", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip("Показать панель выгрузки объектов НСПД по клику по карте")
        self.action.toggled.connect(self._set_dock_visible)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToWebMenu(MENU_NAME, self.action)

    def unload(self):
        if self.dock is not None:
            self.dock.stop()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginWebMenu(MENU_NAME, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None

    def _set_dock_visible(self, visible):
        if visible and self.dock is None:
            self.dock = NspdParserDock(self.iface, self.core)
            area = self.core.qt_enum(Qt, "DockWidgetArea", "RightDockWidgetArea")
            self.iface.addDockWidget(area, self.dock)
            self.dock.visibilityChanged.connect(self.action.setChecked)
        if self.dock is not None:
            self.dock.setVisible(visible)
