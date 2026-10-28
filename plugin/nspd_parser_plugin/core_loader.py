"""Подключение общего модуля nspd_core.

В собранном архиве (plugin/build_zip.py) nspd_core.py лежит внутри пакета
плагина. При работе прямо из клона репозитория (папка плагина подключена
ссылкой в каталог плагинов QGIS) модуль берётся из корня репозитория:
второй копии логики в plugin/ нет.
"""

import importlib
import os
import sys

CORE_MODULE = "nspd_core"

# Модуль, загруженный при этой загрузке плагина. Сам core_loader при каждой
# загрузке плагина импортируется заново, поэтому значение не переживает
# переустановку или перезагрузку плагина.
_core = None


def load_core():
    global _core
    if _core is None:
        _core = _import_core()
    return _core


def _import_core():
    if os.path.isfile(os.path.join(os.path.dirname(__file__), CORE_MODULE + ".py")):
        # Обычный относительный импорт, а не importlib.import_module: QGIS
        # отслеживает только такие импорты и выгружает эти модули при
        # переустановке плагина.
        from . import nspd_core
        return nspd_core
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    if not os.path.isfile(os.path.join(repo_root, CORE_MODULE + ".py")):
        raise ImportError(
            "не найден nspd_core.py: установите плагин из архива, собранного "
            "plugin/build_zip.py, или подключите папку плагина из полного клона репозитория")
    if repo_root not in sys.path:
        sys.path.append(repo_root)
    # Из клона nspd_core грузится отдельным модулем, и QGIS его при
    # перезагрузке плагина не выгружает: перечитываем сами, чтобы правки
    # были видны без перезапуска QGIS.
    if CORE_MODULE in sys.modules:
        return importlib.reload(sys.modules[CORE_MODULE])
    return importlib.import_module(CORE_MODULE)
