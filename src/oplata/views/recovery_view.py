"""Окна восстановления пароля «Конфигурации» по почте (v0.88.0).

Два окна, и оба открываются **до** ввода пароля — иначе восстановление
не имело бы смысла:

* :class:`PasswordRecoveryDialog` — «Забыли пароль?» в окне входа:
  отправить код на привязанную почту, ввести его, придумать новый пароль;
* :class:`MailSettingsDialog` — «Почта для восстановления» в разделе
  «Пользователи и права»: адрес, сервер, пароль приложения, проверочное
  письмо.

Что важно для пользователя
--------------------------
* Письмо уходит **сразу по кнопке**, а не через почтовую программу:
  иначе на четырёх компьютерах пришлось бы настраивать Outlook.
* Адрес получателя показан в виде ``i***@mail.ru``: подсказки «куда
  смотреть» достаточно, а полный адрес не видит каждый, кто подошёл к
  компьютеру.
* Если почта недоступна, окно честно говорит, что остаётся ручной путь
  (убрать строку ``"password"`` из ``users.json`` на Диске), — так было
  до v0.88.0, и этот путь никуда не делся.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .. import mailer, styles


def _title_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"font-weight:800;font-size:{styles.fs(16)}px")
    return label


class PasswordRecoveryDialog(QDialog):
    """Три шага: отправить код → ввести код → придумать новый пароль.

    Шаги идут по порядку на одном окне: так человек не теряет нить и
    видит, сколько ему осталось. Кнопки «назад» нет намеренно —
    восстановление короткое, а лишняя кнопка путает.
    """

    TITLE = "Восстановление пароля"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.TITLE)
        self.resize(560, 340)
        self.new_password = ""  # заполняется при успехе — его читает app.py
        self._step = "send"

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        root.addWidget(_title_label(self.TITLE))

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setObjectName("Muted")
        root.addWidget(self.info_label)

        form = QFormLayout()
        form.setSpacing(8)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("6 цифр из письма")
        self.code_edit.setMaxLength(mailer.CODE_LENGTH)
        self.code_edit.setEnabled(False)
        self.code_edit.returnPressed.connect(self.check_code)
        form.addRow("Код из письма:", self.code_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setEnabled(False)
        form.addRow("Новый пароль:", self.password_edit)

        self.repeat_edit = QLineEdit()
        self.repeat_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.repeat_edit.setEnabled(False)
        form.addRow("Повторите пароль:", self.repeat_edit)
        root.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.hint_label = QLabel(
            "Если почта недоступна: в папке базы на Google Диске откройте\n"
            "users.json и удалите строку \"password\" — программа попросит\n"
            "придумать пароль заново."
        )
        self.hint_label.setObjectName("Muted")
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)

        root.addStretch(1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        # Одна основная кнопка, роль которой зависит от шага: «Отправить
        # код» → «Продолжить» → «Сменить пароль». Три кнопки рядом
        # заставляли бы человека выбирать, какая из них сейчас главная.
        self.primary_button = QPushButton("Отправить код на почту")
        self.primary_button.clicked.connect(self.primary_action)
        buttons.addWidget(self.primary_button)

        buttons.addStretch(1)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.close_button)
        root.addLayout(buttons)

        self._refresh()

    # ------------------------------------------------------------------ шаги

    def _refresh(self) -> None:
        """Показать/спрятать поля под текущий шаг."""

        configured = mailer.is_configured()
        active = mailer.reset_active()
        if self._step == "send":
            if configured:
                settings = mailer.load_mail_settings()
                self.info_label.setText(
                    "Код придёт письмом на "
                    + mailer.mask_address(settings["recipient"])
                    + ". Это займёт несколько секунд."
                )
                self.primary_button.setText("Отправить код на почту")
            else:
                self.info_label.setText(
                    "Почта для восстановления не настроена. Попросите\n"
                    "администратора включить её в разделе «Конфигурация» →\n"
                    "«Пользователи и права», либо воспользуйтесь ручным\n"
                    "способом ниже."
                )
                self.primary_button.setText("Почта не настроена")
            self.primary_button.setEnabled(configured)
            self.code_edit.setEnabled(False)
            self.password_edit.setEnabled(False)
            self.repeat_edit.setEnabled(False)
        elif self._step == "code":
            left = mailer.reset_seconds_left()
            self.info_label.setText(
                f"Код отправлен. Действует ещё {left // 60} мин "
                f"{left % 60:02d} с."
            )
            self.primary_button.setText("Продолжить")
            self.primary_button.setEnabled(True)
            self.code_edit.setEnabled(True)
            self.password_edit.setEnabled(False)
            self.repeat_edit.setEnabled(False)
            self.code_edit.setFocus()
        else:  # "password"
            self.info_label.setText("Код принят. Придумайте новый пароль.")
            self.primary_button.setText("Сменить пароль и войти")
            self.primary_button.setEnabled(True)
            self.code_edit.setEnabled(False)
            self.password_edit.setEnabled(True)
            self.repeat_edit.setEnabled(True)
            self.password_edit.setFocus()
        self.status_label.setText("")
        if active and self._step != "password":
            self._start_ticker()

    def _start_ticker(self) -> None:
        """Раз в секунду обновлять «сколько осталось» — без нового запроса."""

        timer = getattr(self, "_ticker", None)
        if timer is None:
            timer = QTimer(self)
            timer.timeout.connect(self._tick)
            self._ticker = timer
        timer.start(1000)

    def _tick(self) -> None:
        if self._step == "code":
            left = mailer.reset_seconds_left()
            if left <= 0:
                self._ticker.stop()
                self._step = "send"
                self._refresh()
                self.status_label.setText(
                    "Срок действия кода истёк. Запросите новый."
                )
            else:
                self.info_label.setText(
                    f"Код отправлен. Действует ещё {left // 60} мин "
                    f"{left % 60:02d} с."
                )

    # ------------------------------------------------------------- действия

    def primary_action(self) -> None:
        """Одна кнопка на три шага: отправить → проверить → сменить."""

        if self._step == "send":
            self.send_code()
        elif self._step == "code":
            self.check_code()
        else:
            self.apply_new_password()

    def send_code(self) -> None:
        """Отправить код письмом."""

        try:
            mailer.send_code()
        except mailer.MailError as exc:
            self.status_label.setText(str(exc))
            return
        except Exception as exc:  # pragma: no cover - непредвиденное
            self.status_label.setText(f"Письмо не отправлено: {exc}")
            return
        self._step = "code"
        self._refresh()

    def check_code(self) -> None:
        """Проверить введённый код и перейти к придумыванию пароля."""

        ok, message = mailer.check_code(self.code_edit.text())
        if not ok:
            self.status_label.setText(message)
            return
        self._step = "password"
        self._refresh()
        self.status_label.setText(message)

    def apply_new_password(self) -> None:
        """Сохранить новый пароль и закрыть окно с успехом."""

        from ..access import set_password

        text = self.password_edit.text()
        if not text:
            self.status_label.setText("Пароль не может быть пустым.")
            return
        if text != self.repeat_edit.text():
            self.status_label.setText("Пароли не совпадают.")
            return
        if len(text) < 4:
            self.status_label.setText("Пароль слишком короткий: нужно хотя бы 4 символа.")
            return
        set_password(text)
        mailer.clear_reset()
        self.new_password = text
        self.accept()

    def accept(self) -> None:  # noqa: D401 - поведение Qt
        timer = getattr(self, "_ticker", None)
        if timer is not None:
            timer.stop()
        super().accept()

    def closeEvent(self, event) -> None:  # noqa: N802 - имя из Qt
        timer = getattr(self, "_ticker", None)
        if timer is not None:
            timer.stop()
        super().closeEvent(event)


class PasswordPromptDialog(QDialog):
    """Окно ввода пароля с кнопкой «Забыли пароль?».

    Появляется вместо простого запроса пароля **только если почта для
    восстановления настроена**: иначе кнопка вела бы в тупик. Пока почта
    не настроена, программа спрашивает пароль как раньше — коротким
    запросом.

    После успешного восстановления окно закрывается как «вход
    выполнен»: человек только что подтвердил доступ почтой, второй раз
    спрашивать пароль (который он сию секунду придумал) незачем.
    """

    def __init__(self, title: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 210)
        self.password = ""
        self.recovered = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        root.addWidget(_title_label(title))

        self.label = QLabel(label)
        self.label.setWordWrap(True)
        root.addWidget(self.label)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.returnPressed.connect(self.accept)
        root.addWidget(self.password_edit)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        root.addStretch(1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.forgot_button = QPushButton("Забыли пароль?")
        self.forgot_button.setToolTip(
            "Прислать код на почту и придумать новый пароль"
        )
        self.forgot_button.clicked.connect(self.recover)
        buttons.addWidget(self.forgot_button)
        buttons.addStretch(1)
        ok_button = QPushButton("Войти")
        ok_button.setDefault(True)
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(ok_button)
        cancel_button = QPushButton("Отмена")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        root.addLayout(buttons)

    def accept(self) -> None:  # noqa: D401 - поведение Qt
        self.password = self.password_edit.text()
        super().accept()

    def recover(self) -> None:
        """Открыть восстановление по почте; при успехе — войти."""

        dialog = PasswordRecoveryDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not dialog.new_password:
            return
        self.recovered = True
        self.password = dialog.new_password
        super().accept()


class MailSettingsDialog(QDialog):
    """Настройка почты: адрес, сервер, пароль приложения.

    Открывается из «Конфигурации» (раздел «Пользователи и права») —
    то есть человек уже ввёл пароль. Есть кнопка «Отправить проверочное
    письмо»: без неё ошибку в пароле приложения видно только в момент,
    когда пароль понадобился, — то есть когда его уже никто не помнит.
    """

    TITLE = "Почта для восстановления пароля"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.TITLE)
        self.resize(620, 420)

        self.settings = mailer.load_mail_settings()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        root.addWidget(_title_label(self.TITLE))

        explain = QLabel(
            "Если пароль на «Конфигурацию» забудут, программа пришлёт на\n"
            "эту почту код, по которому можно будет придумать новый.\n\n"
            "Нужен «пароль приложения» — он создаётся в настройках\n"
            "безопасности почты (Яндекс, Mail.ru, Gmail) и не совпадает с\n"
            "обычным паролем от ящика. Для программы лучше завести\n"
            "отдельный ящик: настройки лежат в общем файле на Диске."
        )
        explain.setWordWrap(True)
        explain.setObjectName("Muted")
        root.addWidget(explain)

        form = QFormLayout()
        form.setSpacing(8)

        self.sender_edit = QLineEdit(self.settings["sender"])
        self.sender_edit.setPlaceholderText("например oplata@yandex.ru")
        form.addRow("Почта, с которой отправлять:", self.sender_edit)

        self.password_edit = QLineEdit(self.settings["password"])
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("пароль приложения")
        form.addRow("Пароль приложения:", self.password_edit)

        self.recipient_edit = QLineEdit(self.settings["recipient"])
        self.recipient_edit.setPlaceholderText(
            "куда присылать код; пусто — на адрес отправки"
        )
        form.addRow("Кому присылать код:", self.recipient_edit)

        provider_row = QHBoxLayout()
        provider_row.setSpacing(6)
        self.host_edit = QLineEdit(self.settings["host"])
        self.host_edit.setPlaceholderText("smtp.yandex.ru")
        provider_row.addWidget(self.host_edit, 2)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(self.settings["port"])
        provider_row.addWidget(self.port_spin, 1)
        self.ssl_check = QCheckBox("SSL")
        self.ssl_check.setChecked(self.settings["ssl"])
        self.ssl_check.setToolTip(
            "Почта с защитой: Яндекс, Mail.ru и Gmail работают по SSL (порт 465)"
        )
        provider_row.addWidget(self.ssl_check)
        form.addRow("Сервер почты:", provider_row)

        self.enabled_check = QCheckBox("Восстановление по почте включено")
        self.enabled_check.setChecked(self.settings["enabled"])
        form.addRow("", self.enabled_check)

        root.addLayout(form)

        self.status_label = QLabel(mailer.describe_settings(self.settings))
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("Muted")
        root.addWidget(self.status_label)

        root.addStretch(1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.test_button = QPushButton("Отправить проверочное письмо")
        self.test_button.setToolTip(
            "Проверить настройки сразу, а не в тот день, когда пароль забыт"
        )
        self.test_button.clicked.connect(self.send_test)
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        self.save_button = QPushButton("Сохранить")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.save_button)
        cancel_button = QPushButton("Закрыть")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        root.addLayout(buttons)

        # Если адрес отправки меняют — сервер и порт подставляем сами.
        self.sender_edit.textChanged.connect(self._apply_provider)

    def _apply_provider(self, text: str) -> None:
        """Подставить сервер и порт известной почты (если не меняли руками)."""

        domain = text.rsplit("@", 1)[-1].lower() if "@" in text else ""
        preset = mailer.PROVIDER_PRESETS.get(domain)
        if not preset:
            return
        host, port, use_ssl = preset
        self.host_edit.setText(host)
        self.port_spin.setValue(port)
        self.ssl_check.setChecked(use_ssl)

    def collect(self) -> dict:
        """Собрать поля в настройки (с проверкой адресов)."""

        payload = {
            "enabled": self.enabled_check.isChecked(),
            "sender": self.sender_edit.text(),
            "recipient": self.recipient_edit.text(),
            "host": self.host_edit.text(),
            "port": self.port_spin.value(),
            "ssl": self.ssl_check.isChecked(),
            "password": self.password_edit.text(),
        }
        return mailer.normalize_mail(payload)

    def save(self) -> None:
        settings = self.collect()
        if settings["enabled"]:
            for address in (settings["sender"], settings["recipient"]):
                if address and not mailer.looks_like_address(address):
                    self.status_label.setText(
                        f"Адрес «{address}» выглядит неверно: нужен вид имя@почта.ru."
                    )
                    return
            problems = mailer.describe_problems(settings)
            if problems:
                self.status_label.setText(problems)
                return
        self.settings = mailer.save_mail_settings(settings)
        self.status_label.setText(mailer.describe_settings(self.settings))
        QMessageBox.information(self, self.TITLE, "Настройки почты сохранены.")
        super().accept()

    def send_test(self) -> None:
        settings = self.collect()
        problems = mailer.describe_problems(settings)
        if problems:
            self.status_label.setText(problems)
            return
        try:
            mailer.send_test_message(settings)
        except mailer.MailError as exc:
            self.status_label.setText(str(exc))
            return
        except Exception as exc:  # pragma: no cover - непредвиденное
            self.status_label.setText(f"Письмо не отправлено: {exc}")
            return
        self.status_label.setText(
            "Проверочное письмо отправлено на "
            + mailer.mask_address(settings["recipient"])
            + ". Если его нет — проверьте папку «Спам»."
        )
