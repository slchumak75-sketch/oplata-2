"""Окна восстановления пароля по почте (v0.88.0).

Проверяем то, что видит человек: «Забыли пароль?» в окне входа, шаги
«отправить код → ввести → придумать новый», окно настройки почты и то,
что после восстановления «Конфигурация» действительно открывается.

Почта подменяется — настоящий сервер в прогоне не нужен и не должен
быть нужен.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from oplata import access as access_module  # noqa: E402
from oplata import mailer  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    """Единственный QApplication на прогон (как в test_ui_smoke.py)."""

    import sys

    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture()
def store(tmp_path):
    from oplata.storage import DataStore

    return DataStore(tmp_path / "data.json")


@pytest.fixture()
def mailbox(tmp_path, monkeypatch):
    """Настроенная почта + подмена отправки: письма «складываются» сюда."""

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    sent: list[str] = []

    def fake_deliver(settings, message):
        sent.append(message.get_content())

    monkeypatch.setattr(mailer, "_deliver", fake_deliver)
    mailer.save_mail_settings(
        {
            "enabled": True,
            "sender": "bot@yandex.ru",
            "password": "app-password",
            "recipient": "ivanov@mail.ru",
        }
    )
    return sent


def last_code(sent: list[str]) -> str:
    """Вытащить код из последнего письма так, как это сделал бы человек."""

    import re

    match = re.search(r"Код: (\d{6})", sent[-1])
    assert match, sent[-1]
    return match.group(1)


def test_recovery_dialog_walks_three_steps(qt_app, mailbox):
    """Отправить код → ввести код → придумать пароль."""

    from oplata.views.recovery_view import PasswordRecoveryDialog

    access_module.set_password("старый-пароль")
    dialog = PasswordRecoveryDialog()
    assert dialog.primary_button.text() == "Отправить код на почту"

    dialog.primary_action()  # шаг 1: письмо
    assert len(mailbox) == 1
    assert dialog.code_edit.isEnabled()
    assert dialog.primary_button.text() == "Продолжить"

    dialog.code_edit.setText("000000")  # неверный код
    dialog.primary_action()
    assert "неверный" in dialog.status_label.text()
    assert not dialog.password_edit.isEnabled()

    dialog.code_edit.setText(last_code(mailbox))  # верный
    dialog.primary_action()
    assert dialog.password_edit.isEnabled()
    assert dialog.primary_button.text() == "Сменить пароль и войти"

    dialog.password_edit.setText("новый-пароль")
    dialog.repeat_edit.setText("новый-пароль")
    dialog.primary_action()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.new_password == "новый-пароль"
    assert access_module.check_password("новый-пароль")
    # Код погашен: второй раз им не войти.
    assert not mailer.reset_active()
    dialog.close()


def test_passwords_must_match(qt_app, mailbox):
    from oplata.views.recovery_view import PasswordRecoveryDialog

    access_module.set_password("старый-пароль")
    dialog = PasswordRecoveryDialog()
    dialog.primary_action()
    dialog.code_edit.setText(last_code(mailbox))
    dialog.primary_action()

    dialog.password_edit.setText("один")
    dialog.repeat_edit.setText("другой")
    dialog.primary_action()

    assert "не совпадают" in dialog.status_label.text()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert access_module.check_password("старый-пароль")
    dialog.close()


def test_empty_password_is_rejected(qt_app, mailbox):
    from oplata.views.recovery_view import PasswordRecoveryDialog

    access_module.set_password("старый-пароль")
    dialog = PasswordRecoveryDialog()
    dialog.primary_action()
    dialog.code_edit.setText(last_code(mailbox))
    dialog.primary_action()

    dialog.primary_action()  # пустые поля

    assert "не может быть пустым" in dialog.status_label.text()
    dialog.close()


def test_dialog_says_when_mail_is_not_configured(qt_app, tmp_path, monkeypatch):
    """Почта не настроена — честно говорим и даём ручной путь."""

    from oplata.views.recovery_view import PasswordRecoveryDialog

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    access_module.set_password("старый-пароль")

    dialog = PasswordRecoveryDialog()

    assert "не настроена" in dialog.info_label.text()
    assert not dialog.primary_button.isEnabled()
    assert "users.json" in dialog.hint_label.text()
    dialog.close()


def test_expired_code_returns_to_first_step(qt_app, mailbox, monkeypatch):
    """Код истёк — окно возвращается к «Отправить код», а не молчит."""

    from oplata.views.recovery_view import PasswordRecoveryDialog

    access_module.set_password("старый-пароль")
    dialog = PasswordRecoveryDialog()
    dialog.primary_action()

    monkeypatch.setattr(mailer, "reset_seconds_left", lambda now=None: 0)
    dialog._tick()

    assert dialog.primary_button.text() == "Отправить код на почту"
    assert "истёк" in dialog.status_label.text()
    dialog.close()


def test_prompt_offers_forgot_button_when_mail_configured(qt_app, mailbox):
    from oplata.views.recovery_view import PasswordPromptDialog

    dialog = PasswordPromptDialog("Конфигурация", "Пароль:")

    assert dialog.forgot_button.text() == "Забыли пароль?"
    assert not dialog.forgot_button.isHidden()
    # Поле ввода пароля на месте — восстановление не заменяет обычный вход.
    assert dialog.password_edit.isEnabled()
    assert not dialog.password_edit.isHidden()
    dialog.close()


def test_prompt_returns_typed_password(qt_app, mailbox):
    from oplata.views.recovery_view import PasswordPromptDialog

    dialog = PasswordPromptDialog("Конфигурация", "Пароль:")
    dialog.password_edit.setText("1234")

    dialog.accept()

    assert dialog.password == "1234"
    assert dialog.recovered is False


def test_recovery_through_prompt_logs_in(qt_app, mailbox, monkeypatch):
    """После восстановления окно входа закрывается как «вошёл»."""

    from oplata.views.recovery_view import PasswordPromptDialog

    access_module.set_password("старый-пароль")
    dialog = PasswordPromptDialog("Конфигурация", "Пароль:")

    class FakeRecovery:
        new_password = "восстановленный"

        def __init__(self, parent=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        "oplata.views.recovery_view.PasswordRecoveryDialog", FakeRecovery
    )

    dialog.recover()

    assert dialog.recovered is True
    assert dialog.password == "восстановленный"
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_mail_settings_dialog_collects_fields(qt_app, tmp_path, monkeypatch):
    from oplata.views.recovery_view import MailSettingsDialog

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    dialog = MailSettingsDialog()

    dialog.enabled_check.setChecked(True)
    dialog.sender_edit.setText("helper@mail.ru")
    dialog.password_edit.setText("секрет")
    dialog.recipient_edit.setText("boss@mail.ru")

    settings = dialog.collect()

    assert settings["enabled"] is True
    assert settings["sender"] == "helper@mail.ru"
    # Сервер и порт подставились по адресу.
    assert settings["host"] == "smtp.mail.ru"
    assert settings["port"] == 465
    assert settings["ssl"] is True
    dialog.close()


def test_mail_settings_rejects_broken_address(qt_app, tmp_path, monkeypatch):
    from oplata.views.recovery_view import MailSettingsDialog

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    dialog = MailSettingsDialog()
    dialog.enabled_check.setChecked(True)
    dialog.sender_edit.setText("не-адрес")
    dialog.password_edit.setText("секрет")

    dialog.save()

    assert "выглядит неверно" in dialog.status_label.text()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.close()


def test_mail_settings_send_test_mail(qt_app, mailbox, tmp_path, monkeypatch):
    from oplata.views.recovery_view import MailSettingsDialog

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    dialog = MailSettingsDialog()
    dialog.enabled_check.setChecked(True)
    dialog.sender_edit.setText("bot@yandex.ru")
    dialog.password_edit.setText("app-password")
    dialog.recipient_edit.setText("ivanov@mail.ru")

    dialog.send_test()

    assert len(mailbox) == 1
    assert "почта настроена" in mailbox[-1] or "проверка связи" in mailbox[-1]
    assert "отправлено" in dialog.status_label.text()
    dialog.close()


def test_mail_settings_explains_bad_app_password(qt_app, tmp_path, monkeypatch):
    """Ошибка почты показывается словами, а не падением."""

    from oplata.views.recovery_view import MailSettingsDialog

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)

    def broken(settings, message):
        raise mailer.MailError("Почта не приняла пароль приложения.")

    monkeypatch.setattr(mailer, "_deliver", broken)
    dialog = MailSettingsDialog()
    dialog.enabled_check.setChecked(True)
    dialog.sender_edit.setText("bot@yandex.ru")
    dialog.password_edit.setText("плохой")
    dialog.recipient_edit.setText("ivanov@mail.ru")

    dialog.send_test()

    assert "не приняла пароль" in dialog.status_label.text()
    dialog.close()


def test_configuration_shows_mail_button_and_status(qt_app, store, mailbox):
    """В «Пользователи и права» есть кнопка и видно, куда уйдёт код."""

    from oplata.app import MainWindow

    access_module.set_password("1234")
    _admin, _manager, _mir, _kor = access_module.default_users()
    window = MainWindow(store, access_module.Access(_admin))
    view = window.data_view

    assert view.users_mail_btn.text() == "Почта для восстановления"
    assert "i***@mail.ru" in view.users_mail_label.text()
    assert "ivanov@mail.ru" not in view.users_mail_label.text()
    window.close()


def test_configured_mail_switches_password_input_to_dialog(
    qt_app, store, mailbox, monkeypatch
):
    """Настроенная почта включает вход через окно с «Забыли пароль?».

    Пока почта не настроена, остаётся прежний короткий запрос — иначе
    кнопка «Забыли пароль?» вела бы в тупик.
    """

    from PySide6.QtWidgets import QInputDialog

    from oplata.app import MainWindow

    access_module.set_password("1234")
    _admin, _manager, _mir, _kor = access_module.default_users()
    window = MainWindow(store, access_module.Access(_admin))

    opened: list[str] = []

    class FakeDialog:
        password = "1234"

        def __init__(self, title, label, parent=None):
            opened.append(title)

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("oplata.views.recovery_view.PasswordPromptDialog", FakeDialog)

    assert window._ask_config_password() is True
    assert opened == ["Конфигурация"]

    # Без настроенной почты — обычный запрос пароля.
    monkeypatch.setattr(
        mailer, "is_configured", lambda payload=None: False
    )
    asked: list[str] = []

    def fake_get_text(*args, **kwargs):
        asked.append(args[1] if len(args) > 1 else "")
        return "1234", True

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(fake_get_text))

    assert window._ask_config_password() is True
    assert asked, "пароль должен спрашиваться коротким запросом"
    window.close()


def test_mail_settings_stored_in_shared_users_file(tmp_path, monkeypatch):
    """Настройки лежат в общем users.json: их видят все компьютеры."""

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    mailer.save_mail_settings(
        {
            "enabled": True,
            "sender": "bot@yandex.ru",
            "password": "app-password",
            "recipient": "ivanov@mail.ru",
        }
    )

    raw = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))

    assert raw["mail"]["sender"] == "bot@yandex.ru"
