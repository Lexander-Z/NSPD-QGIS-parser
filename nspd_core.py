"""Общая логика выгрузки объектов НСПД по клику по карте.

Модуль используют консольный скрипт nspd_parser.py и плагин из plugin/.
Запрос GetFeatureInfo, разбор GeoJSON и запись в GeoPackage существуют
в репозитории только здесь: правка логики делается в одном месте.
"""

import json
import os
from collections import namedtuple

from qgis.PyQt.QtCore import QEventLoop, QUrl, QVariant
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.gui import QgsMapTool
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsJsonUtils,
    QgsNetworkAccessManager,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

# === Таблица слоёв НСПД ===
# title - название для выбора в интерфейсе, code - код слоя в API НСПД,
# gpkg_layer - имя слоя внутри GeoPackage, file_name - имя файла по умолчанию.
LayerDef = namedtuple("LayerDef", "title code gpkg_layer file_name")

LAYERS = (
    LayerDef("Кадастр: земельные участки ЕГРН", "36048",
             "NSPD_Kadastr", "NSPD_Kadastr.gpkg"),
    LayerDef("ЗОУИТ объектов культурного наследия", "37577",
             "ZOUIT_OKN", "ZOUIT_OKN.gpkg"),
    LayerDef("ЗОУИТ объектов энергетики, связи, транспорта", "37578",
             "ZOUIT_Energetika_svaz_transport", "ZOUIT_Energetika_svaz_transport.gpkg"),
    LayerDef("ЗОУИТ охраняемых объектов и безопасности", "37579",
             "ZOUIT_ohranyaemyh_ob_i_bezopasnosti", "ZOUIT_ohranyaemyh_ob_i_bezopasnosti.gpkg"),
    LayerDef("ЗОУИТ природных территорий", "37580",
             "ZOUIT_prirodnyh_territorii", "ZOUIT_prirodnyh_territorii.gpkg"),
    LayerDef("Иные ЗОУИТ", "37581",
             "Inye_ZOUIT", "Inye_ZOUIT.gpkg"),
)

WMS_BASE_URL = "https://nspd.gov.ru/api/aeggis/v3"
REFERER = "https://nspd.gov.ru/map"
WMS_CRS = "EPSG:3857"

FEATURE_COUNT = 10
BBOX_HALF_SPAN_M = 100
IMAGE_SIZE = 800
MAX_FIELD_NAME_LENGTH = 63

GEOMETRY_TYPES = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon")


def wms_url(code):
    """Адрес WMS-сервиса слоя: код слоя входит в путь."""
    return "{0}/{1}/wms".format(WMS_BASE_URL, code)


class Logger:
    """Вывод сообщений с текстовым префиксом уровня."""

    def __init__(self, sink=print):
        self._sink = sink

    def info(self, text):
        self._sink("NSPD: " + text)

    def warn(self, text):
        self._sink("NSPD [внимание]: " + text)

    def error(self, text):
        self._sink("NSPD [ошибка]: " + text)


class NspdRequestError(Exception):
    """Запрос к НСПД не выполнен. Текст исключения готов для показа пользователю."""


# === Запрос GetFeatureInfo ===

def click_bbox(canvas, point):
    """Bbox запроса в EPSG:3857 вокруг точки клика (в CRS проекта)."""
    project_crs = canvas.mapSettings().destinationCrs()
    transform = QgsCoordinateTransform(
        project_crs, QgsCoordinateReferenceSystem(WMS_CRS), QgsProject.instance())
    centre = transform.transform(point)
    return (centre.x() - BBOX_HALF_SPAN_M, centre.y() - BBOX_HALF_SPAN_M,
            centre.x() + BBOX_HALF_SPAN_M, centre.y() + BBOX_HALF_SPAN_M)


def build_request_url(code, bbox):
    centre_pixel = IMAGE_SIZE // 2
    return (
        "{url}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo"
        "&LAYERS={code}&QUERY_LAYERS={code}&CRS={crs}"
        "&BBOX={bbox}&WIDTH={size}&HEIGHT={size}&I={pixel}&J={pixel}"
        "&INFO_FORMAT=application/json&STYLES=&FORMAT=image/png"
        "&FEATURE_COUNT={count}"
    ).format(
        url=wms_url(code), code=code, crs=WMS_CRS,
        bbox=",".join(repr(value) for value in bbox),
        size=IMAGE_SIZE, pixel=centre_pixel, count=FEATURE_COUNT,
    )


