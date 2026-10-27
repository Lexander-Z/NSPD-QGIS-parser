"""Сертификаты Минцифры в хранилище сертификатов QGIS.

У QGIS есть собственный список центров сертификации (Настройки - Параметры -
Аутентификация - Управление сертификатами - Центры сертификации). Доверенные
центры из него QGIS использует при проверке HTTPS-соединений, которые идут
через QGIS: и запросов плагина, и WMS-слоёв. Модуль кладёт туда сертификаты
Минцифры только по явному запросу пользователя.

- В систему (Windows, macOS, Linux) ничего не устанавливается, права
  администратора не нужны, браузеры и другие программы это не затрагивает.
- Сертификаты скачиваются с официального адреса gu-st.ru, на который ведут
  ссылки со страницы https://www.gosuslugi.ru/crt, или берутся из файлов,
  выбранных пользователем. В архиве плагина их нет.
- Принимаются только сертификаты с известными отпечатками SHA-256.
"""

import base64
import binascii
import hashlib
import re
from collections import namedtuple

from qgis.core import QgsApplication, QgsAuthCertUtils
from qgis.PyQt.QtCore import QDateTime
from qgis.PyQt.QtNetwork import QSsl, QSslCertificate, QSslConfiguration

from .core_loader import load_core

core = load_core()

CERTIFICATES_PAGE = "https://www.gosuslugi.ru/crt"

CertificateDef = namedtuple("CertificateDef", "title note sha256 url required")

# Отпечатки SHA-256 (от DER) сверены по двум источникам: файлам с gu-st.ru и
# пакету ca-certificates-digital.gov.ru дистрибутива ALT Linux. Подписи обоих
# промежуточных сертификатов проверены корневым (openssl verify).
KNOWN_CERTIFICATES = (
    CertificateDef(
        "Russian Trusted Root CA", "корневой, срок действия до 2032 года",
        "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31",
        "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt", True),
    CertificateDef(
        "Russian Trusted Sub CA (2024)", "промежуточный, срок действия до июля 2029 года",
        "2155785036c900dbb5f1bb2a1569c80c55595bd6bf94867a29bbddbc7d88a3f2",
        "https://gu-st.ru/content/lending/russian_trusted_sub_ca_2024_pem.crt", False),
    CertificateDef(
        "Russian Trusted Sub CA (2022)", "промежуточный, срок действия до марта 2027 года",
        "bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b",
        "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt", False),
)
ROOT_CERTIFICATE = KNOWN_CERTIFICATES[0]

DOWNLOAD_TIMEOUT_MS = 20000


class CertificateError(Exception):
    """Сертификаты установить или удалить не удалось. Текст готов для показа пользователю."""


# === Разбор файлов и отпечатки (без QGIS) ===

_PEM_BLOCK = re.compile(rb"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", re.S)


def der_certificates(data):
    """DER-содержимое сертификатов из файла: PEM (в том числе несколько подряд) или DER."""
    blocks = _PEM_BLOCK.findall(data)
    if blocks:
        result = []
        for block in blocks:
            try:
                result.append(base64.b64decode(b"".join(block.split()), validate=True))
            except (binascii.Error, ValueError):
                continue
        return result
    # DER-сертификат - ASN.1 SEQUENCE, первый байт 0x30.
    return [bytes(data)] if data[:1] == b"\x30" else []


def sha256_hex(der):
    return hashlib.sha256(der).hexdigest()


def known_certificate(der):
    """Описание сертификата из KNOWN_CERTIFICATES по отпечатку или None."""
    fingerprint = sha256_hex(der)
    for cert_def in KNOWN_CERTIFICATES:
        if cert_def.sha256 == fingerprint:
            return cert_def
    return None


# === Состояние доверия в QGIS ===

def _auth_manager():
    manager = QgsApplication.authManager()
    if manager is None or manager.isDisabled():
        return None
    return manager


def trusted_fingerprints():
    """Отпечатки SHA-256 центров сертификации, которым доверяет QGIS; None - узнать не удалось.

    Объединяются системные центры (их видит Qt) и доверенные центры из
    хранилища QGIS: сертификат может быть установлен и так, и так.
    """
    certificates = []
    found = False
    try:
        certificates.extend(QSslConfiguration.systemCaCertificates())
        found = True
    except (AttributeError, TypeError):
        pass
    manager = _auth_manager()
    if manager is not None:
        try:
            certificates.extend(manager.trustedCaCertsCache())
            found = True
        except (AttributeError, TypeError):
            pass
    if not found:
        return None
    return {sha256_hex(bytes(certificate.toDer())) for certificate in certificates}


