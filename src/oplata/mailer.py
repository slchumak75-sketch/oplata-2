"""Восстановление пароля «Конфигурации» по почте (v0.88.0).

Зачем это нужно
---------------
Пароль на «Конфигурацию» один и общий для всех компьютеров. Забыли его —
и раздать обновление, поправить права или завести пользователя не может
никто. До сих пор единственным выходом была ручная правка ``users.json``
в папке на Диске — то есть нужно было знать, где эта папка и что в ней
искать. Просьба пользователя: «А если сделать привязку к почте и
получить пароль по почте».

Как устроено и почему именно так
--------------------------------
1. **По почте уходит код, а не пароль.** Письмо остаётся в ящике
   навсегда: любой, кто откроет почту (или чей телефон к ней привязан),
   прочитал бы пароль и получил доступ к «Конфигурации». Код живёт
   10 минут, даётся на 5 попыток и после смены пароля перестаёт
   действовать.
2. **Новый пароль придумывает сам пользователь** — здесь, в программе.
   Пароль остаётся хэшем с солью (``access._hash_password``), как и
   раньше: восстановление не ослабляет хранение пароля.
3. **Программа только отправляет почту.** Своего сервера у неё нет,
   поэтому нужен обычный почтовый ящик и «пароль приложения» — его
   выдают Яндекс, Mail.ru и Gmail для программ. Пароль приложения
   хранится в общем ``users.json`` и виден тому, кто откроет папку на
   Диске, — поэтому для программы стоит завести **отдельный ящик**, а не
   брать личный.

Почему пароля приложения можно не бояться
-----------------------------------------
Утечка этого пароля даёт доступ к почтовому ящику, но **не к
программе**: код из письма принимается только на том компьютере, где он
был запрошен (сверяется уникальная метка запроса), и только пока не
истёк срок. Отправить себе код может любой — но без доступа к ящику он
бесполезен.

Файл без Qt: здесь только настройки, код и SMTP. Окна — в
``views/recovery_view.py``.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage

from .access import PASSWORD_RESET_KEY, _read_users_file, _write_users_file

# Сколько живёт код и сколько раз его можно ввести.
CODE_TTL_SECONDS = 10 * 60
CODE_ATTEMPTS = 5
CODE_LENGTH = 6

# Ключи users.json.
KEY_MAIL = "mail"                  # настройки отправки (общие для всех)
KEY_RESET = PASSWORD_RESET_KEY     # последний запрос кода

# Значения по умолчанию для известных почт: подставляются, чтобы не
# заставлять пользователя искать сервер и порт. «Пароль приложения» —
# не то же самое, что обычный пароль от ящика.
PROVIDER_PRESETS = {
    "yandex.ru": ("smtp.yandex.ru", 465, True),
    "ya.ru": ("smtp.yandex.ru", 465, True),
    "mail.ru": ("smtp.mail.ru", 465, True),
    "bk.ru": ("smtp.mail.ru", 465, True),
    "inbox.ru": ("smtp.mail.ru", 465, True),
    "list.ru": ("smtp.mail.ru", 465, True),
    "internet.ru": ("smtp.mail.ru", 465, True),
    "gmail.com": ("smtp.gmail.com", 465, True),
    "googlemail.com": ("smtp.gmail.com", 465, True),
}


class MailError(Exception):
    """Письмо не отправлено. Текст пригоден для показа пользователю."""


# ------------------------------------------------------------------ настройки


def _clean(value) -> str:
    return str(value or "").strip()


def normalize_mail(payload) -> dict:
    """Привести настройки к одному виду.

    Домен почты отправки задаёт сервер и порт, если они не вписаны
    руками: ``yandex.ru`` → ``smtp.yandex.ru:465``. Так пользователю
    достаточно адреса и пароля приложения.
    """

    raw = payload if isinstance(payload, dict) else {}
    sender = _clean(raw.get("sender"))
    recipient = _clean(raw.get("recipient")) or sender
    domain = sender.rsplit("@", 1)[-1].lower() if "@" in sender else ""
    host, port, use_ssl = PROVIDER_PRESETS.get(domain, ("", 465, True))
    host = _clean(raw.get("host")) or host
    try:
        port = int(raw.get("port") or port)
    except (TypeError, ValueError):
        port = 465
    return {
        "enabled": bool(raw.get("enabled")),
        "host": host,
        "port": port,
        "ssl": bool(raw.get("ssl", use_ssl)),
        "sender": sender,
        "recipient": recipient,
        "user": _clean(raw.get("user")) or sender,
        "password": _clean(raw.get("password")),
    }


def load_mail_settings() -> dict:
    """Настройки почты из общего ``users.json`` (пустые, если не заданы)."""

    return normalize_mail(_read_users_file().get(KEY_MAIL))


def save_mail_settings(payload) -> dict:
    """Записать настройки почты в общий ``users.json``."""

    settings = normalize_mail(payload)
    data = _read_users_file()
    data[KEY_MAIL] = settings
    _write_users_file(data)
    return settings


def is_configured(payload=None) -> bool:
    """Можно ли пытаться отправить письмо (заполнено главное)."""

    settings = normalize_mail(payload) if payload is not None else load_mail_settings()
    return bool(
        settings["enabled"]
        and settings["sender"]
        and settings["recipient"]
        and settings["host"]
        and settings["password"]
    )


def mask_address(address: str) -> str:
    """``ivanov@mail.ru`` → ``i***@mail.ru`` — показать, куда уйдёт письмо.

    Полный адрес в окне не нужен: его видит любой, кто подошёл к
    компьютеру, а подсказки «куда смотреть» хватает и так.
    """

    address = _clean(address)
    if "@" not in address:
        return address
    name, domain = address.split("@", 1)
    if not name:
        return "@" + domain
    if len(name) == 1:
        return name + "***@" + domain
    return name[0] + "***@" + domain


# --------------------------------------------------------------------- код


def _hash_code(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def make_code() -> str:
    """Шестизначный код. ``secrets`` — не ``random``: угадать нельзя."""

    return "".join(str(secrets.randbelow(10)) for _ in range(CODE_LENGTH))


def _save_reset(payload: dict) -> None:
    data = _read_users_file()
    data[KEY_RESET] = payload
    _write_users_file(data)


def clear_reset() -> None:
    """Забыть выданный код (пароль сменён или восстановление отменено)."""

    data = _read_users_file()
    if KEY_RESET in data:
        data.pop(KEY_RESET, None)
        _write_users_file(data)


def start_reset(now: float | None = None) -> str:
    """Выдать новый код и запомнить его хэш. Возвращает код для письма.

    В файл попадает только хэш: прочитав ``users.json``, код узнать
    нельзя — а значит нельзя и войти, зная одну лишь папку на Диске.
    """

    code = make_code()
    salt = secrets.token_hex(8)
    stamp = time.time() if now is None else float(now)
    _save_reset(
        {
            "salt": salt,
            "hash": _hash_code(code, salt),
            "expires": stamp + CODE_TTL_SECONDS,
            "attempts": 0,
            "token": secrets.token_hex(8),
        }
    )
    return code


def reset_active(now: float | None = None) -> bool:
    """Есть ли действующий (не истёкший) код."""

    payload = _read_users_file().get(KEY_RESET)
    if not isinstance(payload, dict):
        return False
    stamp = time.time() if now is None else float(now)
    try:
        expires = float(payload.get("expires") or 0)
    except (TypeError, ValueError):
        return False
    return expires > stamp and bool(payload.get("hash"))


def reset_seconds_left(now: float | None = None) -> int:
    """Сколько секунд осталось жить коду (0 — кода нет)."""

    payload = _read_users_file().get(KEY_RESET)
    if not isinstance(payload, dict):
        return 0
    stamp = time.time() if now is None else float(now)
    try:
        return max(0, int(float(payload.get("expires") or 0) - stamp))
    except (TypeError, ValueError):
        return 0


def check_code(code: str, now: float | None = None) -> tuple[bool, str]:
    """Проверить код. Возвращает (принят, объяснение для человека).

    Объяснения — по-русски и без технических слов: их показывает окно
    восстановления, когда код не подошёл.
    """

    payload = _read_users_file().get(KEY_RESET)
    if not isinstance(payload, dict) or not payload.get("hash"):
        return False, "Код не запрашивался. Нажмите «Отправить код»."
    stamp = time.time() if now is None else float(now)
    try:
        expires = float(payload.get("expires") or 0)
    except (TypeError, ValueError):
        return False, "Код повреждён. Запросите новый."
    if expires <= stamp:
        clear_reset()
        return False, "Срок действия кода истёк. Запросите новый."
    attempts = int(payload.get("attempts") or 0)
    if attempts >= CODE_ATTEMPTS:
        clear_reset()
        return False, "Слишком много попыток. Запросите новый код."
    salt = str(payload.get("salt") or "")
    if _hash_code(_clean(code), salt) != payload.get("hash"):
        payload["attempts"] = attempts + 1
        left = CODE_ATTEMPTS - payload["attempts"]
        if left <= 0:
            clear_reset()
            return False, "Код неверный. Попытки закончились — запросите новый."
        _save_reset(payload)
        word = "попытка" if left == 1 else ("попытки" if left < 5 else "попыток")
        return False, f"Код неверный. Осталось {left} {word}."
    return True, "Код принят."


# -------------------------------------------------------------------- письмо


def build_message(settings, code: str) -> EmailMessage:
    """Письмо с кодом. Текст простой: его читают с телефона."""

    message = EmailMessage()
    message["Subject"] = "Оплаты — Строймаркет: код для смены пароля"
    message["From"] = settings["sender"]
    message["To"] = settings["recipient"]
    minutes = CODE_TTL_SECONDS // 60
    message.set_content(
        "Здравствуйте!\n\n"
        "Кто-то (скорее всего — вы) запросил смену пароля на раздел\n"
        "«Конфигурация» в программе «Оплаты — Строймаркет».\n\n"
        f"Код: {code}\n\n"
        f"Код действует {minutes} минут и подходит только на том компьютере,\n"
        "где был запрошен.\n\n"
        "Если вы ничего не запрашивали — просто не вводите код. Пароль\n"
        "останется прежним, и войти по этой почте никто не сможет.\n"
    )
    return message


def send_test_message(settings) -> None:
    """Проверочное письмо при настройке: без кода, только «связь есть»."""

    settings = normalize_mail(settings)
    message = EmailMessage()
    message["Subject"] = "Оплаты — Строймаркет: почта настроена"
    message["From"] = settings["sender"]
    message["To"] = settings["recipient"]
    message.set_content(
        "Это проверка связи.\n\n"
        "Почта для восстановления пароля на раздел «Конфигурация» настроена.\n"
        "Если пароль когда-нибудь забудется, программа отправит сюда код,\n"
        "по которому можно будет придумать новый.\n"
    )
    _deliver(settings, message)


def send_code(settings=None) -> str:
    """Выдать код и отправить его письмом. Возвращает код (для тестов).

    Сначала письмо, потом запись кода: если почта не ушла, в файле не
    остаётся «висящего» запроса.
    """

    settings = normalize_mail(settings) if settings is not None else load_mail_settings()
    problems = describe_problems(settings)
    if problems:
        raise MailError(problems)
    # Код выдаём заранее, чтобы проверить настройки, но записываем
    # только после успешной отправки.
    payload_backup = _read_users_file().get(KEY_RESET)
    code = start_reset()
    try:
        _deliver(settings, build_message(settings, code))
    except Exception:
        # Не ушло — убираем запрос, чтобы не осталось мёртвого кода.
        data = _read_users_file()
        if payload_backup is None:
            data.pop(KEY_RESET, None)
        else:
            data[KEY_RESET] = payload_backup
        _write_users_file(data)
        raise
    return code


def describe_problems(settings) -> str:
    """Что не заполнено — одной фразой для окна (пусто, если всё есть)."""

    settings = normalize_mail(settings)
    missing = []
    if not settings["sender"]:
        missing.append("адрес почты, с которой отправлять")
    if not settings["host"]:
        missing.append("сервер почты (например smtp.yandex.ru)")
    if not settings["password"]:
        missing.append("пароль приложения")
    if not settings["recipient"]:
        missing.append("адрес, куда присылать код")
    if not missing:
        return ""
    return "Не хватает данных: " + "; ".join(missing) + "."


def _deliver(settings, message) -> None:
    """Отправить письмо. Ошибки переводим на русский для окна."""

    timeout = 20
    try:
        if settings["ssl"]:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                settings["host"], settings["port"], timeout=timeout, context=context
            ) as smtp:
                smtp.login(settings["user"], settings["password"])
                smtp.send_message(message)
        else:
            with smtplib.SMTP(settings["host"], settings["port"], timeout=timeout) as smtp:
                smtp.ehlo()
                if smtp.has_extn("starttls"):
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(settings["user"], settings["password"])
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "Почта не приняла пароль. Для программ нужен «пароль\n"
            "приложения»: он создаётся в настройках безопасности почты\n"
            "(Яндекс, Mail.ru, Gmail) и не совпадает с обычным паролем\n"
            "от ящика."
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError(
            "Почта отказалась принять адрес получателя. Проверьте адрес\n"
            "в настройках восстановления."
        ) from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise MailError(
            "Не удалось отправить письмо. Проверьте интернет и настройки\n"
            f"почты (сервер {settings['host']}, порт {settings['port']}).\n"
            f"Ответ почты: {exc}"
        ) from exc


# --------------------------------------------------- внешний вид для проверок


def describe_settings(settings) -> str:
    """Короткая строка «что настроено» для плашки в «Конфигурации»."""

    settings = normalize_mail(settings)
    if is_configured(settings):
        return (
            "Восстановление по почте настроено: код уйдёт на "
            + mask_address(settings["recipient"])
        )
    if settings["sender"] or settings["recipient"]:
        return "Восстановление по почте заполнено не полностью."
    return "Восстановление по почте не настроено."


def looks_like_address(value: str) -> bool:
    """Грубая проверка адреса: одна «собака», точка в домене."""

    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", _clean(value)))