def request_feature_info(code, bbox):
    """Выполняет GetFeatureInfo и возвращает текст ответа."""
    request = QNetworkRequest(QUrl(build_request_url(code, bbox)))
    request.setRawHeader(b"Referer", REFERER.encode("utf-8"))
    reply = QgsNetworkAccessManager.instance().get(request)
    loop = QEventLoop()
    reply.finished.connect(loop.quit)
    loop.exec_()
    try:
        if reply.error():
            raise NspdRequestError("запрос к НСПД не выполнен: {0}".format(reply.errorString()))
        return bytes(reply.readAll()).decode("utf-8", "replace")
    finally:
        reply.deleteLater()


# === Разбор ответа ===

def flatten_properties(properties, parent_key="", separator="_"):
    """Разворачивает вложенные словари атрибутов в плоский словарь."""
    flat = {}
    if not isinstance(properties, dict):
        return flat
    for key, value in properties.items():
        full_key = "{0}{1}{2}".format(parent_key, separator, key) if parent_key else str(key)
        if isinstance(value, dict):
            flat.update(flatten_properties(value, full_key, separator))
        else:
            flat[full_key] = value
    return flat


def normalise_geometry_type(geojson_type):
    """Тип слоя для типа геометрии GeoJSON: всегда Multi-вариант, None для прочих."""
    if geojson_type not in GEOMETRY_TYPES:
        return None
    return geojson_type if geojson_type.startswith("Multi") else "Multi" + geojson_type


def detect_geometry_type(features):
    """Тип слоя по первому объекту с понятной геометрией."""
    for feature in features:
        geometry = feature.get("geometry") or {}
        layer_type = normalise_geometry_type(geometry.get("type"))
        if layer_type:
            return layer_type
    return "MultiPolygon"


def geometry_from_geojson(geometry_json):
    """QgsGeometry из словаря GeoJSON-геометрии или None, если разобрать не удалось.

    Основной путь - QgsJsonUtils. Если он не справился, геометрия собирается
    через WKT без сторонних библиотек.
    """
    geometry = _geometry_via_qgis(geometry_json)
    if geometry is not None:
        return geometry
    wkt = geojson_to_wkt(geometry_json)
    if not wkt:
        return None
    geometry = QgsGeometry.fromWkt(wkt)
    return None if geometry.isNull() else geometry


def _geometry_via_qgis(geometry_json):
    # Сигнатура stringToFeatureList в разных версиях QGIS 3.x отличается
    # необязательными аргументами, поэтому перебираются известные варианты.
    text = json.dumps({"type": "Feature", "properties": {}, "geometry": geometry_json})
    for args in ((text,), (text, QgsFields()), (text, QgsFields(), None)):
        try:
            features = QgsJsonUtils.stringToFeatureList(*args)
        except TypeError:
            continue
        if features and not features[0].geometry().isNull():
            return QgsGeometry(features[0].geometry())
        return None
    return None


def geojson_to_wkt(geometry_json):
    """WKT для геометрии GeoJSON; пустая строка, если тип не поддержан."""
    if not isinstance(geometry_json, dict):
        return ""
    geometry_type = geometry_json.get("type")
    if geometry_type == "GeometryCollection":
        parts = [geojson_to_wkt(item) for item in geometry_json.get("geometries") or []]
        parts = [part for part in parts if part]
        return "GEOMETRYCOLLECTION ({0})".format(", ".join(parts)) if parts else ""
    coordinates = geometry_json.get("coordinates")
    if geometry_type not in GEOMETRY_TYPES or not coordinates:
        return ""
    if geometry_type == "Point":
        body = _wkt_position(coordinates)
    elif geometry_type in ("MultiPoint", "LineString"):
        body = _wkt_positions(coordinates)
    elif geometry_type in ("MultiLineString", "Polygon"):
        body = _wkt_rings(coordinates)
    else:
        body = ", ".join("({0})".format(_wkt_rings(polygon)) for polygon in coordinates)
    return "{0} ({1})".format(geometry_type.upper(), body)


def _wkt_position(position):
    return " ".join(repr(float(value)) for value in position[:2])


def _wkt_positions(positions):
    return ", ".join(_wkt_position(position) for position in positions)


def _wkt_rings(rings):
    return ", ".join("({0})".format(_wkt_positions(ring)) for ring in rings)


def field_names_for(keys):
    """Имена полей GPKG для ключей атрибутов: исходный ключ -> имя поля."""
    return {key: key[:MAX_FIELD_NAME_LENGTH] for key in keys}


# === Запись в GeoPackage ===

