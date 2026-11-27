"""Генерация значка приложения средствами Pillow (без внешних файлов)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .utils import app_root


def _draw(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = max(1, size // 16)
    # Круглый фон с фирменным оранжевым.
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(215, 131, 79, 255),
    )
    # Простая «свеча»/столбик расхода в центре.
    bar_w = size // 6
    gap = size // 12
    base_y = size - margin * 3
    heights = [size // 3, size // 2, int(size / 1.7)]
    start_x = (size - (bar_w * 3 + gap * 2)) // 2
    for index, height in enumerate(heights):
        x0 = start_x + index * (bar_w + gap)
        draw.rounded_rectangle(
            [x0, base_y - height, x0 + bar_w, base_y],
            radius=max(1, bar_w // 3),
            fill=(255, 255, 255, 235),
        )
    return image


def build_icon(target: Path | None = None) -> Path:
    """Создаёт assets/icon.ico с набором размеров. Возвращает путь."""
    target = target or (app_root() / "assets" / "icon.ico")
    target.parent.mkdir(parents=True, exist_ok=True)
    base = _draw(256)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(target, format="ICO", sizes=sizes)
    return target


def ensure_icon() -> Path:
    target = app_root() / "assets" / "icon.ico"
    if not target.exists():
        return build_icon(target)
    return target



if __name__ == "__main__":
    print(build_icon())
