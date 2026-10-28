"""Общая логика выгрузки объектов НСПД по клику по карте и по области.

Модуль используют консольный скрипт nspd_parser.py и плагин из plugin/.
Запрос GetFeatureInfo, разбор GeoJSON и запись в GeoPackage существуют
в репозитории только здесь: правка логики делается в одном месте.

Код рассчитан на QGIS 3.x (Qt5) и QGIS 4 (Qt6): Qt берётся только через
qgis.PyQt, а места, где API отличается, собраны в блоке «Совместимость».
"""

import copy
import json
import math
import os
from collections import namedtuple
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

from qgis.PyQt.QtCore import QEventLoop, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QCursor
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import QApplication
from qgis.gui import QgsMapTool
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsJsonUtils,
    QgsNetworkAccessManager,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)

# === Совместимость QGIS 3 (Qt5) и QGIS 4 (Qt6) ===

def qt_enum(owner, enum_name, member_name):
    """Член перечисления Qt/QGIS.

    В Qt6 (QGIS 4) члены доступны только через класс перечисления
    (QNetworkReply.NetworkError.NoError), в Qt5 (QGIS 3) - и напрямую
    (QNetworkReply.NoError). Сначала берётся полная форма.
    """
    enum = getattr(owner, enum_name, None)
    if enum is not None and hasattr(enum, member_name):
        return getattr(enum, member_name)
    return getattr(owner, member_name)


def exec_event_loop(loop):
    """В Qt6 есть только exec(); имя с подчёркиванием нужно лишь очень старым сборкам Qt5."""
    run = getattr(loop, "exec", None) or getattr(loop, "exec_")
    return run()


_string_field_type = []


def string_field_type():
    """Тип строкового поля для QgsField.

    QGIS 4 (Qt6) принимает только QMetaType.Type.QString, QGIS 3.38+ - оба
    варианта, более старые QGIS 3 - только QVariant.String. Подходящий
    вариант определяется один раз пробным созданием поля.
    """
    if _string_field_type:
        return _string_field_type[0]
    candidates = []
    try:
        from qgis.PyQt.QtCore import QMetaType
        candidates.append(QMetaType.Type.QString)
    except (ImportError, AttributeError):
        pass
    try:
        from qgis.PyQt.QtCore import QVariant
        candidates.append(QVariant.String)
    except (ImportError, AttributeError):
        pass
    for candidate in candidates:
        try:
            QgsField("nspd_probe", candidate)
        except TypeError:
            continue
        _string_field_type.append(candidate)
        return candidate
    raise RuntimeError("не удалось подобрать тип строкового поля для QgsField")


def write_vector_layer(layer, path, options):
    """Запись слоя в файл: (код ошибки, текст ошибки).

    writeAsVectorFormatV3 появился в QGIS 3.20 и заменил устаревший V2,
    на который в QGIS 4 рассчитывать нельзя.
    """
    context = QgsProject.instance().transformContext()
    write = getattr(QgsVectorFileWriter, "writeAsVectorFormatV3", None)
    if write is None:
        write = QgsVectorFileWriter.writeAsVectorFormatV2
    result = write(layer, path, context, options)
    return result[0], (result[1] if len(result) > 1 else "")


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
CERTIFICATES_URL = "https://www.gosuslugi.ru/crt"

GEOMETRY_TYPES = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon")


class RequestSettings:
    """Настройки запроса и записи. Значения по умолчанию можно менять у экземпляра."""

    def __init__(self):
        # Сколько объектов просить у сервера за один клик. Если пришло ровно
        # столько же, выдача могла быть обрезана - об этом будет предупреждение.
        self.feature_count = 20
        # Сколько ждать ответа НСПД, мс. По истечении запрос отменяется.
        self.timeout_ms = 30000
        # Размер картинки запроса в пикселях; клик приходится на её центр.
        self.image_size = 800
        # Пределы полуразмера bbox в метрах EPSG:3857 при пересчёте от масштаба.
        self.min_half_span_m = 5.0
        self.max_half_span_m = 20000.0
        # Метров на пиксель, если разрешение карты определить не удалось.
        # 0,25 м/пиксель при 800 пикселях дают прежние +-100 м.
        self.fallback_units_per_pixel = 0.25
        # Предел длины имени поля (как в PostgreSQL), чтобы GPKG без
        # сюрпризов загружался в PostGIS.
        self.max_field_name_length = 63
        # Выгрузка области: пауза между запросами, мс, чтобы не нагружать НСПД,
        # и предел числа запросов на один слой.
        self.area_pause_ms = 100
        self.max_area_requests = 5000


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
    """Запрос к НСПД не выполнен. Текст исключения готов для показа пользователю.

    connection_failed - ошибка связи (таймаут, TLS, сеть, прокси), а не ответа
    по конкретному слою: остальные слои в этом клике её тоже получат.
    ssl_failed - ошибка TLS, чаще всего из-за отсутствия сертификатов Минцифры.
    """

    def __init__(self, message, connection_failed=False, ssl_failed=False):
        super().__init__(message)
        self.connection_failed = connection_failed or ssl_failed
        self.ssl_failed = ssl_failed


