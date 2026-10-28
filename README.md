# NSPD Parser

Плагин для QGIS: кликаете по участку или зоне на карте, а его границы и сведения из НСПД сохраняются в файл `.gpkg`.

Слои: земельные участки ЕГРН и ЗОУИТ (культурное наследие; энергетика, связь, транспорт; охраняемые объекты; природные территории; иные).

## Установка

1. Скачайте [nspd_parser_plugin.zip](https://github.com/Lexander-Z/NSPD-QGIS-parser/releases/latest/download/nspd_parser_plugin.zip).
2. В QGIS: **Модули - Управление и установка модулей - Установить из ZIP**, выберите файл.

Нужен QGIS 3.16 или новее, подходит и QGIS 4.

## Как пользоваться

1. Нажмите кнопку плагина на панели инструментов.
2. Если панель пишет, что нет сертификатов Минцифры, нажмите «Сертификаты...» и «Скачать и установить в QGIS». Это нужно один раз.
3. Отметьте слои и нажмите «Начать выгрузку».
4. Кликайте по объектам на карте. Когда закончите, нажмите «Остановить».

Чтобы выгрузить всё сразу, нажмите «Нарисовать прямоугольник» и обведите область на карте. Или выделите полигон (например, границу населённого пункта) и нажмите «По выделенным полигонам».

Объекты появятся в проекте отдельным слоем, сведения о них будут в таблице атрибутов:

![image](https://github.com/user-attachments/assets/5eda4a20-ed65-4cb2-8d85-4e08c5e572f6)

## Если не работает

- **Нет связи с НСПД.** Установите сертификаты Минцифры: кнопкой в плагине или с [gosuslugi.ru/crt](https://www.gosuslugi.ru/crt) (после этого перезагрузите компьютер).
- **Ничего не нашлось.** Приблизьте карту и кликните точнее.
- **Слой НСПД не добавился на карту.** Причина будет в журнале панели.
- Подробности смотрите в журнале внизу панели плагина.

<details>
<summary>Без плагина: скрипт в консоли Python</summary>

1. Скачайте репозиторий (**Code - Download ZIP**) и распакуйте. Файлы `nspd_parser.py` и `nspd_core.py` должны лежать рядом.
2. Добавьте на карту слой НСПД, чтобы видеть, куда кликать. Например, через плагин rosreestr-search-qgis-plugin:

   ![image](https://github.com/user-attachments/assets/9c257536-e85c-4dbd-a111-8cfd7b1c0032)
   ![image](https://github.com/user-attachments/assets/4c4ab150-4b0f-4ff8-a9f7-fc83fa933ff6)

3. **Модули - Консоль Python**, затем «Редактор»:

   ![image](https://github.com/user-attachments/assets/20c6ecc4-a2c3-463a-956c-088e0f5bac22)
   ![image](https://github.com/user-attachments/assets/47bf1918-d4d8-4dd5-9a1c-7825b241c401)

4. «Открыть сценарий», выберите `nspd_parser.py` (на скриншоте старые файлы, теперь он один), затем «Выполнить сценарий»:

   ![image](https://github.com/user-attachments/assets/b5a48e6c-7d57-43ac-8808-0543208cc044)
   ![image](https://github.com/user-attachments/assets/fa714ec0-e6c9-48c0-b92f-94ab09f4adf0)

5. Выберите слой и файл для сохранения, кликайте по карте. Сколько объектов добавлено, видно в консоли (сейчас без значков, как на скриншоте):

   ![image](https://github.com/user-attachments/assets/18bef4c9-e654-4fb0-a092-21684e152f3d)
   ![image](https://github.com/user-attachments/assets/2a749204-3fc0-478d-b6c2-ffde29b7f126)

6. Чтобы закончить, выберите на панели QGIS любой другой инструмент:

   ![image](https://github.com/user-attachments/assets/f5d91f4e-c296-4615-9c06-bf64195b0471)

Настройки в начале `nspd_parser.py`: `FEATURE_COUNT` (объектов за клик) и `TIMEOUT_S` (сколько секунд ждать ответа).

Самые старые скрипты `NSPD v6.2 *.py` лежат в `legacy/` и не обновляются. Им нужна папка `C:\1`. Если они сообщили об ошибке сохранения, удалите файл из этой папки:

![image](https://github.com/user-attachments/assets/a0ec7c4e-5b9b-488b-956f-ae9f915320fd)

</details>

<details>
<summary>Для разработчиков</summary>

- `nspd_core.py`: общая логика скрипта и плагина.
- `plugin/`: плагин. `python plugin/build_zip.py` собирает и проверяет `dist/nspd_parser_plugin.zip`.
- Релиз: поднимите `version` в `plugin/nspd_parser_plugin/metadata.txt` и отправьте изменение в `main`. GitHub Actions соберёт архив и опубликует релиз `vX.Y.Z`, если его ещё нет.
- Работа без установки: подключите `plugin/nspd_parser_plugin` ссылкой в каталог плагинов профиля QGIS.
- Разбор старого кода и принятые решения: [docs/ANALYSIS.md](docs/ANALYSIS.md).

</details>
