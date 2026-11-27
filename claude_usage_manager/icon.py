"""Значок приложения на основе фирменного знака R1."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from .utils import app_root


def brand_svg() -> Path:
    return app_root() / "assets" / "r1.svg"


def build_icon(target: Path | None = None) -> Path:
    """Создает ICO с набором размеров для окна, области уведомлений и установщика."""
    target = target or (app_root() / "assets" / "icon.ico")
    source = brand_svg()
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError(f"Не удалось загрузить знак R1: {source}")

    size = 256
    image = QImage(size, size, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    with QPainter(image) as painter:
        renderer.render(painter)
    raster = Image.frombytes("RGBA", (size, size), bytes(image.constBits()))

    target.parent.mkdir(parents=True, exist_ok=True)
    sizes = [(side, side) for side in (16, 24, 32, 48, 64, 128, size)]
    raster.save(target, format="ICO", sizes=sizes)
    return target


def ensure_icon() -> Path:
    target = app_root() / "assets" / "icon.ico"
    if not target.exists():
        return build_icon(target)
    return target


if __name__ == "__main__":
    print(build_icon())