# === Запрос GetFeatureInfo ===

def half_span_for(units_per_pixel, settings):
    """Полуразмер bbox в метрах: картинка запроса повторяет разрешение карты на экране."""
    if not units_per_pixel or units_per_pixel <= 0:
        units_per_pixel = settings.fallback_units_per_pixel
    half_span = units_per_pixel * settings.image_size / 2.0
    return min(max(half_span, settings.min_half_span_m), settings.max_half_span_m)


def units_per_pixel_3857(canvas, transform, point):
    """Метров EPSG:3857 на пиксель экрана в точке клика; None, если не посчитать.

    mapUnitsPerPixel дан в единицах CRS проекта (градусы, метры МСК и т. п.),
    поэтому сдвиг на один пиксель пересчитывается в EPSG:3857 в самой точке клика.
    """
    map_units_per_pixel = canvas.mapUnitsPerPixel()
    if not map_units_per_pixel or map_units_per_pixel <= 0:
        return None
    try:
        here = transform.transform(point)
        shifted = transform.transform(QgsPointXY(point.x() + map_units_per_pixel, point.y()))
    except QgsCsException:
        return None
    return abs(shifted.x() - here.x()) or None


def click_bbox(canvas, point, settings):
    """Bbox запроса в EPSG:3857 вокруг точки клика (point - в CRS проекта)."""
    project_crs = canvas.mapSettings().destinationCrs()
    transform = QgsCoordinateTransform(
        project_crs, QgsCoordinateReferenceSystem(WMS_CRS), QgsProject.instance())
    centre = transform.transform(point)
    half_span = half_span_for(units_per_pixel_3857(canvas, transform, point), settings)
    return (centre.x() - half_span, centre.y() - half_span,
            centre.x() + half_span, centre.y() + half_span)


def build_request_url(code, bbox, settings, image_size=None):
    """Адрес GetFeatureInfo. image_size=1 - запрос «окном»: один пиксель на весь bbox."""
    size = image_size or settings.image_size
    centre_pixel = size // 2
    return (
        "{url}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo"
        "&LAYERS={code}&QUERY_LAYERS={code}&CRS={crs}"
        "&BBOX={bbox}&WIDTH={size}&HEIGHT={size}&I={pixel}&J={pixel}"
        "&INFO_FORMAT=application/json&STYLES=&FORMAT=image/png"
        "&FEATURE_COUNT={count}"
    ).format(
        url=wms_url(code), code=code, crs=WMS_CRS,
        bbox=",".join(repr(value) for value in bbox),
        size=size, pixel=centre_pixel, count=settings.feature_count,
    )


SSL_TEXT_MARKERS = ("ssl", "tls", "certificate", "сертификат")


def looks_like_ssl_error(error_text):
    """Похожа ли строка ошибки Qt на проблему с TLS-сертификатом."""
    text = (error_text or "").lower()
    return any(marker in text for marker in SSL_TEXT_MARKERS)


def certificate_hint():
    return ("Похоже, QGIS не доверяет сертификату nspd.gov.ru: нужны корневые "
            "сертификаты Минцифры. Установите их с {0} и перезагрузите компьютер. "
            "Если стоит плагин NSPD Parser, их можно установить только в QGIS: "
            "кнопка «Сертификаты...» в его панели.").format(CERTIFICATES_URL)


# НСПД отвечает 403 на User-Agent, который ставит QGIS, а по HTTP/2 Qt получает
# от него пустой ответ, и WMS-слой выходит «невалидным». Это выяснили авторы
# плагина rosreestr-search-qgis-plugin; поэтому к запросам на nspd.gov.ru
# подставляются браузерный User-Agent и HTTP/1.1.
NSPD_HOST = "nspd.gov.ru"
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"


def is_nspd_host(host):
    host = (host or "").lower()
    return host == NSPD_HOST or host.endswith("." + NSPD_HOST)


def prepare_nspd_request(request):
    """Браузерный User-Agent и HTTP/1.1 для запроса к НСПД."""
    request.setRawHeader(b"User-Agent", BROWSER_USER_AGENT.encode("ascii"))
    try:
        request.setAttribute(qt_enum(QNetworkRequest, "Attribute", "Http2AllowedAttribute"), False)
    except AttributeError:
        pass


def install_nspd_request_hook():
    """Обработчик всех запросов QGIS к НСПД, в том числе WMS-слоёв на карте.

    Возвращает идентификатор для remove_nspd_request_hook или None, если
    QGIS такие обработчики не поддерживает (появились в QGIS 3.22).
    """
    register = getattr(QgsNetworkAccessManager, "setRequestPreprocessor", None)
    if register is None:
        return None

    def preprocess(request):
        url = request.url()
        if url.scheme().lower() == "https" and is_nspd_host(url.host()):
            prepare_nspd_request(request)
        # Обёртка QGIS ожидает от обработчика строку.
        return ""

    return register(preprocess)


def remove_nspd_request_hook(hook_id):
    if not hook_id:
        return
    try:
        QgsNetworkAccessManager.removeRequestPreprocessor(hook_id)
    except (AttributeError, KeyError):
        pass


