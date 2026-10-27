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


def load_core():
    bundled = "{0}.{1}".format(__package__, CORE_MODULE)
    try:
        return importlib.import_module(bundled)
    except ModuleNotFoundError as error:
        # Ошибку импорта внутри самого nspd_core не маскируем.
        if error.name != bundled:
            raise
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    if not os.path.isfile(os.path.join(repo_root, CORE_MODULE + ".py")):
        raise ImportError(
            "не найден nspd_core.py: установите плагин из архива, собранного "
            "plugin/build_zip.py, или подключите папку плагина из полного клона репозитория")
    if repo_root not in sys.path:
        sys.path.append(repo_root)
    return importlib.import_module(CORE_MODULE)