class LayerWriter:
    """Целевой слой GeoPackage для одного слоя НСПД."""

    def __init__(self, output_path, layer_def, logger):
        self.output_path = output_path
        self.layer_def = layer_def
        self.logger = logger
        self.layer = None
        self.field_names = {}

    def write(self, features):
        """Добавляет объекты ответа в слой и возвращает число добавленных."""
        rows = [flatten_properties(feature.get("properties")) for feature in features]
        keys = []
        for row in rows:
            keys.extend(key for key in row if key not in keys)
        if not self._ensure_layer(keys, detect_geometry_type(features)):
            return 0
        return self._append(features, rows)

    def _ensure_layer(self, keys, geometry_type):
        if self.layer is not None:
            return True
        name = self.layer_def.gpkg_layer
        self.field_names = field_names_for(keys)
        memory_layer = QgsVectorLayer("{0}?crs={1}".format(geometry_type, WMS_CRS), name, "memory")
        if not memory_layer.isValid():
            self.logger.error("не удалось создать временный слой «{0}»".format(name))
            return False
        memory_layer.dataProvider().addAttributes(
            [QgsField(field_name, QVariant.String) for field_name in self.field_names.values()])
        memory_layer.updateFields()

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = name
        options.fileEncoding = "UTF-8"
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        result = QgsVectorFileWriter.writeAsVectorFormatV2(
            memory_layer, self.output_path, QgsProject.instance().transformContext(), options)
        if result[0] != QgsVectorFileWriter.NoError:
            self.logger.error("не удалось записать слой в {0}: {1}".format(self.output_path, result[1]))
            return False

        layer = QgsVectorLayer("{0}|layername={1}".format(self.output_path, name), name, "ogr")
        if not layer.isValid():
            self.logger.error("слой «{0}» записан в {1}, но не открылся".format(name, self.output_path))
            return False
        QgsProject.instance().addMapLayer(layer)
        self.layer = layer
        self.logger.info("создан слой «{0}» в {1}".format(name, self.output_path))
        return True

    def _append(self, features, rows):
        layer = self.layer
        if not layer.isEditable():
            layer.startEditing()
        fields = layer.fields()
        added = 0
        for index, (feature, row) in enumerate(zip(features, rows)):
            geometry_json = feature.get("geometry")
            if not geometry_json:
                self.logger.warn("объект {0} пришёл без геометрии, пропущен".format(index))
                continue
            geometry = geometry_from_geojson(geometry_json)
            if geometry is None:
                self.logger.warn("геометрию объекта {0} не удалось разобрать, пропущен".format(index))
                continue
            new_feature = QgsFeature(fields)
            new_feature.setGeometry(geometry)
            for key, field_name in self.field_names.items():
                value = row.get(key)
                new_feature.setAttribute(field_name, "" if value is None else str(value))
            if layer.addFeature(new_feature):
                added += 1
            else:
                self.logger.error("объект {0} не добавлен в слой".format(index))
        if not layer.commitChanges():
            self.logger.error("не удалось сохранить изменения слоя «{0}»".format(self.layer_def.gpkg_layer))
        layer.updateExtents()
        layer.triggerRepaint()
        return added


# === Сеанс выгрузки и инструмент карты ===

class NspdSession:
    """Выгрузка выбранных слоёв НСПД в один файл GeoPackage."""

    def __init__(self, output_path, layer_defs, logger=None):
        self.output_path = output_path
        self.logger = logger or Logger()
        self.writers = [LayerWriter(output_path, layer_def, self.logger) for layer_def in layer_defs]

    def handle_click(self, canvas, point):
        """Опрашивает все слои сеанса в точке клика, возвращает число добавленных объектов."""
        total = 0
        for writer in self.writers:
            total += self._handle_layer(canvas, point, writer)
        if len(self.writers) > 1:
            self.logger.info("всего добавлено объектов: {0}".format(total))
        return total

    def _handle_layer(self, canvas, point, writer):
        title = writer.layer_def.title
        try:
            response = request_feature_info(writer.layer_def.code, click_bbox(canvas, point))
        except NspdRequestError as error:
            self.logger.error("«{0}»: {1}".format(title, error))
            return 0
        try:
            data = json.loads(response)
        except ValueError as error:
            self.logger.error("«{0}»: ответ НСПД не разобран как JSON: {1}".format(title, error))
            return 0
        features = (data.get("features") if isinstance(data, dict) else None) or []
        self.logger.info("«{0}»: объектов в ответе {1}".format(title, len(features)))
        if not features:
            return 0
        added = writer.write(features)
        self.logger.info("«{0}»: добавлено объектов {1}".format(title, added))
        return added


class NspdIdentifyTool(QgsMapTool):
    """Инструмент карты: клик запрашивает объекты НСПД и дописывает их в GeoPackage."""

    def __init__(self, canvas, session):
        super().__init__(canvas)
        self.session = session

    def canvasReleaseEvent(self, event):
        point = self.toMapCoordinates(event.pos())
        self.session.handle_click(self.canvas(), point)
        self.canvas().refreshAllLayers()
