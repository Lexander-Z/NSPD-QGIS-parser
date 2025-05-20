import json
import os
from shapely.geometry import shape

from qgis.PyQt.QtCore import QUrl, QEventLoop
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkAccessManager
from qgis.gui import QgsMapTool
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsVectorFileWriter,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem
)
from PyQt5.QtCore import QVariant

# === Конфигурация ===
wms_url = "https://nspd.gov.ru/api/aeggis/v3/37578/wms"
referer = "https://nspd.gov.ru/map"
output_path = "C:/1/ZOUIT_Energetika_svaz_transport.gpkg"
layer_name_in_gpkg = "ZOUIT_Energetika_svaz_transport"
layer_code = "37578"

result_layer = None

# Разворачиваем вложенные словари
def flatten_dict(d, parent_key='', sep='_'):
    items = {}
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(flatten_dict(v, new_key, sep=sep))
        else:
            items[new_key] = v
    return items

# Автоопределение геометрии
def detect_geom_type(features):
    for feat in features:
        geom = feat.get("geometry")
        if geom and geom.get("type"):
            gtype = geom.get("type")
            return gtype if gtype.startswith("Multi") else "Multi" + gtype
    return "MultiPolygon"

# Запрос GetFeatureInfo
def get_feature_info(point):
    proj_crs = iface.mapCanvas().mapSettings().destinationCrs()
    wms_crs = QgsCoordinateReferenceSystem("EPSG:3857")
    transform = QgsCoordinateTransform(proj_crs, wms_crs, QgsProject.instance())
    point_3857 = transform.transform(point)

    bbox = f"{point_3857.x()-100},{point_3857.y()-100},{point_3857.x()+100},{point_3857.y()+100}"
    url = (
        f"{wms_url}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetFeatureInfo"
        f"&LAYERS={layer_code}&QUERY_LAYERS={layer_code}&CRS=EPSG:3857"
        f"&BBOX={bbox}&WIDTH=800&HEIGHT=800&I=400&J=400"
        f"&INFO_FORMAT=application/json&STYLES=&FORMAT=image/png&FEATURE_COUNT=10"
    )

    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"Referer", referer.encode())
    manager = QNetworkAccessManager()
    reply = manager.get(request)
    loop = QEventLoop()
    reply.finished.connect(loop.quit)
    loop.exec_()

    if reply.error():
        print("❌ Ошибка запроса:", reply.errorString())
        return None

    response_text = reply.readAll().data().decode("utf-8")
    print("📜 Ответ GetFeatureInfo:")
    print(response_text)
    return response_text

# Объединение всех полей объектов
def union_field_defs(features):
    union_dict = {}
    for feat in features:
        props = feat.get("properties", {})
        flat = flatten_dict(props)
        for key, value in flat.items():
            union_dict[key] = value
    return union_dict

# Создание или загрузка слоя
def load_or_create_result_layer(field_defs, geom_type="MultiPolygon"):
    global result_layer
    if result_layer is not None:
        return result_layer

    def truncate_field_name(name):
        return name[:63] if len(name) > 63 else name

    fields = [QgsField(truncate_field_name(k), QVariant.String) for k in field_defs.keys()]
    mem_layer = QgsVectorLayer(f"{geom_type}?crs=EPSG:3857", layer_name_in_gpkg, "memory")
    if not mem_layer.isValid():
        print("❌ Ошибка создания временного слоя.")
        return None

    pr = mem_layer.dataProvider()
    pr.addAttributes(fields)
    mem_layer.updateFields()

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name_in_gpkg
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

    res, err = QgsVectorFileWriter.writeAsVectorFormatV2(
        mem_layer, output_path, QgsProject.instance().transformContext(), options
    )

    if res != QgsVectorFileWriter.NoError:
        print(f"❌ Ошибка записи слоя: {err}")
        return None

    uri = f"{output_path}|layername={layer_name_in_gpkg}"
    result_layer = QgsVectorLayer(uri, layer_name_in_gpkg, "ogr")
    if result_layer.isValid():
        QgsProject.instance().addMapLayer(result_layer)
        print("✅ Новый слой добавлен.")
        return result_layer
    else:
        print("❌ Ошибка загрузки слоя.")
        result_layer = None
        return None

# Инструмент клика
class IdentifyAndAppend(QgsMapTool):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas

    def canvasReleaseEvent(self, event):
        global result_layer
        point = self.toMapCoordinates(event.pos())
        response = get_feature_info(point)
        if not response:
            print("❌ Нет ответа.")
            return

        try:
            data = json.loads(response)
        except Exception as e:
            print("❌ Ошибка парсинга JSON:", e)
            return

        features = data.get("features", [])
        print("🔍 Объектов в ответе:", len(features))
        if not features:
            return

        geom_type = detect_geom_type(features)
        union_defs = union_field_defs(features)
        layer = load_or_create_result_layer(union_defs, geom_type=geom_type)
        if not layer:
            return

        if not layer.isEditable():
            layer.startEditing()

        count_added = 0
        for idx, feat in enumerate(features):
            geom_json = feat.get("geometry")
            if not geom_json:
                print(f"⚠️ Нет геометрии у объекта {idx}")
                continue

            try:
                shapely_geom = shape(geom_json)
                if shapely_geom.is_empty:
                    continue
                qgs_geom = QgsGeometry.fromWkt(shapely_geom.wkt)
            except Exception as e:
                print(f"⚠️ Ошибка геометрии {idx}: {e}")
                continue

            props = flatten_dict(feat.get("properties", {}))
            new_feat = QgsFeature(layer.fields())
            new_feat.setGeometry(qgs_geom)
            for field in layer.fields():
                val = props.get(field.name(), "")
                new_feat.setAttribute(field.name(), str(val))

            if layer.addFeature(new_feat):
                count_added += 1
            else:
                print(f"❌ Ошибка добавления объекта {idx}")

        if layer.commitChanges():
            print(f"✅ Добавлено объектов: {count_added}")
        else:
            print("❌ Ошибка коммита изменений.")

        layer.updateExtents()
        layer.triggerRepaint()
        iface.mapCanvas().refreshAllLayers()

# Запуск
tool = IdentifyAndAppend(iface.mapCanvas())
iface.mapCanvas().setMapTool(tool)
print("🖱️ Кликните по карте, чтобы получить объекты GetFeatureInfo и сохранить в GPKG.")
