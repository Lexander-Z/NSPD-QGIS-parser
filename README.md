Выгрузка информации и векторной геометрии объекта с НСПД через QGIS

# :exclamation: ВНИМАНИЕ! Для работы скрипта необходимо установить сертификаты Минцифры, а затем перезагрузить ПК!
https://www.gosuslugi.ru/crt

Инструкция по запуску

#### 0. Установка сертификатов Минцифры
#### 1. В корне диска C создаём папку с названием 1
#### 2. Запускаем QGIS и создаём проект
#### 3. Добавляем WMS слой из НСПД, например Земельные участки из ЕГРН (Кадастр).
   Легче всего добавить через плагин rosreestr-search-qgis-plugin (скрин ниже)

   ![image](https://github.com/user-attachments/assets/9c257536-e85c-4dbd-a111-8cfd7b1c0032)
   ![image](https://github.com/user-attachments/assets/4c4ab150-4b0f-4ff8-a9f7-fc83fa933ff6)

#### 5. В верхнем меню Модули - Консоль Python
   
   ![image](https://github.com/user-attachments/assets/20c6ecc4-a2c3-463a-956c-088e0f5bac22)

#### 7. В открывшемся окне нажимаем кнопку "Редактор"
   
   ![image](https://github.com/user-attachments/assets/47bf1918-d4d8-4dd5-9a1c-7825b241c401)

#### 8. "Открыть сценарий" - выбираем нужный скрипт в соответсвии с названием слоя: Кадастр (Земельные участки) / ЗОУИТ

   ![image](https://github.com/user-attachments/assets/b5a48e6c-7d57-43ac-8808-0543208cc044)

#### 10. "Выполнить сценарий"
    
   ![image](https://github.com/user-attachments/assets/fa714ec0-e6c9-48c0-b92f-94ab09f4adf0)

#### 11. Нажимаем на необходимый участок для выгрузки

   ![image](https://github.com/user-attachments/assets/18bef4c9-e654-4fb0-a092-21684e152f3d)
      Будет создан новый слой в папке C:\1
      В зависимости от скрипта названия слоёв:

   "C:/1/ZOUIT_OKN.gpkg"

   "C:/1/ZOUIT_Energetika_svaz_transport.gpkg"

   "C:/1/ZOUIT_ohranyaemyh_ob_i_bezopasnosti.gpkg"

   "C:/1/ZOUIT_prirodnyh_territorii.gpkg"

   "C:/1/Inye_ZOUIT.gpkg"

   "C:/1/NSPD_Kadastr.gpkg"

   Все объекты будут добавлены в один слой и будет указано количество объектов выгруженных за раз
   
   ![image](https://github.com/user-attachments/assets/2a749204-3fc0-478d-b6c2-ffde29b7f126)

   Информация об объекте будет заполнена в атрибутивной таблице
  
   ![image](https://github.com/user-attachments/assets/5eda4a20-ed65-4cb2-8d85-4e08c5e572f6)
   
#### 13. Для завершения работы скрипта необходимо в верхней панели QGIS нажать на "Создать объект", "Выбрать объект" и т.д., чтобы сменился тип курсора на любой другой

   ![image](https://github.com/user-attachments/assets/f5d91f4e-c296-4615-9c06-bf64195b0471)

## :exclamation: После окончания выгрузки необходимых объектов рекомендуется пересохранить фаил в другое место, а в папке 1 удалить.
## :exclamation: В случае, если скрипт сообщил об ошибке сохранения удалить слой из папки C:\1
   ![image](https://github.com/user-attachments/assets/a0ec7c4e-5b9b-488b-956f-ae9f915320fd)
