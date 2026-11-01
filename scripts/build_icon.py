"""Сборка иконки приложения «Оплата» (монета с галочкой и символом Br).

Исходники (каталог ``icons/``):

``oplata-coin-base.png``
    основа 1024×1024: золотая монета и зелёная галочка, без символа;
``br-symbol.png``
    символ белорусского рубля «Br» — белый символ на прозрачном фоне,
    снятый с эталонного изображения (см. ADR-077 в docs/DECISIONS.md).

Результат:

``icons/oplata-source.png``
    мастер 1024×1024 (исходник для дальнейших правок);
``assets/oplata.png``
    256×256 — иконка окна программы (``QApplication.setWindowIcon``);
``assets/oplata.ico``
    набор размеров 16…256 — иконка EXE-файла Windows (PyInstaller).

Запуск::

    python scripts/build_icon.py

Нужен только Pillow (в рантайме программы не участвует — скрипт
запускается вручную, когда меняется иконка).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "icons"
ASSETS = ROOT / "assets"

BASE = ICONS / "oplata-coin-base.png"
SYMBOL = ICONS / "br-symbol.png"

SOURCE = ICONS / "oplata-source.png"
PNG256 = ASSETS / "oplata.png"
ICO = ASSETS / "oplata.ico"

# Цвет символа (как в исходном варианте иконки) и его размеры/позиция
# на монете: высота 360 px при диаметре монеты ~730 px.
SYMBOL_COLOR = (55, 55, 52)
SYMBOL_HEIGHT = 360
SYMBOL_CENTER = (512, 508)
# Светлая кромка снизу-справа: символ выглядит выдавленным на монете.
EMBOSS_COLOR = (255, 232, 160)
EMBOSS_OFFSET = 2

ICO_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
             (64, 64), (96, 96), (128, 128), (256, 256)]


def symbol_masks(height: int):
    """Маски символа: ``(alpha, emboss)`` — сам символ и светлая кромка."""
    symbol = Image.open(SYMBOL).convert("RGBA")
    width = max(1, round(height * symbol.width / symbol.height))
    symbol = symbol.resize((width, height), Image.LANCZOS)
    alpha = symbol.getchannel("A")
    # Чуть плотнее: после сжатия LANCZOS тонкие края бледнеют.
    alpha = alpha.point(lambda value: min(255, int(value * 1.35)))
    shifted = alpha.transform(
        alpha.size,
        Image.AFFINE,
        (1, 0, -EMBOSS_OFFSET, 0, 1, -EMBOSS_OFFSET),
    )
    emboss = ImageChops.subtract(shifted, alpha).point(
        lambda value: int(value * 0.55)
    )
    return alpha, emboss


def compose(height: int = SYMBOL_HEIGHT,
            center: tuple[int, int] = SYMBOL_CENTER) -> Image.Image:
    """Накладывает символ Br на монету."""
    base = Image.open(BASE).convert("RGB")
    alpha, emboss = symbol_masks(height)
    width = alpha.width
    box = (round(center[0] - width / 2), round(center[1] - height / 2))

    layer = Image.new("RGB", alpha.size, EMBOSS_COLOR)
    base.paste(layer, box, emboss)
    layer = Image.new("RGB", alpha.size, SYMBOL_COLOR)
    base.paste(layer, box, alpha)
    return base


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    source = compose()
    source.save(SOURCE)
    print(f"мастер 1024×1024: {SOURCE.relative_to(ROOT)}")

    small = source.resize((256, 256), Image.LANCZOS)
    small.save(PNG256)
    print(f"PNG 256×256:      {PNG256.relative_to(ROOT)}")

    small.save(ICO, sizes=ICO_SIZES)
    sizes = ", ".join(str(size[0]) for size in ICO_SIZES)
    print(f"ICO ({sizes}): {ICO.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
