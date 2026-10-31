"""Пользователи и права доступа (v0.18.0).

Программа стоит на четырёх компьютерах, соединённых интернетом
(ADR-029). Схема, согласованная с пользователем:

* **Пользователи заводятся в «Конфигурации»** — их можно править и
  добавлять новых. Список общий для всех компьютеров и лежит в
  ``users.json`` рядом с файлами данных (не внутри базы, потому что
  баз две, а пользователи одни).
* **При установке на компьютер выбирается, кто здесь работает** —
  выбор сохраняется в ``current-user.json`` на этом компьютере.
* **Дальше применяются заложенные ограничения**: какие вкладки видны,
  какая база, можно ли вводить данные.
* **«Конфигурация» открывается по паролю** на любом компьютере.
  Пароль один, придумывает его администратор при первом открытии.

Права применяются в интерфейсе (скрытие вкладок и кнопок, запрет
ввода), а не в хранилище: это защита от случайных действий, а не от
злоумышленника. При появлении сервера проверку можно перенести на
сервер, не трогая экраны.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field

from .storage import BASE_KORELICHI, BASE_MIR, data_dir

# Индексы вкладок главного окна (должны совпадать с app.NAV_TITLES).
IDX_RECEIPTS, IDX_SUPPLIERS, IDX_CUSTOMERS, IDX_DATA = range(4)
PAGE_TITLES = (
    "Поступление и оплаты",
    "Поставщики",
    "Покупатели",
    "Конфигурация",
)
ALL_PAGES = (IDX_RECEIPTS, IDX_SUPPLIERS, IDX_CUSTOMERS, IDX_DATA)

USERS_FILE_NAME = "users.json"
CURRENT_USER_FILE_NAME = "current-user.json"


def users_path():
    """Файл со списком пользователей (общий для всех компьютеров)."""
    return data_dir() / USERS_FILE_NAME


def current_user_path():
    """Файл с выбором пользователя на этом компьютере."""
    return data_dir() / CURRENT_USER_FILE_NAME


# ------------------------------------------------------------------ модель


@dataclass
class User:
    """Один пользователь программы и его права."""

    id: str
    name: str
    pages: list[int] = field(default_factory=lambda: [IDX_CUSTOMERS])
    base: str | None = None  # None — обе базы; иначе BASE_MIR/BASE_KORELICHI
    can_edit: bool = False
    can_switch_base: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "pages": list(self.pages),
            "base": self.base,
            "can_edit": self.can_edit,
            "can_switch_base": self.can_switch_base,
        }

    @staticmethod
    def from_dict(raw: dict) -> "User":
        pages = raw.get("pages") or [IDX_CUSTOMERS]
        pages = [p for p in pages if isinstance(p, int) and p in ALL_PAGES]
        base = raw.get("base")
        return User(
            id=str(raw.get("id") or secrets.token_hex(4)),
            name=str(raw.get("name") or "Пользователь"),
            pages=pages or [IDX_CUSTOMERS],
            base=base if base in (BASE_MIR, BASE_KORELICHI) else None,
            can_edit=bool(raw.get("can_edit")),
            can_switch_base=bool(raw.get("can_switch_base")),
        )


def new_user_id() -> str:
    """Служебный id для нового пользователя."""

    return secrets.token_hex(4)


def default_users() -> list[User]:
    """Четыре пользователя, о которых договорились (v0.18.0)."""

    return [
        User(
            id="admin",
            name="Администратор",
            pages=list(ALL_PAGES),
            base=None,
            can_edit=True,
            can_switch_base=True,
        ),
        User(
            id="manager",
            name="Руководитель",
            pages=list(ALL_PAGES),
            base=None,
            can_edit=False,
            can_switch_base=True,
        ),
        User(
            id="user-mir",
            name="Пользователь МИР",
            pages=[IDX_CUSTOMERS],
            base=BASE_MIR,
            can_edit=False,
            can_switch_base=False,
        ),
        User(
            id="user-korelichi",
            name="Пользователь КОРЕЛИЧИ",
            pages=[IDX_CUSTOMERS],
            base=BASE_KORELICHI,
            can_edit=False,
            can_switch_base=False,
        ),
    ]


# ------------------------------------------------------- файл пользователей


def _read_users_file() -> dict:
    try:
        raw = json.loads(users_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_users_file(payload: dict) -> None:
    try:
        users_path().parent.mkdir(parents=True, exist_ok=True)
        users_path().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        # Незаписанный список пользователей не должен ронять программу.
        pass


def load_users() -> list[User]:
    """Список пользователей; при первом обращении — четыре договорных."""

    raw = _read_users_file()
    users = [User.from_dict(item) for item in raw.get("users") or []]
    if not users:
        users = default_users()
        save_users(users)
    return users


def save_users(users: list[User]) -> None:
    """Записать список пользователей (пароль не трогаем)."""

    raw = _read_users_file()
    raw["users"] = [user.to_dict() for user in users]
    _write_users_file(raw)


def find_user(user_id: str) -> User | None:
    for user in load_users():
        if user.id == user_id:
            return user
    return None


# ------------------------------------------------------------------- пароль


def _hash_password(raw: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{raw}".encode("utf-8")).hexdigest()


def has_password() -> bool:
    """Задан ли пароль на «Конфигурацию»."""
    return bool(_read_users_file().get("password"))


def set_password(raw: str) -> None:
    """Задать (или сменить) пароль на «Конфигурацию»."""

    salt = secrets.token_hex(8)
    data = _read_users_file()
    data["password"] = {"salt": salt, "hash": _hash_password(raw, salt)}
    _write_users_file(data)


def check_password(raw: str) -> bool:
    """Проверить пароль на «Конфигурацию»."""

    stored = _read_users_file().get("password")
    if not isinstance(stored, dict):
        return False
    salt = str(stored.get("salt") or "")
    return _hash_password(raw, salt) == stored.get("hash")


# ------------------------------------------ кто работает на этом компьютере


def load_current_user() -> User | None:
    """Пользователь, выбранный на этом компьютере (None — не выбран)."""

    try:
        raw = json.loads(current_user_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    user_id = raw.get("user_id") if isinstance(raw, dict) else None
    return find_user(str(user_id)) if user_id else None


def save_current_user(user: User) -> None:
    """Запомнить, кто работает на этом компьютере."""

    try:
        current_user_path().parent.mkdir(parents=True, exist_ok=True)
        current_user_path().write_text(
            json.dumps({"user_id": user.id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


# ------------------------------------------------------------------- права


class Access:
    """Права текущего пользователя (по выбранному на компьютере)."""

    def __init__(self, user: User | None = None) -> None:
        self.user = user or default_users()[0]  # без настроек — Администратор

    @property
    def title(self) -> str:
        return self.user.name

    @property
    def can_edit(self) -> bool:
        """Разрешён ли ввод данных."""
        return self.user.can_edit

    @property
    def pages(self) -> tuple[int, ...]:
        """Какие вкладки видны пользователю."""
        return tuple(sorted(set(self.user.pages))) or (IDX_CUSTOMERS,)

    @property
    def fixed_base(self) -> str | None:
        """База, на которую «посажен» пользователь (None — обе)."""
        return self.user.base

    @property
    def can_switch_base(self) -> bool:
        return self.user.can_switch_base and self.user.base is None

    @property
    def can_admin_data(self) -> bool:
        """Раздел «Администрирование» вкладки «Конфигурация»."""
        return self.user.can_edit
