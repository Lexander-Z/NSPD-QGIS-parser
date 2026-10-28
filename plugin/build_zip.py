"""Сборка zip-архива плагина для установки в QGIS.

Запуск из корня репозитория:

    python plugin/build_zip.py

Результат: dist/nspd_parser_plugin.zip. В QGIS он ставится через
«Модули - Управление модулями - Установить из ZIP».

Общий модуль nspd_core.py хранится в репозитории один раз, в корне, и
при сборке копируется внутрь пакета плагина. После сборки архив
проверяется: структура, обязательные файлы, метаданные, синтаксис Python.
QGIS для сборки и проверки не нужен.
"""

import configparser
import os
import sys
import zipfile

PLUGIN_NAME = "nspd_parser_plugin"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
PACKAGE_DIR = os.path.join(HERE, PLUGIN_NAME)
DIST_DIR = os.path.join(REPO_ROOT, "dist")

SHARED_MODULES = ("nspd_core.py",)
INCLUDED_EXTENSIONS = (".py", ".txt", ".svg", ".png")
REQUIRED_FILES = ("__init__.py", "metadata.txt", "nspd_core.py", "core_loader.py",
                  "nspd_plugin.py", "nspd_dock.py", "nspd_certificates.py", "nspd_cert_dialog.py")
REQUIRED_METADATA = ("name", "qgisMinimumVersion", "description", "about", "version",
                     "author", "email", "repository")
LICENSE_FILE = "LICENSE"


def collect_entries():
    """Пары (путь на диске, путь в архиве)."""
    entries = []
    for directory, subdirs, files in os.walk(PACKAGE_DIR):
        subdirs[:] = sorted(name for name in subdirs if name != "__pycache__")
        for name in sorted(files):
            # Общий модуль берётся только из корня репозитория, даже если
            # рядом с плагином лежит его копия или ссылка.
            if name in SHARED_MODULES or not name.endswith(INCLUDED_EXTENSIONS):
                continue
            source = os.path.join(directory, name)
            relative = os.path.relpath(source, PACKAGE_DIR).replace(os.sep, "/")
            entries.append((source, PLUGIN_NAME + "/" + relative))
    for name in SHARED_MODULES + (LICENSE_FILE,):
        entries.append((os.path.join(REPO_ROOT, name), PLUGIN_NAME + "/" + name))
    return entries


def build(output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for source, name in collect_entries():
            archive.write(source, name)


def verify(output_path):
    """Список проблем архива; пустой список - архив в порядке."""
    problems = []
    with zipfile.ZipFile(output_path) as archive:
        names = archive.namelist()
        broken = archive.testzip()
        if broken:
            problems.append("повреждён файл в архиве: " + broken)
        roots = {name.split("/", 1)[0] for name in names}
        if roots != {PLUGIN_NAME}:
            problems.append("в корне архива должна быть одна папка {0}, а есть: {1}".format(
                PLUGIN_NAME, ", ".join(sorted(roots))))
        if len(names) != len(set(names)):
            problems.append("в архиве есть повторяющиеся пути")
        for required in REQUIRED_FILES:
            if PLUGIN_NAME + "/" + required not in names:
                problems.append("нет обязательного файла " + required)
        for name in names:
            if "__pycache__" in name or name.endswith(".pyc"):
                problems.append("лишний файл " + name)
            if name.endswith(".py"):
                try:
                    compile(archive.read(name).decode("utf-8"), name, "exec")
                except SyntaxError as error:
                    problems.append("синтаксическая ошибка в {0}: {1}".format(name, error))

        metadata_name = PLUGIN_NAME + "/metadata.txt"
        if metadata_name in names:
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            parser.read_string(archive.read(metadata_name).decode("utf-8"))
            general = parser["general"] if parser.has_section("general") else {}
            for key in REQUIRED_METADATA:
                if not general.get(key, "").strip():
                    problems.append("в metadata.txt не заполнено " + key)
            icon = general.get("icon", "").strip()
            if icon and PLUGIN_NAME + "/" + icon not in names:
                problems.append("в metadata.txt указана иконка {0}, а в архиве её нет".format(icon))
    return problems


def print_listing(output_path):
    with zipfile.ZipFile(output_path) as archive:
        for info in archive.infolist():
            print("  {0:>7}  {1}".format(info.file_size, info.filename))
    print("Архив: {0} ({1} байт)".format(os.path.relpath(output_path, REPO_ROOT),
                                          os.path.getsize(output_path)))


def main():
    output_path = os.path.join(DIST_DIR, PLUGIN_NAME + ".zip")
    build(output_path)
    print_listing(output_path)
    problems = verify(output_path)
    for problem in problems:
        print("ОШИБКА: " + problem)
    print("Проверка архива: " + ("пройдена" if not problems else "не пройдена"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
