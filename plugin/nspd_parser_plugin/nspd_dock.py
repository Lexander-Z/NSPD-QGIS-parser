"""Док-панель плагина: выбор слоёв НСПД и файла, запуск и остановка инструмента клика."""

import os

from qgis.core import QgsMessageLog, QgsProject, QgsSettings
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

SETTINGS_GROUP = "NSPD-QGIS-parser"
DEFAULT_FILE_NAME = "NSPD.gpkg"
LOG_TAG = "НСПД"


class NspdParserDock(QDockWidget):

    def __init__(self, iface, core):
        super().__init__("Выгрузка НСПД", iface.mainWindow())
        self.setObjectName("NspdParserDock")
        self.iface = iface
        self.core = core
        self.tool = None
        self._build_ui()
        self._load_settings()
        self._update_state()

    # === Интерфейс ===

    def _build_ui(self):
        widget = QWidget(self)
        layout = QVBoxLayout(widget)

        group = QGroupBox("Слои НСПД", widget)
        group_layout = QVBoxLayout(group)
        self.layer_boxes = []
        for layer_def in self.core.LAYERS:
            box = QCheckBox(layer_def.title, group)
            group_layout.addWidget(box)
            self.layer_boxes.append((layer_def, box))
        layout.addWidget(group)

        layout.addWidget(QLabel("Файл GeoPackage:", widget))
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(widget)
        self.path_edit.setPlaceholderText("Выберите или введите путь к файлу .gpkg")
        browse_button = QPushButton("Обзор...", widget)
        browse_button.clicked.connect(self._choose_path)
        path_row.addWidget(self.path_edit)
        path_row.addWidget(browse_button)
        layout.addLayout(path_row)

        form = QFormLayout()
        self.count_spin = QSpinBox(widget)
        self.count_spin.setRange(1, 1000)
        self.timeout_spin = QSpinBox(widget)
        self.timeout_spin.setRange(5, 600)
        self.timeout_spin.setSuffix(" с")
        form.addRow("Объектов за клик:", self.count_spin)
        form.addRow("Таймаут запроса:", self.timeout_spin)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("Начать", widget)
        self.stop_button = QPushButton("Остановить", widget)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)

        self.status_label = QLabel(widget)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit(widget)
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

        self.setWidget(widget)

    def _update_state(self):
        running = self.tool is not None
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.status_label.setText(
            "Выгрузка идёт: кликайте по карте." if running else "Выгрузка остановлена.")

    def _log(self, text):
        self.log_view.appendPlainText(text)
        QgsMessageLog.logMessage(text, LOG_TAG)

    # === Настройки ===

    def _load_settings(self):
        settings = QgsSettings()
        prefix = SETTINGS_GROUP + "/plugin/"
        checked = str(settings.value(prefix + "layers", self.core.LAYERS[0].code) or "").split(",")
        for layer_def, box in self.layer_boxes:
            box.setChecked(layer_def.code in checked)
        self.path_edit.setText(str(settings.value(prefix + "path", "") or ""))
        defaults = self.core.RequestSettings()
        self.count_spin.setValue(_to_int(settings.value(prefix + "featureCount"), defaults.feature_count))
        self.timeout_spin.setValue(_to_int(settings.value(prefix + "timeoutS"), defaults.timeout_ms // 1000))

    def _save_settings(self):
        settings = QgsSettings()
        prefix = SETTINGS_GROUP + "/plugin/"
        codes = [layer_def.code for layer_def, box in self.layer_boxes if box.isChecked()]
        settings.setValue(prefix + "layers", ",".join(codes))
        settings.setValue(prefix + "path", self.path_edit.text().strip())
        settings.setValue(prefix + "featureCount", self.count_spin.value())
        settings.setValue(prefix + "timeoutS", self.timeout_spin.value())

    def _choose_path(self, _checked=False):
        current = self.path_edit.text().strip()
        if not current:
            folder = QgsSettings().value(SETTINGS_GROUP + "/lastDir", "")
            if not folder or not os.path.isdir(folder):
                folder = QgsProject.instance().homePath() or os.path.expanduser("~")
            current = os.path.join(folder, DEFAULT_FILE_NAME)
        # Вопрос «заменить файл?» не нужен: существующий файл не заменяется,
        # объекты дописываются в слои внутри него.
        path, _ = QFileDialog.getSaveFileName(
            self, "Файл GeoPackage для объектов НСПД", current, "GeoPackage (*.gpkg)",
            options=self.core.qt_enum(QFileDialog, "Option", "DontConfirmOverwrite"))
        if path:
            self.path_edit.setText(path)
            QgsSettings().setValue(SETTINGS_GROUP + "/lastDir", os.path.dirname(path))

    # === Запуск и остановка ===

    def start(self, _checked=False):
        layer_defs = [layer_def for layer_def, box in self.layer_boxes if box.isChecked()]
        if not layer_defs:
            self.status_label.setText("Отметьте хотя бы один слой НСПД.")
            return
        path = self.path_edit.text().strip()
        if not path:
            self._choose_path()
            path = self.path_edit.text().strip()
            if not path:
                return
        if not path.lower().endswith(".gpkg"):
            path += ".gpkg"
            self.path_edit.setText(path)
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            self.status_label.setText("Папка {0} не существует.".format(folder))
            return

        settings = self.core.RequestSettings()
        settings.feature_count = self.count_spin.value()
        settings.timeout_ms = self.timeout_spin.value() * 1000
        session = self.core.NspdSession(path, layer_defs, settings, self.core.Logger(self._log))
        canvas = self.iface.mapCanvas()
        tool = self.core.NspdIdentifyTool(canvas, session)
        tool.deactivated.connect(lambda tool=tool: self._on_tool_deactivated(tool))
        self.tool = tool
        canvas.setMapTool(tool)
        self._save_settings()
        self._log("NSPD: выгрузка начата. Слои: {0}. Файл: {1}".format(
            ", ".join(layer_def.title for layer_def in layer_defs), path))
        self._update_state()

    def stop(self, _checked=False):
        tool, self.tool = self.tool, None
        if tool is not None:
            if self.iface.mapCanvas().mapTool() is tool:
                self.iface.actionPan().trigger()
            self._log("NSPD: выгрузка остановлена.")
        self._update_state()

    def _on_tool_deactivated(self, tool):
        # Пользователь выбрал другой инструмент на панели QGIS.
        if self.tool is tool:
            self.tool = None
            self._log("NSPD: выгрузка остановлена: выбран другой инструмент карты.")
            self._update_state()

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