def status():
    """[(CertificateDef, доверяет ли QGIS)]; вместо True/False - None, если узнать не удалось."""
    fingerprints = trusted_fingerprints()
    return [(cert_def, None if fingerprints is None else cert_def.sha256 in fingerprints)
            for cert_def in KNOWN_CERTIFICATES]


def root_trusted():
    """Доверяет ли QGIS корневому сертификату Минцифры; None - узнать не удалось."""
    fingerprints = trusted_fingerprints()
    if fingerprints is None:
        return None
    return ROOT_CERTIFICATE.sha256 in fingerprints


# === Скачивание, установка, удаление ===

def download_known(timeout_ms=DOWNLOAD_TIMEOUT_MS):
    """Скачивает известные сертификаты с официального адреса.

    Возвращает (список DER, список сообщений об ошибках). Файл, отпечаток
    которого не совпал с ожидаемым, отбрасывается.
    """
    ders, errors = [], []
    for cert_def in KNOWN_CERTIFICATES:
        try:
            data = core.http_get(cert_def.url, timeout_ms, ssl_hint=False)
        except core.NspdRequestError as error:
            errors.append("{0}: {1}".format(cert_def.title, error))
            if error.connection_failed:
                break
            continue
        matching = [der for der in der_certificates(data) if known_certificate(der) == cert_def]
        if not matching:
            errors.append("{0}: файл {1} не совпал с ожидаемым отпечатком SHA-256 и не "
                          "установлен".format(cert_def.title, cert_def.url))
            continue
        ders.extend(matching)
    return ders, errors


def install(ders):
    """Кладёт известные сертификаты в хранилище QGIS с политикой «доверять».

    Возвращает (установленные CertificateDef, сообщения о пропущенных).
    Сертификаты с неизвестным отпечатком не устанавливаются никогда.
    """
    manager = _auth_manager()
    if manager is None:
        raise CertificateError(
            "система аутентификации QGIS отключена или недоступна, хранилище сертификатов "
            "QGIS использовать нельзя. Установите сертификаты в систему: " + CERTIFICATES_PAGE)
    trusted = core.qt_enum(QgsAuthCertUtils, "CertTrustPolicy", "Trusted")
    der_format = core.qt_enum(QSsl, "EncodingFormat", "Der")
    now = QDateTime.currentDateTimeUtc()
    installed, skipped = [], []
    for der in ders:
        cert_def = known_certificate(der)
        if cert_def is None:
            skipped.append("неизвестный сертификат (SHA-256 {0}...) не установлен: принимаются только "
                           "сертификаты Минцифры с известными отпечатками".format(sha256_hex(der)[:16]))
            continue
        if cert_def in installed:
            continue
        certificate = QSslCertificate(der, der_format)
        if certificate.isNull():
            skipped.append("{0}: Qt не смог прочитать сертификат".format(cert_def.title))
            continue
        if certificate.expiryDate() < now:
            skipped.append("{0}: срок действия истёк, не установлен".format(cert_def.title))
            continue
        if not (manager.storeCertAuthority(certificate)
                and manager.storeCertTrustPolicy(certificate, trusted)):
            skipped.append("{0}: QGIS не сохранил сертификат в своё хранилище".format(cert_def.title))
            continue
        installed.append(cert_def)
    _rebuild_caches(manager)
    return installed, skipped


def remove_from_qgis():
    """Убирает сертификаты Минцифры из хранилища QGIS. Возвращает удалённые CertificateDef.

    Системные сертификаты не трогаются: их удаляют средствами системы.
    """
    manager = _auth_manager()
    if manager is None or not hasattr(manager, "databaseCAs"):
        raise CertificateError(
            "удалить автоматически не получилось. Удалите сертификаты вручную: Настройки - "
            "Параметры - Аутентификация - Управление сертификатами - Центры сертификации")
    remove_policy = getattr(manager, "removeCertTrustPolicy", None)
    removed = []
    for certificate in manager.databaseCAs():
        cert_def = known_certificate(bytes(certificate.toDer()))
        if cert_def is None:
            continue
        if remove_policy is not None:
            remove_policy(certificate)
        if manager.removeCertAuthority(certificate):
            removed.append(cert_def)
    _rebuild_caches(manager)
    return removed


def _rebuild_caches(manager):
    # Порядок важен: список центров, затем политики доверия, затем доверенные центры.
    for name in ("rebuildCaCertsCache", "rebuildCertTrustCache", "rebuildTrustedCaCertsCache"):
        method = getattr(manager, name, None)
        if method is not None:
            method()
