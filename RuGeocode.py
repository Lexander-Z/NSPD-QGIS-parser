import requests
import time
from qgis.core import (QgsProject, QgsVectorLayer, QgsFeature, 
                       QgsGeometry, QgsPointXY, QgsField, Qgis)
from qgis.utils import iface
from PyQt5.QtCore import QVariant, QCoreApplication, Qt
from PyQt5.QtWidgets import QProgressBar

# ================= НАСТРОЙКИ =================
API_KEY = "ТВОЙ_API_КЛЮЧ"
ADDRESS_FIELD = "Адрес" # Точное название колонки
# =============================================

def geocode_dadata_robust():
    layer = iface.activeLayer()
    
    if not layer:
        print("❌ ОШИБКА: Выделите слой с адресами!")
        return
        
    if ADDRESS_FIELD not in[field.name() for field in layer.fields()]:
        print(f"❌ ОШИБКА: В слое нет колонки '{ADDRESS_FIELD}'.")
        return

    total_count = layer.featureCount()
    print(f"🚀 СТАРТ: Начинаем геокодирование {total_count} адресов...")

    # Создаем новый слой
    crs = "EPSG:4326"
    mem_layer = QgsVectorLayer(f"Point?crs={crs}", f"{layer.name()}_geocoded", "memory")
    provider = mem_layer.dataProvider()
    
    old_fields = layer.fields()
    provider.addAttributes(old_fields)
    provider.addAttributes([
        QgsField("Lat", QVariant.Double),
        QgsField("Lon", QVariant.Double),
        QgsField("QC_Geo", QVariant.String)
    ])
    mem_layer.updateFields()

    features_to_add =[]
    
    url = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
    
    # Создаем сессию, чтобы не плодить подключения
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {API_KEY}"
    })

    # Настройка Прогресс-бара
    progressMessageBar = iface.messageBar().createMessage("DaData Геокодирование...")
    progress = QProgressBar()
    progress.setMaximum(total_count)
    if hasattr(Qt, 'AlignCenter'):
        progress.setAlignment(Qt.AlignCenter)
    progressMessageBar.layout().addWidget(progress)
    iface.messageBar().pushWidget(progressMessageBar, Qgis.Info)

    for i, feature in enumerate(layer.getFeatures()):
        address = feature[ADDRESS_FIELD]
        
        progress.setValue(i + 1)
        QCoreApplication.processEvents() # Чтобы QGIS не зависал
        
        new_feature = QgsFeature()
        new_attributes = feature.attributes()
        
        lat, lon, qc_geo = None, None, None
        
        if address and isinstance(address, str):
            try:
                payload = {"query": address, "count": 1}
                
                # Таймаут на случай подвисания сети
                response = session.post(url, json=payload, timeout=5)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if data.get("suggestions") and len(data["suggestions"]) > 0:
                        best_match = data["suggestions"][0]["data"]
                        lat = best_match.get("geo_lat")
                        lon = best_match.get("geo_lon")
                        qc_geo = str(best_match.get("qc_geo")) if best_match.get("qc_geo") is not None else "Нет данных"
                        
                        # 🔥 ИСПРАВЛЕНИЕ ЗДЕСЬ: Выводим точность (qc_geo) вместо Lat
                        if lat and lon:
                            print(f"✅ [{i+1}/{total_count}] Найдено: {address} -> Точность: {qc_geo}")
                        else:
                            print(f"⚠️ [{i+1}/{total_count}] Без координат: {address}")
                    else:
                        print(f"⚠️[{i+1}/{total_count}] Ничего не найдено: {address}")
                        
                elif response.status_code == 403:
                    print(f"❌ ОШИБКА 403: Проверьте ключ! Остановка.")
                    break
                else:
                    print(f"❌[{i+1}/{total_count}] Ошибка API {response.status_code}: {address}")
                    
            except requests.exceptions.Timeout:
                print(f"⏳ [{i+1}/{total_count}] Сервер не ответил (таймаут) на адресе: {address}")
            except requests.exceptions.RequestException as e:
                print(f"❌ [{i+1}/{total_count}] Сбой соединения: {e}")
                
            time.sleep(0.05) # Крошечная пауза, чтобы не душить сервер
        else:
            print(f"⏭️ [{i+1}/{total_count}] Пропуск пустой строки")
            
        new_attributes.extend([lat, lon, qc_geo])
        new_feature.setAttributes(new_attributes)
        
        if lat and lon:
            geom = QgsGeometry.fromPointXY(QgsPointXY(float(lon), float(lat)))
            new_feature.setGeometry(geom)
            
        features_to_add.append(new_feature)

    print("⏳ Сохранение результатов в новый слой...")
    provider.addFeatures(features_to_add)
    mem_layer.updateExtents()
    QgsProject.instance().addMapLayer(mem_layer)
    
    iface.messageBar().clearWidgets()
    print("🎉 ГОТОВО! Слой добавлен на карту.")

# Запуск
geocode_dadata_robust()