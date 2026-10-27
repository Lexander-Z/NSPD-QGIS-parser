"""Выгрузка объектов НСПД по клику по карте. Скрипт для консоли Python QGIS.

Запуск: Модули - Консоль Python - Редактор - Открыть сценарий - этот файл -
Выполнить сценарий. Скрипт спросит слой НСПД и файл GeoPackage, после чего
каждый клик по карте дописывает объекты под курсором в выбранный файл.

Логика запроса и записи лежит в nspd_core.py рядом с этим файлом.
"""

import os
import sys

from qgis.core import QgsApplication, QgsProject, QgsSettings
from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog
from qgis.utils import iface

# === Настройки ===
# Сколько объектов просить у НСПД за один клик. Если придёт ровно столько,
# в консоли будет предупреждение: выдача могла быть обрезана.
FEATURE_COUNT = 20
# Сколько секунд ждать ответа НСПД, прежде чем отменить запрос.
TIMEOUT_S = 30

SETTINGS_GROUP = "NSPD-QGIS-parser"
CORE_MODULE = "nspd_core"
PLUGIN_PACKAGE = "nspd_parser_plugin"

# Ссылка на активный инструмент: без неё объект инструмента может быть
# удалён сборщиком мусора, и клики перестанут обрабатываться.
_active_tools = []


def _core_dir_candidates():
    candidates = []
    # Консоль QGIS при «Выполнить сценарий» не всегда задаёт __file__.
    if "__file__" in globals():
        candidates.append(os.path.dirname(os.path.abspath(__file__)))
    remembered = QgsSettings().value(SETTINGS_GROUP + "/coreDir", "")
    if remembered:
        candidates.append(remembered)
    # Если установлен плагин, его копия nspd_core тоже подходит.
    candidates.append(os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins", PLUGIN_PACKAGE))
    candidates.append(os.getcwd())
    return candidates


def _try_import_from(directory):
    if not directory or not os.path.isfile(os.path.join(directory, CORE_MODULE + ".py")):
        return None
    # В конец sys.path, чтобы файлы этой папки не заслоняли другие модули.
    if directory not in sys.path:
        sys.path.append(directory)
    try:
        return __import__(CORE_MODULE)
    except ImportError:
        return None


def import_core():
    """Находит и импортирует nspd_core. Если не нашёл - спрашивает папку и запоминает её."""
    try:
        return __import__(CORE_MODULE)
    except ImportError:
        pass
    for directory in _core_dir_candidates():
        core = _try_import_from(directory)
        if core is not None:
            return core
    directory = QFileDialog.getExistingDirectory(
        iface.mainWindow(), "Укажите папку, где лежит nspd_core.py (папка репозитория NSPD-QGIS-parser)")
    core = _try_import_from(directory)
    if core is not None:
        QgsSettings().setValue(SETTINGS_GROUP + "/coreDir", directory)
    return core


def choose_layer(core):
    titles = [layer_def.title for layer_def in core.LAYERS]
    title, accepted = QInputDialog.getItem(
        iface.mainWindow(), "НСПД: выбор слоя", "Какой слой НСПД опрашивать по клику:", titles, 0, False)
    if not accepted:
        return None
    for layer_def in core.LAYERS:
        if layer_def.title == title:
            return layer_def
    return None


def choose_output_path(layer_def):
    settings = QgsSettings()
    last_dir = settings.value(SETTINGS_GROUP + "/lastDir", "")
    if not last_dir or not os.path.isdir(last_dir):
        last_dir = QgsProject.instance().homePath() or os.path.expanduser("~")
    # Вопрос «заменить файл?» не нужен: существующий файл не заменяется,
    # объекты дописываются в слой внутри него.
    path, _ = QFileDialog.getSaveFileName(
        iface.mainWindow(), "Куда сохранять объекты НСПД",
        os.path.join(last_dir, layer_def.file_name), "GeoPackage (*.gpkg)",
        options=QFileDialog.DontConfirmOverwrite)
    if not path:
        return ""
    if not path.lower().endswith(".gpkg"):
        path += ".gpkg"
    settings.setValue(SETTINGS_GROUP + "/lastDir", os.path.dirname(path))
    return path


def main():
    core = import_core()
    if core is None:
        print("NSPD [ошибка]: не найден nspd_core.py, запуск отменён. "
              "Он должен лежать в одной папке с nspd_parser.py.")
        return
    layer_def = choose_layer(core)
    if layer_def is None:
        print("NSPD: слой не выбран, запуск отменён.")
        return
    output_path = choose_output_path(layer_def)
    if not output_path:
        print("NSPD: файл для сохранения не выбран, запуск отменён.")
        return

    canvas = iface.mapCanvas()
    settings = core.RequestSettings()
    settings.feature_count = FEATURE_COUNT
    settings.timeout_ms = int(TIMEOUT_S * 1000)
    session = core.NspdSession(output_path, [layer_def], settings)
    tool = core.NspdIdentifyTool(canvas, session)
    del _active_tools[:]
    _active_tools.append(tool)
    canvas.setMapTool(tool)
    print("NSPD: слой «{0}», файл {1}".format(layer_def.title, output_path))
    print("NSPD: кликните по карте, чтобы выгрузить объекты под курсором. "
          "Чтобы закончить, выберите любой другой инструмент на панели QGIS.")


main()
