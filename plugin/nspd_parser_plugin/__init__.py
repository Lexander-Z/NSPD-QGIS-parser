"""Плагин QGIS для выгрузки объектов НСПД по клику по карте и по области."""

import sys

# При переустановке из ZIP QGIS выгружает только модули, импорт которых он
# отследил. Подмодули, оставшиеся в памяти от прежней версии, удаляются
# здесь: этот код выполняется, только когда пакет загружается заново, и
# свои подмодули новая версия к этому моменту ещё не загрузила. Без этого
# обновление 0.1.0 -> 0.2.0 без перезапуска QGIS получало старый nspd_core.
for _name in [name for name in sys.modules if name.startswith(__name__ + ".")]:
    del sys.modules[_name]


def classFactory(iface):
    from .nspd_plugin import NspdParserPlugin
    return NspdParserPlugin(iface)