def pause(milliseconds):
    """Пауза без остановки интерфейса QGIS."""
    if milliseconds <= 0:
        return
    loop = QEventLoop()
    QTimer.singleShot(int(milliseconds), loop.quit)
    exec_event_loop(loop)


def http_get(url, timeout_ms, headers=None, ssl_hint=True):
    """GET через QgsNetworkAccessManager: с прокси и сертификатами из настроек QGIS.

    Интерфейс QGIS на время запроса блокируется, поэтому ожидание ограничено
    timeout_ms: по истечении запрос отменяется с понятной ошибкой. Возвращает
    тело ответа (bytes), при ошибке поднимает NspdRequestError. ssl_hint
    добавляет к ошибке TLS подсказку про сертификаты Минцифры.
    """
    host = urlsplit(url).hostname or url
    request = QNetworkRequest(QUrl(url))
    if is_nspd_host(host):
        prepare_nspd_request(request)
    for name, value in (headers or {}).items():
        request.setRawHeader(name, value)
    reply = QgsNetworkAccessManager.instance().get(request)
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    reply.finished.connect(loop.quit)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    if not reply.isFinished():
        exec_event_loop(loop)
    timer.stop()
    try:
        if not reply.isFinished():
            reply.abort()
            raise NspdRequestError(
                "нет ответа от {0} за {1:g} с, запрос отменён. Попробуйте ещё раз "
                "или увеличьте таймаут.".format(host, timeout_ms / 1000.0),
                connection_failed=True)
        # В Qt6 член перечисления всегда истинен, поэтому только явное сравнение.
        error = reply.error()
        if error != qt_enum(QNetworkReply, "NetworkError", "NoError"):
            message = "запрос к {0} не выполнен: {1}".format(host, reply.errorString())
            ssl_failed = (error == qt_enum(QNetworkReply, "NetworkError", "SslHandshakeFailedError")
                          or looks_like_ssl_error(reply.errorString()))
            if ssl_failed and ssl_hint:
                message += "\n" + certificate_hint()
            # Коды QNetworkReply до 200 - ошибки сети и прокси, от 200 - ответа сервера.
            code = int(getattr(error, "value", error))
            raise NspdRequestError(message, connection_failed=code < 200, ssl_failed=ssl_failed)
        return bytes(reply.readAll())
    finally:
        reply.deleteLater()


def request_feature_info(code, bbox, settings, image_size=None):
    """Выполняет GetFeatureInfo и возвращает текст ответа."""
    url = build_request_url(code, bbox, settings, image_size)
    body = http_get(url, settings.timeout_ms, {b"Referer": REFERER.encode("utf-8")})
    return body.decode("utf-8", "replace")


# Точка для проверки связи: центр Москвы в EPSG:3857. Что под ней лежит,
# неважно: проверяется только то, что НСПД отвечает на GetFeatureInfo JSON-ом.
CHECK_POINT_3857 = (4187541.0, 7509137.0)


def check_connection(settings, timeout_ms=15000):
    """Пробный GetFeatureInfo к первому слою таблицы.

    Возвращает None, если НСПД ответил в ожидаемом формате, иначе
    NspdRequestError с понятным текстом.
    """
    probe = copy.copy(settings)
    probe.feature_count = 1
    probe.timeout_ms = min(settings.timeout_ms, timeout_ms)
    x, y = CHECK_POINT_3857
    try:
        response = request_feature_info(LAYERS[0].code, (x - 10.0, y - 10.0, x + 10.0, y + 10.0), probe)
    except NspdRequestError as error:
        return error
    try:
        data = json.loads(response)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return NspdRequestError("НСПД ответил, но не данными в формате JSON. Начало ответа: "
                                + response[:200].replace("\n", " "))
    return None


# === WMS-слои НСПД на карте ===

WMS_GROUP_NAME = "НСПД"


def map_referer(code):
    """Referer, как у карты НСПД с включённым слоем, в виде для источника WMS.

    Карта НСПД передаёт включённые слои параметром active_layers: символом,
    код которого равен коду слоя (37577 - символ U+92C9). Знак «=» записан как
    %3D, чтобы QGIS не принял его за разделитель параметров источника.
    """
    return "{0}?active_layers%3D{1}".format(REFERER, quote(chr(int(code)), safe=""))


def wms_layer_uri(layer_def, layer_names=None):
    """Источник WMS-слоя НСПД для провайдера QGIS «wms»."""
    referer = map_referer(layer_def.code)
    parts = [
        ("contextualWMSLegend", "0"),
        ("crs", WMS_CRS),
        ("dpiMode", "7"),
        ("format", "image/png"),
        # Ключ referer читают старые версии QGIS, http-header:referer - QGIS 3.24 и новее.
        ("http-header:referer", referer),
        ("referer", referer),
    ]
    for name in layer_names or [layer_def.code]:
        parts.extend((("layers", name), ("styles", "")))
    # Адрес GetMap из описания сервиса НСПД не используется, только базовый.
    parts.extend((("IgnoreGetMapUrl", "1"), ("url", wms_url(layer_def.code))))
    return "&".join("{0}={1}".format(key, value) for key, value in parts)


