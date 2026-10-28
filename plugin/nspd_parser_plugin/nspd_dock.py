"""Док-панель плагина.

Сверху вниз: связь с НСПД (сертификаты Минцифры, проверка), выбор слоёв и
показ их на карте, файл для сохранения, дополнительные настройки, запуск и
остановка, строка состояния и журнал.
"""

import os

from qgis.core import QgsMessageLog, QgsProject, QgsSettings
from qgis.gui import QgsCollapsibleGroupBox
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QCursor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import nspd_certificates as certificates
from .nspd_cert_dialog import CertificatesDialog

SETTINGS_GROUP = "NSPD-QGIS-parser"
DEFAULT_FILE_NAME = "NSPD.gpkg"
LOG_TAG = "НСПД"

STATUS_COLORS = {"ok": "#2e7d32", "warning": "#b35900", "error": "#c62828", "info": ""}


def plural(number, one, few, many):
    """Форма слова для числа: 1 объект, 2 объекта, 5 объектов."""
    rest = abs(number) % 100
    if 11 <= rest <= 19:
        return many
    rest %= 10
    if rest == 1:
        return one
    if 2 <= rest <= 4:
        return few
    return many


def objects_text(number):
    return "{0} {1}".format(number, plural(number, "объект", "объекта", "объектов"))


