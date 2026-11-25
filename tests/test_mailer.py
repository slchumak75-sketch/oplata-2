"""Восстановление пароля «Конфигурации» по почте (v0.88.0).

Просьба пользователя: «А если сделать привязку к почте и получить пароль
по почте». По почте уходит **код**, а не пароль: письмо лежит в ящике
навсегда, и пароль в нём прочитал бы любой, у кого есть доступ к почте.

Здесь проверяется всё, что не требует настоящего почтового сервера:
настройки, подстановка сервера по адресу, срок жизни кода, ограничение
попыток, отсутствие кода на диске в открытом виде и то, что после смены
пароля старый код бесполезен.
"""

from __future__ import annotations

import json

import pytest

from oplata import access, mailer


@pytest.fixture()
def users_file(tmp_path, monkeypatch):
    """Отдельный ``users.json`` на каждый тест (как в остальных тестах)."""

    monkeypatch.setattr(access, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(mailer, "_read_users_file", lambda: _read(tmp_path))
    monkeypatch.setattr(mailer, "_write_users_file", lambda payload: _write(tmp_path, payload))
    return tmp_path / "users.json"


def _read(tmp_path):
    try:
        raw = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(tmp_path, payload):
    (tmp_path / "users.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def good_settings(**changes):
    payload = {
        "enabled": True,
        "sender": "oplata-bot@yandex.ru",
        "password": "app-password",
        "recipient": "ivanov@mail.ru",
    }
    payload.update(changes)
    return payload


# ------------------------------------------------------------------ настройки


def test_server_is_filled_from_known_address():
    """Достаточно адреса и пароля приложения: сервер и порт — по домену."""

    settings = mailer.normalize_mail(good_settings())

    assert settings["host"] == "smtp.yandex.ru"
    assert settings["port"] == 465
    assert settings["ssl"] is True
    # Логин — тот же адрес, что и отправка, если не указан отдельно.
    assert settings["user"] == "oplata-bot@yandex.ru"


# Имена параметров — без «@»: scripts/run_tests.sh собирает список тестов
# в выражение -k, а «@» в id pytest в нём недопустим (прогон частями
# падал на разборе выражения ещё до запуска тестов).
@pytest.mark.parametrize(
    ("domain", "host"),
    [
        ("mail.ru", "smtp.mail.ru"),
        ("bk.ru", "smtp.mail.ru"),
        ("gmail.com", "smtp.gmail.com"),
        ("ya.ru", "smtp.yandex.ru"),
    ],
)
def test_provider_presets_cover_common_mailboxes(domain, host):
    address = "a@" + domain

    assert mailer.normalize_mail(good_settings(sender=address))["host"] == host


def test_unknown_address_requires_server():
    """Неизвестная почта: сервера нет — программе надо его подсказать."""

    settings = mailer.normalize_mail(good_settings(sender="bot@firma.by"))

    assert settings["host"] == ""
    assert not mailer.is_configured(settings)
    assert "сервер почты" in mailer.describe_problems(settings)


def test_settings_are_saved_and_read_back(users_file):
    mailer.save_mail_settings(good_settings())

    loaded = mailer.load_mail_settings()

    assert loaded["sender"] == "oplata-bot@yandex.ru"
    assert loaded["recipient"] == "ivanov@mail.ru"
    assert mailer.is_configured(loaded)


def test_recipient_defaults_to_sender(users_file):
    """Не вписали «кому» — письмо идёт на тот же ящик."""

    mailer.save_mail_settings(good_settings(recipient=""))

    assert mailer.load_mail_settings()["recipient"] == "oplata-bot@yandex.ru"


def test_disabled_recovery_is_not_configured(users_file):
    mailer.save_mail_settings(good_settings(enabled=False))

    assert not mailer.is_configured()


def test_missing_app_password_is_reported():
    """Пароль приложения — единственное, что нельзя угадать: говорим о нём."""

    problems = mailer.describe_problems(good_settings(password=""))

    assert "пароль приложения" in problems


def test_address_is_masked_in_interface():
    """Полный адрес не показываем: подсказки «куда смотреть» хватает."""

    assert mailer.mask_address("ivanov@mail.ru") == "i***@mail.ru"
    assert mailer.mask_address("a@mail.ru") == "a***@mail.ru"
    assert mailer.mask_address("заявка") == "заявка"


def test_address_check():
    assert mailer.looks_like_address("ivanov@mail.ru")
    assert not mailer.looks_like_address("ivanov")
    assert not mailer.looks_like_address("ivanov@mail")
    assert not mailer.looks_like_address("a b@mail.ru")


# ----------------------------------------------------------------------- код


def test_code_is_six_digits():
    for _ in range(20):
        code = mailer.make_code()
        assert len(code) == mailer.CODE_LENGTH
        assert code.isdigit()


def test_code_is_accepted_once(users_file):
    code = mailer.start_reset()

    ok, message = mailer.check_code(code)

    assert ok is True
    assert message == "Код принят."


def test_wrong_code_is_rejected_and_attempts_are_counted(users_file):
    mailer.start_reset()

    ok, message = mailer.check_code("000000")

    assert ok is False
    assert "Осталось 4" in message


def test_attempts_run_out(users_file):
    """Пятая неудачная попытка гасит запрос: перебирать код нельзя."""

    mailer.start_reset()

    messages = [
        mailer.check_code("000000")[1] for _ in range(mailer.CODE_ATTEMPTS)
    ]

    assert "Осталось 4" in messages[0]
    assert "Осталось 1" in messages[3]
    assert "Попытки закончились" in messages[-1]
    # Запрос погашен — дальше код не принимается вовсе.
    assert not mailer.reset_active()


def test_code_expires(users_file):
    import time

    now = time.time()
    code = mailer.start_reset(now=now)

    ok, message = mailer.check_code(code, now=now + mailer.CODE_TTL_SECONDS + 1)

    assert ok is False
    assert "истёк" in message


def test_code_survives_within_its_lifetime(users_file):
    import time

    now = time.time()
    code = mailer.start_reset(now=now)

    ok, _ = mailer.check_code(code, now=now + mailer.CODE_TTL_SECONDS - 5)

    assert ok is True


def test_only_hash_is_stored(users_file):
    """Прочитав ``users.json``, код узнать нельзя — только хэш."""

    code = mailer.start_reset()

    raw = users_file.read_text(encoding="utf-8")

    assert code not in raw
    stored = json.loads(raw)["password_reset"]
    assert stored["hash"] and stored["salt"]
    assert "code" not in stored


def test_clear_reset_removes_the_request(users_file):
    mailer.start_reset()

    mailer.clear_reset()

    assert not mailer.reset_active()
    assert "password_reset" not in users_file.read_text(encoding="utf-8")


def test_seconds_left_is_counted(users_file):
    import time

    now = time.time()
    mailer.start_reset(now=now)

    assert mailer.reset_seconds_left(now=now) == mailer.CODE_TTL_SECONDS
    assert mailer.reset_seconds_left(now=now + 100) == mailer.CODE_TTL_SECONDS - 100


def test_no_request_means_no_code(users_file):
    ok, message = mailer.check_code("123456")

    assert ok is False
    assert "не запрашивался" in message


def test_new_request_replaces_the_previous_code(users_file):
    first = mailer.start_reset()
    second = mailer.start_reset()

    assert mailer.check_code(first)[0] is False
    assert mailer.check_code(second)[0] is True


# -------------------------------------------------------------------- письмо


def test_message_contains_code_and_no_password(users_file):
    settings = mailer.load_mail_settings()
    settings = mailer.normalize_mail(good_settings())

    message = mailer.build_message(settings, "123456")
    body = message.get_content()

    assert "123456" in body
    assert "10 минут" in body
    assert message["To"] == "ivanov@mail.ru"
    assert message["From"] == "oplata-bot@yandex.ru"


def test_send_code_delivers_and_returns_code(users_file, monkeypatch):
    """Отправка подменяется: настоящий SMTP в тестах не нужен."""

    sent = []

    def fake_deliver(settings, message):
        sent.append((settings, message))

    monkeypatch.setattr(mailer, "_deliver", fake_deliver)
    mailer.save_mail_settings(good_settings())

    code = mailer.send_code()

    assert len(sent) == 1
    assert code in sent[0][1].get_content()
    # Код, который ушёл письмом, — рабочий.
    assert mailer.check_code(code)[0] is True


def test_failed_sending_leaves_no_dangling_code(users_file, monkeypatch):
    """Не ушло письмо — не остаётся и запрос: иначе код «висит» зря."""

    def broken(settings, message):
        raise mailer.MailError("нет связи")

    monkeypatch.setattr(mailer, "_deliver", broken)
    mailer.save_mail_settings(good_settings())

    with pytest.raises(mailer.MailError):
        mailer.send_code()

    assert not mailer.reset_active()
    assert "password_reset" not in users_file.read_text(encoding="utf-8")


def test_send_without_settings_explains_what_is_missing(users_file):
    with pytest.raises(mailer.MailError) as error:
        mailer.send_code()

    assert "Не хватает данных" in str(error.value)


def test_authentication_error_is_explained_in_russian(users_file, monkeypatch):
    """Ошибка почты переводится: «пароль приложения», а не трейсбек."""

    import smtplib

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)

    with pytest.raises(mailer.MailError) as error:
        mailer._deliver(mailer.normalize_mail(good_settings()), None)

    assert "пароль\nприложения" in str(error.value)


def test_password_change_kills_the_code(users_file):
    """Смена пароля обычным путём убирает выданный код восстановления."""

    mailer.start_reset()
    access.set_password("новый-пароль")

    assert not mailer.reset_active()


def test_recovery_changes_the_real_password(users_file):
    """Главное: после восстановления новый пароль действительно работает."""

    access.set_password("старый-пароль")

    code = mailer.start_reset()
    assert mailer.check_code(code)[0] is True
    access.set_password("новый-пароль")
    mailer.clear_reset()

    assert access.check_password("новый-пароль")
    assert not access.check_password("старый-пароль")


def test_describe_settings_shows_mask_not_full_address(users_file):
    mailer.save_mail_settings(good_settings())

    text = mailer.describe_settings(mailer.load_mail_settings())

    assert "i***@mail.ru" in text
    assert "ivanov@mail.ru" not in text