def parse_wms_capabilities(body):
    """(имена слоёв, системы координат) из ответа GetCapabilities.

    ValueError, если ответ - не описание WMS-сервиса.
    """
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise ValueError("ответ не разобран как XML ({0}). Начало ответа: {1}".format(
            error, bytes(body[:200]).decode("utf-8", "replace")))

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    if local(root.tag) not in ("WMS_Capabilities", "WMT_MS_Capabilities"):
        raise ValueError("в ответе {0} вместо описания WMS-сервиса".format(local(root.tag)))
    names, systems = [], set()
    for element in root.iter():
        tag = local(element.tag)
        if tag == "Layer":
            for child in element:
                if local(child.tag) == "Name" and (child.text or "").strip():
                    names.append(child.text.strip())
        elif tag in ("CRS", "SRS") and element.text:
            systems.update(element.text.split())
    return names, sorted(systems)


def find_wms_layer(layer_def):
    """WMS-слой этого слоя НСПД, уже добавленный в проект (в том числе другим плагином)."""
    marker = "nspd.gov.ru/api/aeggis/v3/{0}/wms".format(layer_def.code)
    for layer in QgsProject.instance().mapLayers().values():
        if layer.providerType() == "wms" and marker in unquote(layer.source()):
            return layer
    return None


def add_wms_layer(layer_def):
    """Добавляет WMS-слой НСПД в группу «НСПД» проекта, если его там ещё нет.

    Возвращает (слой или None, добавлен ли он сейчас, текст ошибки). Провайдер
    WMS при создании слоя запрашивает описание сервиса (GetCapabilities),
    поэтому вызов блокирует интерфейс на время этого запроса.
    """
    existing = find_wms_layer(layer_def)
    if existing is not None:
        return existing, False, ""
    layer = QgsRasterLayer(wms_layer_uri(layer_def), "НСПД: " + layer_def.title, "wms")
    if layer.isValid():
        return _place_wms_layer(layer), True, ""
    first_error = _layer_error(layer)

    # QGIS не открыл слой. Читаем описание сервиса сами: в журнал попадёт,
    # что именно ответил НСПД, а если слой в описании назван иначе, чем код,
    # пробуем ещё раз с настоящими именами.
    try:
        body = http_get(wms_url(layer_def.code) + "?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.3.0",
                        WMS_CHECK_TIMEOUT_MS, {b"Referer": REFERER.encode("utf-8")})
        names, systems = parse_wms_capabilities(body)
    except NspdRequestError as error:
        return None, False, "{0}. Описание сервиса (GetCapabilities) не получено: {1}".format(first_error, error)
    except ValueError as error:
        return None, False, "{0}. НСПД ответил на GetCapabilities не описанием сервиса: {1}".format(
            first_error, error)
    if names and layer_def.code not in names:
        layer = QgsRasterLayer(wms_layer_uri(layer_def, names), "НСПД: " + layer_def.title, "wms")
        if layer.isValid():
            return _place_wms_layer(layer), True, ""
    return None, False, (
        "{0}. Слои в описании сервиса: {1}; системы координат: {2}. Подробности: журнал "
        "сообщений QGIS, вкладка WMS").format(
            first_error, ", ".join(names[:20]) or "нет", ", ".join(systems[:10]) or "нет")


WMS_CHECK_TIMEOUT_MS = 15000


def _layer_error(layer):
    texts = []
    sources = [layer]
    provider = layer.dataProvider() if hasattr(layer, "dataProvider") else None
    if provider is not None:
        sources.append(provider)
    for source in sources:
        try:
            summary = source.error().summary()
        except AttributeError:
            continue
        if summary and summary not in texts:
            texts.append(summary)
    return "; ".join(texts) or "провайдер WMS не смог открыть источник"


def _place_wms_layer(layer):
    project = QgsProject.instance()
    project.addMapLayer(layer, False)
    _wms_group(project).addLayer(layer)
    return layer


def _wms_group(project):
    """Группа «НСПД» в дереве слоёв: над верхним растровым слоем, под векторными.

    Растры НСПД не должны закрывать выгруженные объекты, а подложка
    (OpenStreetMap и т. п.) не должна закрывать растры НСПД.
    """
    root = project.layerTreeRoot()
    group = root.findGroup(WMS_GROUP_NAME)
    if group is not None:
        return group
    children = root.children()
    index = len(children)
    for position, node in enumerate(children):
        layer = node.layer() if hasattr(node, "layer") else None
        if isinstance(layer, QgsRasterLayer):
            index = position
            break
    return root.insertGroup(index, WMS_GROUP_NAME)


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