class NspdParserDock(QDockWidget):

    def __init__(self, iface, core):
        super().__init__("Выгрузка НСПД", iface.mainWindow())
        self.setObjectName("NspdParserDock")
        self.iface = iface
        self.core = core
        self.tool = None
        self.session = None
        self._build_ui()
        self._load_settings()
        self._refresh_certificates()
        self._update_buttons()
        self._set_status("Отметьте слои, проверьте файл и нажмите «Начать выгрузку».")

    # === Интерфейс ===

    def _build_ui(self):
        widget = QWidget(self)
        layout = QVBoxLayout(widget)

        connection = QGroupBox("Связь с НСПД", widget)
        connection_layout = QVBoxLayout(connection)
        self.certificates_label = QLabel(connection)
        self.certificates_label.setWordWrap(True)
        connection_layout.addWidget(self.certificates_label)
        row = QHBoxLayout()
        certificates_button = QPushButton("Сертификаты...", connection)
        certificates_button.setToolTip(
            "Состояние сертификатов Минцифры и их установка в QGIS. Без них НСПД не отвечает.")
        certificates_button.clicked.connect(self._open_certificates)
        check_button = QPushButton("Проверить связь", connection)
        check_button.setToolTip("Пробный запрос к НСПД: отвечает ли сервер и доверяет ли QGIS его сертификату")
        check_button.clicked.connect(self._check_connection)
        row.addWidget(certificates_button)
        row.addWidget(check_button)
        row.addStretch(1)
        connection_layout.addLayout(row)
        layout.addWidget(connection)

        layers = QGroupBox("1. Какие слои выгружать", widget)
        layers_layout = QVBoxLayout(layers)
        self.layer_boxes = []
        for layer_def in self.core.LAYERS:
            box = QCheckBox(layer_def.title, layers)
            box.setToolTip("Код слоя НСПД {0}. Слой в файле: {1}".format(layer_def.code, layer_def.gpkg_layer))
            layers_layout.addWidget(box)
            self.layer_boxes.append((layer_def, box))
        self.show_on_map_check = QCheckBox("Показывать отмеченные слои НСПД на карте", layers)
        self.show_on_map_check.setToolTip(
            "При запуске выгрузки в проект добавляются WMS-слои НСПД (группа «НСПД»), "
            "если их там ещё нет: так видно, куда кликать.")
        layers_layout.addWidget(self.show_on_map_check)
        add_button = QPushButton("Добавить отмеченные слои на карту", layers)
        add_button.setToolTip("Добавить WMS-слои НСПД в проект сейчас, не начиная выгрузку")
        add_button.clicked.connect(self._add_layers_to_map)
        layers_layout.addWidget(add_button)
        layout.addWidget(layers)

        output = QGroupBox("2. Куда сохранять", widget)
        output_layout = QVBoxLayout(output)
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(output)
        self.path_edit.setToolTip("Файл GeoPackage. Каждый слой НСПД пишется в свой слой внутри файла.")
        browse_button = QPushButton("Обзор...", output)
        browse_button.clicked.connect(self._choose_path)
        path_row.addWidget(self.path_edit)
        path_row.addWidget(browse_button)
        output_layout.addLayout(path_row)
        hint = QLabel("Если файл уже есть, объекты дописываются в него, а не заменяют старые.", output)
        hint.setWordWrap(True)
        output_layout.addWidget(hint)
        layout.addWidget(output)

        advanced = QgsCollapsibleGroupBox("Дополнительно", widget)
        advanced.setObjectName("NspdParserAdvanced")
        form = QFormLayout(advanced)
        self.count_spin = QSpinBox(advanced)
        self.count_spin.setRange(1, 1000)
        self.count_spin.setToolTip(
            "Сколько объектов просить у НСПД за один клик (FEATURE_COUNT). Если придёт ровно "
            "столько, в журнале будет предупреждение, что выдача могла быть обрезана.")
        self.timeout_spin = QSpinBox(advanced)
        self.timeout_spin.setRange(5, 600)
        self.timeout_spin.setSuffix(" с")
        self.timeout_spin.setToolTip("Сколько ждать ответа НСПД. Пока идёт запрос, QGIS не реагирует.")
        form.addRow("Объектов за клик:", self.count_spin)
        form.addRow("Таймаут запроса:", self.timeout_spin)
        advanced.setCollapsed(True)
        layout.addWidget(advanced)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("Начать выгрузку", widget)
        self.start_button.setToolTip("Включить инструмент: каждый клик по карте выгружает объекты под курсором")
        self.stop_button = QPushButton("Остановить", widget)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)

        self.status_label = QLabel(widget)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        log_group = QGroupBox("Журнал", widget)
        log_layout = QVBoxLayout(log_group)
        self.log_view = QPlainTextEdit(log_group)
        self.log_view.setReadOnly(True)
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_group, 1)

        self.setWidget(widget)

    def _update_buttons(self):
        running = self.tool is not None
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _set_status(self, text, level="info"):
        self.status_label.setText(text)
        color = STATUS_COLORS.get(level, "")
        self.status_label.setStyleSheet("color: {0};".format(color) if color else "")

    def _log(self, text):
        self.log_view.appendPlainText(text)
        QgsMessageLog.logMessage(text, LOG_TAG)

    def _selected_layers(self):
        return [layer_def for layer_def, box in self.layer_boxes if box.isChecked()]

    def _request_settings(self):
        settings = self.core.RequestSettings()
        settings.feature_count = self.count_spin.value()
        settings.timeout_ms = self.timeout_spin.value() * 1000
        return settings

    def _busy(self):
        QApplication.setOverrideCursor(QCursor(self.core.qt_enum(Qt, "CursorShape", "WaitCursor")))

    # === Сертификаты и связь ===

    def _refresh_certificates(self):
        trusted = certificates.root_trusted()
        if trusted is None:
            text, level = "Не удалось проверить, установлены ли сертификаты Минцифры.", "warning"
        elif trusted:
            text, level = "Сертификаты Минцифры: QGIS им доверяет.", "ok"
        else:
            text, level = ("Сертификаты Минцифры не установлены, без них НСПД не отвечает. "
                           "Нажмите «Сертификаты...», чтобы установить их в QGIS."), "warning"
        self.certificates_label.setText(text)
        color = STATUS_COLORS[level]
        self.certificates_label.setStyleSheet("color: {0};".format(color) if color else "")

    def _open_certificates(self, _checked=False):
        dialog = CertificatesDialog(self)
        self.core.exec_event_loop(dialog)
        dialog.deleteLater()
        self._refresh_certificates()

    def _run_connection_check(self):
        """Пробный запрос к НСПД. Возвращает NspdRequestError или None, если связь есть."""
        self._busy()
        try:
            error = self.core.check_connection(self._request_settings())
        finally:
            QApplication.restoreOverrideCursor()
        if error is None:
            self._log("NSPD: проверка связи: НСПД отвечает.")
        else:
            self._log("NSPD [ошибка]: проверка связи: {0}".format(error))
        self._refresh_certificates()
        return error

    def _check_connection(self, _checked=False):
        error = self._run_connection_check()
        if error is None:
            self._set_status("Связь с НСПД есть.", "ok")
        else:
            self._set_status("Связи с НСПД нет: {0}".format(error), "error")

    def _confirm_without_certificates(self):
        """Предупреждение перед запуском, если QGIS не доверяет сертификатам Минцифры."""
        qt_enum = self.core.qt_enum
        box = QMessageBox(self)
        box.setIcon(qt_enum(QMessageBox, "Icon", "Warning"))
        box.setWindowTitle("Нет сертификатов Минцифры")
        box.setText("QGIS не доверяет сертификатам Минцифры, поэтому НСПД, скорее всего, не ответит.")
        box.setInformativeText(
            "Установить их в QGIS сейчас? Если сертификаты уже установлены в систему, "
            "а QGIS их почему-то не видит, нажмите «Всё равно попробовать»: перед "
            "запуском будет пробный запрос к НСПД.")
        install_button = box.addButton("Установить...", qt_enum(QMessageBox, "ButtonRole", "AcceptRole"))
        anyway_button = box.addButton("Всё равно попробовать", qt_enum(QMessageBox, "ButtonRole", "ActionRole"))
        box.addButton(qt_enum(QMessageBox, "StandardButton", "Cancel"))
        self.core.exec_event_loop(box)
        clicked = box.clickedButton()
        box.deleteLater()
        if clicked is anyway_button:
            return True
        if clicked is install_button:
            self._open_certificates()
            return certificates.root_trusted() is not False
        return False

    # === Слои НСПД на карте ===

    def _add_layers_to_map(self, _checked=False, layer_defs=None, connection_checked=False):
        """Добавляет WMS-слои НСПД в проект. Возвращает текст ошибки связи или пустую строку."""
        layer_defs = layer_defs or self._selected_layers()
        if not layer_defs:
            self._set_status("Отметьте хотя бы один слой НСПД.", "warning")
            return ""
        missing = [layer_def for layer_def in layer_defs if self.core.find_wms_layer(layer_def) is None]
        if not missing:
            self._set_status("Отмеченные слои НСПД уже есть на карте.")
            return ""
        # Провайдер WMS сам ждёт ответа до таймаута сети QGIS (обычно 60 с) на
        # каждый слой. Короткая проверка связи заранее не даёт QGIS зависнуть
        # на минуты, если НСПД недоступен.
        error = None if connection_checked else self._run_connection_check()
        if error is not None:
            self._set_status("Слои НСПД на карту не добавлены, нет связи с НСПД: {0}".format(error), "error")
            return str(error)
        added, failed = [], []
        self._busy()
        try:
            for layer_def in missing:
                layer, created, error = self.core.add_wms_layer(layer_def)
                if created:
                    added.append(layer_def.title)
                elif layer is None:
                    failed.append("{0}: {1}".format(layer_def.title, error))
        finally:
            QApplication.restoreOverrideCursor()
        for title in added:
            self._log("NSPD: на карту добавлен слой «{0}»".format(title))
        for text in failed:
            self._log("NSPD [ошибка]: слой не добавлен на карту: " + text)
        if failed:
            self._set_status("Не все слои НСПД удалось добавить на карту, подробности в журнале.", "warning")
        else:
            self._set_status("На карту добавлено слоёв НСПД: {0}.".format(len(added)), "ok")
        return ""

    # === Настройки ===

    def _default_path(self):
        folder = QgsSettings().value(SETTINGS_GROUP + "/lastDir", "")
        if not folder or not os.path.isdir(folder):
            folder = QgsProject.instance().homePath() or os.path.expanduser("~")
        return os.path.join(folder, DEFAULT_FILE_NAME)

    def _load_settings(self):
        settings = QgsSettings()
        prefix = SETTINGS_GROUP + "/plugin/"
        checked = str(settings.value(prefix + "layers", self.core.LAYERS[0].code) or "").split(",")
        for layer_def, box in self.layer_boxes:
            box.setChecked(layer_def.code in checked)
        self.show_on_map_check.setChecked(str(settings.value(prefix + "showOnMap", "1")) != "0")
        self.path_edit.setText(str(settings.value(prefix + "path", "") or "") or self._default_path())
        defaults = self.core.RequestSettings()
        self.count_spin.setValue(_to_int(settings.value(prefix + "featureCount"), defaults.feature_count))
        self.timeout_spin.setValue(_to_int(settings.value(prefix + "timeoutS"), defaults.timeout_ms // 1000))

    def _save_settings(self):
        settings = QgsSettings()
        prefix = SETTINGS_GROUP + "/plugin/"
        settings.setValue(prefix + "layers", ",".join(layer_def.code for layer_def in self._selected_layers()))
        settings.setValue(prefix + "showOnMap", "1" if self.show_on_map_check.isChecked() else "0")
        settings.setValue(prefix + "path", self.path_edit.text().strip())
        settings.setValue(prefix + "featureCount", self.count_spin.value())
        settings.setValue(prefix + "timeoutS", self.timeout_spin.value())

    def _choose_path(self, _checked=False):
        current = self.path_edit.text().strip() or self._default_path()
        # Вопрос «заменить файл?» не нужен: существующий файл не заменяется,
        # объекты дописываются в слои внутри него.
        path, _ = QFileDialog.getSaveFileName(
            self, "Файл GeoPackage для объектов НСПД", current, "GeoPackage (*.gpkg)",
            options=self.core.qt_enum(QFileDialog, "Option", "DontConfirmOverwrite"))
        if path:
            self.path_edit.setText(path)
            QgsSettings().setValue(SETTINGS_GROUP + "/lastDir", os.path.dirname(path))

    def _output_path(self):
        """Проверенный путь к файлу или пустая строка (причина - в строке состояния)."""
        path = self.path_edit.text().strip()
        if not path:
            self._choose_path()
            path = self.path_edit.text().strip()
            if not path:
                self._set_status("Укажите файл GeoPackage для сохранения.", "warning")
                return ""
        if not path.lower().endswith(".gpkg"):
            path += ".gpkg"
            self.path_edit.setText(path)
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            self._set_status("Папки {0} нет. Выберите другой файл кнопкой «Обзор...».".format(folder), "warning")
            return ""
        return path

    # === Запуск и остановка ===

    def start(self, _checked=False):
        layer_defs = self._selected_layers()
        if not layer_defs:
            self._set_status("Отметьте хотя бы один слой НСПД.", "warning")
            return
        path = self._output_path()
        if not path:
            return
        if certificates.root_trusted() is False and not self._confirm_without_certificates():
            return
        self._save_settings()
        # Одна короткая проверка до включения инструмента: иначе при недоступном
        # НСПД каждый клик ждал бы таймаут и заканчивался ошибкой.
        error = self._run_connection_check()
        if error is not None:
            self._set_status("Выгрузка не начата, нет связи с НСПД: {0}".format(error), "error")
            return
        if self.show_on_map_check.isChecked():
            self._add_layers_to_map(layer_defs=layer_defs, connection_checked=True)

        session = self.core.NspdSession(path, layer_defs, self._request_settings(), self.core.Logger(self._log))
        canvas = self.iface.mapCanvas()
        tool = self.core.NspdIdentifyTool(canvas, session)
        tool.deactivated.connect(lambda tool=tool: self._on_tool_deactivated(tool))
        tool.clickProcessed.connect(self._on_click_processed)
        self.tool, self.session = tool, session
        canvas.setMapTool(tool)
        self._log("NSPD: выгрузка начата. Слои: {0}. Файл: {1}".format(
            ", ".join(layer_def.title for layer_def in layer_defs), path))
        self._set_status("Выгрузка идёт: кликайте по объектам на карте. Выгруженные объекты "
                         "подсвечиваются.", "ok")
        self._update_buttons()

    def stop(self, _checked=False):
        tool, self.tool = self.tool, None
        if tool is not None:
            if self.iface.mapCanvas().mapTool() is tool:
                self.iface.actionPan().trigger()
            self._log("NSPD: выгрузка остановлена.")
            self._set_status(self._session_summary("Выгрузка остановлена."))
        self._update_buttons()

    def _on_tool_deactivated(self, tool):
        # Пользователь выбрал другой инструмент на панели QGIS.
        if self.tool is tool:
            self.tool = None
            self._log("NSPD: выгрузка остановлена: выбран другой инструмент карты.")
            self._set_status(self._session_summary(
                "Выгрузка остановлена: выбран другой инструмент карты."))
            self._update_buttons()

    def _session_summary(self, text):
        if self.session is not None and self.session.total_added:
            text += " Всего за сеанс: {0}.".format(objects_text(self.session.total_added))
        return text

    def _on_click_processed(self, added):
        session = self.session
        if session is None:
            return
        if any(error.ssl_failed for error in session.last_errors):
            self._set_status("НСПД не ответил: QGIS не доверяет его сертификату. Нажмите "
                             "«Сертификаты...» вверху панели.", "error")
            self._refresh_certificates()
            return
        if session.last_errors and not session.last_counts:
            self._set_status("НСПД не ответил, подробности в журнале.", "error")
            return
        if added:
            text = "Последний клик: добавлено {0}".format(objects_text(added))
            if len(session.last_counts) > 1:
                text += " ({0})".format("; ".join(
                    "{0}: {1}".format(layer_def.title, count) for layer_def, count in session.last_counts))
            text += "."
        else:
            text = "Последний клик: новых объектов нет."
        if session.last_errors:
            text += " По части слоёв была ошибка, подробности в журнале."
        self._set_status(self._session_summary(text), "ok" if added else "info")

    def showEvent(self, event):
        # Сертификаты могли поставить или удалить, пока панель была скрыта.
        self._refresh_certificates()
        super().showEvent(event)

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
