"""Окно «Обновление программы» (v0.87.0).

Просьба пользователя: файл обновления он получает сам (почтой или из
облака), проверяет доработку у себя и кладёт файл на Google Диск —
оттуда остальные компьютеры его видят и обновляются по подтверждению.
Доступа к GitHub у компьютеров нет и не будет.

Окно открывается кнопкой **«Обновить»** на верхней плашке — она видна
всем, без пароля: установить обновление на свой компьютер может любой
сотрудник. Кнопки **раздачи** («Положить в тест», «Выпустить»,
«Отозвать») создаются только на тестовом компьютере и только при
открытой по паролю «Конфигурации», — см. ``DataView``.

Данные при обновлении не трогаются: программа меняет только сама себя,
база лежит отдельно (в ``%APPDATA%`` или на Диске).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .. import local_settings, styles, updates
from ..storage import DataStore


def base_data_dir(store: DataStore) -> Path:
    """Папка с базой: рядом с ней лежат «Обновления» на Диске."""

    return Path(store.path).parent


def program_dir() -> Path:
    """Папка программы (где стоит Oplata.exe)."""

    override = os.environ.get("OPLATA_PROGRAM_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):  # pragma: no cover - только в сборке
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


class UpdateDialog(QDialog):
    """Окно обновления: что установлено, что нашли, что делать."""

    TITLE = "Обновление программы"

    def __init__(
        self,
        store: DataStore,
        access,
        parent: QDialog | None = None,
        *,
        publication_allowed: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.access = access
        # Без разрешения из открытой по паролю «Конфигурации» окно
        # работает только для установки обновлений.
        self._publication_allowed = publication_allowed or (lambda: False)
        self.version = _current_version()
        self.info: updates.UpdateInfo | None = None
        self.setWindowTitle(self.TITLE)
        self.resize(760, 470)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel(self.TITLE)
        title.setStyleSheet(f"font-weight:800;font-size:{styles.fs(16)}px")
        root.addWidget(title)

        self.installed_label = QLabel(f"Установлена версия {self.version}")
        root.addWidget(self.installed_label)

        self.folder_label = QLabel(self._folder_text())
        self.folder_label.setObjectName("Muted")
        self.folder_label.setWordWrap(True)
        root.addWidget(self.folder_label)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        # --- кнопки установки (видны всем, без пароля) ---
        install_row = QHBoxLayout()
        install_row.setSpacing(8)
        self.check_button = QPushButton("Проверить обновление")
        self.check_button.setToolTip(
            "Посмотреть папку обновлений на Google Диске"
        )
        self.check_button.clicked.connect(self.check_for_update)
        install_row.addWidget(self.check_button)

        self.pick_button = QPushButton("Выбрать файл…")
        self.pick_button.setToolTip(
            "Обновление, присланное письмом: архив вида "
            "«Oplata-0.87.0-Update.zip»"
        )
        self.pick_button.clicked.connect(self.pick_file)
        install_row.addWidget(self.pick_button)

        self.install_button = QPushButton("Установить")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install)
        install_row.addWidget(self.install_button)
        install_row.addStretch(1)
        root.addLayout(install_row)

        # --- раздача (только тестовый компьютер) ---
        self.publish_row = QHBoxLayout()
        self.publish_row.setSpacing(8)
        self.test_button = QPushButton("Положить в тест")
        self.test_button.clicked.connect(self.publish_to_test)
        self.release_button = QPushButton("Выпустить")
        self.release_button.setToolTip(
            "Переложить файл из «Тест» в «Выпуск»: после этого его увидят "
            "все компьютеры и предложат обновление"
        )
        self.release_button.clicked.connect(self.publish_release)
        self.retract_button = QPushButton("Отозвать")
        self.retract_button.setToolTip(
            "Убрать выпуск: у тех, кто ещё не обновился, предложение "
            "исчезнет"
        )
        self.retract_button.clicked.connect(self.retract)
        for button in (
            self.test_button,
            self.release_button,
            self.retract_button,
        ):
            self.publish_row.addWidget(button)
        self.publish_row.addStretch(1)
        root.addLayout(self.publish_row)

        self.publish_hint = QLabel("")
        self.publish_hint.setObjectName("Muted")
        self.publish_hint.setWordWrap(True)
        root.addWidget(self.publish_hint)

        root.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.close)
        bottom.addWidget(close_button)
        root.addLayout(bottom)

        self._apply_publish_visibility()
        self.check_for_update(silent=True)

    @property
    def publish_visible(self) -> bool:
        """Раздача только из конфигурации и только с тестового ПК."""
        return bool(
            self._publication_allowed()
            and local_settings.can_publish(self.access.user.id)
        )

    def set_publication_context(self, allowed: Callable[[], bool]) -> None:
        """Изменить доступ, если уже открытое окно вызвали из конфигурации."""
        self._publication_allowed = allowed
        self._apply_publish_visibility()

    # ------------------------------------------------------------- состояние
    def _folder_text(self) -> str:
        folder = updates.release_dir(base_data_dir(self.store))
        return (
            f"Папка обновлений: {updates.updates_dir(base_data_dir(self.store))}"
            "\nОтсюда обновляются все: «Выпуск» (проверяется раз в час)"
        )

    def _apply_publish_visibility(self) -> None:
        """Кнопки раздачи — только в разблокированной конфигурации тестового ПК."""

        for index in range(self.publish_row.count()):
            item = self.publish_row.itemAt(index)
            widget = item.widget()
            if widget is not None:
                widget.setVisible(self.publish_visible)
        if self.publish_visible:
            self.publish_hint.setText(
                "Это тестовый компьютер: с него можно готовить и раздавать "
                "обновления. Сначала «Положить в тест»: программа предлагает "
                "его только тестовым компьютерам. Файл в папке Диска могут "
                "открыть все, у кого есть доступ к папке. Когда убедитесь, "
                "что всё работает, нажмите «Выпустить»."
            )
        else:
            self.publish_hint.setText("")

    def _set_status(self, text: str, *, ok: bool | None = None) -> None:
        self.status_label.setText(text)
        color = styles.TEXT if ok is None else (styles.GREEN if ok else styles.RED)
        self.status_label.setStyleSheet(f"color:{color}")

    # --------------------------------------------------------------- проверка
    def check_for_update(self, checked: bool = False, *, silent: bool = False) -> None:
        """Посмотреть папку обновлений на Диске."""

        _ = checked
        folder = base_data_dir(self.store)
        try:
            info = updates.find_update(
                folder,
                self.version,
                # Установка тестового обновления на тестовом ПК доступна
                # и с верхней кнопки; пароль нужен именно для раздачи.
                allow_test=local_settings.can_publish(self.access.user.id),
            )
        except OSError as error:
            if not silent:
                self._set_status(f"Не удалось прочитать папку: {error}", ok=False)
            return
        if info is None:
            self.info = None
            self.install_button.setEnabled(False)
            if not silent:
                self._set_status(
                    "Обновлений нет: в «Выпуске» лежит не новее "
                    f"{self.version}.",
                    ok=True,
                )
            return
        self.info = info
        self.install_button.setEnabled(True)
        self._set_status(
            f"Доступно обновление: {updates.describe(info)}. "
            f"Установлена {self.version}.",
            ok=True,
        )

    def pick_file(self) -> None:
        """Выбрать файл обновления вручную (присланный письмом)."""

        start = updates.release_dir(base_data_dir(self.store))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Файл обновления",
            str(start if start.exists() else Path.home()),
            "Обновление программы (*.zip);;Все файлы (*)",
        )
        if not path:
            return
        self._load_file(Path(path))

    def _load_file(self, path: Path) -> None:
        try:
            info = updates.read_manifest(path)
        except updates.UpdateError as error:
            self.info = None
            self.install_button.setEnabled(False)
            self._set_status(str(error), ok=False)
            updates.log_event(
                base_data_dir(self.store),
                f"файл обновления отклонён: {path.name}: {error}",
                user=self.access.title,
            )
            return
        current = updates.version_tuple(self.version)
        if updates.version_tuple(info.version) <= current:
            self.info = None
            self.install_button.setEnabled(False)
            self._set_status(
                f"В файле версия {info.version} — она не новее установленной "
                f"{self.version}. Устанавливать нечего.",
                ok=False,
            )
            return
        self.info = info
        self.install_button.setEnabled(True)
        self._set_status(
            f"Файл проверен: {updates.describe(info)}. Можно устанавливать.",
            ok=True,
        )

    # -------------------------------------------------------------- установка
    def install(self) -> None:
        """Проверить установщик и запустить его; программа закроется."""

        if self.info is None:
            return
        try:
            installer = updates.check_installer(self.info)
        except updates.UpdateError as error:
            self._set_status(str(error), ok=False)
            updates.log_event(
                base_data_dir(self.store),
                f"установка отменена: {error}",
                user=self.access.title,
            )
            return

        answer = QMessageBox.question(
            self,
            "Установить обновление",
            (
                f"Установить версию {self.info.version}?\n\n"
                "Установщик рассчитан на замену файлов программы, а не "
                "данных. До обновления сохраните независимые копии обеих "
                "баз и настроек вне синхронизируемой папки. Попытка "
                "автоматической копии относится только к текущей базе, "
                "не включает настройки и может не удаться.\n\n"
                "Если Windows покажет предупреждение — нажмите «Подробнее → "
                "Выполнить в любом случае»."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            from ..storage import make_backup

            make_backup(self.store.base_key, "перед-обновлением")
        except Exception:  # noqa: BLE001 - копия не должна мешать обновлению
            pass

        updates.log_event(
            base_data_dir(self.store),
            f"запуск установки {self.info.version}: {installer.name}",
            user=self.access.title,
        )
        self._launch_installer(installer)

    def _launch_installer(self, installer: Path) -> None:
        """Запустить установщик и закрыть программу."""

        if os.name != "nt":  # pragma: no cover - в песочнице не проверяем
            # На Linux/в тестах установщик Windows не запустить: просто
            # сообщаем, что файл проверен и распакован.
            self._set_status(
                f"Установщик проверен и распакован: {installer}", ok=True
            )
            return
        args = [
            str(installer),
            "/SILENT",
            "/CLOSEAPPLICATIONS",
            "/RESTARTAPPLICATIONS",
        ]
        subprocess.Popen(args)  # noqa: S603 - путь наш, из проверенного архива
        window = self.parent()
        if window is not None and hasattr(window, "close"):
            window.close()
        else:
            self.close()

    # ---------------------------------------------------------------- раздача
    def publish_to_test(self) -> None:
        """Положить выбранный файл в «Тест» (видят только тестовые)."""

        if not self.publish_visible:
            return
        if self.info is None or self.info.path is None:
            self._set_status(
                "Сначала выберите файл обновления кнопкой «Выбрать файл…».",
                ok=False,
            )
            return
        try:
            target = updates.copy_to_test(
                self.info.path, base_data_dir(self.store)
            )
        except (updates.UpdateError, OSError) as error:
            self._set_status(str(error), ok=False)
            return
        updates.log_event(
            base_data_dir(self.store),
            f"положено в тест: {target.name}",
            user=self.access.title,
        )
        self._set_status(
            f"Файл в «Тест»: {target.name}. Обычным компьютерам программа "
            "его не предлагает; доступ к самому файлу зависит от прав "
            "общей папки. Проверьте доработку перед выпуском.",
            ok=True,
        )

    def publish_release(self) -> None:
        """Переложить файл в «Выпуск» — с этого момента обновление видят все."""

        if not self.publish_visible:
            return
        base = base_data_dir(self.store)
        candidate = updates._newest_archive(updates.test_dir(base)) if hasattr(
            updates, "_newest_archive"
        ) else None
        source: Path | None = None
        if self.info is not None and self.info.path is not None:
            source = Path(self.info.path)
        elif candidate is not None and candidate.path is not None:
            source = candidate.path
        if source is None:
            self._set_status(
                "Нет файла для выпуска: положите его в «Тест» или выберите "
                "кнопкой «Выбрать файл…».",
                ok=False,
            )
            return
        answer = QMessageBox.question(
            self,
            "Выпустить обновление",
            (
                f"Выпустить версию {self.info.version if self.info else ''}"
                " для всех компьютеров?\n\n"
                "После этого на остальных компьютерах появится предложение "
                "обновиться (устанавливается только с их подтверждения)."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        # Пока открыто подтверждение, мог истечь срок конфигурации или
        # измениться право раздачи. Проверяем его повторно перед записью.
        if answer != QMessageBox.StandardButton.Yes or not self.publish_visible:
            return
        try:
            target = updates.publish(source, base)
        except (updates.UpdateError, OSError) as error:
            self._set_status(str(error), ok=False)
            return
        updates.log_event(
            base,
            f"выпущено: {target.name}",
            user=self.access.title,
        )
        self._set_status(
            f"Выпущено: {target.name}. После синхронизации Диска "
            "остальные компьютеры обнаружат его при следующей проверке "
            "(обычно раз в час).",
            ok=True,
        )

    def retract(self) -> None:
        """Убрать выпуск из общей папки."""

        if not self.publish_visible:
            return
        base = base_data_dir(self.store)
        answer = QMessageBox.question(
            self,
            "Отозвать обновление",
            (
                "Убрать обновление из «Выпуска»?\n\n"
                "У тех, кто ещё не обновился, предложение исчезнет. "
                "Уже обновившиеся останутся на новой версии."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes or not self.publish_visible:
            return
        moved = updates.retract(base)
        updates.log_event(
            base,
            f"отозвано файлов: {len(moved)}",
            user=self.access.title,
        )
        self._set_status(
            "Выпуск отозван: в «Архив» убрано файлов — "
            f"{len(moved)}. Предложение обновиться исчезнет у всех.",
            ok=True,
        )
        self.check_for_update(silent=True)


def _current_version() -> str:
    from .. import __version__

    return __version__

