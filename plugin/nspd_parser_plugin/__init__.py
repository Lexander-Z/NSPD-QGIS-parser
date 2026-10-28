"""Плагин QGIS для выгрузки объектов НСПД по клику по карте."""


def classFactory(iface):
    from .nspd_plugin import NspdParserPlugin
    return NspdParserPlugin(iface)