def attribute_text(value):
    """Строковое значение атрибута: списки сохраняются как JSON, None - как пустая строка."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


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


def _feature_in_area(feature, area):
    geometry = geometry_from_geojson(feature.get("geometry"))
    return geometry is not None and area.intersects(geometry)


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


def safe_field_names(keys, max_length, used=None):
    """Имена полей для ключей атрибутов: исходный ключ -> имя не длиннее max_length.

    Если после обрезки имя совпадает с уже занятым (в том числе из used),
    к нему добавляется суффикс _2, _3 и т. д., поэтому два разных атрибута
    никогда не попадают в одно поле. Сравнение без учёта регистра: в GPKG
    (SQLite) имена столбцов Name и name считаются одинаковыми.
    """
    taken = {name.lower() for name in used or ()}
    names = {}
    for key in keys:
        name = key[:max_length]
        index = 2
        while name.lower() in taken:
            suffix = "_{0}".format(index)
            name = key[:max_length - len(suffix)] + suffix
            index += 1
        taken.add(name.lower())
        names[key] = name
    return names


def _find_project_layer(path, layer_name):
    """Слой GPKG, уже загруженный в проект: второй экземпляр того же слоя не открывается."""
    target = os.path.normcase(os.path.abspath(path))
    for layer in QgsProject.instance().mapLayers().values():
        if layer.providerType() != "ogr":
            continue
        parts = layer.source().split("|")
        if (os.path.normcase(os.path.abspath(parts[0])) == target
                and "layername=" + layer_name in parts[1:]):
            return layer
    return None


def _layer_geometry_name(layer):
    """Тип геометрии слоя в терминах GeoJSON (MultiPolygon и т. п.) или None.

    None отключает проверку типа геометрии при записи, а не запрещает её.
    """
    try:
        return normalise_geometry_type(QgsWkbTypes.displayString(layer.wkbType()))
    except (AttributeError, TypeError):
        return None


# === Запись в GeoPackage ===

class LayerWriter:
    """Целевой слой GeoPackage для одного слоя НСПД."""

    def __init__(self, output_path, layer_def, settings, logger):
        self.output_path = output_path
        self.layer_def = layer_def
        self.settings = settings
        self.logger = logger
        self.layer = None
        self.layer_type = None
        self.field_names = {}
        # Геометрии объектов, добавленных последним вызовом write (для подсветки).
        self.last_added = []

    def write(self, features):
        """Добавляет объекты ответа в слой и возвращает число добавленных."""
        self.last_added = []
        rows = [flatten_properties(feature.get("properties")) for feature in features]
        keys = []
        for row in rows:
            keys.extend(key for key in row if key not in keys)
        if not self._ensure_layer(keys, detect_geometry_type(features)):
            return 0
        self._add_missing_fields(keys)
        return self._append(features, rows)

    def _uri(self):
        return "{0}|layername={1}".format(self.output_path, self.layer_def.gpkg_layer)

    def _ensure_layer(self, keys, layer_type):
        if self.layer is not None:
            return True
        layer = self._open_existing_layer(keys)
        if layer is None:
            layer = self._create_layer(keys, layer_type)
        if layer is None:
            return False
        self.layer = layer
        return True

    def _open_existing_layer(self, keys):
        """Слой из прошлой выгрузки в том же файле: в него объекты дописываются."""
        if not os.path.exists(self.output_path):
            return None
        layer = _find_project_layer(self.output_path, self.layer_def.gpkg_layer)
        if layer is None:
            layer = QgsVectorLayer(self._uri(), self.layer_def.gpkg_layer, "ogr")
            if not layer.isValid():
                return None
            QgsProject.instance().addMapLayer(layer)
        existing = set(layer.fields().names())
        limit = self.settings.max_field_name_length
        self.field_names = {key: key[:limit] for key in keys if key[:limit] in existing}
        self.layer_type = _layer_geometry_name(layer)
        self.logger.info("слой «{0}» уже есть в {1}, объекты будут дописаны в него".format(
            self.layer_def.gpkg_layer, self.output_path))
        return layer

    def _create_layer(self, keys, layer_type):
        name = self.layer_def.gpkg_layer
        field_names = safe_field_names(keys, self.settings.max_field_name_length)
        memory_layer = QgsVectorLayer("{0}?crs={1}".format(layer_type, WMS_CRS), name, "memory")
        if not memory_layer.isValid():
            self.logger.error("не удалось создать временный слой «{0}»".format(name))
            return None
        memory_layer.dataProvider().addAttributes(
            [QgsField(field_name, string_field_type()) for field_name in field_names.values()])
        memory_layer.updateFields()

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = name
        options.fileEncoding = "UTF-8"
        # В существующий файл добавляется только наш слой: остальные слои
        # файла (в том числе чужие) не затрагиваются.
        action = "CreateOrOverwriteLayer" if os.path.exists(self.output_path) else "CreateOrOverwriteFile"
        options.actionOnExistingFile = qt_enum(QgsVectorFileWriter, "ActionOnExistingFile", action)
        error, message = write_vector_layer(memory_layer, self.output_path, options)
        if error != qt_enum(QgsVectorFileWriter, "WriterError", "NoError"):
            self.logger.error("не удалось записать слой в {0}: {1}".format(self.output_path, message))
            return None

        layer = QgsVectorLayer(self._uri(), name, "ogr")
        if not layer.isValid():
            self.logger.error("слой «{0}» записан в {1}, но не открылся".format(name, self.output_path))
            return None
        QgsProject.instance().addMapLayer(layer)
        self.field_names = field_names
        self.layer_type = layer_type
        self.logger.info("создан слой «{0}» в {1}".format(name, self.output_path))
        return layer

    def _add_missing_fields(self, keys):
        """Добавляет поля для атрибутов, которых ещё нет в слое."""
        missing = [key for key in keys if key not in self.field_names]
        if not missing:
            return
        name = self.layer_def.gpkg_layer
        new_names = safe_field_names(missing, self.settings.max_field_name_length,
                                     used=self.layer.fields().names())
        if self.layer.isEditable():
            self.logger.warn("слой «{0}» открыт на редактирование, новые атрибуты не добавлены: {1}. "
                             "Сохраните или отмените правку слоя, и при следующем клике поля "
                             "будут добавлены.".format(name, ", ".join(new_names.values())))
            return
        fields = [QgsField(field_name, string_field_type()) for field_name in new_names.values()]
        if not self.layer.dataProvider().addAttributes(fields):
            self.logger.error("не удалось добавить в слой «{0}» поля: {1}".format(
                name, ", ".join(new_names.values())))
            return
        self.layer.updateFields()
        self.field_names.update(new_names)
        self.logger.info("в слой «{0}» добавлены поля: {1}".format(name, ", ".join(new_names.values())))

    def _build_feature(self, index, feature, row, fields):
        geometry_json = feature.get("geometry")
        if not geometry_json:
            self.logger.warn("объект {0} пришёл без геометрии, пропущен".format(index))
            return None
        feature_type = normalise_geometry_type(geometry_json.get("type"))
        if self.layer_type is not None and feature_type != self.layer_type:
            self.logger.warn("объект {0}: геометрия {1}, а слой «{2}» создан как {3}; объект "
                             "пропущен".format(index, geometry_json.get("type"),
                                               self.layer_def.gpkg_layer, self.layer_type))
            return None
        geometry = geometry_from_geojson(geometry_json)
        if geometry is None:
            self.logger.warn("геометрию объекта {0} не удалось разобрать, пропущен".format(index))
            return None
        new_feature = QgsFeature(fields)
        new_feature.setGeometry(geometry)
        for key, field_name in self.field_names.items():
            if fields.indexOf(field_name) >= 0:
                new_feature.setAttribute(field_name, attribute_text(row.get(key)))
        return new_feature

    def _append(self, features, rows):
        layer = self.layer
        name = self.layer_def.gpkg_layer
        fields = layer.fields()
        new_features = []
        for index, (feature, row) in enumerate(zip(features, rows)):
            new_feature = self._build_feature(index, feature, row, fields)
            if new_feature is not None:
                new_features.append(new_feature)
        if not new_features:
            return 0

        # Если пользователь сам открыл слой на редактирование, объекты
        # добавляются в его правку без сохранения: решать, сохранять ли
        # её, должен он. Иначе правка открывается и закрывается здесь.
        own_session = not layer.isEditable()
        if own_session and not layer.startEditing():
            self.logger.error("не удалось открыть слой «{0}» на редактирование".format(name))
            return 0
        if not layer.addFeatures(new_features):
            self.logger.error("объекты не добавлены в слой «{0}»".format(name))
            if own_session:
                layer.rollBack()
            return 0
        if own_session:
            if not layer.commitChanges():
                errors = "; ".join(layer.commitErrors())
                layer.rollBack()
                self.logger.error("не удалось сохранить слой «{0}»: {1}".format(name, errors))
                return 0
        else:
            self.logger.warn("слой «{0}» был открыт на редактирование, объекты добавлены в "
                             "текущую правку. Сохраните слой сами.".format(name))
        layer.updateExtents()
        layer.triggerRepaint()
        self.last_added = [QgsGeometry(feature.geometry()) for feature in new_features]
        return len(new_features)


# === Выгрузка области ===

# Самая маленькая ячейка, которую ещё делим на четыре, м; размер «точечного»
# окна для проверки, что сервер отдаёт объекты окном, м.
MIN_AREA_CELL_M = 5.0
PROBE_CELL_M = 1.0

AreaResult = namedtuple("AreaResult", "features requests capped_cells confirmed stopped")


def split_rect(rect):
    xmin, ymin, xmax, ymax = rect
    xmid, ymid = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
    return [(xmin, ymin, xmid, ymid), (xmid, ymin, xmax, ymid),
            (xmin, ymid, xmid, ymax), (xmid, ymid, xmax, ymax)]


def _centre_cell(rect, size):
    x, y = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
    return (x - size / 2.0, y - size / 2.0, x + size / 2.0, y + size / 2.0)


def feature_key(feature):
    """Ключ объекта для отсечения повторов: id из ответа, иначе атрибуты целиком."""
    identifier = feature.get("id")
    if identifier not in (None, ""):
        return "id:{0}".format(identifier)
    return "p:" + json.dumps(feature.get("properties"), sort_keys=True, ensure_ascii=False, default=str)


def collect_area(request, rect, feature_count, intersects_area=None, should_stop=None,
                 on_request=None, max_requests=5000):
    """Все объекты слоя в прямоугольнике rect (EPSG:3857) запросами «окном».

    request(cell) возвращает объекты GetFeatureInfo картинкой в один пиксель
    на всю ячейку: WMS-серверы обычно отдают так всё, что пересекает ячейку.
    Если объектов пришло столько же, сколько feature_count (выдача могла быть
    обрезана), ячейка делится на четыре. Ячейки вне области
    (intersects_area(cell) == False) не запрашиваются.

    Сервер мог бы отдавать и только объекты в центре ячейки. Поэтому для
    ячеек с объектами делается запрос крошечным окном в их центре, пока
    ячейка не даст больше объектов, чем её центр: так подтверждается, что
    сервер работает окном (confirmed=True). При работе «точкой» подтверждения
    не будет никогда.
    """
    found = {}
    requests = 0
    capped = 0
    confirmed = False
    stopped = ""
    stack = [rect]
    while stack:
        if should_stop is not None and should_stop():
            stopped = "отменено"
            break
        cell = stack.pop()
        if intersects_area is not None and not intersects_area(cell):
            continue
        if requests >= max_requests:
            stopped = "достигнут предел в {0} запросов".format(max_requests)
            break
        features = request(cell)
        requests += 1
        if len(features) >= feature_count:
            if min(cell[2] - cell[0], cell[3] - cell[1]) / 2.0 >= MIN_AREA_CELL_M:
                stack.extend(split_rect(cell))
                if on_request is not None:
                    on_request(requests, len(found))
                continue
            capped += 1
        keys = {feature_key(feature) for feature in features}
        if keys and not confirmed:
            probe = {feature_key(feature) for feature in request(_centre_cell(cell, PROBE_CELL_M))}
            requests += 1
            confirmed = bool(keys - probe)
        for feature in features:
            found.setdefault(feature_key(feature), feature)
        if on_request is not None:
            on_request(requests, len(found))
    return AreaResult(list(found.values()), requests, capped, confirmed, stopped)


def _to_wms_crs(geometry, crs):
    result = QgsGeometry(geometry)
    result.transform(QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem(WMS_CRS), QgsProject.instance()))
    return result


def area_from_rectangle(rect, crs):
    """Область (QgsGeometry в EPSG:3857) из прямоугольника QgsRectangle в CRS crs."""
    return _to_wms_crs(QgsGeometry.fromRect(rect), crs)


def area_from_selection(layer):
    """Область из выделенных полигонов слоя; None, если полигонов не выделено."""
    geometries = [feature.geometry() for feature in layer.selectedFeatures() if feature.hasGeometry()]
    if not geometries:
        return None
    geometry = QgsGeometry.unaryUnion(geometries)
    if geometry is None or geometry.isNull() or geometry.area() <= 0:
        return None
    return _to_wms_crs(geometry, layer.crs())


def area_km2(area):
    """Примерная площадь области в км²: поправка проекции Меркатора по широте центра."""
    centre_y = area.centroid().asPoint().y()
    latitude = math.atan(math.sinh(centre_y / 6378137.0))
    return area.area() * math.cos(latitude) ** 2 / 1e6


def flash_added(canvas, session):
    """На секунду подсвечивает объекты, добавленные последним действием сеанса."""
    if not hasattr(canvas, "flashGeometries"):
        return
    for writer in session.writers:
        if writer.last_added and writer.layer is not None:
            canvas.flashGeometries(writer.last_added, writer.layer.crs())


# === Сеанс выгрузки и инструмент карты ===

class NspdSession:
    """Выгрузка выбранных слоёв НСПД в один файл GeoPackage."""

    def __init__(self, output_path, layer_defs, settings=None, logger=None):
        self.output_path = output_path
        self.settings = settings or RequestSettings()
        self.logger = logger or Logger()
        self.writers = [LayerWriter(output_path, layer_def, self.settings, self.logger)
                        for layer_def in layer_defs]
        # Итоги последнего клика и всего сеанса - для панели плагина.
        self.last_counts = []
        self.last_errors = []
        self.total_added = 0

    def handle_click(self, canvas, point):
        """Опрашивает все слои сеанса в точке клика, возвращает число добавленных объектов."""
        self.last_counts = []
        self.last_errors = []
        for writer in self.writers:
            writer.last_added = []
        try:
            bbox = click_bbox(canvas, point, self.settings)
        except QgsCsException as error:
            self.logger.error("точку клика не удалось пересчитать в {0}: {1}".format(WMS_CRS, error))
            return 0
        total = 0
        for index, writer in enumerate(self.writers):
            try:
                response = request_feature_info(writer.layer_def.code, bbox, self.settings)
            except NspdRequestError as error:
                self.last_errors.append(error)
                self.logger.error("«{0}»: {1}".format(writer.layer_def.title, error))
                # Иначе при недоступном НСПД каждый следующий слой снова ждал бы таймаут.
                if error.connection_failed and index + 1 < len(self.writers):
                    self.logger.warn("остальные слои в этом клике не опрашивались: "
                                     "ошибка связи с НСПД одна для всех слоёв")
                    break
                continue
            added = self._handle_response(response, writer)
            self.last_counts.append((writer.layer_def, added))
            total += added
        if len(self.writers) > 1:
            self.logger.info("всего добавлено объектов: {0}".format(total))
        self.total_added += total
        return total

    def export_area(self, area, should_stop=None, on_progress=None):
        """Выгружает все объекты слоёв сеанса в области area (QgsGeometry в EPSG:3857).

        on_progress(название слоя, запросов, найдено объектов) вызывается после
        каждого запроса. Возвращает число добавленных объектов.
        """
        self.last_counts = []
        self.last_errors = []
        for writer in self.writers:
            writer.last_added = []
        box = area.boundingBox()
        rect = (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())

        def intersects(cell):
            return area.intersects(QgsGeometry.fromRect(QgsRectangle(*cell)))

        total = 0
        for writer in self.writers:
            title = writer.layer_def.title
            progress = None if on_progress is None else (
                lambda done, found, title=title: on_progress(title, done, found))
            try:
                result = collect_area(self._area_request(writer.layer_def.code), rect,
                                      self.settings.feature_count, intersects, should_stop,
                                      progress, self.settings.max_area_requests)
            except NspdRequestError as error:
                self.last_errors.append(error)
                self.logger.error("«{0}»: {1}".format(title, error))
                if error.connection_failed:
                    break
                continue
            if result.stopped:
                self.logger.warn("«{0}»: выгрузка области остановлена ({1}), найденное не "
                                 "записано".format(title, result.stopped))
                break
            inside = [feature for feature in result.features if _feature_in_area(feature, area)]
            self.logger.info("«{0}»: запросов {1}, объектов в области {2}".format(
                title, result.requests, len(inside)))
            if result.capped_cells:
                self.logger.warn("«{0}»: в {1} местах объекты лежат так плотно, что даже в ячейке "
                                 "{2:g} м их не меньше {3}. Часть могла не выгрузиться: увеличьте "
                                 "«Объектов за клик».".format(title, result.capped_cells,
                                                              MIN_AREA_CELL_M, self.settings.feature_count))
            if inside and not result.confirmed:
                self.logger.warn("«{0}»: не удалось подтвердить, что НСПД отдаёт все объекты "
                                 "ячейки, а не только объекты в её центре. Выгрузка может быть "
                                 "неполной: сверьте с картой.".format(title))
            added = writer.write(inside) if inside else 0
            self.last_counts.append((writer.layer_def, added))
            total += added
            self.logger.info("«{0}»: добавлено объектов {1}".format(title, added))
        self.total_added += total
        return total

    def _area_request(self, code):
        def request(cell):
            pause(self.settings.area_pause_ms)
            text = request_feature_info(code, cell, self.settings, image_size=1)
            try:
                data = json.loads(text)
            except ValueError:
                raise NspdRequestError("ответ НСПД не разобран как JSON. Начало ответа: "
                                       + text[:200].replace("\n", " "))
            return (data.get("features") if isinstance(data, dict) else None) or []
        return request

    def _handle_response(self, response, writer):
        title = writer.layer_def.title
        try:
            data = json.loads(response)
        except ValueError as error:
            self.logger.error("«{0}»: ответ НСПД не разобран как JSON: {1}. Начало ответа: {2}".format(
                title, error, response[:300]))
            return 0
        features = (data.get("features") if isinstance(data, dict) else None) or []
        self.logger.info("«{0}»: объектов в ответе {1}".format(title, len(features)))
        if len(features) >= self.settings.feature_count:
            self.logger.warn("«{0}»: пришло ровно столько объектов, сколько запрошено "
                             "(FEATURE_COUNT={1}). Выдача могла быть обрезана: увеличьте "
                             "FEATURE_COUNT или кликните точнее.".format(title, self.settings.feature_count))
        if not features:
            return 0
        added = writer.write(features)
        self.logger.info("«{0}»: добавлено объектов {1}".format(title, added))
        return added


class NspdIdentifyTool(QgsMapTool):
    """Инструмент карты: клик запрашивает объекты НСПД и дописывает их в GeoPackage.

    Пока инструмент активен, курсор - перекрестие; на время запроса - часы.
    Добавленные объекты на секунду подсвечиваются на карте.
    """

    # Сигнал после обработки клика: число добавленных объектов.
    clickProcessed = pyqtSignal(int)

    def __init__(self, canvas, session):
        super().__init__(canvas)
        self.session = session
        self.setCursor(QCursor(qt_enum(Qt, "CursorShape", "CrossCursor")))

    def canvasReleaseEvent(self, event):
        # mapPoint() сразу даёт координаты карты; QMouseEvent.pos() в Qt6 устарел.
        if hasattr(event, "mapPoint"):
            point = event.mapPoint()
        else:
            point = self.toMapCoordinates(event.pos())
        QApplication.setOverrideCursor(QCursor(qt_enum(Qt, "CursorShape", "WaitCursor")))
        try:
            added = self.session.handle_click(self.canvas(), point)
        finally:
            QApplication.restoreOverrideCursor()
        self.canvas().refreshAllLayers()
        flash_added(self.canvas(), self.session)
        self.clickProcessed.emit(added)
