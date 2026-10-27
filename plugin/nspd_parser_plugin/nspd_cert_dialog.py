"""Окно «Сертификаты Минцифры»: состояние, установка в QGIS и удаление."""

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QCursor, QDesktopServices
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from . import nspd_certificates as certificates

INTRO = (
    "<p>Сайт НСПД (nspd.gov.ru) использует сертификат удостоверяющего центра "
    "Минцифры России. Пока QGIS не доверяет этому центру, НСПД не отвечает: "
    "не работают ни выгрузка, ни слои НСПД на карте.</p>"
    "<p>Кнопка «Скачать и установить в QGIS»:</p>"
    "<ul>"
    "<li>скачает сертификаты с официального адреса gu-st.ru, на который ведут ссылки "
    "со страницы <a href=\"{page}\">gosuslugi.ru/crt</a>;</li>"
    "<li>сверит их отпечатки SHA-256 с известными и не установит несовпавшие;</li>"
    "<li>добавит их только в хранилище сертификатов QGIS. В систему ничего не "
    "устанавливается, права администратора не нужны, браузеры и другие программы "
    "это не затрагивает.</li>"
    "</ul>"
    "<p>Учтите: QGIS будет доверять всем сайтам с сертификатами этого центра, а не "
    "только НСПД. Удалить сертификаты можно кнопкой «Удалить из QGIS» или в меню "
    "Настройки - Параметры - Аутентификация - Управление сертификатами.</p>"
).format(page=certificates.CERTIFICATES_PAGE)


class CertificatesDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        core = certificates.core
        self.setWindowTitle("Сертификаты Минцифры")
        self.setMinimumWidth(520)
        rich_text = core.qt_enum(Qt, "TextFormat", "RichText")
        layout = QVBoxLayout(self)

        intro = QLabel(INTRO, self)
        intro.setTextFormat(rich_text)
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        layout.addWidget(intro)

        self.status_label = QLabel(self)
        self.status_label.setTextFormat(rich_text)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.result_label = QLabel(self)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        actions = QHBoxLayout()
        self.install_button = QPushButton("Скачать и установить в QGIS", self)
        self.install_button.setDefault(True)
        self.install_button.clicked.connect(self._install_downloaded)
        files_button = QPushButton("Установить из файлов...", self)
        files_button.setToolTip("Если сертификаты уже скачаны с gosuslugi.ru/crt через браузер")
        files_button.clicked.connect(self._install_from_files)
        actions.addWidget(self.install_button)
        actions.addWidget(files_button)
        layout.addLayout(actions)

        other = QHBoxLayout()
        self.remove_button = QPushButton("Удалить из QGIS", self)
        self.remove_button.clicked.connect(self._remove)
        site_button = QPushButton("Открыть gosuslugi.ru/crt", self)
        site_button.setToolTip("Официальная страница: установка сертификатов в систему и браузер")
        site_button.clicked.connect(self._open_site)
        close_button = QPushButton("Закрыть", self)
        close_button.clicked.connect(self.accept)
        other.addWidget(self.remove_button)
        other.addWidget(site_button)
        other.addStretch(1)
        other.addWidget(close_button)
        layout.addLayout(other)

        self._refresh()

    def _refresh(self):
        lines = ["<b>Состояние</b>"]
        for cert_def, trusted in certificates.status():
            if trusted is None:
                state = "проверить не удалось"
            elif trusted:
                state = "QGIS доверяет"
            else:
                state = "<b>не установлен</b>"
            lines.append("{0} ({1}): {2}".format(cert_def.title, cert_def.note, state))
        self.status_label.setText("<br>".join(lines))

    def _busy(self):
        QApplication.setOverrideCursor(QCursor(certificates.core.qt_enum(Qt, "CursorShape", "WaitCursor")))

    def _install_downloaded(self, _checked=False):
        self._busy()
        try:
            ders, errors = certificates.download_known()
        finally:
            QApplication.restoreOverrideCursor()
        self._install(ders, errors)

    def _install_from_files(self, _checked=False):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Файлы сертификатов Минцифры", "",
            "Сертификаты (*.cer *.crt *.pem *.der);;Все файлы (*)")
        if not paths:
            return
        ders, errors = [], []
        for path in paths:
            try:
                with open(path, "rb") as handle:
                    found = certificates.der_certificates(handle.read())
            except OSError as error:
                errors.append("{0}: {1}".format(path, error))
                continue
            if not found:
                errors.append("{0}: в файле не найден сертификат".format(path))
            ders.extend(found)
        self._install(ders, errors)

    def _install(self, ders, errors):
        messages = list(errors)
        installed = []
        if ders:
            try:
                installed, skipped = certificates.install(ders)
                messages.extend(skipped)
            except certificates.CertificateError as error:
                messages.append(str(error))
        if installed:
            text = "Установлено в QGIS: {0}.".format(", ".join(item.title for item in installed))
            if certificates.root_trusted() is False:
                text += " QGIS пока не доверяет корневому сертификату, перезапустите QGIS."
        else:
            text = "Ничего не установлено."
        if messages:
            text += "\n" + "\n".join(messages)
        self.result_label.setText(text)
        self._refresh()

    def _remove(self, _checked=False):
        answer = QMessageBox.question(
            self, "Удалить сертификаты Минцифры",
            "Удалить сертификаты Минцифры из хранилища QGIS? После этого НСПД "
            "перестанет открываться в QGIS, если сертификаты не установлены в систему.")
        if answer != certificates.core.qt_enum(QMessageBox, "StandardButton", "Yes"):
            return
        try:
            removed = certificates.remove_from_qgis()
        except certificates.CertificateError as error:
            self.result_label.setText(str(error))
            return
        if removed:
            self.result_label.setText("Удалено из QGIS: {0}.".format(
                ", ".join(item.title for item in removed)))
        else:
            self.result_label.setText(
                "В хранилище QGIS сертификатов Минцифры нет. Если они установлены в "
                "систему, удаляются они средствами системы.")
        self._refresh()

    def _open_site(self, _checked=False):
        QDesktopServices.openUrl(QUrl(certificates.CERTIFICATES_PAGE))
